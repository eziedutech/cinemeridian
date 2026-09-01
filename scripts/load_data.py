#!/usr/bin/env python3
"""Load the generated CSVs into ClickHouse over the HTTPS interface.

**Setup tooling only** — the same caveat as apply_schema.py. Nothing in the
application talks to ClickHouse directly; every runtime query goes through the
mcp-clickhouse MCP server.

Files are streamed rather than read into memory, because env_telemetry.csv at
1 Hz is a few hundred thousand rows and there is no reason to hold it.

Usage:
    python scripts/generate_telemetry.py --out data/
    python scripts/load_data.py --data data/
    python scripts/load_data.py --data data/ --truncate    # reload from clean
"""

from __future__ import annotations

import argparse
import base64
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_schema import WAKE_TIMEOUT_S, execute, load_credentials  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: file -> destination table. Column order in the CSV header decides the
#: mapping, which is why the generator writes CSVWithNames.
LOADS = [
    ("ephemeris.csv", "cinemeridian.ephemeris"),
    ("env_telemetry.csv", "cinemeridian.env_telemetry"),
    ("takes.csv", "cinemeridian.takes"),
    ("edit_decisions.csv", "cinemeridian.edit_decisions"),
    ("shot_render_config.csv", "cinemeridian.shot_render_config"),
    ("frame_observations.csv", "cinemeridian.frame_observations"),
]


def stream_insert(env: dict[str, str], path: Path, table: str) -> None:
    query = urllib.parse.urlencode({"query": f"INSERT INTO {table} FORMAT CSVWithNames"})
    url = f"https://{env['CLICKHOUSE_HOST']}:{env.get('CLICKHOUSE_PORT') or '8443'}/?{query}"
    token = base64.b64encode(
        f"{env['CLICKHOUSE_USER']}:{env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()

    size = path.stat().st_size
    with path.open("rb") as body:
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "text/csv",
                "Content-Length": str(size),
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=WAKE_TIMEOUT_S).read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise SystemExit(f"INSERT into {table} failed:\n{detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data", help="directory holding the CSVs")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="empty each table before loading, so a reload is idempotent",
    )
    args = parser.parse_args()

    env = load_credentials()
    data_dir = Path(args.data) if Path(args.data).is_absolute() else ROOT / args.data
    if not data_dir.is_dir():
        sys.exit(f"{data_dir} does not exist. Run scripts/generate_telemetry.py first.")

    print(f"ClickHouse {env['CLICKHOUSE_HOST']}\n")
    loaded = 0
    for filename, table in LOADS:
        path = data_dir / filename
        if not path.is_file():
            print(f"  skip   {filename:<26} (not generated yet)")
            continue
        if args.truncate:
            execute(env, f"TRUNCATE TABLE IF EXISTS {table}")
        stream_insert(env, path, table)
        count = execute(env, f"SELECT count() FROM {table}").strip()
        print(f"  loaded {filename:<26} -> {table:<38} {int(count):>9,} rows")
        loaded += 1

    if not loaded:
        sys.exit("Nothing to load. Run scripts/generate_telemetry.py first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
