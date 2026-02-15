# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenSCAD MCP Server — a Python MCP (Model Context Protocol) server built with FastMCP that exposes OpenSCAD 3D rendering capabilities to AI assistants. It wraps the OpenSCAD CLI, rendering SCAD code to PNG images and returning results as base64 or file paths.

**External dependency**: OpenSCAD must be installed on the system. The server auto-detects it via PATH or common install locations.

## Build and Run Commands

```bash
# Install dependencies (uses uv, lockfile committed)
uv sync --dev

# Run the MCP server
uv run openscad-mcp
# or: uv run python -m openscad_mcp

# Run all tests with coverage
uv run pytest

# Run specific test markers
uv run pytest -m unit
uv run pytest -m performance
uv run pytest -m "not slow"

# Run a single test file
uv run pytest tests/test_openscad_mcp.py

# Run a single test
uv run pytest tests/test_openscad_mcp.py::TestParameterParsers::test_parse_camera_param_list

# Lint
uv run ruff check src/ tests/
uv run black --check src/ tests/

# Format
uv run black src/ tests/

# Type check
uv run mypy src/
```

## Architecture

### Core module: `src/openscad_mcp/server.py`

This file contains the FastMCP server instance, all MCP tools, helpers, and rendering logic:

- **`mcp = FastMCP("OpenSCAD MCP Server")`** — the server instance
- **`find_openscad()`** — locates the OpenSCAD binary on the system
- **`render_scad_to_png()`** — synchronous subprocess call to OpenSCAD with caching, security validation, include path support, and timeout enforcement
- **Parameter parsers** (`parse_camera_param`, `parse_list_param`, `parse_dict_param`, `parse_image_size_param`) — accept flexible input formats (JSON strings, lists, dicts, CSV) for AI assistant compatibility
- **Response size management** (`manage_response_size`) — auto-selects between base64, compressed, or file-path output based on size thresholds
- **`VIEW_PRESETS`** — predefined camera positions (front, back, top, isometric, etc.)
- **`QUALITY_PRESETS`** — draft/normal/high rendering quality via `$fn`/`$fa`/`$fs` variables
- **Render caching** (`_compute_render_cache_key`, `_check_cache`, `_save_to_cache`, `_evict_cache_if_needed`) — SHA-256 hash of all render parameters, TTL-based expiration, LRU eviction

### MCP Tools (registered with `@mcp.tool`)

**Rendering:**
- `render_single` — render a single view with quality presets and caching
- `render_perspectives` — multi-view parallel rendering using VIEW_PRESETS
- `compare_renders` — before/after diff rendering

**Export & Model Management:**
- `export_model` — STL/3MF/AMF/OFF/DXF/SVG export
- `create_model`, `get_model`, `update_model`, `list_models`, `delete_model` — CRUD for .scad files

**Analysis & Validation:**
- `validate_scad` — syntax checking without full render
- `analyze_model` — bounding box/dimensions via STL vertex parsing
- `get_libraries` — discover installed OpenSCAD libraries
- `check_openscad` — verify OpenSCAD installation and version

**Project Support:**
- `get_project_files` — list .scad files and dependency graph in a directory
- `clear_cache` — manage the render cache

### Supporting modules

- **`types.py`** — Pydantic v2 models and enums: `ColorScheme`, `TransportType`, `Vector3D`, `ImageSize`, `OpenSCADInfo`, `ServerInfo`
- **`utils/config.py`** — Configuration via Pydantic models with env var, `.env`, and YAML support. Singleton access via `get_config()`/`set_config()`. Configs: `RenderingConfig`, `CacheConfig`, `SecurityConfig`, `ServerConfig`, `Config`

### Security

- **Path validation**: `scad_file` and `include_paths` validated against `config.security.allowed_paths`
- **File size limits**: `scad_content` checked against `config.security.max_file_size_mb`
- **Variable name validation**: regex `^[a-zA-Z_][a-zA-Z0-9_]*$` prevents injection
- **Subprocess timeout**: `config.rendering.timeout_seconds` (default 300s)
- **Model name validation**: alphanumeric + hyphens/underscores, no path traversal

### Testing

- pytest with `asyncio_mode = auto` — async tests run without explicit marks
- Tests mock OpenSCAD subprocess calls; they don't require OpenSCAD installed
- `conftest.py` has an `autouse` fixture (`reset_environment`) that clears env vars and temp dirs between tests
- **FunctionTool pattern**: FastMCP's `@mcp.tool()` wraps functions as `FunctionTool` objects. In tests, access the underlying function via `render_single.fn` (e.g., `render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single`)
- **Caching in tests**: When testing `render_scad_to_png` command construction, disable caching in the config to prevent cache hits from skipping subprocess calls
- Custom markers: `unit`, `integration`, `performance`, `slow`, `edge`, `smoke`, `mcp`, `render`, `config`

## Key Design Decisions

- **Flexible parameter parsing**: All input parsers accept multiple formats (string, list, dict, JSON) because AI assistants send parameters in unpredictable formats. This is intentional — don't simplify these parsers.
- **`--hardwarnings` flag**: Passed to OpenSCAD so warnings surface to the AI assistant. Added by external contributor.
- **Response size management**: Large renders auto-save to files instead of returning base64 to avoid oversized MCP responses.
- **Camera format**: 6-value eye+center format (`--camera=eye_x,eye_y,eye_z,center_x,center_y,center_z`), not the 7-value translate+rotate format.
- **Render caching**: Enabled by default, cache key is SHA-256 of all render parameters. Cache stored in `~/.cache/openscad-mcp/`.

## Tool Configuration

- **Ruff**: line-length 100, Python 3.10 target, rules: E, W, F, I, B, C4, UP, ARG, SIM
- **Black**: line-length 100
- **Mypy**: Python 3.10, `ignore_missing_imports = true`
- **Coverage**: 80% minimum configured (`--cov-fail-under=80`)

## Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Package uses Hatchling build backend
- `uv` is the standard package manager (not pip)
