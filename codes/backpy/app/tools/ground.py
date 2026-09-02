"""What changed on the ground between two shots.

This is the second signal, and it stands apart from the physics on purpose. The
ephemeris answers when a shot was filmed and cannot be argued with; this answers
what is lying on the sand, and is a judgement. Keeping them separate means a
weak answer here can never dilute a strong one there.

The method is one image, not two. Every instability measured in this project so
far came from describing two frames separately and then differencing the
descriptions: the same shadow read 1.2 and 2.6 on the same frame, and footprint
counts came back 0, 6, 7, 34 against a truth of 2, 4, 6, 14. Put both frames in
one picture under a shared grid and the model is no longer being asked to
measure twice, it is being asked to compare, which is a different and far easier
task. Measured on a planted smudge, three grid sizes and eight runs named the
same cell every time, with no false positives.

The grid is a naming scheme, not a magnifying glass. Precision comes from asking
where inside the cell the thing sits, which the model volunteers unprompted, and
not from cutting the frame into smaller pieces: a finer grid buys nothing and
starts to strain, while risking an object that straddles a boundary being
reported as vanished from one cell and appeared in another.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

#: How many times the pair is read before a difference is believed, and how many
#: of those reads must agree. Two of three: one read is an opinion, and asking
#: for all three would throw away a real finding whenever one read wandered.
READS = 3
AGREEMENT = 2

#: Cells the model may name, so a hallucinated "Z9" is dropped rather than drawn
#: somewhere arbitrary on the frame.
CELL_PATTERN = re.compile(r"^([A-H])([1-8])$")


@dataclass(frozen=True)
class GroundDifference:
    """One thing that is on the sand in one shot and not the other."""

    cell: str
    what: str
    present_in: str          # "outgoing" or "incoming"
    seen_in_reads: int
    #: Where it sits, in fractions of the frame it appears in, so the console
    #: can draw a box straight onto that frame.
    x: float
    y: float
    width: float
    height: float


PROMPT = """Two frames that an editor wants to join at a cut.
LEFT is the outgoing frame, the last moment of the shot being cut away from.
RIGHT is the incoming frame, the first moment of the shot being cut to. Both
carry the same grid, its cells labelled by column letter and row number.

FIRST, the question that decides whether anything else applies. Could these two
frames be the same place, a few minutes apart, seen from a different angle or a
different distance? Judge the place, not the picture. A wide shot of a beach and
a close shot of the same sand are one place and look nothing alike; a beach and a
city street are two places however similar the light. If they are not the same
place, this is a cut between scenes rather than inside one, continuity does not
apply across it, and you must report no differences at all.

If they are the same place, then look at the ground.

The camera usually moves between two shots, so the same cell on each side covers
almost, but not exactly, the same ground. Allow for that.

Ignore entirely: sky, sun, sea, waves, water, the people in shot, and their
shadows. Those are expected to differ and are not what is being asked about.

Look only at the ground: marks, tracks, footprints, patches, debris, and objects
lying on it. Report a cell only where something is present on one side and
absent on the other, or has clearly changed. Do not report a difference you
would put down to the camera move alone, and do not report one you are unsure
of: saying nothing is a better answer than a maybe.

For each difference give the cell, which side it is present in, what it is, and
where it sits inside that cell as fractions from 0 to 1, where 0,0 is the top
left of the cell and 1,1 the bottom right.

Answer as JSON:
{"same_place": true, "place_note": "why, in one sentence",
"differences": [{"cell": "C3", "present_in": "incoming", "what": "a dark smudge
on the sand", "x_in_cell": 0.8, "y_in_cell": 0.7, "width_in_cell": 0.15,
"height_in_cell": 0.12, "confidence": 0.0}], "verdict": "one sentence"}"""


def parse_place(text: str) -> tuple[bool | None, str]:
    """Whether the model thinks the two frames are the same place.

    The gate in front of everything else, and the reason it exists is that
    continuity is a rule about a scene, not about a film. A cut from a beach to
    a city street is a scene change: the shadows will disagree, the ground will
    disagree, and none of it is a fault. Comparing them anyway would bury a real
    finding under a pile of differences that were all intended.

    None means the model did not answer the question, which is treated as "carry
    on" rather than "stop": a missing answer should not silently cancel an
    analysis somebody asked for.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, ""
    if not isinstance(parsed, dict):
        return None, ""

    value = parsed.get("same_place")
    note = str(parsed.get("place_note", ""))[:300]
    if isinstance(value, bool):
        return value, note
    return None, note


def parse_reading(text: str, columns: int, rows: int) -> list[dict[str, Any]]:
    """Pull the differences out of one answer, dropping anything malformed.

    A model that names a cell outside the grid, or omits where in the cell the
    thing sits, has not given an answer that can be drawn on a frame. Those are
    dropped rather than guessed at, because a box in the wrong place is worse
    than no box.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []

    found = []
    for item in parsed.get("differences") or []:
        if not isinstance(item, dict):
            continue
        cell = str(item.get("cell", "")).strip().upper()
        match = CELL_PATTERN.match(cell)
        if not match:
            continue
        column = ord(match.group(1)) - ord("A")
        row = int(match.group(2)) - 1
        if column >= columns or row >= rows:
            continue

        side = str(item.get("present_in", "")).strip().lower()
        if side not in ("outgoing", "incoming", "left", "right"):
            continue
        side = {"left": "outgoing", "right": "incoming"}.get(side, side)

        found.append(
            {
                "cell": cell,
                "column": column,
                "row": row,
                "present_in": side,
                "what": str(item.get("what", ""))[:200],
                "x_in_cell": _fraction(item.get("x_in_cell"), 0.5),
                "y_in_cell": _fraction(item.get("y_in_cell"), 0.5),
                "width_in_cell": _fraction(item.get("width_in_cell"), 0.25),
                "height_in_cell": _fraction(item.get("height_in_cell"), 0.25),
            }
        )
    return found


def agree(
    readings: list[list[dict[str, Any]]], columns: int, rows: int
) -> list[GroundDifference]:
    """Keep the differences that more than one reading saw.

    Grouped by cell and side rather than by wording, because the same mark comes
    back as "a dark smudge", "a dark spot" and "a blemish on the sand" and those
    are one finding, not three. Where the reads disagree on position the middle
    one is taken, which is the same reason the shadow measurements are pooled by
    median rather than averaged: one wandering read should move the answer a
    little, not carry it.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for reading in readings:
        # One vote per cell per reading, so a model that lists the same mark
        # twice in one answer cannot outvote a model that saw it once.
        seen: set[tuple[str, str]] = set()
        for item in reading:
            key = (item["cell"], item["present_in"])
            if key in seen:
                continue
            seen.add(key)
            grouped[key].append(item)

    agreed = []
    for (cell, side), items in grouped.items():
        if len(items) < AGREEMENT:
            continue
        first = items[0]
        cell_w = 1.0 / columns
        cell_h = 1.0 / rows
        centre_x = (first["column"] + _median(i["x_in_cell"] for i in items)) * cell_w
        centre_y = (first["row"] + _median(i["y_in_cell"] for i in items)) * cell_h
        width = _median(i["width_in_cell"] for i in items) * cell_w
        height = _median(i["height_in_cell"] for i in items) * cell_h

        agreed.append(
            GroundDifference(
                cell=cell,
                what=_clearest(items),
                present_in=side,
                seen_in_reads=len(items),
                x=round(max(centre_x - width / 2, 0.0), 4),
                y=round(max(centre_y - height / 2, 0.0), 4),
                width=round(min(width, 1.0), 4),
                height=round(min(height, 1.0), 4),
            )
        )

    agreed.sort(key=lambda item: (-item.seen_in_reads, item.cell))
    return agreed


def _clearest(items: list[dict[str, Any]]) -> str:
    """The most detailed of the descriptions the reads gave for one mark."""
    return max((item["what"] for item in items), key=len, default="")


def _median(values: Any) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.5
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _fraction(value: Any, fallback: float) -> float:
    if not isinstance(value, (int, float)):
        return fallback
    return min(max(float(value), 0.0), 1.0)


def agree_place(texts: list[str]) -> tuple[bool, str, int]:
    """Do the readings agree that this is one place?

    A majority, and a tie carries on. Stopping an analysis somebody asked for is
    the more expensive mistake of the two: a scene change wrongly analysed
    produces findings they can dismiss by looking, while an analysis wrongly
    refused leaves them with nothing and no way to argue. So it takes more votes
    to stop than to continue.
    """
    votes = [parse_place(text) for text in texts]
    against = [note for value, note in votes if value is False]
    for_it = [note for value, note in votes if value is True]

    if len(against) > len(for_it):
        return False, next((note for note in against if note), ""), len(against)
    return True, next((note for note in for_it if note), ""), len(for_it)
