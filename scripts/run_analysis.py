#!/usr/bin/env python3
"""Drive one full analysis from the command line, and print what the agent did.

The same code path the SSE endpoint uses, without the HTTP in the way. Useful
for watching an investigation end to end, and for the thing that matters most
when judging this system: whether the agent is deciding anything, or whether it
is walking a script with commentary.

Read the trace with that question in mind. Queries it wrote itself, candidates
it dismissed with a reason, an adjudication it chose not to spend - those are
the signs of the former.

Usage:
    python scripts/run_analysis.py
    python scripts/run_analysis.py --edit-version v13
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codes" / "backpy"))

from app.agent import build_agent  # noqa: E402
from app.prompts import ANALYSIS_TASK  # noqa: E402
from app.settings import get_settings  # noqa: E402

PRODUCTION_ID = "prod_tideline"
LATITUDE = 8.75
LONGITUDE = -83.5


def _short(value: object, limit: int = 220) -> str:
    return " ".join(str(value).split())[:limit]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edit-version", default="v14")
    parser.add_argument("--scene-id", default="sc14")
    args = parser.parse_args()

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    settings = get_settings()
    agent = build_agent(settings)
    runner = InMemoryRunner(agent=agent, app_name="cinemeridian_cli")
    session = await runner.session_service.create_session(
        app_name="cinemeridian_cli", user_id="cli"
    )

    task = ANALYSIS_TASK.format(
        edit_version=args.edit_version,
        scene_id=args.scene_id,
        production_id=PRODUCTION_ID,
        latitude=LATITUDE,
        longitude=LONGITUDE,
    )

    print(f"Analysing {args.scene_id} {args.edit_version} with {settings.model}\n")
    started = time.perf_counter()
    calls = 0
    reasoning: list[str] = []

    async for event in runner.run_async(
        user_id="cli",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=task)]),
    ):
        elapsed = time.perf_counter() - started
        for call in event.get_function_calls() or []:
            calls += 1
            print(f"[{elapsed:6.1f}s] CALL   {call.name}")
            for key, value in (call.args or {}).items():
                print(f"                 {key} = {_short(value, 400)}")
        for response in event.get_function_responses() or []:
            print(f"[{elapsed:6.1f}s] RESULT {_short(response.response, 300)}")
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reasoning.append(part.text)
                    print(f"[{elapsed:6.1f}s] SAYS   {_short(part.text, 600)}")
        # A run that stops without saying anything has usually hit a limit
        # rather than finished. Surfacing the reason turns a silent stop into
        # something diagnosable.
        if getattr(event, "error_code", None):
            print(f"[{elapsed:6.1f}s] ERROR  {event.error_code}: {event.error_message}")
        finish = getattr(event, "finish_reason", None)
        if finish and str(finish) not in ("STOP", "FinishReason.STOP", "None"):
            print(f"[{elapsed:6.1f}s] FINISH {finish}")
        usage = getattr(event, "usage_metadata", None)
        if usage is not None:
            print(
                f"[{elapsed:6.1f}s] TOKENS prompt={getattr(usage, 'prompt_token_count', '?')} "
                f"candidates={getattr(usage, 'candidates_token_count', '?')} "
                f"total={getattr(usage, 'total_token_count', '?')}"
            )

    print(f"\n{'=' * 70}")
    print(f"{calls} tool calls in {time.perf_counter() - started:.1f}s\n")
    print("".join(reasoning).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
