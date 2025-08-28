# OpenSCAD MCP Server

[![MCP Version](https://img.shields.io/badge/MCP-1.0.0-blue)](https://modelcontextprotocol.io)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.11.3-green)](https://github.com/jlowin/fastmcp)
[![Test Status](https://img.shields.io/badge/tests-100%25%20passing-brightgreen)](./openscad_mcp_test_report.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

A production-ready Model Context Protocol (MCP) server that provides OpenSCAD 3D rendering capabilities to AI assistants and LLM applications. Built with FastMCP for robust, scalable performance.

## 🎯 Zero-Installation Execution

**Run directly from GitHub without any setup:**
```bash
uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp
```

Perfect for MCP clients, CI/CD pipelines, and instant deployment scenarios!

## 🚀 Quick Start

### Zero-Installation Execution with `uv`

No installation required! Run directly from GitHub:

```bash
# Run via script entry point (recommended)
uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp

# Or run as Python module
uv run --with git+https://github.com/quellant/openscad-mcp.git python -m openscad_mcp
```

### Traditional Installation (Optional)

```bash
# Clone and run locally
git clone https://github.com/quellant/openscad-mcp.git
cd openscad-mcp
uv run openscad-mcp
```

### Configure with Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

#### Option 1: Direct from GitHub (Recommended)
```json
{
  "mcpServers": {
    "openscad": {
      "command": "uv",
      "args": ["run", "--with", "git+https://github.com/quellant/openscad-mcp.git", "openscad-mcp"],
      "env": {
        "OPENSCAD_PATH": "/usr/bin/openscad"
      }
    }
  }
}
```

#### Option 2: Local Installation
```json
{
  "mcpServers": {
    "openscad": {
      "command": "uv",
      "args": ["run", "openscad-mcp"],
      "cwd": "/path/to/openscad-mcp",
      "env": {
        "OPENSCAD_PATH": "/usr/bin/openscad"
      }
    }
  }
}
```

## 🔒 Security: --allowedTools Requirement

**IMPORTANT:** When using the OpenSCAD MCP server with Claude CLI or other MCP clients, you MUST explicitly grant permissions using the `--allowedTools` flag for security. This prevents unauthorized tool usage and ensures controlled access to server capabilities.

### Required Permissions

The OpenSCAD MCP server requires explicit permission for each tool:
- `openscad-mcp:check_openscad` - Permission to check OpenSCAD installation
- `openscad-mcp:render_single` - Permission to render single views
- `openscad-mcp:render_perspectives` - Permission to render multiple perspectives

### Example Usage with Claude CLI

```bash
# Start the server with uv and grant permissions
# (Create mcp-config.json with uv configuration first)

# Grant permission for a single tool
claude --config mcp-config.json \
       --allowedTools openscad-mcp:check_openscad \
       "Check if OpenSCAD is installed"

# Grant permission for multiple tools
claude --config mcp-config.json \
       --allowedTools openscad-mcp:check_openscad,openscad-mcp:render_single \
       "Render a cube with dimensions 30x30x30"

# Grant full permissions for all OpenSCAD tools
claude --config mcp-config.json \
       --allowedTools openscad-mcp:check_openscad,openscad-mcp:render_single,openscad-mcp:render_perspectives \
       "Create a gear model and show it from multiple angles"
```

**Create `mcp-config.json` for Claude CLI:**
```json
{
  "mcpServers": {
    "openscad-mcp": {
      "command": "uv",
      "args": ["run", "--with", "git+https://github.com/quellant/openscad-mcp.git", "openscad-mcp"],
      "env": {
        "OPENSCAD_PATH": "/usr/bin/openscad"
      }
    }
  }
}
```

### Testing with Permissions

Use the provided test script to verify proper permission configuration:

```bash
# Run all permission tests
./test_claude_with_permissions.sh all

# Test individual tools
./test_claude_with_permissions.sh check        # Test check_openscad only
./test_claude_with_permissions.sh single       # Test render_single only
./test_claude_with_permissions.sh perspectives # Test render_perspectives only

# Launch interactive session with full permissions
./test_claude_with_permissions.sh interactive

# Show usage examples
./test_claude_with_permissions.sh examples
```

## ✨ Features

- **🎯 Production-Ready**: 100% test success rate with comprehensive integration testing
- **🔧 Single View Rendering**: Render OpenSCAD models from any camera angle with full control
- **📐 Multiple Perspectives**: Generate standard orthographic and isometric views automatically
- **🎬 Animation Support**: Create turntable animations for 360° model visualization
- **⚡ Async Processing**: Non-blocking renders using FastMCP framework for optimal performance
- **🖼️ Base64 Encoding**: Images returned as base64-encoded PNGs for seamless integration
- **✅ Verified Compatibility**: Fully compatible with FastMCP 2.11.3 and Pydantic 2.11.7

## 📋 Prerequisites

- **Python 3.10+** (required for FastMCP 2.11.3)
- **OpenSCAD** ([Download from openscad.org](https://openscad.org))
- **uv** ([Install from docs.astral.sh](https://docs.astral.sh/uv/getting-started/installation/)) - *Only needed for zero-installation execution*

*No additional Python packages required when using `uv` - all dependencies are handled automatically!*

## 📦 Installation

### Method 1: Zero-Installation with `uv` (Recommended)

No installation required! Just run directly:

```bash
# Run directly from GitHub - uv handles everything automatically
uv run --with git+https://github.com/quellant/openscad-mcp.git openscad-mcp

# All dependencies (FastMCP 2.11.3, Pydantic 2.11.7, etc.) are resolved and installed automatically
```

### Method 2: Local Development with `uv`

```bash
# Clone for local development
git clone https://github.com/quellant/openscad-mcp.git
cd openscad-mcp

# Run with uv (installs dependencies automatically)
uv run openscad-mcp

# Or run as module
uv run python -m openscad_mcp
```

### Method 3: Traditional pip Installation

```bash
# Only if you can't use uv
git clone https://github.com/quellant/openscad-mcp.git
cd openscad-mcp
pip install -e .
python -m openscad_mcp
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
# OpenSCAD Configuration
OPENSCAD_PATH=/usr/bin/openscad  # Path to OpenSCAD executable
OPENSCAD_TIMEOUT=30               # Timeout for rendering operations (seconds)

# Server Configuration
MCP_SERVER_NAME=openscad-mcp
MCP_SERVER_VERSION=1.0.0
MCP_SERVER_TRANSPORT=stdio        # Transport: stdio, sse, or http

# Rendering Configuration
MCP_RENDER_MAX_CONCURRENT=4       # Max concurrent renders
MCP_RENDER_DEFAULT_WIDTH=800      # Default image width
MCP_RENDER_DEFAULT_HEIGHT=600     # Default image height
```

### Configuration File (Optional)

Create `config.yaml` for advanced configuration:

```yaml
server:
  name: "OpenSCAD MCP Server"
  version: "1.0.0"
  transport: stdio

openscad:
  timeout: 30
  default_color_scheme: "Cornfield"

rendering:
  max_concurrent: 4
  default_width: 800
  default_height: 600
```

## 🛠️ Available MCP Tools

### 1. `render_single`

Render a single view of an OpenSCAD model with complete camera control.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `scad_content` | string | No* | OpenSCAD code to render |
| `scad_file` | string | No* | Path to .scad file |
| `camera_position` | [x,y,z] | No | Camera position (default: [30,30,30]) |
| `camera_target` | [x,y,z] | No | Look-at point (default: [0,0,0]) |
| `camera_up` | [x,y,z] | No | Up vector (default: [0,0,1]) |
| `image_size` | [w,h] | No | Image dimensions in pixels |
| `color_scheme` | string | No | OpenSCAD color scheme |
| `variables` | dict | No | Variables to pass to OpenSCAD |
| `auto_center` | bool | No | Auto-center the model |

*Either `scad_content` or `scad_file` must be provided

**Example Request:**
```json
{
  "tool": "render_single",
  "arguments": {
    "scad_content": "cube([10, 10, 10]);",
    "camera_position": [30, 30, 30],
    "image_size": [800, 600],
    "color_scheme": "Cornfield"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "image": "data:image/png;base64,iVBORw0KGgo...",
  "metadata": {
    "width": 800,
    "height": 600,
    "render_time": 1.23
  }
}
```

### 2. `render_perspectives`

Generate multiple standard views of a model (front, back, left, right, top, bottom, isometric).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `scad_content` | string | No* | OpenSCAD code to render |
| `scad_file` | string | No* | Path to .scad file |
| `distance` | float | No | Camera distance from origin |
| `image_size` | [w,h] | No | Image dimensions for each view |
| `variables` | dict | No | Variables to pass to OpenSCAD |
| `views` | list | No | Specific views to render |

**Example Request:**
```json
{
  "tool": "render_perspectives",
  "arguments": {
    "scad_file": "model.scad",
    "views": ["front", "isometric", "top"],
    "distance": 200,
    "image_size": [400, 400]
  }
}
```

### 3. `check_openscad`

Verify OpenSCAD installation and get version information.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `include_paths` | bool | No | Include searched paths in response |

**Example Request:**
```json
{
  "tool": "check_openscad",
  "arguments": {
    "include_paths": true
  }
}
```

## 📚 MCP Resources

### `resource://server/info`

Get server configuration and capabilities.

**Returns:**
- Server version and status
- OpenSCAD version and path
- Rendering capabilities
- Active operations count
- Configuration details

## 🏗️ Architecture

The server implements a clean architecture pattern:

```
┌─────────────────────────────────────┐
│         MCP Client (Claude)         │
└─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────┐
│    FastMCP Server (Presentation)    │
│        - MCP Protocol Handler       │
│        - Tool Decorators            │
└─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────┐
│     Application Layer (Business)    │
│        - Rendering Logic            │
│        - Parameter Validation       │
└─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────┐
│   Infrastructure Layer (External)   │
│        - OpenSCAD CLI               │
│        - File I/O Operations        │
└─────────────────────────────────────┘
```

## 🧪 Testing

The server has achieved **100% test success rate** across all integration tests:

```bash
# Run comprehensive test suite
python comprehensive_test.py

# Run MCP tools integration test
python test_mcp_tools.py

# Run server startup test
python test_server_startup.py
```

See [Test Report](./openscad_mcp_test_report.md) for detailed results.

## 🔧 Development

### Project Structure

```
openscad-mcp-server/
├── src/
│   └── openscad_mcp/
│       ├── __init__.py         # Package initialization
│       ├── __main__.py          # Entry point
│       ├── server.py            # FastMCP server implementation
│       ├── types.py             # Pydantic models & types
│       └── utils/
│           └── config.py        # Configuration management
├── tests/                       # Test suite
├── pyproject.toml              # Project configuration
├── README.md                   # This file
├── CHANGELOG.md               # Version history
├── API.md                     # Detailed API documentation
└── .env.example               # Environment template
```

### Extending the Server

To add new rendering capabilities:

1. Define parameter types in `types.py`:
```python
class CustomRenderParams(BaseModel):
    # Your parameters here
    pass
```

2. Add tool function in `server.py`:
```python
@mcp.tool
async def custom_render(params: CustomRenderParams) -> RenderResult:
    """Your custom rendering logic"""
    # Implementation
    pass
```

3. Update documentation in `API.md`

## 🐛 Troubleshooting

### OpenSCAD Not Found

```bash
# Check OpenSCAD installation
which openscad

# Set environment variable
export OPENSCAD_PATH=/path/to/openscad

# Or add to .env file
OPENSCAD_PATH=/usr/local/bin/openscad
```

### Permission Errors

```bash
# Ensure execute permissions
chmod +x /path/to/openscad
```

### Rendering Timeouts

Increase timeout in configuration:
```bash
export OPENSCAD_TIMEOUT=60  # 60 seconds
```

### FastMCP Compatibility Issues

Ensure correct versions:
```bash
pip install fastmcp==2.11.3 pydantic==2.11.7
```

## 📈 Performance

- **Single view rendering**: ~1-3 seconds (complexity dependent)
- **Multiple perspectives**: ~5-10 seconds for 8 views
- **Base64 encoding**: < 0.1s overhead
- **Memory footprint**: ~50 MB base + 10-20 MB per render
- **Concurrent renders**: Up to 4 (configurable)

## 🚦 Production Status

✅ **Ready for Production**

- All tests passing (100% success rate)
- FastMCP 2.11.3 compatibility verified
- Comprehensive error handling
- Async operation support
- Resource management implemented
- Documentation complete

## 📝 License

MIT License - See [LICENSE](./LICENSE) file for details

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📮 Support

- **Issues**: [GitHub Issues](https://github.com/quellant/openscad-mcp/issues)
- **Documentation**: [Full API Documentation](./API.md)
- **Test Report**: [Integration Test Results](./openscad_mcp_test_report.md)

## 🙏 Acknowledgments

- [FastMCP](https://github.com/jlowin/fastmcp) - The excellent MCP framework
- [OpenSCAD](https://openscad.org) - The programmable CAD software
- [Model Context Protocol](https://modelcontextprotocol.io) - The MCP specification

---

**Version:** 1.0.0 | **Status:** Production Ready | **Last Updated:** 2025-08-25