# OpenSCAD MCP Server.
#
#   docker build -t openscad-mcp .                       # install this checkout
#   docker build --target release -t openscad-mcp:rel .  # install from PyPI
#
#   docker run --rm -i -v "$PWD:/work" openscad-mcp                   # stdio server
#   docker run --rm -v "$PWD:/work" openscad-mcp check checks.yaml    # CI check run
#
# Debian ships OpenSCAD 2021.01, which needs an X display to export PNG, so the
# entrypoint starts Xvfb before exec'ing the server. A dev snapshot
# (openscad-nightly) renders through EGL and needs no display; set DISPLAY to
# any value, or swap the binary, and the entrypoint skips Xvfb.

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        openscad \
        xvfb \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional: BOSL2, the library most models include. Drop this if you do not
# need it; it adds about 30 MB.
RUN mkdir -p /usr/share/openscad/libraries \
    && apt-get update && apt-get install -y --no-install-recommends git \
    && git clone --depth 1 https://github.com/BelfrySCAD/BOSL2.git \
        /usr/share/openscad/libraries/BOSL2 \
    && rm -rf /usr/share/openscad/libraries/BOSL2/.git \
    && apt-get purge -y git && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 mcp \
    && mkdir -p /work /home/mcp/.cache/openscad-mcp /tmp/openscad-mcp \
    && chown -R mcp:mcp /work /home/mcp /tmp/openscad-mcp

# The server reads every one of these; see src/openscad_mcp/utils/config.py.
# MCP_ALLOWED_PATHS is what turns on path validation: leave it unset and the
# server will read any file it can, and log a warning saying so.
ENV OPENSCAD_PATH=/usr/bin/openscad \
    MCP_ALLOWED_PATHS=/work \
    MCP_TEMP_DIR=/tmp/openscad-mcp \
    MCP_TRANSPORT=stdio \
    MCP_MAX_MEMORY_MB=4096 \
    MCP_RENDER_TIMEOUT=300 \
    MCP_LOG_LEVEL=INFO \
    HOME=/home/mcp \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONUNBUFFERED=1

WORKDIR /work

# The render cache lives under $HOME/.cache/openscad-mcp and is not settable by
# environment variable, so mount a volume there to keep it across runs.
VOLUME ["/work", "/home/mcp/.cache/openscad-mcp"]

HEALTHCHECK --interval=60s --timeout=15s --start-period=5s --retries=3 \
    CMD ["openscad", "--version"]

# xvfb-run is deliberately not used: as PID 1 it never reaps its child and the
# container hangs after the command finishes. Starting Xvfb here and exec'ing
# the server keeps the server as PID 1 and the exit code intact.
COPY <<'SH' /usr/local/bin/entrypoint.sh
#!/bin/sh
set -e
if [ -z "$DISPLAY" ]; then
    export DISPLAY=:99
    Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
    i=0
    while [ ! -e /tmp/.X11-unix/X99 ] && [ $i -lt 100 ]; do
        i=$((i + 1))
        sleep 0.1
    done
fi
exec openscad-mcp "$@"
SH
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]


# --- install a published release (docker build --target release) ------------
FROM base AS release
ARG OPENSCAD_MCP_VERSION=0.6.1
RUN pip install --no-cache-dir "openscad-mcp==${OPENSCAD_MCP_VERSION}"
USER mcp


# --- install this checkout (default target) ---------------------------------
FROM base AS local
WORKDIR /src
COPY pyproject.toml README.md AGENTS.md LICENSE mcp-config.json ./
COPY src ./src
COPY skills ./skills
COPY .claude-plugin ./.claude-plugin
RUN pip install --no-cache-dir . && rm -rf /src
WORKDIR /work
USER mcp
