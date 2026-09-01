"""FastAPI surface for CineMeridian.

Small on purpose. The interesting behaviour lives in the agent; this module
exists to expose it over HTTP and — just as importantly — to let the MCP path
be verified *inside the deployed container*, which is where it actually has to
work. A stdio subprocess that runs on a laptop and dies in Cloud Run is the
standard way to fail this track, and `/api/health/mcp` is how we find out
before a judge does.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import AGENT_NAME, build_agent, build_clickhouse_toolset
from app.settings import ConfigError, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cinemeridian")

APP_NAME = "cinemeridian"

#: The scene the demo analyses. One production, one location — hard-coded here
#: because it is demo scaffolding rather than configuration, and pretending
#: otherwise would add a settings knob nobody turns.
PRODUCTION_ID = "prod_tideline"
SCENE_LATITUDE = 8.75
SCENE_LONGITUDE = -83.5


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly at startup rather than on the first request. Cloud Run will
    # refuse to route to a container that cannot start, which is the correct
    # outcome for a misconfigured deploy.
    settings = get_settings()
    logger.info("starting %s", settings)

    # One toolset for the process, warmed here. Launching mcp-clickhouse costs
    # more than ten seconds the first time, and that cost belongs to startup,
    # not to whoever asks the first question.
    toolset = build_clickhouse_toolset(settings)
    started = time.perf_counter()
    try:
        tools = await toolset.get_tools()
        logger.info(
            "mcp-clickhouse ready in %.1fs: %s",
            time.perf_counter() - started,
            ", ".join(sorted(tool.name for tool in tools)),
        )
    except Exception:  # noqa: BLE001
        # Do not take the container down for this. The health endpoint reports
        # it accurately, and a running service that can say what is wrong is
        # more useful than a crash loop that cannot.
        logger.exception("mcp-clickhouse failed to start")

    app.state.clickhouse_toolset = toolset
    app.state.agent = build_agent(settings, clickhouse_toolset=toolset)
    app.state.runner = None
    try:
        yield
    finally:
        await toolset.close()


app = FastAPI(
    title="CineMeridian",
    description="Continuity intelligence for the shoot and the cut.",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]


class AskResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCall]
    elapsed_ms: int


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness only. Deliberately does not touch ClickHouse or Vertex AI."""
    return {"status": "ok", "agent": AGENT_NAME}


@app.get("/api/health/mcp")
async def health_mcp() -> dict[str, Any]:
    """Prove the MCP path works *in this container*.

    Starts the mcp-clickhouse subprocess and lists its tools. This is the
    check that distinguishes "deployed" from "deployed and actually able to
    reach ClickHouse the way the rules require".
    """
    settings = get_settings()
    toolset = build_clickhouse_toolset(settings)
    started = time.perf_counter()
    try:
        tools = await toolset.get_tools()
        names = sorted(tool.name for tool in tools)
    except Exception as exc:  # noqa: BLE001 - the message is the diagnosis
        logger.exception("mcp health check failed")
        raise HTTPException(status_code=503, detail=f"mcp-clickhouse unavailable: {exc}") from exc
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await toolset.close()

    ok = "run_query" in names
    return {
        "status": "ok" if ok else "degraded",
        "tools": names,
        "can_query": ok,
        "restricted_user": settings.uses_restricted_user,
        "startup_ms": elapsed_ms,
    }


@app.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Put a question to the agent and report what it did to answer it.

    The tool calls come back alongside the answer rather than being hidden.
    An answer about the footage that was not produced by a query against the
    footage is worth nothing, and the caller should be able to tell.
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner: InMemoryRunner = app.state.runner or InMemoryRunner(
        agent=app.state.agent, app_name=APP_NAME
    )
    app.state.runner = runner

    user_id = f"api-{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id)

    calls: list[ToolCall] = []
    answer: list[str] = []
    started = time.perf_counter()

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=request.question)]),
    ):
        for call in event.get_function_calls() or []:
            calls.append(ToolCall(name=call.name, args=dict(call.args or {})))
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    answer.append(part.text)

    return AskResponse(
        answer="".join(answer).strip(),
        tool_calls=calls,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


class AnalyzeRequest(BaseModel):
    edit_version: str = Field(default="v14", max_length=32)
    scene_id: str = Field(default="sc14", max_length=32)


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    """Review an edit version, streaming the agent's reasoning as it goes.

    Streamed rather than returned in one piece because the interesting part is
    not the verdict — it is watching the agent narrow three hundred measured
    contradictions down to the few worth an editor's attention, and say why.
    A console that only showed the final list would hide the actual work.
    """
    from sse_starlette.sse import EventSourceResponse

    from app.prompts import ANALYSIS_TASK

    task = ANALYSIS_TASK.format(
        edit_version=request.edit_version,
        scene_id=request.scene_id,
        production_id=PRODUCTION_ID,
        latitude=SCENE_LATITUDE,
        longitude=SCENE_LONGITUDE,
    )

    async def events():
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        runner: InMemoryRunner = app.state.runner or InMemoryRunner(
            agent=app.state.agent, app_name=APP_NAME
        )
        app.state.runner = runner

        user_id = f"analyze-{uuid.uuid4().hex[:8]}"
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=user_id
        )
        started = time.perf_counter()

        yield {
            "event": "started",
            "data": json.dumps(
                {"edit_version": request.edit_version, "scene_id": request.scene_id}
            ),
        }

        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=task)]),
            ):
                for call in event.get_function_calls() or []:
                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {
                                "name": call.name,
                                "args": {
                                    key: str(value)[:2000]
                                    for key, value in (call.args or {}).items()
                                },
                                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                            }
                        ),
                    }
                for response in event.get_function_responses() or []:
                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "name": response.name,
                                "result": str(response.response)[:4000],
                                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                            }
                        ),
                    }
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            yield {
                                "event": "reasoning",
                                "data": json.dumps({"text": part.text}),
                            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("analysis failed")
            yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
            return

        yield {
            "event": "done",
            "data": json.dumps({"elapsed_ms": int((time.perf_counter() - started) * 1000)}),
        }

    return EventSourceResponse(events())


@app.get("/api/findings")
async def findings(edit_version: str = "v14", scene_id: str = "sc14") -> dict[str, Any]:
    """What the agent recorded, for the review queue.

    Read through the same MCP tool the agent uses, so there is exactly one path
    to ClickHouse in this service and no second one to keep honest.
    """
    toolset = app.state.clickhouse_toolset
    tools = {tool.name: tool for tool in await toolset.get_tools()}
    query_tool = tools.get("run_query")
    if query_tool is None:
        raise HTTPException(status_code=503, detail="run_query unavailable over MCP")

    sql = (
        "SELECT finding_id, created_at, finding_type, severity, take_a, take_b, "
        "entity, attribute, observed_delta, computed_expectation, gemini_verdict, "
        "recommendation, visible_in_cut, human_reviewed "
        f"FROM cinemeridian.continuity_findings "
        f"WHERE edit_version = '{edit_version}' AND scene_id = '{scene_id}' "
        "ORDER BY severity DESC, created_at DESC FORMAT JSONEachRow"
    )
    result = await query_tool.run_async(args={"query": sql}, tool_context=None)
    return {"edit_version": edit_version, "scene_id": scene_id, "result": result}


@app.exception_handler(ConfigError)
async def config_error_handler(_request, exc: ConfigError):
    raise HTTPException(status_code=500, detail=str(exc))
