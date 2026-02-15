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

### Claude Code

Add the server with a single command:

```bash
claude mcp add openscad --transport stdio -- \
  uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp
```

Or, if OpenSCAD is not on your PATH:

```bash
claude mcp add openscad --transport stdio \
  --env OPENSCAD_PATH=/path/to/openscad -- \
  uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp
```

Use the `--scope` flag to control where the configuration is saved:

| Scope | Flag | Effect |
|-------|------|--------|
| Local (default) | `--scope local` | Available only to you in the current project |
| Project | `--scope project` | Shared with the team via `.mcp.json` |
| User | `--scope user` | Available to you across all projects |

### Claude Desktop

Add to your configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "openscad": {
      "command": "uv",
      "args": [
        "run",
        "--with", "git+https://github.com/quellant/openscad-mcp.git",
        "openscad-mcp"
      ],
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
      "command": "uv",
      "args": [
        "run",
        "--with", "git+https://github.com/quellant/openscad-mcp.git",
        "openscad-mcp"
      ]
    }
  }
}
```

### Manual / Standalone

```bash
# Run directly from GitHub (no install required)
uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp

# Or clone and run locally
git clone https://github.com/quellant/openscad-mcp.git
cd openscad-mcp
uv run openscad-mcp
```

## Available Tools

### Rendering

| Tool | Description |
|------|-------------|
| `render_single` | Render a single view with camera control, quality presets, and view presets |
| `render_perspectives` | Render multiple standard views (front, back, left, right, top, bottom, isometric) in parallel |
| `compare_renders` | Side-by-side before/after renders for visual comparison |

### Export & Model Management

| Tool | Description |
|------|-------------|
| `export_model` | Export to STL, 3MF, AMF, OFF, DXF, or SVG |
| `create_model` | Create a new `.scad` file in the workspace |
| `get_model` | Read a model file and its metadata |
| `update_model` | Update an existing model's content |
| `list_models` | List all models in the workspace |
| `delete_model` | Delete a model file |

### Analysis & Validation

| Tool | Description |
|------|-------------|
| `validate_scad` | Syntax-check code without a full render (errors, warnings, echo output) |
| `analyze_model` | Compute bounding box, dimensions, and triangle count via STL export |
| `get_libraries` | Discover installed OpenSCAD libraries |
| `check_openscad` | Verify OpenSCAD installation and version |

### Project Support

| Tool | Description |
|------|-------------|
| `get_project_files` | List `.scad` files and their `include`/`use` dependency graph |
| `clear_cache` | Clear the render cache |

## Usage Examples

Once connected, ask your AI assistant:

- *"Render a cube with rounded edges"*
- *"Show me this model from all angles"*
- *"Export my gear model to STL"*
- *"Compare the model before and after changing the radius to 15"*
- *"Validate this OpenSCAD code for errors"*
- *"What are the dimensions of this model?"*

### Tool Parameters

#### `render_single`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scad_content` | string | — | OpenSCAD code to render* |
| `scad_file` | string | — | Path to `.scad` file* |
| `view` | string | — | Preset view: `front`, `back`, `left`, `right`, `top`, `bottom`, `isometric`, `dimetric` |
| `camera_position` | list/string | `[70,70,70]` | Camera eye position `[x,y,z]` |
| `camera_target` | list/string | `[0,0,0]` | Camera look-at point |
| `image_size` | list/string | `[800,600]` | Output dimensions `[w,h]` or `"800x600"` |
| `color_scheme` | string | `Cornfield` | OpenSCAD color scheme |
| `variables` | dict | `{}` | OpenSCAD `-D` variables |
| `quality` | string | — | `draft`, `normal`, or `high` |
| `include_paths` | list | — | Extra `-I` include directories |
| `output_format` | string | `auto` | `auto`, `base64`, `file_path`, or `compressed` |

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
  allowed_paths: null  # null = no restrictions
```

## Security

- **Path validation**: `scad_file` and `include_paths` validated against configurable `allowed_paths`
- **File size limits**: Content checked against `max_file_size_mb`
- **Variable name validation**: Only `^[a-zA-Z_][a-zA-Z0-9_]*$` allowed (prevents injection)
- **Subprocess timeout**: Configurable, default 300s
- **Model name validation**: Alphanumeric, hyphens, and underscores only; no path traversal

## Development

```bash
# Clone the repo
git clone https://github.com/quellant/openscad-mcp.git
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
uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp

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
