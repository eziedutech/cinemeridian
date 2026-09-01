"""The function tools the agent is given, alongside the ClickHouse MCP server.

Each one is deliberately narrow. The agent reads data through MCP, decides what
is worth pursuing, and then calls exactly one of these to do a thing it cannot
do by reasoning: compute physics, look at two frames, or write a finding down.

The signatures are flat and JSON-shaped because that is what survives the round
trip through a model. Every argument the agent has to supply is one it read
from a query, which keeps the data flow visible in the trace instead of hidden
inside a helper.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.settings import get_settings
from app.tools import audit, prescribe, vision

logger = logging.getLogger(__name__)

UTC = timezone.utc


def _parse(ts: str) -> datetime:
    """Accept the timestamp shapes ClickHouse hands back."""
    text = ts.strip().replace("T", " ")
    for suffix in (".000", ".0"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def compute_light_rig(
    reference_take_id: str,
    reference_time_utc: str,
    latitude: float,
    longitude: float,
    camera_heading_deg: float,
    cloud_cover_pct: float = 0.0,
) -> dict:
    """Compute the key light a CG shot needs to match a practical take.

    Use this when a CG or LED-volume shot has to cut against footage shot on
    location. Pass the practical take's own capture time and position, read
    from the takes table. Returns key light azimuth, elevation, colour
    temperature and softness, plus the shadow those values will produce.

    Args:
        reference_take_id: the practical take the CG shot must match.
        reference_time_utc: that take's started_at, as 'YYYY-MM-DD HH:MM:SS'.
        latitude: the take's latitude.
        longitude: the take's longitude, east positive.
        camera_heading_deg: the take's camera_heading_deg.
        cloud_cover_pct: cloud cover at the time, 0 to 100. Affects softness
            only - cloud spreads the source without moving it.
    """
    rig = prescribe.prescribe_light_rig(
        reference_take_id=reference_take_id,
        reference_time_utc=_parse(reference_time_utc),
        latitude=latitude,
        longitude=longitude,
        camera_heading_deg=camera_heading_deg,
        cloud_cover_pct=cloud_cover_pct,
    )
    logger.info("prescribed rig for %s", reference_take_id)
    return {
        "key_light_azimuth_deg": rig.key_light_azimuth_deg,
        "key_light_elevation_deg": rig.key_light_elevation_deg,
        "key_light_temp_k": rig.key_light_temp_k,
        "key_light_softness": rig.key_light_softness,
        "expected_shadow_direction_deg": rig.shadow_direction_deg,
        "expected_shadow_length_ratio": rig.shadow_length_ratio,
        "reference_take_id": rig.reference_take_id,
        "reference_time": rig.reference_time,
        "note": rig.note,
    }


def compute_render_error(
    submitted_azimuth_deg: float,
    submitted_elevation_deg: float,
    submitted_temp_k: int,
    prescribed_azimuth_deg: float,
    prescribed_elevation_deg: float,
    prescribed_temp_k: int,
) -> dict:
    """How far a submitted render config sits from what the physics requires.

    Handles the wrap at 360 degrees, which is the mistake to avoid doing by
    hand: 359 and 1 are two degrees apart, not three hundred and fifty-eight.
    """
    rig = prescribe.LightRig(
        key_light_azimuth_deg=prescribed_azimuth_deg,
        key_light_elevation_deg=prescribed_elevation_deg,
        key_light_temp_k=prescribed_temp_k,
        key_light_softness=0.0,
        shadow_direction_deg=0.0,
        shadow_length_ratio=0.0,
        reference_take_id="",
        reference_time="",
        note="",
    )
    return prescribe.rig_error(
        submitted_azimuth_deg=submitted_azimuth_deg,
        submitted_elevation_deg=submitted_elevation_deg,
        submitted_temp_k=submitted_temp_k,
        prescribed=rig,
    )


def find_pickup_windows(
    reference_time_utc: str,
    latitude: float,
    longitude: float,
    search_from_utc: str,
    search_to_utc: str,
    azimuth_tolerance_deg: float = 0.5,
    elevation_tolerance_deg: float = 0.5,
) -> dict:
    """Find when the sun will return to a take's geometry, for a pickup.

    Use this after concluding two takes cannot be cut together, to answer the
    question that actually helps: when could the shot be taken again.

    Be prepared for an empty answer and report it plainly if it comes. Sun
    geometry repeats when declination repeats, and declination is symmetric
    about a solstice - a shoot near an equinox may have no matching window for
    months, and that is a real production fact rather than a failed query.

    Args:
        reference_time_utc: the take to match, as 'YYYY-MM-DD HH:MM:SS'.
        latitude: shooting latitude.
        longitude: shooting longitude, east positive.
        search_from_utc: start of the search range.
        search_to_utc: end of the search range.
        azimuth_tolerance_deg: how close the sun's bearing must come.
        elevation_tolerance_deg: how close its height must come.
    """
    windows = prescribe.find_match_windows(
        reference_time_utc=_parse(reference_time_utc),
        latitude=latitude,
        longitude=longitude,
        search_from=_parse(search_from_utc),
        search_to=_parse(search_to_utc),
        azimuth_tolerance_deg=azimuth_tolerance_deg,
        elevation_tolerance_deg=elevation_tolerance_deg,
    )
    if not windows:
        return {
            "windows": [],
            "note": (
                "No window in the range searched. Sun geometry repeats when "
                "declination repeats, so try the mirror of this date on the "
                "other side of the nearest solstice, or report that there is "
                "no pickup window this season."
            ),
        }
    return {
        "windows": [
            {
                "opens_at": w.opens_at,
                "closes_at": w.closes_at,
                "minutes": w.minutes,
                "worst_azimuth_error_deg": w.max_azimuth_error_deg,
                "worst_elevation_error_deg": w.max_elevation_error_deg,
            }
            for w in windows
        ],
        "note": f"{len(windows)} window(s). These are minutes per day, not hours.",
    }


def adjudicate_cut(
    frame_a_uri: str,
    frame_b_uri: str,
    entity: str,
    attribute: str,
    observed_delta: str,
    computed_expectation: str,
) -> dict:
    """Look at two frames that are cut together and judge whether it reads.

    Expensive. Use it only on contradictions that survived ranking - something
    with real frame coverage, in focus, on a cut that actually exists in this
    edit version. Answers the one question the database cannot: would an
    audience notice at speed.

    Args:
        frame_a_uri: gs:// URI of the outgoing frame, from frame_observations.
        frame_b_uri: gs:// URI of the incoming frame.
        entity: what disagrees, e.g. 'primary_shadow'.
        attribute: which measurement, e.g. 'length_ratio'.
        observed_delta: what the data says the difference is.
        computed_expectation: what the physics says should be true.
    """
    # A bad URI must cost this one adjudication, not the whole investigation.
    # Vertex answers a malformed or missing file with a bare 400
    # INVALID_ARGUMENT, which says nothing useful, so check first and hand the
    # agent something it can act on.
    for label, uri in (("frame_a_uri", frame_a_uri), ("frame_b_uri", frame_b_uri)):
        if not uri or not uri.startswith("gs://"):
            return {
                "error": (
                    f"{label} is not a gs:// URI ({uri!r}). Use the frame_uri "
                    f"column from frame_observations verbatim."
                )
            }

    try:
        verdict = vision.adjudicate_pair_by_uri(
            frame_a_uri,
            frame_b_uri,
            entity=entity,
            attribute=attribute,
            delta=observed_delta,
            expectation=computed_expectation,
            settings=get_settings(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("adjudication failed")
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "hint": (
                "Check both frames exist in GCS. You can continue without this "
                "adjudication - record the finding from the data alone and say "
                "it was not visually confirmed."
            ),
        }

    logger.info(
        "adjudicated %s/%s: notice=%s", entity, attribute,
        verdict.get("would_an_audience_notice"),
    )
    return verdict


#: The live mcp-clickhouse toolset, handed over when the agent is built.
#: record_finding writes through it, so a finding still travels the MCP path.
#: The requirement is that ClickHouse is reached through the MCP server, not
#: that the model has to be the one to type the statement - and asking the
#: model to chain "compose SQL" into "run SQL" produced exactly the failure you
#: would expect: it tried to nest one tool call inside another and emitted a
#: malformed call.
_clickhouse_toolset = None


def set_clickhouse_toolset(toolset) -> None:
    """Give the write-back tool the MCP session the agent is already using."""
    global _clickhouse_toolset
    _clickhouse_toolset = toolset


async def _run_via_mcp(sql: str) -> str:
    if _clickhouse_toolset is None:
        raise RuntimeError(
            "No ClickHouse toolset registered; call set_clickhouse_toolset() "
            "when building the agent."
        )
    tools = {tool.name: tool for tool in await _clickhouse_toolset.get_tools()}
    run_query = tools.get("run_query")
    if run_query is None:
        raise RuntimeError("run_query is not exposed by mcp-clickhouse")
    return str(await run_query.run_async(args={"query": sql}, tool_context=None))


async def record_finding(
    edit_version: str,
    scene_id: str,
    finding_type: str,
    severity: str,
    take_a: str,
    take_b: str = "",
    entity: str = "",
    attribute: str = "",
    observed_delta: str = "",
    computed_expectation: str = "",
    gemini_verdict: str = "",
    recommendation: str = "",
    visible_in_cut: bool = True,
) -> dict:
    """Compose the SQL that records one finding for human review.

    This returns a statement; it does not execute it. Run the returned SQL with
    the ClickHouse run_query tool. Two steps on purpose - the statement appears
    in your trace, which is what makes the audit trail auditable.

    Record only what you would defend to an editor. A finding that turns out to
    be invisible costs more trust than the one it was meant to catch.

    Args:
        edit_version: the cut this applies to, e.g. 'v14'.
        scene_id: the scene, e.g. 'sc14'.
        finding_type: one of monotonic_violation, cross_take_drift,
            physics_mismatch, asset_version_drift, volume_plate_drift,
            slate_error.
        severity: info, low, medium or high.
        take_a: the take the finding is about.
        take_b: the take it clashes with, if there is one.
        entity: what disagrees, e.g. 'footprints'.
        attribute: which measurement, e.g. 'count'.
        observed_delta: what the data showed.
        computed_expectation: what the physics said.
        gemini_verdict: the visual adjudication, if one was run.
        recommendation: what a human should do about it.
        visible_in_cut: whether an audience would actually see it.
    """
    sql = audit.record_finding(
        edit_version=edit_version,
        scene_id=scene_id,
        finding_type=finding_type,
        severity=severity,
        take_a=take_a,
        take_b=take_b,
        entity=entity,
        attribute=attribute,
        observed_delta=observed_delta,
        computed_expectation=computed_expectation,
        gemini_verdict=gemini_verdict,
        recommendation=recommendation,
        visible_in_cut=visible_in_cut,
    )
    try:
        result = await _run_via_mcp(sql)
        recorded = "isError': True" not in result and "failed" not in result.lower()
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to record finding")
        return {"recorded": False, "sql": sql, "error": str(exc)}

    logger.info("recorded %s finding on %s", finding_type, take_a)
    return {
        "recorded": recorded,
        "sql": sql,
        "detail": result[:400],
    }
