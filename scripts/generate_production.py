#!/usr/bin/env python3
"""Generate the fictional production: takes, edit versions, and render configs.

This builds Scene 14 of a film that does not exist — two figures talking on a
beach through the late afternoon — with continuity errors planted deliberately
so there is something true to measure the agent against.

**Everything is invented.** No real production, no real crew, no real
footage. Weather and tide come from scripts/generate_telemetry.py and are
simulated.

The planted errors are the point. Because we place them, we know the answer
key, and "the agent found seven of nine" is a claim that can be checked
instead of asserted. The key lands in assets/ground_truth.json and must never
reach a prompt, a ClickHouse table, or a file path — if the answers leak, the
score is theatre.

Take timings are derived from the real ephemeris, not invented, so that a
shadow measured in a frame and a shadow predicted from the slate time can
genuinely agree or genuinely disagree.

Usage:
    python scripts/generate_production.py --out data/
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))

from app import ephemeris as eph  # noqa: E402

from generate_telemetry import (  # noqa: E402
    LATITUDE,
    LONGITUDE,
    PRODUCTION_ID,
    SHOOT_DAYS,
    UTC_OFFSET_H,
)

UTC = timezone.utc

SCENE_ID = "sc14"
FILM_TITLE = "The Tide Line"  # fictional

#: Setups, and the compass bearing the camera faces. The beach runs
#: north-south with the water to the west, so the master looks out to sea and
#: the reverses look back at the dunes. Camera heading is what turns a
#: compass-frame shadow bearing into one an observer can read off the frame.
SETUPS = [
    # setup_id, description, camera_heading_deg, lens_mm, source_kind
    ("su01", "master wide, two figures, sea behind", 270.0, 32.0, "practical"),
    ("su02", "reverse on A, dunes behind", 90.0, 50.0, "practical"),
    ("su03", "reverse on B, dunes behind", 95.0, 50.0, "practical"),
    ("su04", "close-up A", 88.0, 85.0, "practical"),
    ("su05", "close-up B", 92.0, 85.0, "practical"),
    ("su06", "two-shot, profile", 180.0, 40.0, "practical"),
    ("su07", "insert, footprints in wet sand", 200.0, 35.0, "practical"),
    ("su08", "set extension, headland added in CG", 270.0, 32.0, "cg_render"),
]

#: Story beats the scene is broken into. Two takes of the same beat from
#: different setups must agree; that is the whole basis of the cross-take join.
BEATS = list(range(1, 13))

#: Which day each setup was covered on, and the local clock time it started.
#: Note su03 and su05 come back on the second block of days, twelve days after
#: their matching reverses. That gap is the scene's built-in hazard and it is
#: how real coverage actually falls.
#:
#: Nothing is scheduled past about 16:40 local. Past that the sun drops below
#: three degrees and the shadow length ratio hits its clamp, so two shots that
#: are genuinely different both read as "20" and the signal is gone. Between
#: 13:00 and 16:40 the ratio runs 0.8 to 7.3 — a ninefold spread, all of it
#: measurable — and the dominant drift axis flips from direction to length at
#: about 16:00, which is the point the scene exists to make.
SCHEDULE = [
    # setup_id, shoot_day index into SHOOT_DAYS, local start hour, take count
    ("su01", 0, 15.0, 5),    # master: sun high, direction is the fragile axis
    ("su02", 0, 16.0, 4),    # reverse A: past the flip, length is now fragile
    ("su04", 1, 14.5, 4),
    ("su06", 1, 15.6, 3),
    ("su07", 2, 16.3, 3),    # footprint insert, long raking shadows
    ("su03", 3, 16.2, 4),    # reverse B, twelve days after its own reverse
    ("su05", 4, 14.2, 4),
    ("su08", 4, 15.2, 3),    # CG plate reference
]

TAKE_LENGTH_S = 95
TURNAROUND_S = 420  # time to reset between takes: relight, reslate, go again


def _utc(day_index: int, local_hour: float) -> datetime:
    day = SHOOT_DAYS[day_index]
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=local_hour - UTC_OFFSET_H
    )


def build_takes() -> tuple[list[dict], list[dict]]:
    """Every take in the scene, plus the answer key entries they carry."""
    takes: list[dict] = []
    planted: list[dict] = []

    heading = {s[0]: s[2] for s in SETUPS}
    lens = {s[0]: s[3] for s in SETUPS}
    kind = {s[0]: s[4] for s in SETUPS}

    for setup_id, day_index, start_local, count in SCHEDULE:
        for take_number in range(1, count + 1):
            offset_s = (take_number - 1) * (TAKE_LENGTH_S + TURNAROUND_S)
            started = _utc(day_index, start_local) + timedelta(seconds=offset_s)
            take_id = f"{SCENE_ID}_{setup_id}_t{take_number:02d}"

            slate_started = started
            slate_verified = 1

            # PLANTED ERROR: a mis-slated take. The camera report says this one
            # was shot 70 minutes earlier than it was. Nothing in the metadata
            # contradicts it — but at this latitude 70 minutes near sunset
            # moves the sun far enough that the shadows in the frame cannot
            # match the slate. The agent should catch it without being asked.
            if take_id == "sc14_su03_t03":
                slate_started = started - timedelta(minutes=70)
                slate_verified = 0
                planted.append(
                    {
                        "id": "E1",
                        "type": "slate_error",
                        "take_id": take_id,
                        "detail": (
                            "slate timestamp is 70 minutes earlier than the real "
                            "capture time; shadows will not match the ephemeris"
                        ),
                        "true_started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
                        "slated_started_at": slate_started.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

            takes.append(
                {
                    "take_id": take_id,
                    "production_id": PRODUCTION_ID,
                    "scene_id": SCENE_ID,
                    "setup_id": setup_id,
                    "take_number": take_number,
                    "shoot_day": day_index + 1,
                    "unit": "main",
                    "started_at": slate_started.strftime("%Y-%m-%d %H:%M:%S.000"),
                    "ended_at": (slate_started + timedelta(seconds=TAKE_LENGTH_S)).strftime(
                        "%Y-%m-%d %H:%M:%S.000"
                    ),
                    "latitude": LATITUDE,
                    "longitude": LONGITUDE,
                    "camera_heading_deg": heading[setup_id],
                    "lens_mm": lens[setup_id],
                    "source_kind": kind[setup_id],
                    "slate_verified": slate_verified,
                }
            )

    return takes, planted


#: The cut, as two versions. v13 is the editor's earlier assembly; v14 is the
#: one they just locked, and it moves the footprint insert forward. The footage
#: did not change — the adjacencies did. That is the argument for recomputing
#: on every version rather than once at ingest.
#:
#: One cut per setup, so a take appears once and carries one set of values.
EDIT_V13 = ["su01", "su02", "su04", "su05", "su03", "su06", "su07", "su08"]
EDIT_V14 = ["su01", "su04", "su07", "su02", "su05", "su03", "su06", "su08"]

#: How many footprints are in the sand when each setup was shot. Footprints
#: only accumulate — the crew rakes between setups, so the count tracks the
#: story rather than the shoot, and a cut that shows fewer of them later is
#: running the scene backwards.
#:
#: These values are monotonic in v13 order (2, 4, 6, 8, 10, 12, 14, 16) and are
#: not in v14, where su07's fourteen prints land between shots carrying six and
#: four. No single frame is wrong. The order is.
FOOTPRINTS_BY_SETUP = {
    "su01": 2, "su02": 4, "su04": 6, "su05": 8,
    "su03": 10, "su06": 12, "su07": 14, "su08": 16,
}

#: Which take of each setup made the cut.
SELECTED_TAKE = {
    "su01": 3, "su02": 2, "su03": 3, "su04": 4,
    "su05": 1, "su06": 2, "su07": 2, "su08": 1,
}


def build_edit_decisions() -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    planted: list[dict] = []
    created = datetime(2026, 12, 18, 11, 0, tzinfo=UTC)

    for version, order, offset_days in (("v13", EDIT_V13, 0), ("v14", EDIT_V14, 4)):
        for position, setup_id in enumerate(order, start=1):
            beat = position
            rows.append(
                {
                    "edit_version": version,
                    "cut_position": position,
                    "take_id": f"{SCENE_ID}_{setup_id}_t{SELECTED_TAKE[setup_id]:02d}",
                    "in_beat": beat,
                    "out_beat": beat,
                    "created_at": (created + timedelta(days=offset_days)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )

    # PLANTED ERROR: the footprint insert (su07) sits at position 4 in v14,
    # before the wide it should follow. Footprints only accumulate, so a cut
    # that shows fewer of them later runs the scene backwards. No single frame
    # is wrong; the order is. In v13 su07 sat at position 7 and the sequence
    # was monotonic.
    planted.append(
        {
            "id": "E2",
            "type": "monotonic_violation",
            "edit_version": "v14",
            "entity": "footprints",
            "detail": (
                "su07 moved from cut position 7 (v13) to position 3 (v14). Its "
                "fourteen footprints now sit between shots carrying six and four, "
                "so the count runs 2, 6, 14, 4 across the opening cuts and the "
                "scene plays backwards. Every individual frame is correct."
            ),
            "v13_sequence": [FOOTPRINTS_BY_SETUP[s] for s in EDIT_V13],
            "v14_sequence": [FOOTPRINTS_BY_SETUP[s] for s in EDIT_V14],
        }
    )

    # PLANTED ERROR: su03 and su02 are reverses of each other but were shot
    # twelve days and about an hour of sun apart. In v14 they land three cuts
    # from each other with su05 between them; in the wide-ish reverses the
    # shadow direction difference is visible.
    planted.append(
        {
            "id": "E3",
            "type": "cross_take_drift",
            "edit_version": "v14",
            "entity": "primary_shadow",
            "detail": (
                "su02 (shoot day 1, 16:00 local) cuts against su03 (shoot day 4, "
                "16:20 local, twelve calendar days later). The azimuths are close, "
                "so a bearing check would pass; the shadow length ratio is not, "
                "because both sit past the point where length is the fragile axis"
            ),
        }
    )
    return rows, planted


def build_render_configs() -> tuple[list[dict], list[dict]]:
    """The CG side: the set-extension shot, and what is wrong with it."""
    rows: list[dict] = []
    planted: list[dict] = []
    submitted = datetime(2026, 12, 19, 9, 30, tzinfo=UTC)

    master_take = f"{SCENE_ID}_su01_t{SELECTED_TAKE['su01']:02d}"
    cg_take = f"{SCENE_ID}_su08_t{SELECTED_TAKE['su08']:02d}"

    # What the physics says the key light should be, taken from the master's
    # real capture time. This is the number the agent must arrive at
    # independently.
    master_started = _utc(0, 15.0) + timedelta(
        seconds=(SELECTED_TAKE["su01"] - 1) * (TAKE_LENGTH_S + TURNAROUND_S)
    )
    sun = eph.solar_position(LATITUDE, LONGITUDE, master_started)

    # PLANTED ERROR: the CG shot was lit from the wrong reference. The key
    # light matches mid-afternoon, not the late-afternoon master it has to cut
    # against — 14 degrees off in azimuth and over 700 K too cool.
    wrong_azimuth = sun.azimuth_deg - 14.2
    wrong_temp = sun.color_temp_k + 740

    for version, (azimuth, elevation, temp, plate_frame, assets, hours) in enumerate(
        [
            (wrong_azimuth, sun.elevation_deg + 3.1, wrong_temp, 41820,
             '{"beach_headland":"v012","boat":"v004","dune_grass":"v007"}', 1840.0),
            (wrong_azimuth, sun.elevation_deg + 3.1, wrong_temp, 41820,
             '{"beach_headland":"v013","boat":"v004","dune_grass":"v007"}', 2110.0),
        ],
        start=1,
    ):
        rows.append(
            {
                "shot_id": f"{SCENE_ID}_cg01",
                "take_id": cg_take,
                "render_version": version,
                "key_light_azimuth_deg": round(azimuth % 360.0, 2),
                "key_light_elevation_deg": round(elevation, 2),
                "key_light_temp_k": int(temp),
                "key_light_intensity": 1.0,
                "key_light_softness": 0.35,
                "volume_plate_id": "plate_headland_a",
                "volume_plate_frame": plate_frame,
                "volume_brightness_nits": 1200,
                "asset_versions": assets,
                "submitted_at": (submitted + timedelta(hours=version * 6)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "core_hours_est": hours,
            }
        )

    planted.append(
        {
            "id": "E4",
            "type": "physics_mismatch",
            "shot_id": f"{SCENE_ID}_cg01",
            # The take as well as the shot: an analyst naturally refers to the
            # take it was rendered for, and a scorer that only knows the shot id
            # would mark a correct find as a miss.
            "take_id": cg_take,
            "detail": (
                "CG key light is 14.2 deg off in azimuth and 740 K too cool for the "
                "master it cuts against"
            ),
            "correct_key_light_azimuth_deg": round(sun.azimuth_deg, 2),
            "correct_key_light_elevation_deg": round(sun.elevation_deg, 2),
            "correct_key_light_temp_k": sun.color_temp_k,
            "reference_take_id": master_take,
            "core_hours_at_risk": 2110.0,
        }
    )
    planted.append(
        {
            "id": "E5",
            "type": "asset_version_drift",
            "shot_id": f"{SCENE_ID}_cg01",
            "take_id": cg_take,
            "detail": (
                "beach_headland moved v012 -> v013 between render versions while "
                "the rest of the scene still references v012"
            ),
            "core_hours_at_risk": 2110.0,
        }
    )
    return rows, planted


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data")
    args = parser.parse_args()

    out_dir = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    takes, planted_takes = build_takes()
    edits, planted_edits = build_edit_decisions()
    renders, planted_renders = build_render_configs()

    write_csv(out_dir / "takes.csv", takes)
    write_csv(out_dir / "edit_decisions.csv", edits)
    write_csv(out_dir / "shot_render_config.csv", renders)

    print(f"CineMeridian - fictional production {PRODUCTION_ID}, scene {SCENE_ID}")
    print(f"  takes.csv               {len(takes):>5} takes across {len(SETUPS)} setups")
    print(f"  edit_decisions.csv      {len(edits):>5} cuts across 2 edit versions")
    print(f"  shot_render_config.csv  {len(renders):>5} render versions")

    key = {
        "_warning": (
            "ANSWER KEY. Never send this to a model, never load it into "
            "ClickHouse before evaluation, never encode it in a file path."
        ),
        "film_title": FILM_TITLE,
        "production_id": PRODUCTION_ID,
        "scene_id": SCENE_ID,
        "planted_errors": planted_takes + planted_edits + planted_renders,
    }
    assets_dir = ROOT / "assets"
    assets_dir.mkdir(exist_ok=True)
    (assets_dir / "ground_truth.json").write_text(
        json.dumps(key, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n  assets/ground_truth.json  {len(key['planted_errors'])} planted errors")
    for error in key["planted_errors"]:
        print(f"    {error['id']}  {error['type']:<22} {error['detail'][:60]}")
    print("\n  More will be planted in the frames themselves at asset-generation time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
