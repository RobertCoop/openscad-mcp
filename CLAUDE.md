# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenSCAD MCP Server — a Python MCP (Model Context Protocol) server built with FastMCP that wraps the OpenSCAD CLI. It renders `.scad` source to PNG, exports meshes, returns exact measurements from the exported geometry, and checks assemblies. 12 tools; the design principle is *numbers decide, pictures confirm*.

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
uv run pytest tests/test_openscad_mcp.py::TestParameterParsers::test_parse_list_param_with_csv_string

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
- **`_evaluate_scad()`** — shared runner for every non-render OpenSCAD call (`export_model`, `measure`, `validate`, `scad_eval`, `check`): security checks, `-d` dependency closure, stderr parsing
- **`_run_openscad()`** — every subprocess goes through this: timeout with partial stderr kept, `RLIMIT_AS` via an `sh -c 'ulimit -v'` exec wrapper (never `preexec_fn`), new session
- **`find_openscad()` / `get_openscad_capabilities()`** — memoised discovery (stable and `openscad-nightly` layouts, newest version wins) plus a cached capability record
- **Parameter parsers** (`parse_camera_param`, `parse_list_param`, `parse_dict_param`, `parse_image_size_param`) — accept flexible input formats (JSON strings, lists, dicts, CSV) for AI assistant compatibility
- **Response size management** (`manage_response_size`) — auto-selects between base64, compressed, or file-path output based on size thresholds
- **`VIEW_PRESETS`** — predefined camera positions (front, back, top, isometric, etc.)
- **`QUALITY_PRESETS`** — draft/normal/high rendering quality via `$fn`/`$fa`/`$fs` variables
- **Render caching** (`_compute_render_cache_key`, `_check_cache`, `_save_to_cache`, `_evict_cache_if_needed`) — SHA-256 of all render parameters plus binary identity, length-prefixed fields. Each `<key>.png` has a `<key>.json` manifest listing every file OpenSCAD read (from `-d`) with size/mtime/sha256; a hit requires all of them unchanged and no previously-missing include to have appeared

### MCP Tools (registered with `@mcp.tool`)

**Rendering:**
- `render` — one tool, `mode=views|section|parts|compare`. Each image is preceded by a spatial digest (camera, view direction, mm/px when `grounded`, bbox) and followed by a metadata JSON with diagnostics. `grounded=true` measures the bbox first (cached in `_measure_cache`) and uses `camera.fit_camera` + `--projection=o`; `annotate=true` draws with `camera.annotate`. Sections and parts are source-level wrappers from `wrappers.py`

**Assemblies (`assembly.py`, `checks.py`):**
- `check` — `mode=interference|clearance|contact|alignment|motion|rules`. Parts are named `{name, code, place, frame, ghost, mass_g, motion}`; each is exported *separately* through `_export_parts` (parallel, on-disk cache under `cache/parts/` keyed on source + static dependency fingerprint + placement + variables + `$fn` + binary) and loaded as `geom.Mesh`. **Never union an assembly**: CGAL's union destroys part identity non-uniformly. Relations come from `geom.classify_pair` (min-distance ladder: clear / contact / interference; flush contact is contact, never interference; OpenSCAD `intersection()` volume is an opt-in cross-check via `volume=true`, never the oracle). `checks.RuleEngine` evaluates check-file rules; `openscad-mcp check file.yaml` is the CLI with exit code 0/1/2.
- Quality provenance: every geometric row carries `quality.fn`; distances inside the inscribed-polygon bound `r*(1-cos(180/$fn))` are `UNRESOLVED`.

**Export & Model Management:**
- `export_model` — STL/3MF/AMF/OFF/NEF3/DXF/SVG/PDF/CSG export; mesh formats return `mesh_health`; `parts=[...]` bundles per-part exports into one named-object 3MF (`threemf.py`) or a directory of STLs
- `model` — `action=create|get|update|list|delete` (the five CRUD tools collapsed); `template="part:<id>"` writes a catalog part module

**Analysis & Validation:**
- `measure` — `mode=model|parts|section|mass|probe|features|printability|orientation|anchors`: numbers from `mesh.py` (model/parts/section), `massprops.py` (mass, inertia, composition with `mass_g` overrides), `geom.py` (probe: winding-number point-in-solid, ray crossings, polylines), `csgfeatures.py` (features: cylinders from `--export-format=csg` with hull/minkowski masking), `printability.py` (facts only; no verdict, no "best" orientation), and an echo-based BOSL2 anchor probe (`_anchor_probe_body`)
- `validate` — `mode=syntax|geometry|predicates|includes|printability`; predicates take `sweep=`; includes runs `analysis.lint_use_shadowing` and applies safe `use`→`include` rewrites with `autofix=true`
- `scad_eval` — typed expression evaluation in the model's scope via echo read-back (`wrappers.eval_wrapper`, `parse_echo_values`)
- `reference` — static engineering data from `reference.py` with confidence labels; also exposed as resources and as server `instructions`
- `get_libraries` — discover installed OpenSCAD libraries
- `check_openscad` — verify OpenSCAD installation and version

**Project Support:**
- `get_project_files` — list .scad files and dependency graph in a directory
- `clear_cache` — manage the render cache

### Supporting modules

- **`assembly.py`** — the assembly model: `Part`/`Frame`/`Assembly`, check-file grammar (YAML/JSON), frame composition as SCAD prefix text, `part_body()` (`!union(){ placement { code } }`); expression-valued numbers (`EXPRESSION_KEYS`): string values under coordinate/limit keys are evaluated in model scope by `check` before the rules run (`collect_expression_slots` / `apply_expression_values`), a failed one marks the rule `_unresolved`
- **`checks.py`** — `RuleEngine` over `geom` meshes: interference/clearance/contact/predicate/probe/ray/sweep/alignment/print/mass rules, one row shape, `exit_code`
- **`geom.py`** — the one mesh kernel: BVH (leaf 1), exact tri–tri distance, generalized winding number (the point-in-solid primitive; ray parity is internal only), Möller–Trumbore with the behind-origin guard, coplanar contact area, penetration depth, sweeps, the (r,z) full-turn certificate
- **`csgfeatures.py`** — CSG-dump parser: cylinders with world transforms and polarity, hull/minkowski masking, stubs dropped, pattern grouping, fit candidates, cross-part alignment
- **`massprops.py` / `printability.py`** — exact inertia by tetrahedra; overhang with a bracket, thickness distribution by rays, islands by canonical slicing, support estimate, orientation candidates with a bed-contact floor
- **`analysis.py`** — static analysis: BOSL2 `$var` shadowing lint with rewrite plans and private shadow copies, constant dependency trace, section-expression validator, name-hashed stable colours
- **`parts_catalog.py` + `parts/*.scad`** — purchased-parts catalog (28BYJ-48, NEMA 17, lazy susan, lever microswitch, TCRT5000) with sources, confidence, `verify[]`, and generated BOSL2 attachables with named anchors and clearance masks (plain `difference()`, never `tag()/diff()`)
- **`threemf.py`** — multi-object 3MF writer
- **`wrappers.py`** — source-level wrappers. `hoist_source()` lifts the model's `include`/`use` lines to file scope (a library's `use <>` is a syntax error inside a module, so BOSL2 files broke otherwise) and `build_wrapper()` inlines the remaining text inside `module __model(){...}` with caller variables as trailing assignments (`-D` does not reach a module body). The wrapper file is written to the server temp dir (`_ModelSource.wrapper_file`, never into the user's project); the model's directory goes on OPENSCADPATH (`include_paths_for_wrapper`) and relative `import()`/`surface()` paths are rewritten absolute (`absolutize_file_refs`). Caller variables are injected at file scope *and* module scope: constants derived in a hoisted include are file-scope values. `!union(){...}` limits output to the wrapped operation. `WrappedSource.rebase_line` maps wrapper line numbers back to the model
- **`mesh.py`** — stdlib STL/SVG analysis (welding, union-find components, signed volumes, edge census)
- **`camera.py`** — orthographic camera model (`view_height_mm = 0.397825 * distance`, keyed to image height), `fit_camera`, Pillow annotation, spatial digest, part palette
- **`reference.py`** — sourced fits/fasteners/inserts/bearings/joints/DFM/materials data, `conventions_brief()` (server instructions), `cheatsheet()`
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
- **FunctionTool pattern**: on fastmcp 2.x `@mcp.tool()` wraps functions as `FunctionTool` objects (coroutine behind `.fn`); on fastmcp 4.x it returns the bare function. In tests use `render_fn = render.fn if hasattr(render, "fn") else render`; inside `server.py` call other tools through `_tool_fn(tool)(...)`, never `tool.fn(...)`.
- **Caching in tests**: When testing `render_scad_to_png` command construction, disable caching in the config to prevent cache hits from skipping subprocess calls
- ~1,500 tests. Markers that actually select something: `unit`, `config`, `integration`, `slow`, `performance`, `edge`, `render`. They are declared in the root `pytest.ini` and topped up by `pytest_configure` in `tests/conftest.py`; `--strict-markers` is on, so a new marker needs declaring in one of those two places
- Tests that need the real binary skip when OpenSCAD is absent. CI installs OpenSCAD 2021.01 and BOSL2 and runs under `xvfb-run` (PNG export on 2021.01 needs a display)

## Key Design Decisions

- **Flexible parameter parsing**: All input parsers accept multiple formats (string, list, dict, JSON) because AI assistants send parameters in unpredictable formats. This is intentional — don't simplify these parsers.
- **Exit code is not success**: on 2021.01 a failed `assert()`, an unknown module, a non-closed polyhedron and a missing include all exit 0. Every tool parses stderr and sets `success` from `Diagnostics.ok`; renders return the image *with* the errors.
- **`--hardwarnings` is off by default** (`rendering.hard_warnings`): it aborts evaluation at the first warning while exiting 0, blanking renders and truncating echo output. Warnings surface through diagnostics instead. Never add it back to echo-bearing paths.
- **`Volumes:` in the CGAL banner is not a body count**: a hollow shell and two disjoint cubes both report 3. Report it as `nef_volumes`; gate manifoldness on `Simple:` only.
- **Framing**: `render` auto-fits (`--autocenter --viewall`) unless `grounded=true`; the default is a single isometric view because each image costs ~640 vision tokens. Auto-fit destroys absolute scale, so the digest says "scale: unknown" unless grounded.
- **Wrapped modes cannot use `-D`**: for section/parts/eval the model text is inlined inside a module, so variables are injected as assignments appended to that module body (verified: `-D` is ignored there, appended assignments override silently). Never put `include`/`use` inside the module: hoist them.
- **`$preview` guards**: files that instantiate geometry only under `if ($preview)` export nothing; sections and measurements need the guard variable passed via `variables`.
- **Fix by private copy or rewrite**: when a toolchain limitation blocks a correct answer (e.g. BOSL2 `attach()` across `use <>`), the server may evaluate a patched private copy or, when there are no name collisions, rewrite the project file (`validate(mode=includes, autofix=true)`). Correctness outranks preserving formatting.
- **Tool surface budget**: 12 tools. A feature is a *mode* of an existing tool until it proves it needs a tool, because tool-selection accuracy degrades as the surface grows; `check` earned its slot because its subject is a relation between two parts and `mode=rules` is a distinct verb. `tests/test_correctness_fixes.py::TestToolSurfaceBudget` caps the total schema at 21,000 chars and any one tool at 3,600.
- **Response size management**: Large renders auto-save to files instead of returning base64 to avoid oversized MCP responses.
- **Camera format**: 6-value eye+center format (`--camera=eye_x,eye_y,eye_z,center_x,center_y,center_z`), not the 7-value translate+rotate format.
- **Render caching**: Enabled by default, validated against a per-entry dependency manifest (see Architecture). Cache stored in `~/.cache/openscad-mcp/`. Never cache a render without recording what it read.

## Tool Configuration

- **Ruff**: line-length 100, Python 3.10 target, rules: E, W, F, I, B, C4, UP, ARG, SIM
- **Black**: line-length 100
- **Mypy**: Python 3.10, `ignore_missing_imports = true`
- **Coverage**: 80% minimum (`--cov-fail-under=80` in the root `pytest.ini`, which is the config pytest picks up from the repo root)
- **Lint debt**: `ruff check src/ tests/` reports ~1,260 pre-existing findings (style rules) and `black --check` wants to reformat 18 files. CI gates on `ruff check --select F,E9,B src/openscad_mcp/`, which is clean and must stay clean. Write new code clean; do not reformat the tree wholesale in an unrelated change

## Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Package uses Hatchling build backend
- `uv` is the standard package manager (not pip)
