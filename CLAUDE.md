# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenSCAD MCP Server — a Python MCP (Model Context Protocol) server built with FastMCP that exposes OpenSCAD 3D rendering capabilities to AI assistants. It wraps the OpenSCAD CLI, rendering SCAD code to PNG images and returning results as base64 or file paths.

**External dependency**: OpenSCAD must be installed on the system. The server auto-detects it via PATH or common install locations.

## Build and Run Commands

```bash
# Install dependencies (uses uv, lockfile committed)
uv sync --extra dev

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
- **`render_scad_to_png()`** — synchronous OpenSCAD call returning a `RenderResult` (base64 PNG + parsed `Diagnostics` + cache/dependency info). The image is returned even when diagnostics contain errors; tools compute `success` from the diagnostics, never from the exit code
- **`_evaluate_scad()`** — shared runner for `export_model`, `analyze_model`, `validate_scad`: security checks, `-d` dependency closure, stderr parsing
- **`_run_openscad()`** — every subprocess goes through this: timeout with partial stderr kept, `RLIMIT_AS` via an `sh -c 'ulimit -v'` exec wrapper (never `preexec_fn`), new session
- **`find_openscad()` / `get_openscad_capabilities()`** — memoised discovery (stable and `openscad-nightly` layouts, newest version wins) plus a cached capability record
- **Parameter parsers** (`parse_camera_param`, `parse_list_param`, `parse_dict_param`, `parse_image_size_param`) — accept flexible input formats (JSON strings, lists, dicts, CSV) for AI assistant compatibility
- **Response size management** (`manage_response_size`) — auto-selects between base64, compressed, or file-path output based on size thresholds
- **`VIEW_PRESETS`** — predefined camera positions (front, back, top, isometric, etc.)
- **`QUALITY_PRESETS`** — draft/normal/high rendering quality via `$fn`/`$fa`/`$fs` variables
- **Render caching** (`_compute_render_cache_key`, `_check_cache`, `_save_to_cache`, `_evict_cache_if_needed`) — SHA-256 of all render parameters plus binary identity, length-prefixed fields. Each `<key>.png` has a `<key>.json` manifest listing every file OpenSCAD read (from `-d`) with size/mtime/sha256; a hit requires all of them unchanged and no previously-missing include to have appeared

### MCP Tools (registered with `@mcp.tool`)

**Rendering:**
- `render_single` — render a single view with quality presets and caching
- `render_perspectives` — multi-view parallel rendering using VIEW_PRESETS
- `compare_renders` — before/after diff rendering

**Export & Model Management:**
- `export_model` — STL/3MF/AMF/OFF/NEF3/DXF/SVG/PDF/CSG export; mesh formats return `mesh_health` from the CGAL statistics banner (`manifold` true/false/null)
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

- **`diagnostics.py`** — `parse_openscad_output()` turns stderr into `Diagnostics` (records with file/line and folded TRACE call stacks, capped echo output, CGAL statistics, repair hints keyed to real 2021.01 message strings), `parse_deps_file()` for `-d` output, `extract_source_dependencies()` for static include/use/import/surface scanning
- **`types.py`** — Pydantic v2 models and enums: `ColorScheme`, `TransportType`, `Vector3D`, `ImageSize`, `OpenSCADInfo`, `ServerInfo`
- **`utils/config.py`** — Configuration via Pydantic models with env var, `.env`, and YAML support. Singleton access via `get_config()`/`set_config()`. Configs: `RenderingConfig`, `CacheConfig`, `SecurityConfig`, `ServerConfig`, `Config`

### Security

All of this is conditional on `config.security.allowed_paths` being set (default `None` = no validation; `main()` logs a warning). See README "Threat model".

- **Path validation on arguments**: `scad_file`, `include_paths` (all four OpenSCAD tools) and export `output_path` via `_check_allowed_path` / `_validate_include_paths`
- **Path validation on the dependency closure**: `_check_dependency_closure` checks every file listed in the `-d` output against `allowed_paths` + library dirs + temp dir and withholds output on violation. This is what stops `include <...>` / `surface(file=...)` from reading arbitrary files
- **Memory ceiling**: `config.security.max_memory_mb` (default 4096, 0 disables) applied by `_wrap_with_memory_limit`
- **File size limits**: `scad_content` checked against `config.security.max_file_size_mb`
- **Variable name validation**: regex `^\$?[a-zA-Z_][a-zA-Z0-9_]*$` prevents injection
- **Subprocess timeout**: `config.rendering.timeout_seconds` (default 300s)
- **Echo output** is capped and rewritten so temp paths appear as `<inline>`
- **Model name validation**: alphanumeric + hyphens/underscores, no path traversal

### Testing

- pytest with `asyncio_mode = auto` — async tests run without explicit marks
- Tests mock OpenSCAD subprocess calls; they don't require OpenSCAD installed. Mocks that emulate a render should write the `-o` file and the `-d` dependency file (see `_write_outputs` in `tests/test_correctness_fixes.py`)
- `conftest.py` has an `autouse` fixture (`reset_environment`) that clears env vars, temp dirs, and the memoised OpenSCAD discovery between tests
- Tools accept a bare base64 string from a mocked `render_scad_to_png` (`_as_render_result`), so older mocks keep working
- **FunctionTool pattern**: FastMCP's `@mcp.tool()` wraps functions as `FunctionTool` objects. In tests, access the underlying function via `render_single.fn` (e.g., `render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single`)
- **Caching in tests**: When testing `render_scad_to_png` command construction, disable caching in the config to prevent cache hits from skipping subprocess calls
- Custom markers: `unit`, `integration`, `performance`, `slow`, `edge`, `smoke`, `mcp`, `render`, `config`

## Key Design Decisions

- **Flexible parameter parsing**: All input parsers accept multiple formats (string, list, dict, JSON) because AI assistants send parameters in unpredictable formats. This is intentional — don't simplify these parsers.
- **Exit code is not success**: on 2021.01 a failed `assert()`, an unknown module, a non-closed polyhedron and a missing include all exit 0. Every tool parses stderr and sets `success` from `Diagnostics.ok`; renders return the image *with* the errors.
- **`--hardwarnings` is off by default** (`rendering.hard_warnings`): it aborts evaluation at the first warning while exiting 0, blanking renders and truncating echo output. Warnings surface through diagnostics instead. Never add it back to echo-bearing paths.
- **`Volumes:` in the CGAL banner is not a body count**: a hollow shell and two disjoint cubes both report 3. Report it as `nef_volumes`; gate manifoldness on `Simple:` only.
- **Framing**: `render_single` without a `view` and without an explicit camera auto-frames (`--autocenter --viewall`); `render_perspectives` defaults to 3 views (front, top, isometric) because each image costs ~640 vision tokens.
- **Response size management**: Large renders auto-save to files instead of returning base64 to avoid oversized MCP responses.
- **Camera format**: 6-value eye+center format (`--camera=eye_x,eye_y,eye_z,center_x,center_y,center_z`), not the 7-value translate+rotate format.
- **Render caching**: Enabled by default, validated against a per-entry dependency manifest (see Architecture). Cache stored in `~/.cache/openscad-mcp/`. Never cache a render without recording what it read.

## Tool Configuration

- **Ruff**: line-length 100, Python 3.10 target, rules: E, W, F, I, B, C4, UP, ARG, SIM
- **Black**: line-length 100
- **Mypy**: Python 3.10, `ignore_missing_imports = true`
- **Coverage**: 80% minimum configured (`--cov-fail-under=80`)

## Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Package uses Hatchling build backend
- `uv` is the standard package manager (not pip)
