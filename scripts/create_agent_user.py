#!/usr/bin/env python3
"""Create the restricted ClickHouse user the agent runs as.

The agent has to write its findings back through MCP, which means the
mcp-clickhouse server must run with write access enabled. That flag is
all-or-nothing: with it set, anything the model puts in a `run_query` call
reaches the server, DROP TABLE included.

So the boundary is not the flag. It is the grant. This user can read
everything and insert into exactly one table:

    SELECT  on cinemeridian.*
    INSERT  on cinemeridian.continuity_findings

No DDL, no writes anywhere else. If the model ever emits something
destructive, ClickHouse refuses it. Setup scripts keep using `default`, which
never runs with a model attached to it.

The generated password is written to credentials/clickhouse.env, which is
gitignored. It is not printed.

Usage:
    python scripts/create_agent_user.py
    python scripts/create_agent_user.py --rotate    # new password for an existing user
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_schema import execute, load_credentials  # noqa: E402

CREDENTIALS = ROOT / "credentials" / "clickhouse.env"
AGENT_USER = "cinemeridian_agent"
DATABASE = "cinemeridian"
FINDINGS_TABLE = f"{DATABASE}.continuity_findings"


#: Special characters that survive an .env file and a shell unquoted. ClickHouse
#: Cloud's password policy demands at least one of them; $ ` " ' # = and \ are
#: excluded because each one breaks something downstream.
_SPECIALS = "-_.@+~"


def generate_password(length: int = 32) -> str:
    """A password that satisfies ClickHouse Cloud's policy and nothing else's spite.

    The policy requires a mix of classes, so each class is placed explicitly
    rather than hoped for: a 32-character random string will almost always
    contain one of each, and "almost always" is a bad property for setup code.
    """
    classes = [string.ascii_lowercase, string.ascii_uppercase, string.digits, _SPECIALS]
    alphabet = "".join(classes)
    chars = [secrets.choice(group) for group in classes]
    chars += [secrets.choice(alphabet) for _ in range(length - len(classes))]
    # Shuffle, so the guaranteed characters are not always in the first four slots.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def upsert_env(key: str, value: str) -> None:
    """Set a key in credentials/clickhouse.env, preserving everything else."""
    lines = CREDENTIALS.read_text(encoding="utf-8").splitlines() if CREDENTIALS.is_file() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# Restricted user for the agent - see scripts/create_agent_user.py")
        lines.append(f"{key}={value}")
    CREDENTIALS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true", help="replace an existing user's password")
    args = parser.parse_args()

    env = load_credentials()
    print(f"ClickHouse {env['CLICKHOUSE_HOST']}")

    existing = execute(
        env, f"SELECT count() FROM system.users WHERE name = '{AGENT_USER}'"
    ).strip()
    if existing != "0" and not args.rotate:
        print(f"  user {AGENT_USER} already exists; pass --rotate to reset its password")
        show_grants(env)
        return 0

    password = generate_password()
    verb = "CREATE OR REPLACE" if existing != "0" else "CREATE"
    execute(env, f"{verb} USER {AGENT_USER} IDENTIFIED WITH sha256_password BY '{password}'")
    print(f"  {'rotated' if existing != '0' else 'created'} user {AGENT_USER}")

    execute(env, f"GRANT SELECT ON {DATABASE}.* TO {AGENT_USER}")
    execute(env, f"GRANT INSERT ON {FINDINGS_TABLE} TO {AGENT_USER}")
    print(f"  granted SELECT on {DATABASE}.*")
    print(f"  granted INSERT on {FINDINGS_TABLE}")

    upsert_env("CLICKHOUSE_AGENT_USER", AGENT_USER)
    upsert_env("CLICKHOUSE_AGENT_PASSWORD", password)
    print(f"  password written to {CREDENTIALS.relative_to(ROOT)} (gitignored, not printed)")

    show_grants(env)
    return 0


def show_grants(env: dict[str, str]) -> None:
    rows = execute(
        env,
        f"SELECT access_type, database, table FROM system.grants "
        f"WHERE user_name = '{AGENT_USER}' ORDER BY access_type FORMAT TSV",
    ).strip()
    print("\n  effective grants:")
    for line in rows.splitlines():
        access, database, table = (line.split("\t") + ["", ""])[:3]
        # ClickHouse writes \N for "every table in the database".
        target = f"{database}.*" if table in ("", "\\N") else f"{database}.{table}"
        print(f"    {access:<8} {target}")


if __name__ == "__main__":
    raise SystemExit(main())
