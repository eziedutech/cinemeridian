"""Gemini vision, used in two very different ways.

`observe_frame` is the bulk pass: cheap, structured, run over every sampled
frame at ingest to turn pixels into rows. `adjudicate_pair` is the expensive
pass: two frames at once, run only on the handful of contradictions the
database has already flagged as worth a human's attention.

Both return structured output through `response_schema`. Nothing here parses
prose - a continuity measurement recovered by regex from a sentence is a
measurement nobody should trust.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from google.genai import Client, types
from google.genai.errors import APIError

from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: The closed vocabulary. Keeping it small is what makes the cross-take
#: self-join possible: two takes can only contradict each other on an
#: attribute they both describe in the same words.
ENTITIES = [
    "primary_shadow",
    "hair_a",
    "footprints",
    "background_cloud",
    "breath_vapour",
    "waterline",
]
ATTRIBUTES = [
    "direction_deg",
    "length_ratio",
    "count",
    "present",
    "warmth_k",
    "hardness",
    "height_m",
]

OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "observations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "entity": {"type": "STRING", "enum": ENTITIES},
                    "attribute": {"type": "STRING", "enum": ATTRIBUTES},
                    "value": {
                        "type": "STRING",
                        "description": "the measurement as text, e.g. '243' or 'absent'",
                    },
                    "numeric_value": {
                        "type": "NUMBER",
                        "description": "the same measurement as a number where one applies",
                        "nullable": True,
                    },
                    "in_focus": {"type": "BOOLEAN"},
                    "frame_coverage_pct": {
                        "type": "NUMBER",
                        "description": "how much of the frame this occupies, 0 to 100",
                    },
                    "confidence": {"type": "NUMBER", "description": "0 to 1"},
                },
                "required": [
                    "entity",
                    "attribute",
                    "value",
                    "in_focus",
                    "frame_coverage_pct",
                    "confidence",
                ],
            },
        }
    },
    "required": ["observations"],
}

FIGURE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "figures": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "label": {"type": "STRING", "description": "'left' or 'right'"},
                    "feet_x": {"type": "NUMBER", "description": "0 to 1, left to right"},
                    "feet_y": {"type": "NUMBER", "description": "0 to 1, top to bottom"},
                    "head_y": {"type": "NUMBER", "description": "0 to 1, top to bottom"},
                },
                "required": ["label", "feet_x", "feet_y", "head_y"],
            },
        },
        "horizon_y": {
            "type": "NUMBER",
            "description": "0 to 1, where the sea meets the sky",
        },
    },
    "required": ["figures", "horizon_y"],
}

OBSERVE_PROMPT = """
You are measuring one frame of a film for continuity analysis. Report only
what is visible in this frame. Do not infer, do not guess at what happened
before or after, and do not describe the story.

Measure whichever of these are actually present:

- primary_shadow / direction_deg - the compass-style bearing the main figure's
  shadow points *within the frame*, where 0 is straight up the frame (away
  from camera), 90 is frame right, 180 is straight down (toward camera).
- primary_shadow / length_ratio - the shadow's length divided by the height of
  the figure casting it.
- primary_shadow / hardness - 0 for a soft diffuse edge, 1 for a razor edge.
- footprints / count - how many distinct footprints are visible in the sand.
- waterline / height_m - how far up the beach the wet sand reaches, as a
  number between 0 and 1 of the visible beach depth. A number, never words.
- background_cloud / count - distinct cloud masses on the horizon.
- breath_vapour / present - whether visible breath is present.
- hair_a / direction_deg - which way hair is being blown, same frame
  convention as shadows.

Calibration, which matters more than any single reading:

- A shadow shorter than about one and a half times its caster has no reliable
  direction - it is a stub under the feet. Report direction_deg for it only
  with confidence at or below 0.3, or leave it out. Being confidently wrong
  about a short shadow is worse than saying nothing.
- Long shadows are routinely underestimated. If a shadow runs off toward the
  edge of frame, measure it against the caster's height deliberately rather
  than by impression.
- frame_coverage_pct must be 0 whenever the thing is absent. Do not report the
  area a thing would have occupied.
- Something occupying 1% of frame will not be noticed by an audience, and
  saying so is more useful than reporting it as though it matters.

Omit anything you cannot actually see. An empty list is a valid answer.
""".strip()

LOCATE_PROMPT = """
Locate the standing figures on the beach in this frame, and the horizon.

For each figure give the point where the feet meet the sand, and the point at
the top of the head, in fractional image coordinates: x from 0 at the left
edge to 1 at the right, y from 0 at the top to 1 at the bottom.

Label the leftmost figure 'left' and the other 'right'. If there is only one
figure, label it 'left'.

horizon_y is where the sea meets the sky.
""".strip()


@dataclass(frozen=True)
class Figure:
    label: str
    feet_x: float
    feet_y: float
    head_y: float

    @property
    def height_fraction(self) -> float:
        return max(self.feet_y - self.head_y, 1e-4)


#: Safety filters, set explicitly rather than left to whatever the default
#: happens to be on the day.
#:
#: Every image the model sees here is a beach plate this project generated
#: itself, and every prompt asks for a measurement. The realistic failure is
#: not unsafe output, it is a *false* block: a frame refused for some reason
#: nobody can inspect, which silently becomes a missing observation and a
#: contradiction the agent never sees. So the threshold is set to catch high
#: confidence harm and let the rest through, and it is written down here so
#: the choice is reviewable instead of implicit.
SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold="BLOCK_ONLY_HIGH")
    for category in (
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    )
]


@lru_cache(maxsize=4)
def _client(project_id: str, location: str) -> Client:
    """One client per project/location, held for the process lifetime.

    Caching is not an optimisation here, it is a correctness fix. Building the
    client inline - `Client(...).models.generate_content(...)` - leaves no
    strong reference to it, so it can be collected while the request is still
    in flight and the underlying HTTP client closes underneath the call.
    """
    return Client(vertexai=True, project=project_id, location=location)


#: How the pool says it is busy. These models run on shared capacity rather
#: than a quota this project owns, so there is no number to raise: 429 means
#: the region was crowded for a moment, and 503 is the same thing under another
#: code. Neither says anything about the request itself.
BUSY_CODES = (429, 503)

#: Long enough to let a crowd pass, short enough that somebody watching the
#: overlay does not think it has stopped. Two waits, then the error stands.
BUSY_WAITS_S = (2.0, 6.0)


class _Retrying:
    """The models client, with a pause and another try when the pool is busy.

    Left to itself a single crowded second cost a whole adjudication: the tool
    handed the agent an error, and whether the finding was ever looked at came
    down to whether the agent happened to ask again. That is not a thing to
    leave to chance, and it is not a thing to hide either - the wait is logged.

    Only the busy codes are retried. A refusal, a malformed request or a
    missing frame is an answer, and asking again would only ask again.
    """

    def __init__(self, models: Any) -> None:
        self._models = models

    def __getattr__(self, name: str) -> Any:
        return getattr(self._models, name)

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        for wait in (*BUSY_WAITS_S, None):
            try:
                return self._models.generate_content(*args, **kwargs)
            except APIError as busy:
                if wait is None or getattr(busy, "code", None) not in BUSY_CODES:
                    raise
                logger.warning(
                    "the model pool was busy (%s), waiting %.0fs and asking again",
                    getattr(busy, "code", "?"),
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError("unreachable")


def _models(settings: Settings) -> Any:
    return _Retrying(_client(settings.project_id, settings.gemini_location).models)


def _image_part(image_bytes: bytes, mime_type: str = "image/png") -> types.Part:
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def locate_figures(
    image_bytes: bytes, settings: Settings | None = None
) -> tuple[list[Figure], float]:
    """Find the figures' feet and heads, and the horizon.

    Run once per plate at asset-preparation time, not per frame. The composite
    step needs to know where a shadow starts and how tall its caster is; asking
    the model once is more robust than hard-coding pixel coordinates that break
    the moment a plate is regenerated.
    """
    settings = settings or get_settings()
    response = _models(settings).generate_content(
        model=settings.model,
        contents=[_image_part(image_bytes), LOCATE_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FIGURE_SCHEMA,
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    payload = json.loads(response.text)
    figures = [Figure(**item) for item in payload["figures"]]
    return figures, float(payload["horizon_y"])


def observe_frame(
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Turn one frame into structured observations.

    The bulk half of the perception pass. Called once per sampled frame at
    ingest; the rows it returns are what the ClickHouse self-joins operate on.
    """
    settings = settings or get_settings()
    response = _models(settings).generate_content(
        model=settings.model,
        contents=[_image_part(image_bytes, mime_type), OBSERVE_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=OBSERVATION_SCHEMA,
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    observations = json.loads(response.text).get("observations", [])
    logger.info("observed %d attributes", len(observations))
    return observations


ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "would_an_audience_notice": {"type": "BOOLEAN"},
        "severity": {"type": "STRING", "enum": ["info", "low", "medium", "high"]},
        "what_differs": {
            "type": "STRING",
            "description": "one sentence, describing only what is visibly different",
        },
        "why_it_reads_or_does_not": {
            "type": "STRING",
            "description": "one sentence on shot size, focus, and where the eye goes",
        },
        "confidence": {"type": "NUMBER"},
    },
    "required": [
        "would_an_audience_notice",
        "severity",
        "what_differs",
        "why_it_reads_or_does_not",
        "confidence",
    ],
}

ADJUDICATE_PROMPT = """
These two frames are cut directly against each other. The first is the outgoing
shot, the second is the incoming one.

The data already says they disagree about {entity} / {attribute}: {delta}.
The physics says: {expectation}

Your job is not to re-measure. It is to answer one question a database cannot:
**would an audience notice, at speed, in this cut?**

Weigh what actually governs that:

- Shot size. The same discrepancy is glaring in a wide and invisible in a
  close-up where the subject fills the frame.
- Where the eye goes. A mismatch behind the speaking actor's head is not seen.
- Focus. A difference in a defocused background is not a continuity error.
- Duration. Something on screen for twelve frames is not read.

Say "no" freely. Most measured differences are not visible ones, and a tool
that flags everything gets switched off. Reserve 'high' for something that
would pull a viewer out of the scene.
""".strip()


def adjudicate_pair_by_uri(
    frame_a_uri: str,
    frame_b_uri: str,
    *,
    entity: str,
    attribute: str,
    delta: str,
    expectation: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Adjudicate two frames Gemini reads straight from GCS.

    The agent only ever holds URIs - they are what `frame_observations` stores
    - so pulling the bytes down just to send them back up would be wasted
    round trips.
    """
    settings = settings or get_settings()
    prompt = ADJUDICATE_PROMPT.format(
        entity=entity, attribute=attribute, delta=delta, expectation=expectation
    )
    response = _models(settings).generate_content(
        model=settings.model,
        contents=[
            types.Part.from_uri(file_uri=frame_a_uri, mime_type="image/jpeg"),
            types.Part.from_uri(file_uri=frame_b_uri, mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ADJUDICATION_SCHEMA,
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    return json.loads(response.text)


def adjudicate_pair(
    frame_a: bytes,
    frame_b: bytes,
    *,
    entity: str,
    attribute: str,
    delta: str,
    expectation: str,
    mime_type: str = "image/jpeg",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Judge whether a measured difference is a visible one.

    The expensive half of the perception pass, and the reason the database
    comes first: this runs on the handful of contradictions that survived
    ranking, not on every pair. Two frames go in together, because the question
    is about the cut rather than about either frame alone.
    """
    settings = settings or get_settings()
    prompt = ADJUDICATE_PROMPT.format(
        entity=entity, attribute=attribute, delta=delta, expectation=expectation
    )
    response = _models(settings).generate_content(
        model=settings.model,
        contents=[
            _image_part(frame_a, mime_type),
            _image_part(frame_b, mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ADJUDICATION_SCHEMA,
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    return json.loads(response.text)


def compare_pair(
    image_bytes: bytes,
    prompt: str,
    settings: Settings | None = None,
    *,
    mime_type: str = "image/jpeg",
) -> str:
    """Ask one question about one image that happens to contain two frames.

    Deliberately thin. Everything about what is being compared, and how the
    answer should be shaped, lives in the prompt the caller passes; this only
    carries it to the model and hands back the raw text. The alternative was a
    second observe-shaped function with its own schema, and a schema here would
    have been a third place for the shape of a ground difference to be written
    down and to drift out of step with the other two.
    """
    settings = settings or get_settings()
    response = _models(settings).generate_content(
        model=settings.model,
        contents=[_image_part(image_bytes, mime_type), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    return response.text or ""
