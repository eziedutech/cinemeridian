"""The CineMeridian agent, and its ClickHouse connection.

The single most important thing in this file is that **ClickHouse is reached
through an MCP server, never through a database client**. `mcp-clickhouse`
runs as a stdio subprocess launched by `uv`, and its tools are attached to the
agent as a toolset. Every runtime query the agent makes — reads and the
finding write-back alike — travels that path.

Nothing else in codes/backpy/app opens a ClickHouse connection. The scripts in
scripts/ do talk to ClickHouse over HTTPS, but they are setup tooling that runs
before the agent exists, not part of the running system.
"""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StdioServerParameters,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from app.prompts import CONTINUITY_AGENT_INSTRUCTION
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

AGENT_NAME = "cinemeridian_continuity_agent"

#: mcp-clickhouse is not a project dependency; uv fetches and runs it on
#: demand. Pinning the interpreter keeps the container and a developer laptop
#: on the same runtime. 3.13 is deliberately *not* used: uv does not ship it
#: everywhere, and 3.12 is what the image installs.
MCP_CLICKHOUSE_PYTHON = "3.12"

#: A cold ClickHouse Cloud service takes a while to answer its first query, and
#: uv may need to fetch the server package on the very first launch. Both land
#: inside this timeout.
MCP_STARTUP_TIMEOUT_S = 180.0

#: The tool names mcp-clickhouse actually exposes. Note `run_query`, not
#: `run_select_query` — the server renamed it, and an agent filtered on the old
#: name silently ends up with no way to query at all.
#:
#: run_query is read-only unless CLICKHOUSE_ALLOW_WRITE_ACCESS is set. The
#: agent needs to write findings back, so the safety boundary is a ClickHouse
#: user grant (see scripts/create_agent_user.py), not a client-side flag.
CLICKHOUSE_TOOLS = ["run_query", "list_databases", "list_tables"]


def build_clickhouse_toolset(settings: Settings | None = None) -> McpToolset:
    """The mcp-clickhouse stdio server, as an ADK toolset."""
    settings = settings or get_settings()
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=[
                    "run",
                    "--quiet",
                    "--with",
                    "mcp-clickhouse",
                    "--python",
                    MCP_CLICKHOUSE_PYTHON,
                    "mcp-clickhouse",
                ],
                env=settings.mcp_clickhouse_env(),
            ),
            timeout=MCP_STARTUP_TIMEOUT_S,
        ),
        tool_filter=CLICKHOUSE_TOOLS,
    )


def build_agent(
    settings: Settings | None = None,
    clickhouse_toolset: McpToolset | None = None,
) -> LlmAgent:
    """The continuity agent, with ClickHouse attached over MCP.

    Pass an existing toolset to share one already-warm subprocess. Launching
    mcp-clickhouse takes well over ten seconds the first time — uv has to
    resolve and start the server — and paying that on the first request means
    a dead pause in front of whoever is watching.
    """
    settings = settings or get_settings()
    logger.info("building %s on %s", AGENT_NAME, settings.model)
    return LlmAgent(
        model=settings.model,
        name=AGENT_NAME,
        description=(
            "Finds physical continuity errors across takes, edit versions and "
            "CG shots by comparing observed frames against computed physics."
        ),
        instruction=CONTINUITY_AGENT_INSTRUCTION,
        tools=[clickhouse_toolset or build_clickhouse_toolset(settings)],
    )


#: ADK's tooling looks for a module-level `root_agent`. Built lazily so that
#: importing this module does not require credentials.
def __getattr__(name: str):
    if name == "root_agent":
        return build_agent()
    raise AttributeError(name)
