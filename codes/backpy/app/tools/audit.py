"""Writing findings back, and the shape of the audit trail.

Every finding the agent produces carries its whole chain: what was observed,
what the physics expected, what the visual adjudication concluded, and what to
do about it. A finding without that chain is an assertion, and an assertion is
not reviewable.

`human_reviewed` starts at 0 and only a person moves it. The agent recommends;
it does not act on the edit, and it does not mark its own work as accepted.
That is a deliberate boundary and it is worth being loud about — it is the
difference between a tool an editorial department will let near a cut and one
it will not.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

UTC = timezone.utc

FINDING_TYPES = (
    "monotonic_violation",
    "cross_take_drift",
    "physics_mismatch",
    "asset_version_drift",
    "volume_plate_drift",
    "slate_error",
)

SEVERITIES = ("info", "low", "medium", "high")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    created_at: str
    edit_version: str
    scene_id: str
    finding_type: str
    severity: str
    take_a: str
    take_b: str
    entity: str
    attribute: str
    observed_delta: str
    computed_expectation: str
    gemini_verdict: str
    recommendation: str
    visible_in_cut: int
    human_reviewed: int


def _quote(value: str) -> str:
    """Single-quote a value for a ClickHouse literal.

    The agent composes SQL, so anything reaching a literal has to survive a
    quote in a free-text verdict. The database user this runs as can only
    insert into one table, but that is a second line of defence, not a reason
    to hand it broken SQL.
    """
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_finding(
    *,
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
) -> Finding:
    if finding_type not in FINDING_TYPES:
        raise ValueError(f"unknown finding_type {finding_type!r}; expected one of {FINDING_TYPES}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}; expected one of {SEVERITIES}")

    now = datetime.now(UTC)
    # Deterministic id, so re-running an analysis of the same edit version
    # updates the same row rather than piling up duplicates.
    fingerprint = "/".join(
        [edit_version, scene_id, finding_type, take_a, take_b, entity, attribute]
    )
    return Finding(
        finding_id=hashlib.sha1(fingerprint.encode()).hexdigest()[:16],
        created_at=now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
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
        visible_in_cut=int(visible_in_cut),
        human_reviewed=0,   # only a person moves this
    )


def finding_insert_sql(finding: Finding, database: str = "cinemeridian") -> str:
    """The INSERT the agent runs through the MCP server.

    Returned as SQL rather than executed here, because the agent's only route
    to ClickHouse is the MCP tool. Nothing in this package opens a database
    connection of its own.
    """
    values = asdict(finding)
    columns = ", ".join(values)
    literals = ", ".join(
        str(value) if isinstance(value, int) else _quote(value) for value in values.values()
    )
    return f"INSERT INTO {database}.continuity_findings ({columns}) VALUES ({literals})"


def record_finding(
    *,
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
) -> str:
    """Compose the SQL that records one finding for human review.

    Give the agent this tool and it will hand the resulting statement to the
    MCP `run_query` tool. Two steps rather than one, deliberately: the SQL is
    visible in the agent's trace, which is what makes the audit trail auditable
    rather than merely present.
    """
    finding = build_finding(
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
    return finding_insert_sql(finding)


def summarise(findings: list[dict]) -> str:
    """A short, honest summary of a run, for the timeline panel."""
    if not findings:
        return "No findings. Every contradiction examined was below the threshold to matter."
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    parts = [f"{count} {severity}" for severity, count in sorted(by_severity.items())]
    return f"{len(findings)} findings awaiting human review: " + ", ".join(parts)
