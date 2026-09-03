#!/usr/bin/env python3
"""Write a capture time into a sample clip's `mvhd` box.

Editing a clip in most tools drops the creation time, and a file that has
forgotten when it was recorded is exactly the case `/try` handles by asking the
visitor. For the sample clips that ship with the project that is the wrong
answer: they are a worked example, and a judge should not have to type in facts
before the example works.

So the time is written back. Only the sample clips, only the field an editor
stripped, and only to what the clip is meant to depict. The patch is four or
eight bytes in place, the same width as what was there, so nothing else in the
file moves.

What this must never be used for is making footage claim a time it was not
filmed at in order to produce a finding. The faults in these samples are in the
pictures - a flipped shadow, a bag that appears, a room that turns to night -
and the timestamps only say when each shot claims to have happened, which is
what a slate does on a real set.

Usage:
    python scripts/stamp_sample.py path/to/clip.mp4 "2026-09-02 23:53:54"
    python scripts/stamp_sample.py path/to/clip.mp4 --show
"""

from __future__ import annotations

import argparse
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: MP4 counts seconds from 1904, not 1970.
EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


def walk(data: bytes, start: int, end: int):
    """Yield every box between two offsets, without descending."""
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8].decode("latin-1")
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header:
            return
        yield kind, offset + header, offset + size
        offset += size


def find_mvhd(data: bytes) -> tuple[int, int] | None:
    """Where the movie header's body starts, and which version it is."""
    for kind, body, end in walk(data, 0, len(data)):
        if kind != "moov":
            continue
        for inner, inner_body, _ in walk(data, body, end):
            if inner == "mvhd":
                return inner_body, data[inner_body]
    return None


def read_time(data: bytes) -> datetime | None:
    found = find_mvhd(data)
    if not found:
        return None
    body, version = found
    if version == 1:
        seconds = struct.unpack(">Q", data[body + 4 : body + 12])[0]
    else:
        seconds = struct.unpack(">I", data[body + 4 : body + 8])[0]
    return EPOCH + timedelta(seconds=seconds) if seconds else None


def write_time(data: bytearray, when: datetime) -> None:
    """Set creation and modification time to the same moment, in place."""
    found = find_mvhd(bytes(data))
    if not found:
        raise SystemExit("this file has no mvhd box; it may not be an MP4")

    body, version = found
    seconds = int((when.astimezone(timezone.utc) - EPOCH).total_seconds())
    if seconds < 0:
        raise SystemExit("MP4 cannot express a time before 1904")

    if version == 1:
        struct.pack_into(">Q", data, body + 4, seconds)
        struct.pack_into(">Q", data, body + 12, seconds)
    else:
        if seconds > 0xFFFFFFFF:
            raise SystemExit("this file's header is too narrow for that date")
        struct.pack_into(">I", data, body + 4, seconds)
        struct.pack_into(">I", data, body + 8, seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    parser.add_argument("when", nargs="?", help='UTC, as "YYYY-MM-DD HH:MM:SS"')
    parser.add_argument("--show", action="store_true", help="read it and stop")
    args = parser.parse_args()

    data = bytearray(args.clip.read_bytes())
    before = read_time(bytes(data))

    if args.show or not args.when:
        print(f"{args.clip.name}: {before or '(no capture time)'}")
        return 0

    moment = datetime.strptime(args.when, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    write_time(data, moment)
    args.clip.write_bytes(bytes(data))

    after = read_time(bytes(args.clip.read_bytes()))
    print(f"{args.clip.name}: {before or '(none)'} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
