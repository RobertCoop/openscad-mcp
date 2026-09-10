# OpenSCAD MCP Server

[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.14.5-green)](https://gofastmcp.com)
[![Tests](https://img.shields.io/badge/tests-300%20passing-brightgreen)](#testing)
[![Coverage](https://img.shields.io/badge/coverage-80%25-brightgreen)](#testing)
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that gives AI assistants the ability to render, export, and analyze 3D models using [OpenSCAD](https://openscad.org). Built with [FastMCP](https://gofastmcp.com) for Python.

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

#### `render`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scad_content` | string | — | OpenSCAD code to render* |
| `scad_file` | string | — | Path to `.scad` file* |
| `mode` | string | `views` | `views`, `section`, `parts`, `compare` |
| `views` | list | `["isometric"]` | Any of `front`, `back`, `left`, `right`, `top`, `bottom`, `isometric`, `dimetric` |
| `camera_position` / `camera_target` / `camera_up` | list/string | — | Custom camera (used when `views` is omitted) |
| `grounded` | bool | `false` | Measure the model, then render orthographically with a stated mm/px scale |
| `annotate` | bool | `false` | Scale bar, axis triad, bbox dimensions (implies `grounded`) |
| `section_axis` / `section_offset` | string / number | `z` / `0` | Cut plane for `mode=section` |
| `parts` / `isolate` | list / string | — | `[{"name": "lid", "code": "lid();"}]` for `mode=parts` |
| `variables_after` / `scad_content_after` | dict / string | — | The "after" side for `mode=compare` |
| `image_size` | list/string | `[800,600]` | Output dimensions, clamped to 1568 px |
| `color_scheme` | string | `Cornfield` | OpenSCAD color scheme |
| `variables` | dict | `{}` | OpenSCAD variables |
| `quality` | string | — | `draft`, `normal`, or `high` |
| `include_paths` | list | — | Extra include directories (via `OPENSCADPATH`) |

Each image costs roughly 640 vision tokens at 800x600; ask for the views that
answer a question rather than all of them. Auto-fit renders (`grounded=false`)
have no recoverable absolute scale, which the digest states.

*Exactly one of `scad_content` or `scad_file` must be provided.

All parameter parsers accept multiple input formats (JSON strings, lists, dicts, CSV) for AI assistant compatibility.

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

# Install dependencies
uv sync --dev

# Run the server
uv run openscad-mcp

# Run tests
uv run pytest

# Lint & format
uv run ruff check src/ tests/
uv run black --check src/ tests/

# Type check
uv run mypy src/
```

### Project Structure

```
openscad-mcp/
├── src/openscad_mcp/
│   ├── __init__.py          # Package exports
│   ├── server.py            # FastMCP server, all MCP tools and helpers
│   ├── types.py             # Pydantic models and enums
│   └── utils/
│       └── config.py        # Configuration with env/YAML/dotenv support
├── tests/                   # 300 tests, 80%+ coverage
├── pyproject.toml
└── README.md
```

### Testing

```bash
# Run all tests with coverage
uv run pytest

# Run specific markers
uv run pytest -m unit
uv run pytest -m performance

# Run a single file
uv run pytest tests/test_helpers.py -v
```

Tests mock the OpenSCAD subprocess — no OpenSCAD installation required to run them. Coverage target: 80% minimum.

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

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Ensure tests pass (`uv run pytest`)
5. Open a Pull Request

Commit style: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

## License

MIT — see [LICENSE](./LICENSE)

## Acknowledgments

- [FastMCP](https://gofastmcp.com) — Python MCP framework
- [OpenSCAD](https://openscad.org) — Programmable CAD software
- [Model Context Protocol](https://modelcontextprotocol.io) — The MCP specification
