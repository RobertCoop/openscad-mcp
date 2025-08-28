# Multi-stage build for OpenSCAD MCP Server
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Create working directory
WORKDIR /build

# Copy package files
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Build the package
RUN uv venv && \
    uv pip install --no-cache-dir build && \
    python -m build --wheel --outdir /dist

# Runtime stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    openscad \
    xvfb \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv in runtime image
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Create non-root user
RUN useradd -m -u 1000 mcp && \
    mkdir -p /tmp/openscad-renders /tmp/openscad-cache /tmp/openscad-output && \
    chown -R mcp:mcp /tmp/openscad-*

# Set working directory
WORKDIR /app

# Copy wheel from builder
COPY --from=builder /dist/*.whl /tmp/

# Install the package
RUN uv pip install --system --no-cache-dir /tmp/*.whl && \
    rm /tmp/*.whl

# Copy configuration
COPY .env.example /app/.env.example

# Switch to non-root user
USER mcp

# Set environment variables
ENV OPENSCAD_PATH=/usr/bin/openscad \
    OPENSCAD_TEMP_DIR=/tmp/openscad-renders \
    OPENSCAD_CACHE_DIR=/tmp/openscad-cache \
    OPENSCAD_OUTPUT_DIR=/tmp/openscad-output \
    SERVER_TRANSPORT=stdio \
    PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from openscad_mcp.server import mcp; import sys; sys.exit(0 if mcp else 1)"

# Volume for persistent data
VOLUME ["/tmp/openscad-renders", "/tmp/openscad-cache", "/tmp/openscad-output"]

# Default command - run with xvfb for headless rendering
ENTRYPOINT ["xvfb-run", "-a"]
CMD ["python", "-m", "openscad_mcp"]