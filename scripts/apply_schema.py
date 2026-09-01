#!/usr/bin/env python3
"""Apply a .sql file to ClickHouse Cloud over the HTTPS interface.

**Setup tooling only.** At runtime the agent reaches ClickHouse exclusively
through the mcp-clickhouse MCP server; nothing in codes/backpy/app opens a
database connection of its own. This script exists so the schema can be
created and the simulated data loaded before the agent ever runs.

Stdlib only, so it works before anything is installed.

Usage:
    python scripts/apply_schema.py                     # apply sql/001_schema.sql
    python scripts/apply_schema.py sql/002_seed.sql
    python scripts/apply_schema.py --check             # just show what exists
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = ROOT / "credentials" / "clickhouse.env"

#: A cold ClickHouse Cloud service has to wake before it answers. The first
#: request after an idle period can take the better part of a minute; that is
#: not a failure, and it is worth remembering before a live demo.
WAKE_TIMEOUT_S = 120


def load_credentials() -> dict[str, str]:
    env = {}
    if CREDENTIALS.is_file():
        for line in CREDENTIALS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    for key in ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"):
        env[key] = os.environ.get(key) or env.get(key, "")
    if not env["CLICKHOUSE_HOST"] or env["CLICKHOUSE_HOST"].startswith("xxxx"):
        sys.exit(
            "CLICKHOUSE_HOST is missing or still a placeholder. "
            "Copy .env.example into credentials/clickhouse.env and fill it in."
        )
    if not env["CLICKHOUSE_PASSWORD"]:
        sys.exit(
            "CLICKHOUSE_PASSWORD is empty. "
            "Copy .env.example into credentials/clickhouse.env and fill it in."
        )
    return env


def execute(env: dict[str, str], sql: str, timeout: int = WAKE_TIMEOUT_S) -> str:
    url = f"https://{env['CLICKHOUSE_HOST']}:{env.get('CLICKHOUSE_PORT') or '8443'}/"
    token = base64.b64encode(
        f"{env['CLICKHOUSE_USER']}:{env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()
    request = urllib.request.Request(
        url,
        data=sql.encode("utf-8"),
        headers={"Authorization": f"Basic {token}", "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"ClickHouse rejected the statement:\n{detail}") from exc


def split_statements(sql: str) -> list[str]:
    """Split on semicolons, after stripping -- comments.

    Fine for schema files, which is all this handles. Anything with a string
    literal containing a semicolon needs a real parser, and does not belong
    in a setup script.
    """
    stripped = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in stripped.split(";") if s.strip()]


def summarise(env: dict[str, str]) -> None:
    rows = execute(
        env,
        "SELECT name, engine, formatReadableQuantity(total_rows) "
        "FROM system.tables WHERE database = 'cinemeridian' ORDER BY name FORMAT TSV",
    ).strip()
    if not rows:
        print("  (database cinemeridian has no tables)")
        return
    print(f"  {'table':<22} {'engine':<24} rows")
    for line in rows.splitlines():
        name, engine, count = line.split("\t")
        print(f"  {name:<22} {engine:<24} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sql_file", nargs="?", default="sql/001_schema.sql")
    parser.add_argument("--check", action="store_true", help="list existing tables and exit")
    args = parser.parse_args()

    env = load_credentials()
    print(f"ClickHouse {env['CLICKHOUSE_HOST']} (database: cinemeridian)")

    version = execute(env, "SELECT version()").strip()
    print(f"  server version {version}\n")

    if not args.check:
        path = ROOT / args.sql_file if not Path(args.sql_file).is_absolute() else Path(args.sql_file)
        statements = split_statements(path.read_text(encoding="utf-8"))
        print(f"Applying {args.sql_file} ({len(statements)} statements)")
        for statement in statements:
            head = " ".join(statement.split())[:70]
            execute(env, statement)
            print(f"  ok  {head}")
        print()

    summarise(env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
