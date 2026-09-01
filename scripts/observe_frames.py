#!/usr/bin/env python3
"""Run the perception pass: turn rendered frames into ClickHouse rows.

This is the slow lane of the system — the overnight pass after a shoot day.
Every sampled frame goes through Gemini once, and what comes back is not a
description but a set of measurements: shadow bearing, shadow length, how many
footprints, how warm the light. Those rows are what the combinatorial queries
then operate on.

The frames themselves also go to GCS, because the console shows evidence pairs
and the agent's adjudication pass reads them back by URI.

The answer key is never loaded here. It exists to score the result afterwards,
and a pipeline that could see it would not be measuring anything.

Usage:
    python scripts/observe_frames.py --frames assets/frames --out data/
    python scripts/observe_frames.py --frames assets/frames --out data/ --upload
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))

from app.settings import get_settings  # noqa: E402
from app.tools.vision import observe_frame  # noqa: E402

SCENE_ID = "sc14"

#: Attributes that only ever move one way in story time. Marking them here is
#: what lets a query find a sequence that runs backwards without anyone having
#: to say which direction is correct for each one.
MONOTONIC = {
    ("footprints", "count"): "increasing",
}


def frame_uri(bucket: str, relative: Path) -> str:
    return f"gs://{bucket}/frames/{relative.as_posix()}"


def upload(bucket_name: str, relative: Path, path: Path) -> None:
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(f"frames/{relative.as_posix()}")
    blob.upload_from_filename(str(path), content_type="image/jpeg")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", default="assets/frames")
    parser.add_argument("--out", default="data")
    parser.add_argument("--upload", action="store_true", help="also push frames to GCS")
    parser.add_argument("--limit", type=int, help="stop after N frames, for a smoke test")
    args = parser.parse_args()

    settings = get_settings()
    frames_root = ROOT / args.frames if not Path(args.frames).is_absolute() else Path(args.frames)
    scene_root = frames_root / SCENE_ID
    if not scene_root.is_dir():
        sys.exit(f"{scene_root} does not exist. Run composite_variants.py --all first.")

    truth = json.loads((scene_root / "frame_truth.json").read_text(encoding="utf-8"))
    beats = {row["take_id"]: row["story_beat"] for row in truth}
    captured = {row["take_id"]: row["captured_at"] for row in truth}

    frames = sorted(scene_root.rglob("f*.jpg"))
    if args.limit:
        frames = frames[: args.limit]
    if not frames:
        sys.exit("no frames found")

    rows: list[dict] = []
    print(f"Observing {len(frames)} frames with {settings.model}\n")

    for index, path in enumerate(frames, start=1):
        relative = path.relative_to(frames_root)
        # frames/sc14/su01/t03/f000.jpg -> take id sc14_su01_t03
        setup_id, take_dir = relative.parts[1], relative.parts[2]
        take_id = f"{SCENE_ID}_{setup_id}_{take_dir}"

        observations = observe_frame(path.read_bytes(), mime_type="image/jpeg", settings=settings)
        uri = frame_uri(settings.gcs_asset_bucket, relative)
        if args.upload:
            upload(settings.gcs_asset_bucket, relative, path)

        kept = 0
        for observation in observations:
            entity = observation["entity"]
            attribute = observation["attribute"]
            numeric = observation.get("numeric_value")
            # A row with no number cannot take part in a numeric comparison,
            # and the comparisons are the point. Keep the presence flags,
            # drop measurements that came back as prose.
            if numeric is None and attribute != "present":
                continue
            rows.append(
                {
                    "obs_id": hashlib.sha1(
                        f"{take_id}/{entity}/{attribute}".encode()
                    ).hexdigest()[:16],
                    "take_id": take_id,
                    "scene_id": SCENE_ID,
                    "story_beat": beats.get(take_id, 0),
                    "frame_ts": captured.get(take_id, "2026-12-03 21:00:00") + ".000",
                    "frame_uri": uri,
                    "entity": entity,
                    "attribute": attribute,
                    "value": str(observation.get("value", ""))[:120],
                    "numeric_value": "" if numeric is None else round(float(numeric), 4),
                    "monotonic_dir": MONOTONIC.get((entity, attribute), "none"),
                    "in_focus": int(bool(observation.get("in_focus"))),
                    "frame_coverage_pct": round(
                        float(observation.get("frame_coverage_pct", 0.0)), 2
                    ),
                    "confidence": round(float(observation.get("confidence", 0.0)), 3),
                }
            )
            kept += 1

        print(f"  [{index:>2}/{len(frames)}] {take_id:<20} {kept:>2} measurements")

    out_dir = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "frame_observations.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    try:
        shown = out_path.relative_to(ROOT)
    except ValueError:
        shown = out_path  # writing outside the repo, e.g. a scratch smoke test
    print(f"\n{len(rows)} observations -> {shown}")
    print("Load with: python scripts/load_data.py --data data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
