"""Turn somebody's own clips into a production the agent can investigate.

The demo scene works because there is a database behind it: thirty takes, a
hundred thousand ephemeris rows, telemetry, render configs. The agent's power
is not that it looks at pictures, it is that it can ask questions across all of
that at once. Two clips and a vision call cannot reproduce that, and a page
that imitated the look of it without the substance would be a lie.

So this does the real thing instead. Clips arrive, and what goes into ClickHouse
is the same shape as the demo scene: takes, the observations read from their
first and last frames, an ephemeris computed for the time and place they claim,
and a cut order. Then the same agent is pointed at it through the same MCP
server. What comes back is not a rendering of the demo, it is the demo's
machinery running on footage nobody here has seen.

Two things are deliberately unlike the demo. Each visitor's rows carry their own
`production_id`, and every query the agent is given is fenced to it, because
these tables also hold the demo scene and mixing the two would produce findings
about a beach in Costa Rica that nobody filmed. And the rows are written to be
thrown away: a project is a session, not an archive.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app import ephemeris as eph

#: How many takes one visitor may bring. Not a limit of the code: each take
#: costs two vision calls at ingest, and this project has already met
#: "Resource exhausted" at six calls in quick succession.
MAX_TAKES = 6
MIN_TAKES = 2

#: How finely the sun is tabulated for the window the clips cover. A minute is
#: what the demo scene uses, and the agent's queries join on it.
EPHEMERIS_STEP = timedelta(minutes=1)

#: How far either side of the clips the ephemeris runs, so a query that reaches
#: slightly outside the takes still lands on rows.
EPHEMERIS_MARGIN = timedelta(minutes=30)

#: The two moments of a take that a cut can touch. Named as story beats so the
#: rows sit in the same vocabulary as the demo scene's.
HEAD_BEAT = 1
TAIL_BEAT = 2


@dataclass(frozen=True)
class TakeInput:
    """One clip, as the browser describes it."""

    index: int
    #: When the file says it was recorded. Synthetic and ordered when no file
    #: carried a time and nobody typed one, in which case `times_known` on the
    #: project is false and nothing may be concluded from these.
    recorded_at: datetime
    duration_seconds: float
    head_observations: list[dict[str, Any]]
    tail_observations: list[dict[str, Any]]

    #: Where the frames were kept, if the visitor chose to keep them. Empty
    #: when they did not, which the agent reads as "there is nothing to look
    #: at" rather than following a path to nowhere.
    head_uri: str = ""
    tail_uri: str = ""

    #: What was lighting this take, read once during the scene-change check and
    #: carried here rather than asked again. Regime, apparent time of day, and
    #: whether the room's opening and lamps were doing anything. Empty when the
    #: perception call could not answer, which is different from answering "no".
    conditions: dict[str, Any] | None = None


@dataclass(frozen=True)
class Project:
    """The identifiers everything else in the session hangs off."""

    production_id: str
    scene_id: str
    edit_version: str

    @classmethod
    def new(cls) -> "Project":
        token = uuid.uuid4().hex[:10]
        return cls(
            production_id=f"try_{token}",
            scene_id=f"sc_{token}",
            edit_version=f"v_{token}",
        )


def build_statements(
    project: Project,
    takes: list[TakeInput],
    latitude: float | None,
    longitude: float | None,
) -> list[str]:
    """Every INSERT this project needs, in the order they must run.

    Composed as text rather than executed here, for the same reason the agent's
    own writes are: the only path to ClickHouse in this system is the MCP
    server, and a second path that quietly bypassed it would make the claim
    this project rests on untrue.
    """
    if not MIN_TAKES <= len(takes) <= MAX_TAKES:
        raise ValueError(f"a project needs between {MIN_TAKES} and {MAX_TAKES} takes")

    # No position, no ephemeris. Every sun check joins against those rows, so
    # their absence switches the whole physics branch off by itself rather than
    # by a flag somebody has to remember to check. What is left still answers
    # plenty: what changed on the ground, what runs backwards, and what the
    # light itself says.
    statements = [_takes_insert(project, takes, latitude, longitude)]
    if latitude is not None and longitude is not None:
        statements.append(_ephemeris_insert(project, takes, latitude, longitude))
    statements.append(_observations_insert(project, takes))
    statements.append(_edit_insert(project, takes))
    return statements


def _takes_insert(
    project: Project,
    takes: list[TakeInput],
    latitude: float | None,
    longitude: float | None,
) -> str:
    rows = []
    for take in takes:
        ended = take.recorded_at + timedelta(seconds=max(take.duration_seconds, 1.0))
        rows.append(
            "("
            + ", ".join(
                [
                    _text(take_id(project, take.index)),
                    _text(project.production_id),
                    _text(project.scene_id),
                    # One setup per take, because a visitor's clips are not
                    # coverage of one camera position and pretending otherwise
                    # would let the agent normalise across framings that have
                    # nothing to do with each other.
                    _text(f"su{take.index:02d}"),
                    "1",
                    "1",
                    _text("main"),
                    _text(_stamp(take.recorded_at)),
                    _text(_stamp(ended)),
                    # NaN rather than zero when no position was given. Zero is
                    # a real place in the Gulf of Guinea, and a reader who took
                    # it at face value would be told about a sun that was never
                    # there.
                    "nan" if latitude is None else repr(float(latitude)),
                    "nan" if longitude is None else repr(float(longitude)),
                    # No file carries a camera heading and no visitor was asked
                    # for one. Zero here would be a measurement that nobody
                    # made, so the physics tools recover it from the shadow.
                    "0",
                    "0",
                    _text("practical"),
                    # The whole point is that the timestamp has not been checked
                    # against the shadows yet. That is what the agent does.
                    "0",
                ]
            )
            + ")"
        )
    return (
        "INSERT INTO cinemeridian.takes (take_id, production_id, scene_id, setup_id, "
        "take_number, shoot_day, unit, started_at, ended_at, latitude, longitude, "
        "camera_heading_deg, lens_mm, source_kind, slate_verified) VALUES "
        + ", ".join(rows)
    )


def _ephemeris_insert(
    project: Project, takes: list[TakeInput], latitude: float, longitude: float
) -> str:
    """Tabulate the sun over the window the clips cover.

    Computed rather than fetched, and computed for this visitor's window only.
    The demo scene's ephemeris covers a shoot in Costa Rica in December; a clip
    filmed anywhere else has no rows to join against until these exist.
    """
    starts = [take.recorded_at for take in takes]
    ends = [
        take.recorded_at + timedelta(seconds=max(take.duration_seconds, 1.0))
        for take in takes
    ]
    first = min(starts) - EPHEMERIS_MARGIN
    last = max(ends) + EPHEMERIS_MARGIN

    series = eph.ephemeris_series(
        production_id=project.production_id,
        lat_deg=latitude,
        lon_deg=longitude,
        start=first.replace(second=0, microsecond=0),
        end=last,
        step=EPHEMERIS_STEP,
    )

    rows = []
    for point in series:
        rows.append(
            "("
            + ", ".join(
                [
                    _text(point["ts"].strftime("%Y-%m-%d %H:%M:%S")),
                    _text(project.production_id),
                    repr(round(float(point["sun_azimuth_deg"]), 4)),
                    repr(round(float(point["sun_elevation_deg"]), 4)),
                    repr(round(float(point["shadow_len_ratio"]), 4)),
                    str(int(point["daylight_color_temp_k"])),
                    repr(round(float(point["moon_phase"]), 4)),
                    repr(round(float(point["moon_azimuth_deg"]), 4)),
                    repr(round(float(point["tide_level_m"]), 4)),
                    "1" if point["is_civil_daylight"] else "0",
                ]
            )
            + ")"
        )

    return (
        "INSERT INTO cinemeridian.ephemeris (ts, production_id, sun_azimuth_deg, "
        "sun_elevation_deg, shadow_len_ratio, daylight_color_temp_k, moon_phase, "
        "moon_azimuth_deg, tide_level_m, is_civil_daylight) VALUES " + ", ".join(rows)
    )


#: What each condition becomes as a row. Kept in the same entity and attribute
#: vocabulary as everything else, so the joins that compare a shadow across a
#: cut compare a lighting regime across a cut with no new machinery.
CONDITION_ROWS = (
    ("lighting", "regime", "regime", False),
    ("lighting", "time_of_day", "time_of_day", False),
    ("lighting", "shadows_are", "shadows_are", False),
    ("opening", "in_frame", "opening_in_frame", True),
    ("opening", "is_bright", "opening_is_bright", True),
    ("lamps", "on", "lamps_visibly_on", True),
)


def _condition_observations(conditions: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Turn a take's lighting into observations, skipping what was not answered.

    An unanswered condition is left out rather than written as a default. A
    regime of `artificial` invented from silence would switch the sun off on a
    shot filmed in full daylight, which is a worse error than having no row.
    """
    if not conditions:
        return []

    rows = []
    for entity, attribute, key, is_flag in CONDITION_ROWS:
        value = conditions.get(key)
        if value is None or value == "":
            continue
        rows.append(
            {
                "entity": entity,
                "attribute": attribute,
                "value": "yes" if value is True else "no" if value is False else str(value),
                "numeric_value": (1.0 if value else 0.0) if is_flag else None,
                "monotonic_dir": "none",
                "in_focus": True,
                # These describe the whole frame rather than a thing in it, so
                # there is no meaningful coverage. A hundred says so honestly
                # instead of a zero that the filters would read as invisible.
                "frame_coverage_pct": 100.0,
                "confidence": 0.0,
            }
        )
    return rows


def _observations_insert(project: Project, takes: list[TakeInput]) -> str:
    """The vision pass, as rows the agent's self-joins can operate on."""
    rows = []
    for take in takes:
        ended = take.recorded_at + timedelta(seconds=max(take.duration_seconds, 1.0))
        # The lighting belongs to the take rather than to one of its ends, so it
        # is written against both: the joins that compare a cut always look at
        # one take's tail against the next take's head, and a condition present
        # on only one of those would never be compared with anything.
        conditions = _condition_observations(take.conditions)

        for beat, moment, observations, uri in (
            (HEAD_BEAT, take.recorded_at, take.head_observations + conditions, take.head_uri),
            (TAIL_BEAT, ended, take.tail_observations + conditions, take.tail_uri),
        ):
            for observation in observations:
                rows.append(_observation_row(project, take, beat, moment, uri, observation))

    if not rows:
        # An INSERT with no rows is a syntax error, and a project whose clips
        # yielded nothing measurable is a real outcome rather than a fault.
        return ""

    return (
        "INSERT INTO cinemeridian.frame_observations (obs_id, take_id, scene_id, "
        "story_beat, frame_ts, frame_uri, entity, attribute, value, numeric_value, "
        "monotonic_dir, in_focus, frame_coverage_pct, confidence) VALUES "
        + ", ".join(rows)
    )


def _observation_row(
    project: Project,
    take: TakeInput,
    beat: int,
    moment: datetime,
    frame_uri: str,
    observation: dict[str, Any],
) -> str:
    entity = str(observation.get("entity", ""))[:60]
    attribute = str(observation.get("attribute", ""))[:60]
    numeric = observation.get("numeric_value")

    seed = f"{project.edit_version}|{take.index}|{beat}|{entity}|{attribute}"
    obs_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

    return (
        "("
        + ", ".join(
            [
                _text(obs_id),
                _text(take_id(project, take.index)),
                _text(project.scene_id),
                str(beat),
                _text(_stamp(moment)),
                # Empty unless the visitor chose to keep the frames. An empty
                # string says honestly that there is nothing to look at; a made
                # up gs:// path would send anyone who followed it nowhere, and
                # the agent does follow them.
                _text(frame_uri),
                _text(entity),
                _text(attribute),
                _text(str(observation.get("value", ""))[:200]),
                repr(float(numeric)) if isinstance(numeric, (int, float)) else "NULL",
                _text(str(observation.get("monotonic_dir", "none"))),
                "1" if observation.get("in_focus", True) else "0",
                repr(_number(observation.get("frame_coverage_pct"), 0.0)),
                repr(_number(observation.get("confidence"), 0.0)),
            ]
        )
        + ")"
    )


def _edit_insert(project: Project, takes: list[TakeInput]) -> str:
    """The cut order, which is the order the visitor put the clips in.

    This is the table that makes the problem the agent's rather than a script's:
    the same clips in a different order produce different adjacent pairs, and
    every pair has to be hunted again.
    """
    rows = []
    for position, take in enumerate(takes, start=1):
        rows.append(
            "("
            + ", ".join(
                [
                    _text(project.edit_version),
                    str(position),
                    _text(take_id(project, take.index)),
                    str(HEAD_BEAT),
                    str(TAIL_BEAT),
                    _text(_stamp(datetime.now(timezone.utc))),
                ]
            )
            + ")"
        )
    return (
        "INSERT INTO cinemeridian.edit_decisions (edit_version, cut_position, take_id, "
        "in_beat, out_beat, created_at) VALUES " + ", ".join(rows)
    )


def take_id(project: Project, index: int) -> str:
    return f"{project.scene_id}_t{index:02d}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _number(value: Any, fallback: float) -> float:
    return round(float(value), 4) if isinstance(value, (int, float)) else fallback


def _text(value: str) -> str:
    """Quote a string for ClickHouse.

    Every string reaching these statements comes from a visitor's file or a
    model's answer, so none of it is trusted. Backslash first, or the escape
    added for the quote would itself be escaped away.
    """
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + escaped + "'"
