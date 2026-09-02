"""FastAPI surface for CineMeridian.

Small on purpose. The interesting behaviour lives in the agent; this module
exists to expose it over HTTP and - just as importantly - to let the MCP path
be verified *inside the deployed container*, which is where it actually has to
work. A stdio subprocess that runs on a laptop and dies in Cloud Run is the
standard way to fail this track, and `/api/health/mcp` is how we find out
before a judge does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from starlette.datastructures import UploadFile as StarletteUploadFile
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import AGENT_NAME, build_agent, build_clickhouse_toolset
from app.settings import ConfigError, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cinemeridian")

APP_NAME = "cinemeridian"

#: The scene the demo analyses. One production, one location - hard-coded here
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

    # A second connection to the same MCP server, as the user that may write a
    # visitor's project and nothing else. Built lazily: most sessions never
    # bring their own footage, and a subprocess nobody uses is ten seconds of
    # startup spent on nothing.
    app.state.ingest_toolset = None

    try:
        yield
    finally:
        if app.state.ingest_toolset is not None:
            await app.state.ingest_toolset.close()
        await toolset.close()


app = FastAPI(
    title="CineMeridian",
    description="Continuity intelligence for the shoot and the cut.",
    lifespan=lifespan,
)

# The console is a separate Cloud Run service, so the browser calls this one
# cross-origin. Open, because everything served here is a synthetic demo and a
# judge has to reach it without logging in to anything.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
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

    # A visitor's own project lives in the same tables as the demo scene, so
    # the agent has to be told which production it is looking at. Left at the
    # demo's values these mean exactly what they always did.
    production_id: str = Field(default=PRODUCTION_ID, max_length=48)
    latitude: float = Field(default=SCENE_LATITUDE, ge=-90, le=90)
    longitude: float = Field(default=SCENE_LONGITUDE, ge=-180, le=180)


async def _candidates_for(request: Any) -> str:
    """Work out every contradiction in a project before the agent starts.

    One query, a third of a second, the same answer every time. The agent used
    to find these itself over sixteen round trips, which is six minutes of
    somebody waiting for arithmetic a database does instantly.

    A failure here is not fatal. The agent is told the table could not be built
    and falls back to looking for candidates itself, which is slow and still
    correct; refusing to run at all because a convenience failed would be worse.
    """
    from app.tools import agent_tools
    from app.tools.candidates import as_table, candidate_query, count_of

    sql = candidate_query(
        edit_version=request.edit_version,
        scene_id=request.scene_id,
        production_id=request.production_id,
    )
    try:
        # The first query on a fresh connection can pass mcp-clickhouse's thirty
        # second limit, and this is often the first.
        await agent_tools._run_via_mcp("SELECT 1")
        result = await agent_tools._run_via_mcp(sql)
    except Exception:  # noqa: BLE001
        logger.exception("could not compute candidates")
        return "(the candidate query failed; find the candidates yourself)"

    logger.info("computed %s candidates for %s", count_of(result), request.edit_version)
    return as_table(result)


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    """Review an edit version, streaming the agent's reasoning as it goes.

    Streamed rather than returned in one piece because the interesting part is
    not the verdict - it is watching the agent narrow three hundred measured
    contradictions down to the few worth an editor's attention, and say why.
    A console that only showed the final list would hide the actual work.
    """
    from sse_starlette.sse import EventSourceResponse

    from app.prompts import ANALYSIS_TASK, PROJECT_TASK

    # A visitor's own project gets its candidates computed for it. The demo
    # scene does not, deliberately: it is thirty takes with a scored answer key,
    # and changing how the agent approaches it would invalidate a measured
    # result. That switch happens once the same score has been shown to hold.
    if request.production_id == PRODUCTION_ID:
        task = ANALYSIS_TASK.format(
            edit_version=request.edit_version,
            scene_id=request.scene_id,
            production_id=request.production_id,
            latitude=request.latitude,
            longitude=request.longitude,
        )
    else:
        task = PROJECT_TASK.format(
            edit_version=request.edit_version,
            scene_id=request.scene_id,
            production_id=request.production_id,
            latitude=request.latitude,
            longitude=request.longitude,
            candidates=await _candidates_for(request),
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
                                **_summarise_tool_result(response.name, response.response),
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

    # No FORMAT clause: mcp-clickhouse appends `FORMAT Native` of its own and
    # ClickHouse rejects a statement carrying two. The tool hands back columns
    # and rows already, so asking for a format here buys nothing anyway.
    sql = (
        "SELECT finding_id, toString(created_at) AS created_at, finding_type, "
        "toString(severity) AS severity, take_a, take_b, entity, attribute, "
        "observed_delta, computed_expectation, gemini_verdict, recommendation, "
        "visible_in_cut, human_reviewed "
        "FROM cinemeridian.continuity_findings "
        f"WHERE edit_version = '{edit_version}' AND scene_id = '{scene_id}' "
        "ORDER BY severity DESC, created_at DESC"
    )
    result = await query_tool.run_async(args={"query": sql}, tool_context=None)
    return {"edit_version": edit_version, "scene_id": scene_id, "result": result}


#: Frames rendered per take by scripts/composite_variants.py. The first is the
#: take's head, the last is its tail, and a cut joins one shot's tail to the
#: next shot's head.
FRAMES_PER_TAKE = 8


@app.get("/api/takes")
async def takes(scene_id: str = "sc14", edit_version: str = "v14") -> dict[str, Any]:
    """Every take that was shot, with the frames sampled from each.

    The console shows these as a library rather than as a cut, because a take
    exists whether or not it made the edit. Which ones did make it, and where,
    comes back as `cut_position` - null for the ones that were shot and not
    used, which is most of them, as on any production.
    """
    toolset = app.state.clickhouse_toolset
    tools = {tool.name: tool for tool in await toolset.get_tools()}
    query_tool = tools.get("run_query")
    if query_tool is None:
        raise HTTPException(status_code=503, detail="run_query unavailable over MCP")

    sql = (
        # Alias every column. ClickHouse returns a qualified name for anything
        # ambiguous across the joins, so `take_id` comes back as `t.take_id`
        # and the client sees a field it was not expecting.
        "SELECT t.take_id AS take_id, t.setup_id AS setup_id, "
        "t.take_number AS take_number, t.shoot_day AS shoot_day, "
        "toString(t.started_at) AS started_at, toString(t.ended_at) AS ended_at, "
        "t.camera_heading_deg AS camera_heading_deg, t.lens_mm AS lens_mm, "
        "toString(t.source_kind) AS source_kind, "
        "t.slate_verified AS slate_verified, e.cut_position AS cut_position, "
        "round(eph.sun_azimuth_deg, 2) AS sun_azimuth_deg, "
        "round(eph.sun_elevation_deg, 2) AS sun_elevation_deg, "
        "round(eph.shadow_len_ratio, 2) AS shadow_len_ratio, "
        "eph.daylight_color_temp_k AS daylight_color_temp_k "
        "FROM cinemeridian.takes t "
        "LEFT JOIN cinemeridian.edit_decisions e "
        f"       ON e.take_id = t.take_id AND e.edit_version = '{edit_version}' "
        "LEFT JOIN cinemeridian.ephemeris eph "
        "       ON eph.production_id = t.production_id "
        "      AND eph.ts = toStartOfMinute(toDateTime(t.started_at)) "
        f"WHERE t.scene_id = '{scene_id}' "
        "ORDER BY t.setup_id, t.take_number"
    )
    result = await query_tool.run_async(args={"query": sql}, tool_context=None)
    return {
        "scene_id": scene_id,
        "edit_version": edit_version,
        "frames_per_take": FRAMES_PER_TAKE,
        "bucket": get_settings().gcs_asset_bucket,
        "result": result,
    }


#: A frame arrives already scaled to 1280 on its longest edge, so anything much
#: past this is not a frame from our extractor.
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_FRAMES_PER_INSPECT = 8


def _summarise_tool_result(name: str, response: Any) -> dict[str, Any]:
    """Turn a tool's return value into something a person can read at a glance.

    The console shows one line per action, so what it needs is whether the
    action worked and what came back, not the payload. Two shapes arrive here.
    MCP tools answer with a content envelope carrying an `isError` flag and the
    real answer as JSON inside `structuredContent`; the function tools in
    `agent_tools` answer with a plain dictionary. Both are reduced to the same
    three facts.

    Nothing here raises. A summary that fails is a cosmetic loss, and taking a
    running investigation down over one would not be a trade worth making.
    """
    ok = True
    rows: int | None = None
    detail = ""

    try:
        if isinstance(response, dict):
            if "isError" in response:
                ok = not response.get("isError")

            payload = response.get("structuredContent")
            raw = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(raw, str):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for key in ("rows", "tables", "databases"):
                        if isinstance(parsed.get(key), list):
                            rows = len(parsed[key])
                            break

            # The function tools say what they did in their own words, and
            # those words are better than anything that could be inferred.
            for key in ("error", "note", "recorded", "would_an_audience_notice"):
                if key in response:
                    detail = f"{key}: {response[key]}"
                    break
            if response.get("error"):
                ok = False
    except Exception:  # noqa: BLE001 - a summary is never worth an exception
        pass

    return {"ok": ok, "rows": rows, "detail": detail[:200]}


@app.post("/api/inspect")
async def inspect(
    head: UploadFile = File(...),
    tail: UploadFile = File(...),
    recorded_at: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    head_at_seconds: float = Form(0.0),
    tail_at_seconds: float = Form(0.0),
) -> dict[str, Any]:
    """Read someone else's clip: measure the shadows, recover what the file lacks.

    Two frames come in, the clip's first moment and its last, already extracted
    in the browser - the video itself never leaves the machine it was chosen on.

    What comes back is the part a camera report cannot fill in. The file knows
    when it was recorded and a phone usually knows where it stood, but nothing
    anywhere records which way the camera was pointing, because that is a
    compass bearing somebody would have had to measure on set. The sun supplies
    it: a shadow falls opposite the sun, so with time and place known the
    bearing in frame leaves the camera's heading as the only unknown.

    The same arithmetic checks the timestamp. Shadow length is the cotangent of
    solar elevation, so the time on the file predicts a length; a frame that
    disagrees by more than the vision pass's own error is more likely to have a
    wrong timestamp than wrong physics.
    """
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="latitude or longitude out of range")

    when = _parse_moment(recorded_at, "clip")
    settings = get_settings()
    results = []

    for role, upload, offset in (
        ("head", head, head_at_seconds),
        ("tail", tail, tail_at_seconds),
    ):
        payload = await _read_frame(upload, role)
        observations = await _observe(payload, role, settings)

        # The timestamp on the file marks the clip's start, so the tail frame
        # is that many seconds later. Over a short clip the sun barely moves,
        # but using the same instant for both would be quietly wrong, and this
        # is the one function whose whole job is not being quietly wrong.
        moment = when + timedelta(seconds=offset)
        inferred = _infer(observations, moment, latitude, longitude)

        results.append(
            {
                "role": role,
                "at_seconds": round(offset, 2),
                "moment": moment.strftime("%Y-%m-%d %H:%M:%S"),
                "observations": observations,
                "inferred": _inferred_payload(inferred),
            }
        )

    return {
        "recorded_at": when.strftime("%Y-%m-%d %H:%M:%S"),
        "latitude": latitude,
        "longitude": longitude,
        "model": settings.model,
        "frames": results,
    }


@app.post("/api/compare")
async def compare(
    outgoing: UploadFile = File(...),
    incoming: UploadFile = File(...),
    outgoing_recorded_at: str = Form(...),
    incoming_recorded_at: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    outgoing_at_seconds: float = Form(0.0),
    incoming_at_seconds: float = Form(0.0),
) -> dict[str, Any]:
    """Two clips, cut together. Does the sun agree they are one moment?

    This is the single-clip inspection turned into the question an editor
    actually has. `outgoing` is the last frame of the shot being cut away
    from, `incoming` the first frame of the shot being cut to. On screen those
    two frames are adjacent and an audience reads them as continuous, so the
    light in them has to be continuous too.

    Both clips are assumed to be the same place, which is what a cut inside a
    scene means. Their times come from their own files, so a wrong timestamp on
    either one is exactly the thing this is able to notice.

    Everything expensive here is done on two JPEGs. The videos stay on the
    machine they were chosen on; the browser decodes them and sends two frames.
    """
    from app.tools.prescribe import compare_cut

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="latitude or longitude out of range")

    settings = get_settings()
    sides = []

    for role, upload, stamp, offset in (
        ("outgoing", outgoing, outgoing_recorded_at, outgoing_at_seconds),
        ("incoming", incoming, incoming_recorded_at, incoming_at_seconds),
    ):
        when = _parse_moment(stamp, role) + timedelta(seconds=offset)
        payload = await _read_frame(upload, role)
        observations = await _observe(payload, role, settings)
        sides.append((role, when, observations, _infer(observations, when, latitude, longitude)))

    (_, out_when, out_obs, out_inf), (_, in_when, in_obs, in_inf) = sides
    verdict = compare_cut(
        outgoing=out_inf,
        incoming=in_inf,
        outgoing_at_utc=out_when,
        incoming_at_utc=in_when,
        latitude=latitude,
        longitude=longitude,
    )

    return {
        "latitude": latitude,
        "longitude": longitude,
        "model": settings.model,
        "verdict": {
            "verdict": verdict.verdict,
            "headline": verdict.headline,
            "detail": verdict.detail,
            "minutes_apart": verdict.minutes_apart,
            "sun_elevation_change_deg": verdict.sun_elevation_change_deg,
            "sun_azimuth_change_deg": verdict.sun_azimuth_change_deg,
            "expected_length_ratio": verdict.expected_length_ratio,
            "observed_length_ratio": verdict.observed_length_ratio,
            "ratio_agreement": verdict.ratio_agreement,
            "camera_heading_change_deg": verdict.camera_heading_change_deg,
            "detectable_from_minutes": verdict.detectable_from_minutes,
        },
        "reads_expected": READS_PER_FRAME,
        "frames": [
            {
                "role": role,
                "moment": when.strftime("%Y-%m-%d %H:%M:%S"),
                "reads": _reads_in(observations),
                "observations": observations,
                "inferred": _inferred_payload(inferred),
            }
            for role, when, observations, inferred in sides
        ],
    }


def _parse_moment(stamp: str, role: str) -> datetime:
    """Read an ISO timestamp, defaulting a missing zone to UTC."""
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"unreadable {role} timestamp: {stamp!r}"
        ) from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


async def _read_frame(upload: UploadFile, role: str) -> bytes:
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=400, detail=f"{role} frame is empty")
    if len(payload) > MAX_FRAME_BYTES:
        raise HTTPException(status_code=413, detail=f"{role} frame is too large")
    return payload


#: How many times each frame is read before its measurements are believed.
#:
#: One reading is not enough, and that is measured rather than assumed. Asked
#: five times about one unchanged frame, the model answered between 1.2 and 2.6
#: for the same shadow, and on a pair sitting near the verdict's tolerance that
#: spread flipped the answer three times out of five. The median of three is
#: stable across repeats on the same evidence, which is the least a person can
#: ask of a tool that is telling them their timestamps are wrong.
#:
#: The three reads go out together, so this costs latency once rather than
#: three times.
READS_PER_FRAME = 3

#: How long to wait before each retry of a read that failed. Short and rising,
#: because the limit being hit is a burst limit: a couple of seconds usually
#: clears it, and a flat twenty second pause turned a slow request into a five
#: hundred second one that the browser gave up on.
RETRY_PAUSES_S = (2.0, 5.0)

#: How long a single frame may spend being measured before the answer is given
#: on whatever readings came back. A person waiting on a page needs a bounded
#: wait more than a perfect one, and a short reading is now reported as short
#: rather than passed off as whole.
FRAME_DEADLINE_S = 45.0

#: How long any single read may take. Without this the frame deadline is only
#: checked between attempts, so one call that hangs carries the whole request
#: past three minutes and the browser resets the connection. Which is what
#: happened.
READ_TIMEOUT_S = 40.0

#: A small spread on the first reads, so three requests do not land in the same
#: instant.
#:
#: Deliberately small. Four seconds was tried against the deployed service on
#: the theory that this is a burst limit, and it changed nothing except adding
#: forty seconds to every request: the readings came back short either way. The
#: refusals are Vertex reporting exhausted shared capacity rather than a rate
#: this side can smooth out, so the answer is the backoff below and reporting a
#: short reading as short, not spacing.
READ_STAGGER_S = 0.6


async def _observe(
    payload: bytes, role: str, settings: Any, reads: int = READS_PER_FRAME
) -> list[dict[str, Any]]:
    """Measure one frame several times and keep the middle answer.

    A vision call can also fail for reasons that have nothing to do with the
    clip somebody just handed us. Those must not reach the browser as a bare
    500, which would tell a person their footage was rejected when it was not.
    """
    from app.tools.vision import observe_frame

    async def read_once(delay: float = 0.0) -> list[dict[str, Any]]:
        if delay:
            await asyncio.sleep(delay)
        return await asyncio.wait_for(
            asyncio.to_thread(
                observe_frame, payload, mime_type="image/jpeg", settings=settings
            ),
            timeout=READ_TIMEOUT_S,
        )

    started = time.monotonic()

    # Fired together but not in the same instant, and allowed to fail apart.
    # Losing a whole frame because one of three came back 429 would throw away
    # two good measurements.
    attempts = await asyncio.gather(
        *(read_once(index * READ_STAGGER_S) for index in range(reads)),
        return_exceptions=True,
    )
    readings = [r for r in attempts if not isinstance(r, BaseException)]

    # A shortfall is usually a burst limit rather than a bad frame, and carrying
    # on with what came back is not good enough: in production a frame that got
    # one reading of three returned a single noisy measurement that flipped the
    # verdict. So the missing reads are asked for again, backing off, until they
    # are filled or the frame runs out of time.
    for pause in RETRY_PAUSES_S:
        if len(readings) >= reads:
            break
        if time.monotonic() - started > FRAME_DEADLINE_S:
            break
        try:
            readings.append(await read_once(pause))
        except Exception as error:  # noqa: BLE001
            logger.warning("a retried read of the %s frame failed: %s", role, error)

    if len(readings) < reads:
        logger.warning(
            "the %s frame was measured %s times of %s", role, len(readings), reads
        )

    if not readings:
        logger.error("every read of the %s frame failed", role)
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not read the {role} frame: the vision service returned an "
                f"error on every attempt. This is on our side, not your clip."
            ),
        )

    return _median_of(readings)


def _median_of(readings: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Collapse repeated readings of one frame into one set of measurements.

    Keyed on entity and attribute, because that pair is what the rest of the
    system addresses a measurement by. A row that only some of the readings
    produced is kept, on the medians of whatever did produce it: the model
    noticing a shadow twice out of three times is still the model noticing a
    shadow, and dropping it would trade a noisy measurement for none at all.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []

    for reading in readings:
        for row in reading:
            key = (row.get("entity"), row.get("attribute"))
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(row)

    merged: list[dict[str, Any]] = []
    for key in order:
        rows = grouped[key]
        # The row nearest the middle carries the text fields, so free-form
        # values stay a thing the model actually said rather than a blend.
        middle = dict(_representative(rows))
        for field in ("numeric_value", "confidence", "frame_coverage_pct"):
            values = [
                row[field]
                for row in rows
                if isinstance(row.get(field), (int, float))
            ]
            if values:
                middle[field] = round(statistics.median(values), 4)
        middle["reads"] = len(rows)
        merged.append(middle)

    return merged


def _reads_in(observations: list[dict[str, Any]]) -> int:
    """How many times the frame was actually measured, at its weakest row.

    Reported rather than kept quiet. A row built from one reading and a row
    built from three look identical in the output, and only one of them is a
    measurement, so the count travels with the answer.
    """
    counts = [row.get("reads") for row in observations if isinstance(row.get("reads"), int)]
    return min(counts) if counts else 0


def _representative(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The reading closest to the median of the numbers, or just the first."""
    values = [
        (row["numeric_value"], row)
        for row in rows
        if isinstance(row.get("numeric_value"), (int, float))
    ]
    if not values:
        return rows[0]
    target = statistics.median(value for value, _ in values)
    return min(values, key=lambda pair: abs(pair[0] - target))[1]


def _infer(
    observations: list[dict[str, Any]], when: datetime, latitude: float, longitude: float
) -> Any:
    from app.tools.prescribe import infer_capture

    measured = {(o["entity"], o["attribute"]): o for o in observations}

    def numeric(entity: str, attribute: str) -> float | None:
        row = measured.get((entity, attribute))
        value = row.get("numeric_value") if row else None
        return float(value) if isinstance(value, (int, float)) else None

    return infer_capture(
        captured_at_utc=when,
        latitude=latitude,
        longitude=longitude,
        observed_shadow_direction_deg=numeric("primary_shadow", "direction_deg"),
        observed_shadow_length_ratio=numeric("primary_shadow", "length_ratio"),
    )


def _inferred_payload(inferred: Any) -> dict[str, Any]:
    return {
        "camera_heading_deg": inferred.camera_heading_deg,
        "heading_uncertainty_deg": inferred.heading_uncertainty_deg,
        "sun_azimuth_deg": inferred.sun_azimuth_deg,
        "sun_elevation_deg": inferred.sun_elevation_deg,
        "expected_shadow_length_ratio": inferred.expected_shadow_length_ratio,
        "observed_shadow_length_ratio": inferred.observed_shadow_length_ratio,
        "length_agreement": inferred.length_agreement,
        "timestamp_trustworthy": inferred.timestamp_trustworthy,
        "note": inferred.note,
    }


@app.post("/api/ground")
async def ground(
    pair: UploadFile = File(...),
    columns: int = Form(4),
    rows: int = Form(3),
    reads: int = Form(0),
) -> dict[str, Any]:
    """What is on the ground in one shot and not the other.

    The second signal, and deliberately not part of `/api/compare`. The sun
    answers when a shot was filmed and will not be argued with; this answers
    what is lying on the sand, and is a judgement. The two are reported side by
    side and never blended, so a soft answer here cannot dilute a hard one there.

    What arrives is a single image the browser has already assembled: both
    frames beside each other under a shared grid. That is the whole trick.
    Asking a model to describe two frames and then differencing the descriptions
    is what produced every wobble measured in this project; asking it to compare
    two pictures it can see at once is a different question, and on a planted
    mark it answered the same way eight times out of eight.
    """
    from app.tools.ground import AGREEMENT, READS, agree, agree_place, parse_reading

    if not 2 <= columns <= 8 or not 2 <= rows <= 8:
        raise HTTPException(status_code=400, detail="grid must be between 2 and 8 each way")

    payload = await _read_frame(pair, "pair")
    settings = get_settings()

    # A project with six takes has five adjacent pairs, and reading each three
    # times is fifteen calls on top of the ingest. The gate question, whether
    # these are the same place at all, is far easier than counting marks on
    # sand and survives a single reading; the caller says which it needs.
    answers = await _read_pair(payload, settings, reads or READS_PER_FRAME)
    if not answers:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not read the pair: the vision service returned an error on "
                "every attempt. This is on our side, not your clips."
            ),
        )

    # The gate. Continuity is a rule about a scene, so two shots in different
    # places are a scene change rather than a fault, and everything downstream
    # of here would be measuring a difference that was intended.
    same_place, place_note, place_votes = agree_place(answers)
    readings = (
        [parse_reading(text, columns, rows) for text in answers] if same_place else []
    )

    needed = 1 if (reads or READS_PER_FRAME) < 2 else AGREEMENT
    differences = agree(readings, columns, rows, needed) if same_place else []
    return {
        "grid": {"columns": columns, "rows": rows},
        "reads": len(answers),
        "reads_expected": reads or READS_PER_FRAME,
        "agreement_needed": needed,
        "model": settings.model,
        "same_place": same_place,
        "place_note": place_note,
        "place_votes": place_votes,
        "differences": [
            {
                "cell": item.cell,
                "what": item.what,
                "present_in": item.present_in,
                "seen_in_reads": item.seen_in_reads,
                "box": {
                    "x": item.x,
                    "y": item.y,
                    "width": item.width,
                    "height": item.height,
                },
            }
            for item in differences
        ],
    }


async def _read_pair(payload: bytes, settings: Any, reads: int) -> list[str]:
    """Ask the same question a few times and keep the answers that came back.

    Same shape as the frame reads: sent together, allowed to fail apart, and a
    shortfall left visible rather than hidden. One answer here decides whether
    somebody is told their footage has a continuity error in it, which is not a
    thing to settle on a single opinion.
    """
    from app.tools.ground import PROMPT
    from app.tools.vision import compare_pair

    async def read_once(delay: float) -> str:
        if delay:
            await asyncio.sleep(delay)
        return await asyncio.wait_for(
            asyncio.to_thread(compare_pair, payload, PROMPT, settings),
            timeout=READ_TIMEOUT_S,
        )

    attempts = await asyncio.gather(
        *(read_once(index * READ_STAGGER_S) for index in range(reads)),
        return_exceptions=True,
    )
    readings = [r for r in attempts if not isinstance(r, BaseException)]
    if len(readings) < reads:
        logger.warning("the pair was read %s times of %s", len(readings), reads)
    return readings


#: Where a visitor's frames live when they choose to keep them, and for how
#: long the page promises they will.
PROJECT_FRAME_PREFIX = "projects"
PROJECT_FRAME_TTL_HOURS = 24


@app.post("/api/project")
async def create_project(
    request: Request,
    latitude: float = Form(...),
    longitude: float = Form(...),
    store_frames: bool = Form(False),
) -> dict[str, Any]:
    """Turn a visitor's clips into a production the agent can investigate.

    The demo scene is convincing because there is a database under it, and the
    agent's power is that it can ask questions across the whole of it at once.
    A page that copied the look of that from two vision calls would be a
    pretence. So this writes the real thing: takes, the observations read from
    each clip's first and last frame, an ephemeris computed for the time and
    place the files claim, and the cut order. Then the same agent runs on it,
    through the same MCP server, and finds what it finds.

    Frames arrive as `take_1_head`, `take_1_tail`, `take_2_head` and so on,
    each with a `take_1_recorded_at` and a `take_1_duration`. Numbered rather
    than listed because multipart has no natural way to say "an array of
    objects", and a flat naming scheme is easier to read in a browser's network
    tab than a JSON blob smuggled through a form field.

    What comes back is the identifiers. Analysis is a separate call, because it
    takes minutes and streams.
    """
    from app.tools.project import MAX_TAKES, MIN_TAKES, Project, TakeInput, build_statements

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="latitude or longitude out of range")

    form = await request.form()
    indices = sorted(
        {
            int(match.group(1))
            for key in form
            if (match := re.match(r"take_(\d+)_head$", key))
        }
    )
    if not MIN_TAKES <= len(indices) <= MAX_TAKES:
        raise HTTPException(
            status_code=400,
            detail=f"bring between {MIN_TAKES} and {MAX_TAKES} takes; got {len(indices)}",
        )

    settings = get_settings()
    project = Project.new()
    takes = []

    for position, index in enumerate(indices, start=1):
        head = form.get(f"take_{index}_head")
        tail = form.get(f"take_{index}_tail")
        if not isinstance(head, StarletteUploadFile) or not isinstance(
            tail, StarletteUploadFile
        ):
            raise HTTPException(
                status_code=400, detail=f"take {index} is missing a head or tail frame"
            )

        recorded_at = _parse_moment(str(form.get(f"take_{index}_recorded_at", "")), f"take {index}")
        try:
            duration = float(form.get(f"take_{index}_duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0

        # One reading per frame here, not the three the pairwise check uses.
        # That is what the demo scene's own ingest does, and a project of six
        # takes would otherwise be thirty six vision calls against a quota that
        # has already refused six.
        head_bytes = await _read_frame(head, f"take {index} head")
        tail_bytes = await _read_frame(tail, f"take {index} tail")

        # A frame the vision service refused is a gap in the evidence, not a
        # reason to throw away the project. Twelve sequential calls for six
        # takes means one rate limit anywhere would otherwise lose the lot, and
        # a take with no measurements is still a take: it has a time, a place
        # and a position in the cut, and the agent can say what it could not
        # see rather than never being asked.
        head_observations = await _observe_or_none(
            head_bytes, f"take {index} head", settings
        )
        tail_observations = await _observe_or_none(
            tail_bytes, f"take {index} tail", settings
        )

        # Only if asked. Without a stored frame the agent has nothing to point
        # a visual adjudication at, and it says so in its own report; with one,
        # it can look. That is the real trade behind the checkbox, and it is a
        # larger one than "would you like to keep this".
        head_uri = tail_uri = ""
        if store_frames:
            head_uri = await _store_frame(head_bytes, project, position, "head", settings)
            tail_uri = await _store_frame(tail_bytes, project, position, "tail", settings)

        takes.append(
            TakeInput(
                index=position,
                recorded_at=recorded_at,
                duration_seconds=duration,
                head_observations=head_observations,
                tail_observations=tail_observations,
                head_uri=head_uri,
                tail_uri=tail_uri,
            )
        )

    statements = build_statements(project, takes, latitude, longitude)
    written = await _write_project(statements)

    return {
        "production_id": project.production_id,
        "scene_id": project.scene_id,
        "edit_version": project.edit_version,
        "takes": [
            {
                "take_id": f"{project.scene_id}_t{take.index:02d}",
                "cut_position": take.index,
                "recorded_at": take.recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
                "observations": len(take.head_observations) + len(take.tail_observations),
            }
            for take in takes
        ],
        "rows_written": written,
        "frames_stored": store_frames,
        "latitude": latitude,
        "longitude": longitude,
        "model": settings.model,
    }


async def _observe_or_none(
    payload: bytes, role: str, settings: Any
) -> list[dict[str, Any]]:
    """Measure a frame, or report nothing and carry on."""
    try:
        return await _observe(payload, role, settings, reads=1)
    except HTTPException as refused:
        logger.warning("no measurements for the %s: %s", role, refused.detail)
        return []


async def _store_frame(
    payload: bytes, project: Any, position: int, role: str, settings: Any
) -> str:
    """Keep one frame, so the agent can look at it rather than only read numbers.

    A failure here is not fatal. The project is still worth analysing without
    pictures, and losing the whole thing because a bucket write failed would
    trade something large for something small.
    """
    from google.cloud import storage

    name = f"{PROJECT_FRAME_PREFIX}/{project.production_id}/t{position:02d}_{role}.jpg"
    try:
        def upload() -> None:
            client = storage.Client(project=settings.project_id)
            blob = client.bucket(settings.gcs_asset_bucket).blob(name)
            blob.upload_from_string(payload, content_type="image/jpeg")

        await asyncio.to_thread(upload)
    except Exception:  # noqa: BLE001
        logger.exception("could not store the %s frame of take %s", role, position)
        return ""
    return f"gs://{settings.gcs_asset_bucket}/{name}"


async def _ingest_tool() -> Any:
    """The `run_query` tool on the writing connection, started on first use."""
    settings = get_settings()
    if not settings.can_ingest_projects:
        raise HTTPException(
            status_code=503,
            detail=(
                "This deployment has no ingest user, so it cannot accept projects. "
                "Run scripts/create_ingest_user.py and redeploy."
            ),
        )

    if app.state.ingest_toolset is None:
        from app.agent import build_clickhouse_toolset

        app.state.ingest_toolset = build_clickhouse_toolset(settings, for_ingest=True)

    tools = {tool.name: tool for tool in await app.state.ingest_toolset.get_tools()}
    tool = tools.get("run_query")
    if tool is None:
        raise HTTPException(status_code=502, detail="mcp-clickhouse exposes no run_query")
    return tool


async def _write_project(statements: list[str]) -> int:
    """Run the inserts through MCP, which is the only way into the database.

    A direct ClickHouse client here would be simpler and would also make the
    claim this project rests on untrue: that every database access, the agent's
    and the application's alike, goes through the same MCP server.

    On the writing connection, whose user holds INSERT on four tables and can
    touch nothing else. The agent could not run these statements if it tried,
    which is the point.
    """
    tool = await _ingest_tool()

    async def run(sql: str) -> str:
        return str(await tool.run_async(args={"query": sql}, tool_context=None))

    # The first query on a fresh connection is slow, and mcp-clickhouse gives up
    # on any query at thirty seconds. Measured: about six seconds locally and
    # past thirty against ClickHouse Cloud from Cloud Run, which killed the
    # first project every time on its first INSERT. So the connection is opened
    # with something trivial and the real work starts warm.
    try:
        await run("SELECT 1")
    except Exception:  # noqa: BLE001
        logger.warning("the warming query failed; carrying on to the writes anyway")

    written = 0
    for statement in statements:
        if not statement:
            continue
        result = ""
        for attempt in range(2):
            try:
                result = await run(statement)
            except Exception as error:  # noqa: BLE001
                logger.exception("failed to write a project statement")
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not write the project to ClickHouse: {error}",
                ) from error
            # A timeout is worth one more go; a refusal is not, because the
            # second answer would be the same refusal.
            if "isError': True" in result and "timed out" in result and attempt == 0:
                logger.warning("a project write timed out; trying once more")
                continue
            break

        if "isError': True" in result:
            logger.error("clickhouse refused a project insert: %s", result[:300])
            raise HTTPException(
                status_code=502, detail=f"ClickHouse refused a write: {result[:200]}"
            )
        written += 1
    return written


@app.get("/api/frame")
async def frame(uri: str):
    """Stream one frame out of GCS.

    The console needs to show evidence pairs to a judge who is not logged in to
    anything, and the alternative - making the bucket world-readable - hands
    out every frame in the production to anyone who guesses a path. Proxying
    keeps the bucket private and still lets the page render.

    Only gs:// URIs inside the project's own asset bucket are served. Without
    that check this endpoint would happily fetch any object the runtime service
    account can read.
    """
    from fastapi.responses import Response
    from google.cloud import storage

    settings = get_settings()
    prefix = f"gs://{settings.gcs_asset_bucket}/"
    if not uri.startswith(prefix):
        raise HTTPException(status_code=400, detail="uri must be inside the asset bucket")

    blob_name = uri[len(prefix) :]
    try:
        client = storage.Client(project=settings.project_id)
        blob = client.bucket(settings.gcs_asset_bucket).blob(blob_name)
        data = await asyncio.to_thread(blob.download_as_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"frame not available: {exc}") from exc

    return Response(
        content=data,
        media_type="image/jpeg",
        # The frames are immutable once rendered, so let the browser keep them.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.exception_handler(ConfigError)
async def config_error_handler(_request, exc: ConfigError):
    raise HTTPException(status_code=500, detail=str(exc))
