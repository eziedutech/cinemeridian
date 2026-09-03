#!/usr/bin/env python3
"""Freeze one complete run of the demo scene, so the front page opens instantly.

A visitor arriving at the front page should see a finished piece of work, not a
spinner and four minutes of waiting. The findings already live in ClickHouse and
are cheap to read, but the two things that make a review readable are not: the
agent's own report, and the comparison of the two frames a cut joins.

So they are computed once, here, and written to a file the page loads. Nothing
is invented: the report is the text the agent produced on a real run, the grid
differences are what the perception pass actually returned, and both are stamped
with when they were made. If the scene changes, this is run again.

The pair chosen for the comparison is deliberately from inside one setup. Cuts
in this scene are mostly between setups, wide against close, where the framing
moves and a grid stops meaning anything by our own rule. Two takes of the same
setup share framing exactly, and that is also where the mis-slated take lives.

Usage:
    python scripts/build_showcase.py
    python scripts/build_showcase.py --api http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "codes" / "frontremix" / "app" / "showcase.json"

DEFAULT_API = "https://cinemeridian-api-802348533365.us-central1.run.app"
EDIT_VERSION = "v14"
SCENE_ID = "sc14"

#: Two takes of one setup, so the framing is identical and the grid means what
#: it says. This is also the setup carrying the seventy minute slate error.
PAIR = (("su03", "t02", 7), ("su03", "t03", 0))

GRID = {"columns": 4, "rows": 3}
GAP, BAND = 24, 34


#: Where the demo scene's frames live. The API proxies them rather than the
#: bucket being public, so this asks for them the same way the console does.
BUCKET = "cinemeridian-assets"


def fetch_frame(api: str, setup: str, take: str, index: int) -> Image.Image:
    """Pull one demo frame through the API's own proxy."""
    uri = f"gs://{BUCKET}/frames/{SCENE_ID}/{setup}/{take}/f{index:03d}.jpg"
    response = requests.get(f"{api}/api/frame", params={"uri": uri}, timeout=120)
    response.raise_for_status()
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def with_grid(image: Image.Image, title: str) -> Image.Image:
    """The same grid the browser draws, so a named cell means the same thing."""
    canvas = Image.new("RGB", (image.width, image.height + BAND), "black")
    canvas.paste(image, (0, BAND))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), title, fill="white")

    cell_w = image.width / GRID["columns"]
    cell_h = image.height / GRID["rows"]
    for column in range(1, GRID["columns"]):
        x = column * cell_w
        draw.line([(x, BAND), (x, canvas.height)], fill=(255, 90, 60), width=2)
    for row in range(1, GRID["rows"]):
        y = BAND + row * cell_h
        draw.line([(0, y), (canvas.width, y)], fill=(255, 90, 60), width=2)
    for row in range(GRID["rows"]):
        for column in range(GRID["columns"]):
            draw.text(
                (column * cell_w + 8, BAND + row * cell_h + 6),
                f"{chr(65 + column)}{row + 1}",
                fill=(255, 200, 120),
            )
    return canvas


def compose_pair(api: str) -> bytes:
    (setup_a, take_a, frame_a), (setup_b, take_b, frame_b) = PAIR
    left = with_grid(fetch_frame(api, setup_a, take_a, frame_a), "LEFT: outgoing")
    right = with_grid(fetch_frame(api, setup_b, take_b, frame_b), "RIGHT: incoming")

    pair = Image.new(
        "RGB", (left.width + GAP + right.width, max(left.height, right.height)), "black"
    )
    pair.paste(left, (0, 0))
    pair.paste(right, (left.width + GAP, 0))

    buffer = io.BytesIO()
    pair.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def run_agent(api: str) -> tuple[str, list[dict], float]:
    """Stream one analysis and keep the report and the steps it took."""
    started = time.perf_counter()
    response = requests.post(
        f"{api}/api/analyze",
        json={"edit_version": EDIT_VERSION, "scene_id": SCENE_ID},
        stream=True,
        timeout=900,
    )
    response.raise_for_status()

    report = ""
    steps: list[dict] = []
    buffer = ""

    for chunk in response.iter_content(chunk_size=None):
        buffer += chunk.decode("utf-8", errors="replace")
        frames = re.split(r"\r?\n\r?\n", buffer)
        buffer = frames.pop()
        for frame in frames:
            kind = data = None
            for line in re.split(r"\r?\n", frame):
                if line.startswith("event:"):
                    kind = line[6:].strip()
                if line.startswith("data:"):
                    data = (data or "") + line[5:].strip()
            if not kind or not data:
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if kind == "reasoning":
                report = payload.get("text", "")
            elif kind in ("tool_call", "tool_result"):
                steps.append({"kind": kind, **payload})

    return report, steps, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="keep the report already on file and refresh only the comparison",
    )
    args = parser.parse_args()

    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}

    print("composing the two frames one cut joins")
    pair = compose_pair(args.api)
    print(f"  {len(pair)} bytes")

    print("asking what changed between them")
    ground = requests.post(
        f"{args.api}/api/ground",
        files={"pair": ("pair.jpg", pair, "image/jpeg")},
        data={"columns": GRID["columns"], "rows": GRID["rows"]},
        timeout=300,
    )
    ground.raise_for_status()
    comparison = ground.json()
    print(f"  {len(comparison.get('differences', []))} cells differ")

    if args.skip_agent and existing.get("report"):
        report = existing["report"]
        steps = existing.get("steps", [])
        seconds = existing.get("seconds", 0)
        print("keeping the report already on file")
    else:
        print("running the agent (this takes minutes)")
        report, steps, seconds = run_agent(args.api)
        print(f"  {len(steps) // 2} steps in {seconds:.0f}s")

    OUT.write_text(
        json.dumps(
            {
                "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "edit_version": EDIT_VERSION,
                "scene_id": SCENE_ID,
                "seconds": round(seconds, 1),
                "report": report,
                "steps": steps,
                "comparison": comparison,
                "pair": {
                    "outgoing": {"setup": PAIR[0][0], "take": PAIR[0][1], "frame": PAIR[0][2]},
                    "incoming": {"setup": PAIR[1][0], "take": PAIR[1][1], "frame": PAIR[1][2]},
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"written to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
