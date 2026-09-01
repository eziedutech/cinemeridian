"""Configuration, read from the environment.

Deliberately stdlib-only. Every value here is either public (a region, a
model name) or a secret that arrives through the environment — locally from
``credentials/*.env``, on Cloud Run from Secret Manager. Nothing is ever read
from a checked-in file, and nothing secret is logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_ENV_FILES = ("gcp.env", "clickhouse.env")


def _credentials_dir() -> Path | None:
    """Find credentials/ by walking up, if it is there at all.

    Deliberately not a fixed number of parent hops. In the repository this
    file sits at codes/backpy/app/settings.py; in the container it is at
    /app/app/settings.py, where counting parents raises IndexError and takes
    the whole service down at import time. Walking up finds it in the first
    case and finds nothing in the second, which is the correct answer for
    both — the container gets its configuration from the environment.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "credentials"
        if candidate.is_dir():
            return candidate
    return None


def _load_local_env_files() -> None:
    """Populate os.environ from credentials/*.env, without overriding it.

    Not overriding matters: on Cloud Run the real values are already in the
    environment, and a stale local file must never win.
    """
    directory = _credentials_dir()
    if directory is None:
        return
    for name in _ENV_FILES:
        path = directory / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


class ConfigError(RuntimeError):
    """A required setting is missing or still holds a placeholder."""


def _require(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise ConfigError(
            f"{key} is not set. Locally, copy .env.example into "
            f"credentials/gcp.env and credentials/clickhouse.env and fill it in; "
            f"on Cloud Run, wire it to Secret Manager."
        )
    if value.startswith("xxxx") or value in {"changeme", "TODO"}:
        raise ConfigError(f"{key} still holds a placeholder value ({value!r}).")
    return value


@dataclass(frozen=True)
class Settings:
    # Google Cloud / Vertex AI
    project_id: str
    location: str
    use_vertexai: bool
    gcs_asset_bucket: str
    model: str

    # ClickHouse — consumed only as the environment handed to the
    # mcp-clickhouse subprocess. The application never opens its own
    # connection; every runtime query goes through the MCP tools.
    clickhouse_host: str
    clickhouse_port: str
    clickhouse_user: str
    clickhouse_password: str = field(repr=False)
    clickhouse_secure: str
    clickhouse_database: str

    # The restricted user the agent runs as. Absent until
    # scripts/create_agent_user.py has been run, in which case the agent falls
    # back to the admin user in read-only mode.
    agent_user: str = ""
    agent_password: str = field(default="", repr=False)

    def mcp_clickhouse_env(self) -> dict[str, str]:
        """The environment block for the mcp-clickhouse stdio subprocess.

        Two things here are deliberate and worth not undoing:

        The agent connects as the restricted user when one exists. That user
        can read the whole database but insert only into continuity_findings
        (scripts/create_agent_user.py), so the blast radius of a bad query is
        a grant, not a flag.

        Write access is enabled because the agent records its findings through
        the same MCP server it reads with. Enabling it for the *default* user
        would hand a language model DROP TABLE, which is why the restricted
        user matters.
        """
        return {
            "CLICKHOUSE_HOST": self.clickhouse_host,
            "CLICKHOUSE_PORT": self.clickhouse_port,
            "CLICKHOUSE_USER": self.agent_user or self.clickhouse_user,
            "CLICKHOUSE_PASSWORD": self.agent_password or self.clickhouse_password,
            "CLICKHOUSE_SECURE": self.clickhouse_secure,
            "CLICKHOUSE_DATABASE": self.clickhouse_database,
            # mcp-clickhouse compares this against the literal string "true"
            # (`os.getenv(...).lower() == "true"`). "1" reads as false, and the
            # symptom is every INSERT coming back as "Cannot execute query in
            # readonly mode" with nothing else to suggest a config problem.
            "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true" if self.agent_user else "false",
        }

    @property
    def uses_restricted_user(self) -> bool:
        return bool(self.agent_user)

    def __str__(self) -> str:  # never let the password reach a log line
        return (
            f"Settings(project={self.project_id}, location={self.location}, "
            f"model={self.model}, bucket={self.gcs_asset_bucket}, "
            f"clickhouse={self.agent_user or self.clickhouse_user}@{self.clickhouse_host}"
            f":{self.clickhouse_port}/{self.clickhouse_database}"
            f"{' [restricted]' if self.agent_user else ' [admin, read-only]'})"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_local_env_files()
    return Settings(
        project_id=_require("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_vertexai=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true",
        gcs_asset_bucket=_require("GCS_ASSET_BUCKET"),
        model=os.environ.get("CINEMERIDIAN_MODEL", "gemini-2.5-flash"),
        clickhouse_host=_require("CLICKHOUSE_HOST"),
        clickhouse_port=os.environ.get("CLICKHOUSE_PORT", "8443"),
        clickhouse_user=os.environ.get("CLICKHOUSE_USER", "default"),
        clickhouse_password=_require("CLICKHOUSE_PASSWORD"),
        clickhouse_secure=os.environ.get("CLICKHOUSE_SECURE", "true"),
        clickhouse_database=os.environ.get("CLICKHOUSE_DATABASE", "cinemeridian"),
        agent_user=os.environ.get("CLICKHOUSE_AGENT_USER", "").strip(),
        agent_password=os.environ.get("CLICKHOUSE_AGENT_PASSWORD", "").strip(),
    )
