#!/usr/bin/env python3
"""Put the rendered frames in the bucket, so the console can show them.

Separate from the perception pass on purpose. That pass looks at the two
frames a cut actually joins, the head and the tail, and spending a vision call
on the six between them would double the cost to measure a difference smaller
than the measurement error. But the console scrubs through all eight, so all
eight have to be reachable.

Skips anything already there at the same size, so a re-run after re-rendering
one setup costs one setup's worth of uploads.

Usage:
    python scripts/upload_frames.py
    python scripts/upload_frames.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))

from app.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", default="assets/frames")
    parser.add_argument("--force", action="store_true", help="re-upload even if unchanged")
    args = parser.parse_args()

    from google.cloud import storage

    settings = get_settings()
    frames_root = ROOT / args.frames if not Path(args.frames).is_absolute() else Path(args.frames)
    if not frames_root.is_dir():
        sys.exit(f"{frames_root} does not exist. Run composite_variants.py --all first.")

    paths = sorted(frames_root.rglob("f*.jpg"))
    if not paths:
        sys.exit("no frames found")

    client = storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.gcs_asset_bucket)

    print(f"Uploading {len(paths)} frames to gs://{settings.gcs_asset_bucket}/frames\n")
    sent = skipped = 0

    for path in paths:
        name = f"frames/{path.relative_to(frames_root).as_posix()}"
        blob = bucket.blob(name)
        if not args.force:
            blob.reload(client=client) if blob.exists(client) else None
            if blob.size == path.stat().st_size:
                skipped += 1
                continue
        blob.upload_from_filename(str(path), content_type="image/jpeg")
        sent += 1
        if sent % 20 == 0:
            print(f"  {sent} uploaded")

    print(f"\n{sent} uploaded, {skipped} already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
