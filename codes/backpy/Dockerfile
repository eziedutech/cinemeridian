# CineMeridian backend.
#
# The one line in here that the project depends on is the uv binary. The agent
# reaches ClickHouse by launching mcp-clickhouse as a stdio subprocess through
# uv, so an image without uv produces an agent that starts cleanly, answers
# questions, and never touches the database — the exact failure the track
# rules are written to catch.

FROM python:3.12-slim

# uv, for the mcp-clickhouse stdio subprocess. Copied from the official image
# rather than pip-installed so the binary is present before anything runs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # uv caches the mcp-clickhouse download here. Cloud Run's filesystem is
    # writable only under /tmp, and a read-only cache path makes the MCP
    # subprocess fail on its first launch, in production, under a judge.
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_INSTALL_DIR=/tmp/uv-python

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch the MCP server into the image. Without this the first request
# after a cold start pays for a package download, which is both slow and a
# network dependency at exactly the wrong moment.
RUN uv tool install --python 3.12 mcp-clickhouse || true

COPY app ./app

# Cloud Run sets PORT; 8080 is its default and the local default too.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
