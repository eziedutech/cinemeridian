#!/usr/bin/env python3
"""Prove that ClickHouse is reached through MCP at runtime.

This is the check that decides whether the project meets the track
requirement, so it verifies the thing itself rather than a proxy for it:

  Stage 1  the mcp-clickhouse stdio server starts and advertises its tools
  Stage 2  the agent, driven by Gemini, calls run_query and gets rows
           back — with the tool call and its result printed, not summarised

Stage 2 needs Vertex AI credentials. Without them stage 1 still runs and says
what is missing, because "the MCP server works but the model is unreachable"
and "the MCP server is broken" are very different problems.

Run it inside the container too, not only on a laptop. A stdio subprocess that
works locally and dies in Cloud Run is the classic way to fail this track.

Usage:
    python scripts/verify_mcp.py
    python scripts/verify_mcp.py --stage 1
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

from app.agent import AGENT_NAME, build_agent, build_clickhouse_toolset  # noqa: E402
from app.settings import ConfigError, get_settings  # noqa: E402

QUESTION = (
    "How many rows are in the ephemeris table for production prod_tideline, "
    "and what is the earliest timestamp? Use one SQL query."
)


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


async def stage_one() -> list[str]:
    """Start the MCP server and list what it offers."""
    _rule("Stage 1 - mcp-clickhouse stdio server")
    settings = get_settings()
    # Report the user the subprocess actually connects as, not the admin one
    # in the config — they differ, and that difference is the safety boundary.
    effective_user = settings.mcp_clickhouse_env()["CLICKHOUSE_USER"]
    print(
        f"  target   {effective_user}@{settings.clickhouse_host}"
        f"/{settings.clickhouse_database}"
        f"{' [restricted]' if settings.uses_restricted_user else ' [admin]'}"
    )

    toolset = build_clickhouse_toolset(settings)
    started = time.perf_counter()
    try:
        tools = await toolset.get_tools()
    finally:
        elapsed = time.perf_counter() - started

    names = sorted(tool.name for tool in tools)
    print(f"  started  in {elapsed:.1f}s")
    print(f"  tools    {', '.join(names)}")

    if "run_query" not in names:
        raise SystemExit(
            "  FAIL: run_query is not exposed. Without it the agent "
            "cannot query ClickHouse through MCP."
        )
    print("  PASS: run_query is available over MCP")
    await toolset.close()
    return names


async def stage_two() -> None:
    """Drive the real agent and watch the MCP tool actually get called."""
    _rule("Stage 2 - the agent queries ClickHouse through MCP")

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent = build_agent()
    runner = InMemoryRunner(agent=agent, app_name="cinemeridian_verify")
    session = await runner.session_service.create_session(
        app_name="cinemeridian_verify", user_id="verifier"
    )

    print(f"  asking   {QUESTION}\n")
    tool_calls: list[tuple[str, dict]] = []
    tool_results = 0
    answer_parts: list[str] = []
    started = time.perf_counter()

    async for event in runner.run_async(
        user_id="verifier",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=QUESTION)]),
    ):
        for call in event.get_function_calls() or []:
            tool_calls.append((call.name, dict(call.args or {})))
            print(f"  CALL     {call.name}")
            for key, value in (call.args or {}).items():
                print(f"           {key} = {' '.join(str(value).split())[:200]}")
        for response in event.get_function_responses() or []:
            tool_results += 1
            payload = json.dumps(response.response, default=str)
            print(f"  RESULT   {' '.join(payload.split())[:300]}")
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    answer_parts.append(part.text)

    elapsed = time.perf_counter() - started
    print(f"\n  answer   {' '.join(''.join(answer_parts).split())}")
    print(f"  elapsed  {elapsed:.1f}s, {len(tool_calls)} tool call(s)")

    called = {name for name, _ in tool_calls}
    if "run_query" not in called:
        raise SystemExit(
            f"  FAIL: the agent answered without calling run_query "
            f"(called: {sorted(called) or 'nothing'}). An answer that does not "
            f"come from ClickHouse does not count."
        )
    if not tool_results:
        raise SystemExit("  FAIL: run_query was called but returned nothing.")
    print(f"  PASS: {AGENT_NAME} queried ClickHouse over MCP and used the result")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, choices=(1, 2), help="run only this stage")
    args = parser.parse_args()

    try:
        get_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration problem: {exc}")

    if args.stage != 2:
        await stage_one()
    if args.stage != 1:
        try:
            await stage_two()
        except Exception as exc:  # noqa: BLE001 - the message is the point
            text = str(exc).lower()
            # ADC is a separate login from `gcloud auth login`, and having
            # already done the latter is exactly what makes this surprising.
            if any(
                marker in text
                for marker in ("credential", "reauthentication", "refresh", "unauthenticated")
            ):
                print(
                    "\n  SKIPPED: Vertex AI credentials are missing or stale.\n"
                    "  This is application-default credentials, a SEPARATE login\n"
                    "  from `gcloud auth login`:\n\n"
                    "      gcloud auth application-default login\n"
                    "      gcloud auth application-default set-quota-project "
                    f"{get_settings().project_id}\n\n"
                    f"  ({type(exc).__name__}: {exc})"
                )
                return 0
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
