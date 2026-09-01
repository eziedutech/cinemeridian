#!/usr/bin/env python3
"""Generate the base plates for the synthetic scene, on Vertex AI.

One plate per camera setup, generated flat: overcast, soft, directionless
light. That is deliberate and it is the whole trick.

An image model cannot be asked for "the same scene again, with one variable
changed" — ask twice and you get two different beaches, two different actors,
two different compositions, and nothing is comparable. So the model is used
only for the part that must look real and never has to change: the sand, the
sea, the sky, the figures. Everything that *does* change between takes —
shadow direction and length, colour temperature, footprint count, cloud
position, waterline height — is composited on afterwards by
scripts/composite_variants.py, at values we choose and therefore know exactly.

Flat light in the plate is what makes that possible. A plate generated at
golden hour arrives with shadows baked in at an angle nobody chose and cannot
remove, and every composited shadow then fights one that is already there.

Consistency across setups comes from passing the chosen master plate back in
as a reference image, so su02 through su08 are the same beach on the same day
rather than eight different beaches.

Note on region: the Gemini 3 image models are served from `global`, not from
us-central1. This is setup tooling, so the region difference costs nothing —
runtime vision still runs in us-central1 alongside ClickHouse.

Usage:
    python scripts/make_plates.py --setup su01 --candidates 3
    python scripts/make_plates.py --setup su02 --reference assets/plates/su01.png
    python scripts/make_plates.py --rest        # every setup except the master
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))

from app.settings import get_settings  # noqa: E402

PLATES_DIR = ROOT / "assets" / "plates"

#: Quality matters more than latency here — a handful of images, generated
#: once. Served only from `global`.
MODEL = "gemini-3-pro-image"
LOCATION = "global"

#: A new project's quota for the pro image model is very small — four images
#: was enough to exhaust it. The flash image model is served from us-central1
#: with a far larger allowance and is good enough for the setups that carry
#: less of the frame: close-ups, the sand insert, the empty CG plate.
FALLBACK_MODEL = "gemini-2.5-flash-image"
FALLBACK_LOCATION = "us-central1"

#: 429 on these models is a rate limit rather than a hard wall, so backing off
#: and retrying recovers. Falling back only after that, not instead of it.
RETRY_DELAYS_S = (20, 45, 90)

#: Shared language for every plate, so the setups read as one location shot by
#: one crew. The "no visible sun, no strong shadows" clause is load-bearing.
COMMON = (
    "Photorealistic cinematic film still, 16:9 anamorphic, 35mm film grain, "
    "muted natural colour, neutral white balance. Tropical Pacific beach: wet "
    "dark sand, calm sea, a low green headland on the horizon, dune grass. "
    "Bright overcast sky, flat even diffuse light, no visible sun, no strong "
    "directional shadows, no golden hour, no orange in the sky. Smooth "
    "unmarked sand with no footprints. No text, no watermark, no logo."
)

SETUP_PROMPTS = {
    "su01": (
        "Wide master shot. Two people stand a few metres apart near the "
        "waterline, mid-conversation, seen from behind and slightly to the "
        "side so their faces are not readable. The sea fills the background."
    ),
    "su02": (
        "Medium shot over the left figure's shoulder onto the right figure. "
        "Dunes and dune grass behind them; the sea is out of frame."
    ),
    "su03": (
        "Reverse medium shot onto the left figure, over the right figure's "
        "shoulder. Dunes and dune grass behind."
    ),
    "su04": "Close-up of the left figure in profile, shallow depth of field, soft dune background.",
    "su05": "Close-up of the right figure in profile, shallow depth of field, soft dune background.",
    "su06": (
        "Two-shot in profile, both figures side on to camera, flat open sand "
        "between them and the sea."
    ),
    "su07": (
        "Insert shot looking down at a stretch of smooth wet sand. No people "
        "in frame. Completely unmarked sand."
    ),
    "su08": (
        "Wide plate matching the master framing but empty of people, with the "
        "headland area left plain and uncluttered for a CG set extension."
    ),
}

REFERENCE_NOTE = (
    " Match the location, weather, light, colour and wardrobe of the reference "
    "image exactly. Same beach, same day, same two people, same flat overcast "
    "light. Only the camera position changes."
)


def build_prompt(setup_id: str, has_reference: bool) -> str:
    prompt = f"{SETUP_PROMPTS[setup_id]} {COMMON}"
    return prompt + REFERENCE_NOTE if has_reference else prompt


def generate(setup_id: str, candidates: int, reference: Path | None) -> list[Path]:
    from google.genai import Client, types

    settings = get_settings()

    prompt = build_prompt(setup_id, reference is not None)
    contents: list = [prompt]
    if reference is not None:
        contents.insert(
            0,
            types.Part.from_bytes(
                data=reference.read_bytes(),
                mime_type="image/png" if reference.suffix == ".png" else "image/jpeg",
            ),
        )

    print(f"\n{setup_id}" + (f"  (reference: {reference.name})" if reference else ""))

    def call(model: str, location: str):
        client = Client(vertexai=True, project=settings.project_id, location=location)
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

    def call_with_backoff():
        """Retry a rate limit, then drop to the cheaper model rather than stop.

        429 here is a per-minute rate limit, not an exhausted allowance, so
        waiting recovers. The fallback exists so a long run finishes rather
        than dying two setups from the end.
        """
        for attempt, delay in enumerate(RETRY_DELAYS_S, start=1):
            try:
                return call(MODEL, LOCATION), MODEL
            except Exception as exc:  # noqa: BLE001
                if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                    raise
                print(f"  rate limited (attempt {attempt}), waiting {delay}s")
                time.sleep(delay)
        print(f"  falling back to {FALLBACK_MODEL}")
        return call(FALLBACK_MODEL, FALLBACK_LOCATION), FALLBACK_MODEL

    PLATES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index in range(1, candidates + 1):
        # One image per call. The image models return a single image, so
        # candidates come from repeated calls rather than a count parameter.
        response, used_model = call_with_backoff()
        parts = response.candidates[0].content.parts if response.candidates else []
        for part in parts:
            if part.inline_data and part.inline_data.data:
                path = PLATES_DIR / f"{setup_id}-candidate-{index:02d}.png"
                path.write_bytes(part.inline_data.data)
                written.append(path)
                print(
                    f"  wrote  {path.relative_to(ROOT)}  "
                    f"({len(part.inline_data.data) // 1024} KB, {used_model})"
                )
            elif part.text:
                print(f"  note   {part.text.strip()[:140]}")

    if not written:
        print("  nothing written - every candidate was filtered or empty")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", help="one setup id, e.g. su01")
    parser.add_argument(
        "--rest", action="store_true", help="every setup except su01, using it as reference"
    )
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--reference", help="plate to match location and wardrobe against")
    args = parser.parse_args()

    reference = Path(args.reference) if args.reference else None
    if reference and not reference.is_file():
        raise SystemExit(f"reference not found: {reference}")

    if args.rest:
        master = PLATES_DIR / "su01.png"
        if not master.is_file():
            raise SystemExit(
                "assets/plates/su01.png does not exist. Generate the master first, "
                "pick a candidate, and rename it to su01.png."
            )
        setups = [s for s in SETUP_PROMPTS if s != "su01"]
        reference = master
    elif args.setup:
        if args.setup not in SETUP_PROMPTS:
            raise SystemExit(f"unknown setup {args.setup}; known: {', '.join(SETUP_PROMPTS)}")
        setups = [args.setup]
    else:
        raise SystemExit("pass --setup <id> or --rest")

    total = sum(len(generate(s, args.candidates, reference)) for s in setups)
    print(f"\n{total} candidate plate(s) in {PLATES_DIR.relative_to(ROOT)}")
    print("Pick one per setup, rename it to <setup_id>.png, delete the rest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
