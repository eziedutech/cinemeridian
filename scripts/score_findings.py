#!/usr/bin/env python3
"""Score what the agent found against the errors that were actually planted.

The claim "the agent found four of five" is only worth making if it can be
checked, so this compares `continuity_findings` against
`assets/ground_truth.json` and prints both halves of the ledger: what was
caught, and what was missed.

It also prints the findings that match nothing planted. Those are not
automatically wrong — the scene contains real physical drift that nobody
planted, and a shoot split across twelve days produces genuine cross-take
problems. But they are the number to watch: a tool that reports everything is
one an editorial department turns off in a week.

Read the key only from here. Nothing upstream — no prompt, no table, no file
path — is allowed to see it, or the score means nothing.

Usage:
    python scripts/score_findings.py --edit-version v14
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_schema import execute, load_credentials  # noqa: E402

GROUND_TRUTH = ROOT / "assets" / "ground_truth.json"


def planted_matches(error: dict, finding: dict) -> bool:
    """Does this finding correspond to that planted error?

    Matched on type plus the subject, not on wording. Requiring the agent to
    phrase a finding a particular way would be scoring the prose rather than
    the detection.
    """
    if error["type"] != finding["finding_type"]:
        return False

    subject = error.get("take_id") or error.get("shot_id") or ""
    haystack = " ".join(
        [
            finding.get("take_a", ""),
            finding.get("take_b", ""),
            finding.get("observed_delta", ""),
            finding.get("recommendation", ""),
        ]
    )
    if subject and subject in haystack:
        return True

    # Errors that live in an ordering rather than in one take: the type plus
    # the entity is enough to identify them.
    entity = error.get("entity")
    if entity and entity == finding.get("entity"):
        return True

    # E3 names two setups rather than takes.
    detail = error.get("detail", "")
    for setup in ("su01", "su02", "su03", "su04", "su05", "su06", "su07", "su08"):
        if setup in detail and setup in haystack:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edit-version", default="v14")
    parser.add_argument("--scene-id", default="sc14")
    args = parser.parse_args()

    key = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    planted = key["planted_errors"]

    env = load_credentials()
    rows = execute(
        env,
        "SELECT finding_id, finding_type, severity, take_a, take_b, entity, attribute, "
        "observed_delta, recommendation, visible_in_cut "
        f"FROM cinemeridian.continuity_findings "
        f"WHERE edit_version = '{args.edit_version}' AND scene_id = '{args.scene_id}' "
        "FORMAT JSONEachRow",
    ).strip()
    findings = [json.loads(line) for line in rows.splitlines() if line.strip()]

    print(f"Scene {args.scene_id}, edit {args.edit_version}")
    print(f"  {len(planted)} errors planted, {len(findings)} findings recorded\n")

    matched: set[str] = set()
    caught = 0
    print("Planted errors")
    for error in planted:
        hits = [f for f in findings if planted_matches(error, f)]
        for hit in hits:
            matched.add(hit["finding_id"])
        status = "FOUND  " if hits else "MISSED "
        caught += 1 if hits else 0
        print(f"  {status} {error['id']}  {error['type']:<22} {error['detail'][:70]}")
        for hit in hits:
            print(f"           -> [{hit['severity']}] {hit['observed_delta'][:80]}")

    extra = [f for f in findings if f["finding_id"] not in matched]
    if extra:
        print(f"\nFindings that match nothing planted ({len(extra)})")
        print("  Not necessarily wrong - the scene has real drift nobody planted.")
        for finding in extra:
            visible = "visible" if finding.get("visible_in_cut") else "not visible in cut"
            print(
                f"  [{finding['severity']}] {finding['finding_type']:<22} "
                f"{finding.get('take_a','')} {finding.get('entity','')} ({visible})"
            )

    print(f"\n{'=' * 62}")
    print(f"  {caught} of {len(planted)} planted errors found")
    if extra:
        print(f"  {len(extra)} additional findings, for a human to judge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
