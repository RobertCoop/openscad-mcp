# OpenSCAD MCP Server

[![PyPI](https://img.shields.io/pypi/v/openscad-mcp)](https://pypi.org/project/openscad-mcp/)
[![CI](https://github.com/robertcoop/openscad-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/robertcoop/openscad-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/openscad-mcp)](https://pypi.org/project/openscad-mcp/)
[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io)
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets AI
assistants design 3D-printable parts and assemblies in [OpenSCAD](https://openscad.org):
render with a stated scale, measure exact geometry, check assemblies for interference
and clearance, extract holes and features, judge printability, and export. Built with
[FastMCP](https://gofastmcp.com) for Python; OpenSCAD 2021.01 is the supported floor
and dev snapshots are used when present.

## Prerequisites

- **[OpenSCAD](https://openscad.org/downloads.html)** installed on your system
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (recommended) or Python 3.10+

## Installation

The server is published on PyPI as `openscad-mcp`, so [uv](https://docs.astral.sh/uv/)
runs it with no clone and no virtualenv: `uvx openscad-mcp`. uv keeps a cached
copy; `uv tool upgrade openscad-mcp` (or `uvx openscad-mcp@latest`) pulls a
new release, and `uvx openscad-mcp@0.6.1` pins one.

### Claude Code

Add the server with a single command:

```bash
claude mcp add openscad --transport stdio -- uvx openscad-mcp
```

Or, if OpenSCAD is not on your PATH:

```bash
claude mcp add openscad --transport stdio \
  --env OPENSCAD_PATH=/path/to/openscad -- uvx openscad-mcp
```

Use the `--scope` flag to control where the configuration is saved:

| Scope | Flag | Effect |
|-------|------|--------|
| Local (default) | `--scope local` | Available only to you in the current project |
| Project | `--scope project` | Shared with the team via `.mcp.json` |
| User | `--scope user` | Available to you across all projects |

The repository is also a Claude Code plugin (skill plus server):
`/plugin marketplace add robertcoop/openscad-mcp` then
`/plugin install openscad-mcp@openscad-mcp`.

### Claude Desktop

Add to your configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "openscad": {
      "command": "uvx",
      "args": ["openscad-mcp"],
      "env": {
        "OPENSCAD_PATH": "/usr/bin/openscad"
      }
    }
  }
}
```

Then restart Claude Desktop.

### Cursor / Windsurf / VS Code

Add a `.mcp.json` file to your project root:

```json
{
  "mcpServers": {
    "openscad": {
      "command": "uvx",
      "args": ["openscad-mcp"]
    }
  }
}
```

### Manual / Standalone

```bash
# From PyPI (no install required)
uvx openscad-mcp

# The development version, straight from GitHub
uvx --from git+https://github.com/robertcoop/openscad-mcp.git openscad-mcp

# Or clone and run locally
git clone https://github.com/robertcoop/openscad-mcp.git
cd openscad-mcp
uv run openscad-mcp

# Run an assembly check file from a shell or a Makefile (exit code 0/1/2)
uvx openscad-mcp check checks.yaml --allow /path/to/project
```

## Available Tools

Every tool response carries `errors`, `warnings` and `hints` parsed from
OpenSCAD's output. Check them: OpenSCAD exits 0 on a failed `assert()` or an
unknown module and draws a blank scene.

### Rendering

| Tool | Description |
|------|-------------|
| `render` | Images with a text digest before each one (camera, view direction, scale, bbox). `mode=views` (one image per view, or a custom camera), `mode=section` (exact cross-section with a scale bar), `mode=parts` (each part in its own colour, `isolate` ghosts the rest), `mode=compare` (before/after). `grounded=true` gives an orthographic view with a stated mm/px scale; `annotate=true` adds a scale bar, axis triad and bbox dimensions |

### Assemblies

| Tool | Description |
|------|-------------|
| `check` | Relations between named parts, exported separately and never unioned: `mode=interference` (clear / contact / interference with penetration depth and a witness point), `clearance` (exact minimum distance with closest points), `contact` (area, normal, plane; `kind=static|sliding`), `alignment` (coaxial hole stacks across parts, misalignment, orphans), `motion` (rigid sweeps with a full-turn certificate), `rules` (run a versioned YAML/JSON check file; exit code 0/1/2). Every row carries the tessellation `$fn`, and distances inside its error bound are reported as unresolved rather than as numbers |

Parts are given inline as `parts=[{name, code, place?, frame?, ghost?, mass_g?, motion?}]` or in a check file (`frames`, `quality`, `parts`, `checks`, `model`). `openscad-mcp check <file.yaml>` runs a check file from the shell with a meaningful exit code, so `make check` is one call.

### Export & Model Management

| Tool | Description |
|------|-------------|
| `export_model` | Export to STL, 3MF, AMF, OFF, NEF3, DXF, SVG, PDF or CSG. With `parts=[...]` every part is exported in its assembly position and bundled into one 3MF with named objects (or a directory of STLs) |
| `model` | `action=create|get|update|list|delete` for `.scad` files in a workspace, with content-hash etags. `template="part:<id>"` writes a purchased-part module from the catalog |

### Measurement & Validation

| Tool | Description |
|------|-------------|
| `measure` | Exact numbers from the geometry: `model` (bbox, volume, area, components, watertight, `mesh_health`), `parts`, `section` (contours; the offset may be an expression in the model's scope), `mass` (grams; with `parts=` and `about_axis=` the assembly mass, centre of mass and inertia about an axis, with `mass_g` overrides for purchased parts), `probe` (solid/air and which part at points; ray crossings; line of sight along a polyline), `features` (holes from the CSG tree: axis, diameter, depth, through/blind, undersize at `$fn`, fit names), `printability` (overhang patches with unsupported reach, thickness distribution vs nozzle, islands, support estimate; facts only), `orientation` (candidate orientations, no winner chosen), `anchors` (BOSL2 anchor frames in the assembly frame). Accepts an existing STL/SVG via `mesh` |
| `validate` | `mode=syntax`, `geometry`, `predicates` (with `sweep={variable, values}` reporting the crossing), `includes` (references resolved or not, plus the BOSL2 lint: a module from a `use`d file placed by `attach()` is silently put at CENTER; `autofix=true` applies the rewrite when it is safe), `printability` (rules from the design-rule reference over measured facts) |
| `scad_eval` | Evaluate expressions in a model's variable scope and get typed values (number, vector, string, bool, range, undef) |
| `reference` | Sourced engineering data with confidence labels: fits (also bidirectional: `diameter_mm=3.3` names the hole, `shaft_mm`+`bore_mm` names the fit), metric fasteners, heat-set inserts, bearings, magnets, joints, a purchased-parts catalog with BOSL2 modules and clearance masks, FDM design rules, materials, OpenSCAD cheatsheet, conventions |
| `get_libraries` | Discover installed OpenSCAD libraries |
| `check_openscad` | Verify OpenSCAD installation, version and capabilities |

### Project Support

| Tool | Description |
|------|-------------|
| `get_project_files` | List `.scad` files and their references; `mode=trace` follows a constant through the project (what depends on it, what it depends on) |
| `clear_cache` | Clear the render cache |

## Usage Examples

Once connected, ask your AI assistant:

- *"Render a cube with rounded edges"*
- *"Show me the front and top of this model with a scale bar"*
- *"What is the volume and are there any cavities?"*
- *"Cut a section through the lid at z = 12 and tell me the wall thickness"*
- *"Colour the body and lid differently and ghost the body"*
- *"What clearance should I use for an M3 screw and a press-fit 608 bearing?"*
- *"Compare the model before and after changing the radius to 15"*
- *"Export my gear model to STL"*

The server also publishes MCP resources (`openscad://conventions`,
`openscad://cheatsheet`, `openscad://reference/{topic}`) and server
instructions with the coordinate and assembly conventions it expects. A
Claude Code skill lives in `skills/openscad-design/SKILL.md` and the repo can
be installed as a Claude Code plugin (`.claude-plugin/`).

### Tool Parameters

[API.md](./API.md) documents every tool and every parameter. Four parameters
account for most of the questions:

- **`parts`** — a list of `{"name": ..., "code": ..., "place": ...}` objects,
  accepted by `render(mode="parts")`, `measure(mode="parts")`, `check` and
  `export_model`. `code` is the statement that instantiates the part
  (`lid();`) and `place` is an optional OpenSCAD transform wrapped around it
  (`translate([0,0,20])`). Each part is exported on its own, so its identity
  survives; an assembly is never unioned. `measure(mode="parts")` measures each
  part in its placed position (the response says `frame: "assembly"`), the
  same grammar `check`, `render` and `export_model` use.
- **`quality`** — `draft`, `normal`, `high`, or an integer `$fn`. It sets
  `$fn`/`$fa`/`$fs` for the run. It is a correctness knob, not only a speed
  one: `check` reports a distance smaller than the tessellation error bound as
  `UNRESOLVED` rather than guessing, and the fix is a higher quality.
- **`variables`** — a dict injected as OpenSCAD variables. In the wrapped modes
  (section, parts, `scad_eval`) they are appended to the wrapped module body
  rather than passed with `-D`, and they are injected at file scope as well, so
  a constant derived inside an included file still sees them. This is how you
  set a `$preview` guard variable for an export.
- **`include_paths`** — extra directories added to `OPENSCADPATH`. When
  `MCP_ALLOWED_PATHS` is set, every entry is validated against it, as is every
  file OpenSCAD actually reads.

Exactly one of `scad_content` or `scad_file` is required by every tool that
takes source. All parameter parsers accept multiple input formats (JSON
strings, lists, dicts, CSV) for AI assistant compatibility.

Each image costs roughly 640 vision tokens at 800x600, and image sizes are
clamped to 1568 px on the long edge, above which vision models downscale
anyway. Ask for the views that answer a question rather than all of them.
Auto-fit renders (`grounded=false`) have no recoverable absolute scale, which
the digest states.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSCAD_PATH` | Path to OpenSCAD executable | Auto-detected |
| `MCP_TEMP_DIR` | Temporary file directory | `/tmp/openscad-mcp` |
| `MCP_TRANSPORT` | Transport type: `stdio`, `http`, `sse` | `stdio` |
| `MCP_HOST` | Host for HTTP/SSE transport | `localhost` |
| `MCP_PORT` | Port for HTTP/SSE transport | `8000` |
| `MCP_MAX_CONCURRENT_RENDERS` | Max parallel renders | `5` |
| `MCP_RENDER_TIMEOUT` | Render timeout in seconds | `300` |
| `MCP_CACHE_ENABLED` | Enable render caching | `true` |
| `MCP_CACHE_SIZE_MB` | Max cache size in MB | `500` |
| `MCP_CACHE_TTL_HOURS` | Cache TTL in hours | `24` |
| `MCP_LOG_LEVEL` | Logging level | `INFO` |
| `MCP_MAX_FILE_SIZE_MB` | Max SCAD file size | `10` |
| `MCP_ALLOWED_PATHS` | Directories scripts may read from (`os.pathsep`-separated) | unset = no validation |
| `MCP_MAX_MEMORY_MB` | Address-space limit per OpenSCAD process (POSIX), `0` disables | `4096` |
| `MCP_MAX_IMAGE_WIDTH` / `MCP_MAX_IMAGE_HEIGHT` | Render size clamp (aspect preserved) | `1568` |
| `MCP_HARD_WARNINGS` | Pass `--hardwarnings` to OpenSCAD (see Security) | `false` |

### YAML Configuration

Create a `config.yaml` for advanced configuration:

```yaml
server:
  name: "OpenSCAD MCP Server"
  version: "0.1.0"
  transport: stdio

rendering:
  max_concurrent: 5
  timeout_seconds: 300
  default_color_scheme: Cornfield

cache:
  enabled: true
  max_size_mb: 500
  ttl_hours: 24

security:
  rate_limit: 60
  max_file_size_mb: 10
  allowed_paths:          # null = no path validation at all (a warning is logged)
    - /home/me/projects/parts
  max_memory_mb: 4096
```

## Security

### Threat model

The server runs OpenSCAD on source it is handed. OpenSCAD can read any file
the process can read, through `include <>`, `use <>`, `import()` and
`surface()`, and can return what it read as echo output or as geometry. The
guarantees below hold **only when `allowed_paths` is configured**. Out of the
box it is unset, no path validation is performed, and the server logs a
warning at startup saying so.

What is enforced:

- **Path validation on arguments**: `scad_file`, `include_paths` (in every
  tool) and export `output_path` must lie inside `allowed_paths`. Containment
  uses resolved paths, so symlinks and `..` cannot escape.
- **Path validation on the dependency closure**: every file OpenSCAD actually
  read is recorded with `-d` and checked after the run. If any lies outside
  `allowed_paths`, the standard library directories, or the server temp dir,
  the output (image, mesh, echo text) is withheld and the call fails. This
  closes the `include <...>`-as-data and `surface(file=...)` channels.
- **Memory ceiling**: each OpenSCAD process runs under `RLIMIT_AS`
  (`max_memory_mb`, default 4 GB) on POSIX hosts. OpenSCAD has no ceiling of
  its own; a small `minkowski()` can otherwise consume all host memory.
- **Timeout**: `timeout_seconds`, default 300 s; partial stderr is kept.
- **Echo channel bounds**: `echo_output` is capped (200 lines, 2000 chars
  per line) and labelled as untrusted content from the rendered file.
- **File size limits**, **variable name validation**
  (`^\$?[a-zA-Z_][a-zA-Z0-9_]*$`) and **model name validation** (no path
  traversal) as before.

What is not enforced: no OS-level sandbox (no network isolation, no
filesystem namespace). For untrusted input run the server inside a
container or under Landlock/bubblewrap with only the project directory
mounted.

### Why `--hardwarnings` is off

`--hardwarnings` stops OpenSCAD at the first warning but still exits 0, so
it produced blank renders and silently truncated `echo_output` with no
indication. Warnings now reach the assistant through the structured
`warnings`, `errors` and `hints` fields on every tool response instead.
Set `MCP_HARD_WARNINGS=true` to restore the flag.

## Development

```bash
# Clone the repo
git clone https://github.com/robertcoop/openscad-mcp.git
cd openscad-mcp

# Install dependencies (the dev extra; there is no dependency-groups table,
# so `uv sync --dev` would remove pytest, ruff, black and mypy)
uv sync --extra dev

# Run the server
uv run openscad-mcp

# Run the assembly checker on a check file
uv run openscad-mcp check examples/checks/turntable.yaml

# Run tests
uv run pytest

# Lint & format. The tree carries pre-existing findings, so expect noise;
# CI gates only on `ruff check --select F,E9,B src/openscad_mcp/`.
uv run ruff check src/ tests/
uv run black --check src/ tests/

# Type check (also not clean today)
uv run mypy src/
```

### Project Structure

```
openscad-mcp/
├── src/openscad_mcp/
│   ├── server.py            # FastMCP server, the 12 tools, rendering, cache, CLI
│   ├── assembly.py          # Part/Frame/Assembly model and the check-file grammar
│   ├── checks.py            # RuleEngine: the rules a check file can ask for
│   ├── geom.py              # Mesh kernel: BVH, tri-tri distance, winding number, sweeps
│   ├── csgfeatures.py       # CSG-dump parser: holes, bosses, cross-part alignment
│   ├── massprops.py         # Mass, centre of mass, inertia by tetrahedra
│   ├── printability.py      # Overhangs, wall thickness, islands, orientation candidates
│   ├── analysis.py          # Static analysis: BOSL2 $var shadowing lint, constant tracing
│   ├── parts_catalog.py     # Purchased-parts catalog loader and self-check
│   ├── parts/*.scad         # One generated BOSL2 file per catalogued part
│   ├── threemf.py           # Multi-object 3MF writer
│   ├── wrappers.py          # Source-level wrappers: include hoisting, variable injection
│   ├── diagnostics.py       # stderr -> Diagnostics; -d deps parsing; repair hints
│   ├── camera.py            # Orthographic camera model, fit, annotation, spatial digest
│   ├── mesh.py              # Stdlib STL/SVG analysis: welding, components, volumes
│   ├── reference.py         # Sourced engineering data: fits, fasteners, inserts, DFM
│   ├── types.py             # Pydantic models and enums
│   └── utils/config.py      # Configuration with env/YAML/dotenv support
├── tests/                   # ~1,500 tests; 80% coverage floor
├── evals/                   # Deterministic geometry eval harness, 15 tasks
├── examples/checks/         # A worked check file and its model
├── skills/openscad-design/  # Claude Code skill: the design loop
└── .claude-plugin/          # Claude Code plugin manifest
```

### Testing

```bash
# Run all tests with coverage (about 1,500 tests, roughly a minute)
uv run pytest

# Run specific markers: unit, config, integration, slow, performance, edge, render
uv run pytest -m unit
uv run pytest -m performance
uv run pytest -m "not slow"

# Run a single file, without the coverage gate
uv run pytest tests/test_helpers.py -v --no-cov
```

Most tests mock the OpenSCAD subprocess, so no OpenSCAD installation is needed
to run the suite. A mock that stands in for a render has to write both output
files OpenSCAD would have written: the `-o` target and the `-d` dependency file
that the cache manifest is built from. Tests that do need the real binary skip
themselves when it is absent.

CI runs the suite on Python 3.10 and 3.12 with OpenSCAD 2021.01 and BOSL2
installed, under `xvfb-run` because PNG export on 2021.01 needs a display. It
also builds the wheel, installs it in a clean environment, and asserts that a
client sees exactly 12 tools. Coverage floor: 80%.

## Troubleshooting

### OpenSCAD Not Found

```bash
# Check if OpenSCAD is installed
which openscad        # Linux/macOS
where openscad.exe    # Windows

# Set the path explicitly
export OPENSCAD_PATH=/path/to/openscad
```

### Server Not Connecting

```bash
# Verify the server starts correctly
uvx openscad-mcp

# In Claude Code, check MCP status
/mcp
```

### Render Timeout

Increase the timeout:

```bash
export MCP_RENDER_TIMEOUT=600
```

### "Path not allowed" / "outside the allowed paths"

`MCP_ALLOWED_PATHS` is set and the file is not under one of its roots. The
check covers arguments and, separately, every file OpenSCAD actually opened, so
a model that lives inside an allowed root but does `include <../shared/lib.scad>`
outside it is refused and the output is withheld. Add both roots:

```bash
export MCP_ALLOWED_PATHS="$HOME/cad:$HOME/cad-shared"
```

Leaving the variable unset disables path validation entirely, which the server
logs a warning about at startup. See [Threat model](#threat-model).

### An export or a measurement comes back empty

The usual cause is a `$preview` guard: a file that instantiates its geometry
only inside `if ($preview)` renders in the GUI and exports nothing, because
`$preview` is false for a render to a file. Pass the guard variable explicitly:

```
measure(scad_file="part.scad", variables={"$preview": true})
```

A `difference()` whose first child is smaller than what follows also yields
nothing. `validate(mode="syntax")` will not catch either one; the exit code is
0 in both cases.

### `check` rows say `UNRESOLVED`

The distance in question is smaller than the tessellation error of the mesh, so
the answer would be an artefact of `$fn` rather than of the design. Re-run with
`quality="high"` or an explicit integer `$fn`. Every row reports the `quality.fn`
it was computed at.

### A result looks stale

Renders are cached under `~/.cache/openscad-mcp/`, keyed on every render
parameter plus a manifest of every file OpenSCAD read. Editing an included file
normally invalidates the entry, but if a response says `cached: true` and the
number disagrees with the source, clear it:

```
clear_cache()
```

### Renders fail on a headless machine

OpenSCAD 2021.01 needs a display to export PNG even in headless mode. Run the
server under a virtual framebuffer:

```bash
xvfb-run -a uv run openscad-mcp
```

Exports, measurements and checks produce meshes rather than images and do not
need a display.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Ensure tests pass (`uv run pytest`)
5. Open a Pull Request

Commit style: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

[CONTRIBUTING.md](./CONTRIBUTING.md) covers the module map, how the tests mock
OpenSCAD, the design rules that should not be undone, and how to add a
parts-catalog entry or a check rule.

## License

MIT — see [LICENSE](./LICENSE)

## Acknowledgments

- [OpenSCAD](https://openscad.org) — Programmable CAD software. Everything here
  is a wrapper around its CLI.
- [FastMCP](https://gofastmcp.com) — Python MCP framework
- [Model Context Protocol](https://modelcontextprotocol.io) — The MCP specification
- [BOSL2](https://github.com/BelfrySCAD/BOSL2) — Used, not vendored, by the
  purchased-parts catalog and by the BOSL2 anchor probe. BSD-2-Clause; install
  it separately.
- Dimensional data in `reference` and `parts` is cited entry by entry, with a
  confidence label on every number. See
  [src/openscad_mcp/parts/README.md](./src/openscad_mcp/parts/README.md).
