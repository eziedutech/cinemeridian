#!/usr/bin/env python3
"""Composite the controlled variables onto a base plate.

This is the part of the asset pipeline that has to be exact, and it is exact
precisely because no model is involved. The plate supplies everything that
must look real and never changes. Everything a continuity error could live in
is drawn here, from numbers we choose:

    shadow direction     rotate the cast shape to a computed bearing
    shadow length        scale it by cot(solar elevation)
    shadow hardness      blur radius on the edge
    colour temperature   channel gain, warm to cool
    footprint count      stamp N prints, N is an integer we picked
    waterline height     move the wet-sand boundary

Because the values are chosen rather than measured, the ground truth is exact
by construction. That is the whole reason for building assets this way instead
of generating each variant: two prompts produce two different beaches, and
nothing is comparable. One plate plus arithmetic produces the same beach with
one thing different, which is the only thing worth measuring.

The shadow projection is a deliberate simplification: the sand is treated as a
flat plane, and a compass bearing relative to the camera is mapped into image
space with a single foreshortening constant measured from the plate. It is not
a camera solve. It does not need to be — what matters is that the *same*
projection is used to draw the shadow and to predict it, so agreement and
disagreement both mean something.

Usage:
    python scripts/composite_variants.py --demo
    python scripts/composite_variants.py --all --out assets/frames/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import ephemeris as eph  # noqa: E402
from generate_telemetry import LATITUDE, LONGITUDE, UTC_OFFSET_H  # noqa: E402

UTC = timezone.utc
PLATES_DIR = ROOT / "assets" / "plates"

#: How dark a cast shadow is against wet sand, at full strength. Tuned by
#: eye and then checked the only way that counts: by asking the vision model
#: to measure the result. Too faint and the measurement confidence collapses,
#: which makes the whole pipeline look unreliable when it is only underexposed.
SHADOW_ALPHA = 150

#: A small dark patch where the figure meets the sand. Without it a long cast
#: shadow reads as a detached smudge, because the plate was lit flat and the
#: figures have no contact shadow of their own.
CONTACT_ALPHA = 120

#: Width of a cast shadow relative to the figure's height in frame. A standing
#: person's shadow is a long thin wedge, not an ellipse.
SHADOW_WIDTH_RATIO = 0.30


@dataclass(frozen=True)
class FrameSpec:
    """Everything that varies between frames, and therefore the answer key."""

    take_id: str
    setup_id: str
    story_beat: int
    captured_at: str            # true capture time, UTC
    camera_heading_deg: float
    sun_azimuth_deg: float
    sun_elevation_deg: float
    shadow_direction_deg: float  # in-frame bearing: 0 up, 90 right, 180 down
    shadow_length_ratio: float
    shadow_hardness: float
    color_temp_k: int
    footprint_count: int
    waterline_offset: float      # 0 = as shot, positive = tide further up the beach


#: Setups with no figure standing on visible ground. A close-up shows a face,
#: not the sand; the insert and the CG plate have nobody in them. These frames
#: still carry colour temperature and footprint count, which is most of what
#: they are cut for — but no cast shadow, and claiming one would be a lie the
#: vision pass would then dutifully measure.
NO_GROUND_SHADOW = {"su04", "su05", "su07", "su08"}


def load_plate(setup_id: str) -> tuple[Image.Image, dict]:
    image = Image.open(PLATES_DIR / f"{setup_id}.png").convert("RGB")
    meta_path = PLATES_DIR / f"{setup_id}.meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = {"figures": [], "ground": {"foreshorten": 0.5}, "horizon_y": 0.4}
    meta.setdefault("figures", [])
    meta.setdefault("ground", {"foreshorten": 0.5})
    if setup_id in NO_GROUND_SHADOW:
        meta["figures"] = []
    return image, meta


def project_bearing(bearing_deg: float, foreshorten: float) -> tuple[float, float]:
    """Turn an in-frame compass bearing into an image-space unit vector.

    0 degrees points away from camera (up the frame), 90 to frame right, 180
    toward camera (down the frame). The vertical component is compressed by
    the foreshortening of the ground plane; the horizontal is not.
    """
    radians = math.radians(bearing_deg)
    return math.sin(radians), -math.cos(radians) * foreshorten


def draw_shadow(
    image: Image.Image,
    figure: dict,
    spec: FrameSpec,
    foreshorten: float,
) -> None:
    """Draw one cast shadow, as a tapered wedge from the figure's feet."""
    width, height = image.size
    feet_x = figure["feet_x"] * width
    feet_y = figure["feet_y"] * height
    figure_height_px = (figure["feet_y"] - figure["head_y"]) * height

    dx, dy = project_bearing(spec.shadow_direction_deg, foreshorten)
    length_px = figure_height_px * spec.shadow_length_ratio
    tip_x = feet_x + dx * length_px
    tip_y = feet_y + dy * length_px

    # Perpendicular, for the width of the wedge at the foot end.
    half_width = figure_height_px * SHADOW_WIDTH_RATIO / 2
    perp_x, perp_y = -dy, dx
    norm = math.hypot(perp_x, perp_y) or 1.0
    perp_x, perp_y = perp_x / norm * half_width, perp_y / norm * half_width

    layer = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(layer)
    draw.polygon(
        [
            (feet_x - perp_x, feet_y - perp_y),
            (feet_x + perp_x, feet_y + perp_y),
            (tip_x + perp_x * 0.35, tip_y + perp_y * 0.35),
            (tip_x - perp_x * 0.35, tip_y - perp_y * 0.35),
        ],
        fill=SHADOW_ALPHA,
    )
    # Contact shadow, drawn before the blur so it softens with everything else.
    contact = figure_height_px * 0.055
    draw.ellipse(
        [feet_x - contact * 1.6, feet_y - contact * 0.7,
         feet_x + contact * 1.6, feet_y + contact * 0.7],
        fill=CONTACT_ALPHA,
    )

    # Hardness 1 is a crisp edge, 0 is fully diffuse. Sun low in a clear sky
    # gives long hard shadows; overcast gives none at all.
    blur = (1.0 - spec.shadow_hardness) * figure_height_px * 0.14 + 1.0
    layer = layer.filter(ImageFilter.GaussianBlur(blur))

    shadow = Image.new("RGB", image.size, (18, 20, 26))
    image.paste(shadow, (0, 0), layer)


def apply_color_temperature(image: Image.Image, kelvin: int) -> Image.Image:
    """Grade the frame toward a colour temperature.

    A simple channel gain against a 5500 K neutral. Warmer light lifts red and
    drops blue; cooler does the reverse. Enough to be measured, and honest
    about being an approximation rather than a spectral render.
    """
    neutral = 5500.0
    ratio = max(0.35, min(2.2, neutral / max(kelvin, 1200)))
    red_gain = ratio**0.45
    blue_gain = (1.0 / ratio) ** 0.45
    red, green, blue = image.split()
    red = red.point(lambda v: min(255, int(v * red_gain)))
    blue = blue.point(lambda v: min(255, int(v * blue_gain)))
    return Image.merge("RGB", (red, green, blue))


def stamp_footprints(
    image: Image.Image, count: int, meta: dict, seed_take: str
) -> None:
    """Stamp exactly `count` footprints into the sand.

    Placement is deterministic per take so a frame regenerates identically, but
    the *count* is the variable under test. Footprints only accumulate, which
    is what makes a decreasing count across a cut a continuity error rather
    than a matter of taste.
    """
    import random

    width, height = image.size
    rng = random.Random(f"{seed_take}-prints")
    layer = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(layer)

    # Keep them on the open sand below the figures and clear of the dune grass.
    # With nobody in frame — the insert, the empty CG plate — the sand starts
    # higher and the prints have the whole lower half to live in.
    figures = meta.get("figures") or []
    top = (max(f["feet_y"] for f in figures) + 0.01) if figures else 0.45
    for _ in range(count):
        x = rng.uniform(0.30, 0.68) * width
        y = rng.uniform(top, min(top + 0.14, 0.86)) * height
        # Perspective: prints nearer the camera are larger.
        scale = 1.0 + (y / height - top) * 3.0
        long_axis = 13.0 * scale
        # A footprint is a pair, not a dot. Drawing left and right offset
        # slightly is what makes a count readable as footsteps rather than
        # as sensor noise.
        for side in (-1, 1):
            cx = x + side * long_axis * 0.34
            cy = y + side * long_axis * 0.16
            draw.ellipse(
                [cx - long_axis * 0.26, cy - long_axis * 0.52,
                 cx + long_axis * 0.26, cy + long_axis * 0.52],
                fill=120,
            )

    layer = layer.filter(ImageFilter.GaussianBlur(1.1))
    image.paste(Image.new("RGB", image.size, (28, 26, 24)), (0, 0), layer)


def render(spec: FrameSpec, out_path: Path) -> None:
    image, meta = load_plate(spec.setup_id)
    foreshorten = meta["ground"]["foreshorten"]

    for figure in meta["figures"]:
        draw_shadow(image, figure, spec, foreshorten)

    if spec.footprint_count:
        stamp_footprints(image, spec.footprint_count, meta, spec.take_id)

    image = apply_color_temperature(image, spec.color_temp_k)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, quality=92)


def spec_for_take(
    take_id: str,
    setup_id: str,
    story_beat: int,
    captured_at: datetime,
    camera_heading_deg: float,
    footprint_count: int,
) -> FrameSpec:
    """Derive every visual variable from the physics of the capture time.

    Nothing here is invented. The shadow in the frame is the shadow the sun
    actually casts at that place and instant, which is what lets the agent's
    computed expectation and its visual measurement genuinely agree — or, when
    a slate is wrong, genuinely disagree.
    """
    sun = eph.solar_position(LATITUDE, LONGITUDE, captured_at)
    return FrameSpec(
        take_id=take_id,
        setup_id=setup_id,
        story_beat=story_beat,
        captured_at=captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        camera_heading_deg=camera_heading_deg,
        sun_azimuth_deg=round(sun.azimuth_deg, 3),
        sun_elevation_deg=round(sun.elevation_deg, 3),
        shadow_direction_deg=round(
            eph.shadow_direction_deg(sun.azimuth_deg, camera_heading_deg), 3
        ),
        shadow_length_ratio=round(sun.shadow_len_ratio, 3),
        # Clear low sun gives a hard edge; the higher and hazier, the softer.
        shadow_hardness=round(min(0.95, 0.55 + sun.elevation_deg / 90.0), 3),
        color_temp_k=sun.color_temp_k,
        footprint_count=footprint_count,
        waterline_offset=0.0,
    )


def demo() -> int:
    """Render one setup across the afternoon, so the drift is visible."""
    out_dir = ROOT / "assets" / "frames" / "_spike"
    day = datetime(2026, 12, 4, tzinfo=UTC)

    print("Compositing su01 across the afternoon of 4 Dec 2026.")
    print("Every value below is chosen, not measured - this is the answer key.\n")
    print(f"{'local':>7} {'shadow bearing':>15} {'length x height':>16} "
          f"{'hardness':>9} {'kelvin':>7} {'prints':>7}")

    specs = []
    for index, local_hour in enumerate([13.0, 14.5, 15.5, 16.0, 16.5]):
        captured = day + timedelta(hours=local_hour - UTC_OFFSET_H)
        spec = spec_for_take(
            take_id=f"spike_t{index + 1:02d}",
            setup_id="su01",
            story_beat=1,
            captured_at=captured,
            camera_heading_deg=270.0,   # master looks west, out to sea
            footprint_count=4 + index * 3,
        )
        specs.append(spec)
        render(spec, out_dir / f"{spec.take_id}.jpg")
        print(
            f"{int(local_hour):02d}:{int(round(local_hour % 1 * 60)):02d}  "
            f"{spec.shadow_direction_deg:>14.1f} {spec.shadow_length_ratio:>16.2f} "
            f"{spec.shadow_hardness:>9.2f} {spec.color_temp_k:>7} {spec.footprint_count:>7}"
        )

    (out_dir / "ground_truth.json").write_text(
        json.dumps([asdict(s) for s in specs], indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{len(specs)} frames in {out_dir.relative_to(ROOT)}")
    return 0


def render_all(out_root: Path) -> int:
    """Render one sampled frame per take of the scene, and the answer key.

    Frames are rendered from each take's **true** capture time, never from the
    slate. That is what gives the mis-slated take somewhere to be caught: its
    row in `takes` says one thing, its shadows say another, and only the
    physics knows which to believe.
    """
    from generate_production import (
        FOOTPRINTS_BY_SETUP,
        SCENE_ID,
        SCHEDULE,
        SETUPS,
        TAKE_LENGTH_S,
        TURNAROUND_S,
        _utc,
    )

    headings = {s[0]: s[2] for s in SETUPS}
    beats = {setup: index + 1 for index, setup in enumerate(FOOTPRINTS_BY_SETUP)}

    specs: list[FrameSpec] = []
    skipped: list[str] = []

    for setup_id, day_index, start_local, count in SCHEDULE:
        if not (PLATES_DIR / f"{setup_id}.png").is_file():
            skipped.append(setup_id)
            continue
        for take_number in range(1, count + 1):
            captured = _utc(day_index, start_local) + timedelta(
                seconds=(take_number - 1) * (TAKE_LENGTH_S + TURNAROUND_S)
            )
            take_id = f"{SCENE_ID}_{setup_id}_t{take_number:02d}"
            spec = spec_for_take(
                take_id=take_id,
                setup_id=setup_id,
                story_beat=beats[setup_id],
                captured_at=captured,
                camera_heading_deg=headings[setup_id],
                footprint_count=FOOTPRINTS_BY_SETUP[setup_id],
            )
            specs.append(spec)
            render(
                spec,
                out_root / SCENE_ID / setup_id / f"t{take_number:02d}" / "f000.jpg",
            )

    (out_root / SCENE_ID / "frame_truth.json").write_text(
        json.dumps([asdict(s) for s in specs], indent=2) + "\n", encoding="utf-8"
    )

    print(f"rendered {len(specs)} frames into {out_root.relative_to(ROOT)}/{SCENE_ID}")
    if skipped:
        print(f"  skipped (no plate yet): {', '.join(skipped)}")
    print(f"  answer key: {(out_root / SCENE_ID / 'frame_truth.json').relative_to(ROOT)}")
    print("  the key never enters a prompt, a table, or a file path")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="render the spike sequence")
    parser.add_argument("--all", action="store_true", help="render every take of the scene")
    parser.add_argument("--out", default="assets/frames")
    args = parser.parse_args()
    if args.demo:
        return demo()
    if args.all:
        out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
        return render_all(out)
    raise SystemExit("pass --demo or --all")


if __name__ == "__main__":
    raise SystemExit(main())
