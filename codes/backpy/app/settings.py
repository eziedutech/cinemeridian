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

# Local development loads credentials/*.env, which is gitignored. In Cloud Run
# these files do not exist and the environment is already populated.
_CREDENTIALS_DIR = Path(__file__).resolve().parents[3] / "credentials"
_ENV_FILES = ("gcp.env", "clickhouse.env")


def _load_local_env_files() -> None:
    """Populate os.environ from credentials/*.env, without overriding it.

    Not overriding matters: on Cloud Run the real values are already in the
    environment, and a stale local file must never win.
    """
    for name in _ENV_FILES:
        path = _CREDENTIALS_DIR / name
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
            f"{key} is not set. Locally, fill in credentials/*.env "
            f"(see Docs/SETUP-KREDENSIAL.md); on Cloud Run, wire it to Secret Manager."
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

    def mcp_clickhouse_env(self) -> dict[str, str]:
        """The environment block for the mcp-clickhouse stdio subprocess."""
        return {
            "CLICKHOUSE_HOST": self.clickhouse_host,
            "CLICKHOUSE_PORT": self.clickhouse_port,
            "CLICKHOUSE_USER": self.clickhouse_user,
            "CLICKHOUSE_PASSWORD": self.clickhouse_password,
            "CLICKHOUSE_SECURE": self.clickhouse_secure,
            "CLICKHOUSE_DATABASE": self.clickhouse_database,
        }

    def __str__(self) -> str:  # never let the password reach a log line
        return (
            f"Settings(project={self.project_id}, location={self.location}, "
            f"model={self.model}, bucket={self.gcs_asset_bucket}, "
            f"clickhouse={self.clickhouse_user}@{self.clickhouse_host}:{self.clickhouse_port}"
            f"/{self.clickhouse_database})"
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
    )
