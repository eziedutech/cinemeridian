#!/usr/bin/env python3
"""Create the restricted ClickHouse user that writes visitors' projects.

When somebody brings their own clips, the application has to write a small
production into the same tables the demo scene lives in: takes, the ephemeris
for the window they claim, the observations read from their frames, and a cut
order. Four tables, INSERT only.

That is a different job from the agent's, and it gets a different user. The
agent may read everything and write findings; it must never be able to write a
take or an ephemeris row, because those are the evidence it is reasoning about
and a model that can edit its own evidence is not an auditor. Widening the
agent's grants to save creating a user would have quietly destroyed that.

    SELECT  on cinemeridian.*
    INSERT  on cinemeridian.takes
    INSERT  on cinemeridian.ephemeris
    INSERT  on cinemeridian.frame_observations
    INSERT  on cinemeridian.edit_decisions

No DDL, no writes to continuity_findings, no DELETE. Every statement this user
runs is composed by the application from typed values; none of it is written by
a model.

The generated password is written to credentials/clickhouse.env, which is
gitignored. It is not printed.

Usage:
    python scripts/create_ingest_user.py
    python scripts/create_ingest_user.py --rotate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_schema import execute, load_credentials  # noqa: E402
from create_agent_user import generate_password, upsert_env  # noqa: E402

INGEST_USER = "cinemeridian_ingest"
DATABASE = "cinemeridian"

#: The tables a visitor's project is made of, and the only ones this user may
#: write. `continuity_findings` is deliberately absent: findings are the agent's
#: to write, and nothing else should be able to put one there.
PROJECT_TABLES = (
    "takes",
    "ephemeris",
    "frame_observations",
    "edit_decisions",
    # What has already been read from the clips this project ships, so a
    # visitor can start from it instead of paying for it again. Written by
    # the same connection that writes a project, and keyed by our own file
    # names, so nobody else's footage can land here.
    "sample_clip_readings",
    "sample_pair_readings",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate", action="store_true", help="replace an existing user's password"
    )
    args = parser.parse_args()

    env = load_credentials()
    print(f"ClickHouse {env['CLICKHOUSE_HOST']}")

    existing = execute(
        env, f"SELECT count() FROM system.users WHERE name = '{INGEST_USER}'"
    ).strip()
    if existing != "0" and not args.rotate:
        print(f"  user {INGEST_USER} already exists; pass --rotate to reset its password")
        show_grants(env)
        return 0

    password = generate_password()
    verb = "CREATE OR REPLACE" if existing != "0" else "CREATE"
    execute(env, f"{verb} USER {INGEST_USER} IDENTIFIED WITH sha256_password BY '{password}'")
    print(f"  {'rotated' if existing != '0' else 'created'} user {INGEST_USER}")

    # Read access as well as write: the application reads back what it just
    # wrote to show a visitor their own project, and a user that can write a
    # row it cannot then read is an awkward thing to debug.
    execute(env, f"GRANT SELECT ON {DATABASE}.* TO {INGEST_USER}")
    print(f"  granted SELECT on {DATABASE}.*")
    for table in PROJECT_TABLES:
        execute(env, f"GRANT INSERT ON {DATABASE}.{table} TO {INGEST_USER}")
        print(f"  granted INSERT on {DATABASE}.{table}")

    upsert_env("CLICKHOUSE_INGEST_USER", INGEST_USER)
    upsert_env("CLICKHOUSE_INGEST_PASSWORD", password)
    print("  password written to credentials/clickhouse.env (gitignored, not printed)")

    show_grants(env)
    return 0


def show_grants(env: dict[str, str]) -> None:
    rows = execute(
        env,
        f"SELECT access_type, database, table FROM system.grants "
        f"WHERE user_name = '{INGEST_USER}' ORDER BY access_type, table FORMAT TSV",
    ).strip()
    print("\n  effective grants:")
    for line in rows.splitlines():
        access, database, table = (line.split("\t") + ["", ""])[:3]
        target = f"{database}.*" if table in ("", "\\N") else f"{database}.{table}"
        print(f"    {access:<8} {target}")


if __name__ == "__main__":
    raise SystemExit(main())
