#!/usr/bin/env python3
"""Load the generated CSVs into ClickHouse over the HTTPS interface.

**Setup tooling only** - the same caveat as apply_schema.py. Nothing in the
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
import time
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


#: How long a TRUNCATE is given to finish propagating before anything is
#: inserted on top of it.
TRUNCATE_SETTLE_S = 6.0


def truncate_all(env: dict[str, str], tables: list[str], timeout_s: float = 60.0) -> None:
    """Empty every table, then wait for all of them to actually be empty.

    TRUNCATE on ClickHouse Cloud is asynchronous, and inserting on top of one
    that is still propagating is a race the insert loses: rows land, the
    truncate catches up, and the table ends up short with every step having
    reported success. Two tables came back completely empty that way, and a
    third lost 41,760 of 108,000 rows.

    Truncating everything first and settling once is both faster and safer
    than interleaving, because the wait is paid a single time.
    """
    for table in tables:
        execute(env, f"TRUNCATE TABLE IF EXISTS {table}")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if all(execute(env, f"SELECT count() FROM {t}").strip() == "0" for t in tables):
            break
        time.sleep(1.0)
    else:
        raise SystemExit(f"tables were still not empty {timeout_s:.0f}s after TRUNCATE")

    # Counting zero is necessary but not sufficient; give the drop a moment to
    # finish propagating before writing on top of it.
    time.sleep(TRUNCATE_SETTLE_S)


def load_and_verify(env: dict[str, str], path: Path, table: str, want: int,
                    attempts: int = 3) -> int:
    """Insert, count, and insert again if the count is short.

    Guessing how long a TRUNCATE needs to settle is a losing game: a
    ReplacingMergeTree took longer than a MergeTree, and a fixed wait tuned on
    one is wrong for the other. Checking the result and retrying is exact,
    because it responds to what actually happened rather than to an estimate.
    """
    for attempt in range(1, attempts + 1):
        stream_insert(env, path, table)
        got = int(execute(env, f"SELECT count() FROM {table}").strip())
        if got == want or attempt == attempts:
            return got
        print(f"    {table} came back {got:,} of {want:,}, retrying in 5s")
        execute(env, f"TRUNCATE TABLE IF EXISTS {table}")
        time.sleep(5)
    return got


def expected_rows(path: Path) -> int:
    """Data lines in a CSVWithNames file, so the load can be checked."""
    with path.open("r", encoding="utf-8") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


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
    if args.truncate:
        present = [table for filename, table in LOADS if (data_dir / filename).is_file()]
        print(f"  emptying {len(present)} tables, then settling")
        truncate_all(env, present)

    loaded = 0
    mismatched: list[tuple[str, int, int]] = []
    for filename, table in LOADS:
        path = data_dir / filename
        if not path.is_file():
            print(f"  skip   {filename:<26} (not generated yet)")
            continue
        want = expected_rows(path)
        got = load_and_verify(env, path, table, want)
        # Verify rather than assume. A load that reports success while silently
        # dropping rows is worse than one that fails.
        status = "" if got == want else f"  MISMATCH: expected {want:,}"
        print(f"  loaded {filename:<26} -> {table:<38} {got:>9,} rows{status}")
        if got != want:
            mismatched.append((filename, want, got))
        loaded += 1

    if not loaded:
        sys.exit("Nothing to load. Run scripts/generate_telemetry.py first.")
    if mismatched:
        print()
        for filename, want, got in mismatched:
            print(f"  {filename}: expected {want:,} rows, found {got:,}")
        sys.exit("Row counts do not match the files. The load is not trustworthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
