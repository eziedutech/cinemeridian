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
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
    try:
        yield
    finally:
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


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    """Review an edit version, streaming the agent's reasoning as it goes.

    Streamed rather than returned in one piece because the interesting part is
    not the verdict - it is watching the agent narrow three hundred measured
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
        "frames": [
            {
                "role": role,
                "moment": when.strftime("%Y-%m-%d %H:%M:%S"),
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

#: How long to wait before the last attempt, when every read of a frame failed
#: at once. Long enough for a per-minute limit to breathe.
RETRY_PAUSE_S = 20.0


async def _observe(payload: bytes, role: str, settings: Any) -> list[dict[str, Any]]:
    """Measure one frame several times and keep the middle answer.

    A vision call can also fail for reasons that have nothing to do with the
    clip somebody just handed us. Those must not reach the browser as a bare
    500, which would tell a person their footage was rejected when it was not.
    """
    from app.tools.vision import observe_frame

    async def read_once() -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            observe_frame, payload, mime_type="image/jpeg", settings=settings
        )

    # Sent together and allowed to fail apart. Three calls at once is enough to
    # earn a rate limit on a small quota, and losing the whole frame because
    # one of three came back 429 would be throwing away two good measurements.
    # Two readings are still better than one; one is still better than none.
    attempts = await asyncio.gather(
        *(read_once() for _ in range(READS_PER_FRAME)), return_exceptions=True
    )
    readings = [r for r in attempts if not isinstance(r, BaseException)]
    failures = [r for r in attempts if isinstance(r, BaseException)]

    if failures and not readings:
        # Everything failed, which usually means a rate limit rather than a bad
        # frame. One more try, alone and after a pause, before giving up.
        await asyncio.sleep(RETRY_PAUSE_S)
        try:
            readings = [await read_once()]
        except Exception as error:  # noqa: BLE001 - surfaced verbatim below
            logger.exception("vision failed on the %s frame", role)
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Could not read the {role} frame: the vision service returned "
                    f"an error. This is on our side, not your clip. ({error})"
                ),
            ) from error
    elif failures:
        logger.warning(
            "%s of %s reads failed on the %s frame; using the %s that came back",
            len(failures),
            READS_PER_FRAME,
            role,
            len(readings),
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
