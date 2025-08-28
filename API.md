# OpenSCAD MCP Server API Documentation

## Table of Contents

- [Overview](#overview)
- [MCP Protocol](#mcp-protocol)
- [Authentication](#authentication)
- [Tools](#tools)
  - [render_single](#render_single)
  - [render_perspectives](#render_perspectives)
  - [check_openscad](#check_openscad)
- [Resources](#resources)
  - [server/info](#serverinfo)
- [Data Types](#data-types)
- [Error Handling](#error-handling)
- [Examples](#examples)
- [Performance Considerations](#performance-considerations)

## Overview

The OpenSCAD MCP Server provides a Model Context Protocol interface for rendering OpenSCAD 3D models. It exposes tools for single and multi-view rendering, as well as system verification capabilities.

### Base Information

- **Protocol Version**: MCP 1.0
- **Server Version**: 1.0.0
- **Transport**: stdio (default), HTTP/SSE (configurable)
- **Framework**: FastMCP 2.11.3

## MCP Protocol

The server implements the standard MCP protocol for tool and resource discovery, invocation, and result handling.

### Connection Methods

#### 1. Standard Input/Output (stdio)

```python
# Direct invocation
python -m openscad_mcp

# Via FastMCP CLI
fastmcp run openscad_mcp/server.py
```

#### 2. HTTP/SSE Transport

```python
# Set transport mode
export MCP_SERVER_TRANSPORT=sse
export MCP_SERVER_HOST=0.0.0.0
export MCP_SERVER_PORT=3000

# Run server
python -m openscad_mcp
```

### Protocol Messages

#### List Tools Request
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```

#### List Tools Response
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "render_single",
        "description": "Render a single view of an OpenSCAD model",
        "inputSchema": { ... }
      },
      {
        "name": "render_perspectives",
        "description": "Render multiple standard views",
        "inputSchema": { ... }
      },
      {
        "name": "check_openscad",
        "description": "Check OpenSCAD installation",
        "inputSchema": { ... }
      }
    ]
  }
}
```

## Authentication

Currently, the server does not implement authentication. For production deployments requiring authentication:

1. Use environment variables for API keys
2. Implement transport-level security (HTTPS for HTTP/SSE)
3. Add custom authentication middleware if needed

## Tools

### render_single

Renders a single view of an OpenSCAD model with full camera control.

#### Request Schema

```typescript
interface RenderSingleRequest {
  // Model source (one required)
  scad_content?: string;  // OpenSCAD code as string
  scad_file?: string;     // Path to .scad file
  
  // Camera parameters
  camera_position?: [number, number, number];  // Default: [30, 30, 30]
  camera_target?: [number, number, number];    // Default: [0, 0, 0]
  camera_up?: [number, number, number];        // Default: [0, 0, 1]
  
  // Rendering options
  image_size?: [number, number];  // [width, height], Default: [800, 600]
  color_scheme?: string;           // OpenSCAD color scheme name
  variables?: Record<string, any>; // Variables for parametric models
  auto_center?: boolean;           // Auto-center the model
}
```

#### Response Schema

```typescript
interface RenderSingleResponse {
  success: boolean;
  image?: string;        // Base64-encoded PNG image
  error?: string;        // Error message if failed
  metadata?: {
    width: number;
    height: number;
    render_time: number; // Seconds
    color_scheme: string;
  };
}
```

#### Example Usage

```python
# Python client example
from fastmcp import Client

async with Client("openscad-mcp-server") as client:
    result = await client.call_tool("render_single", {
        "scad_content": """
            $fn = 100;
            difference() {
                sphere(r=20);
                cylinder(h=40, r=10, center=true);
            }
        """,
        "camera_position": [50, 50, 30],
        "camera_target": [0, 0, 0],
        "image_size": [1024, 768],
        "color_scheme": "BeforeDawn"
    })
    
    # Save the image
    import base64
    image_data = base64.b64decode(result.data["image"].split(",")[1])
    with open("output.png", "wb") as f:
        f.write(image_data)
```

#### Camera Positioning Guide

- **Isometric View**: `camera_position: [30, 30, 30]`
- **Front View**: `camera_position: [0, -50, 0]`
- **Top View**: `camera_position: [0, 0, 50]`
- **Side View**: `camera_position: [50, 0, 0]`

### render_perspectives

Renders multiple standard views of a model in a single operation.

#### Request Schema

```typescript
interface RenderPerspectivesRequest {
  // Model source (one required)
  scad_content?: string;
  scad_file?: string;
  
  // Rendering parameters
  distance?: number;              // Camera distance, Default: 100
  image_size?: [number, number];  // Size for each view
  variables?: Record<string, any>;
  views?: string[];               // Subset of views to render
}
```

#### Available Views

- `"front"` - Front orthographic view
- `"back"` - Back orthographic view  
- `"left"` - Left orthographic view
- `"right"` - Right orthographic view
- `"top"` - Top orthographic view
- `"bottom"` - Bottom orthographic view
- `"isometric"` - Isometric 3D view
- `"dimetric"` - Dimetric 3D view

If `views` is not specified, all 8 views are rendered.

#### Response Schema

```typescript
interface RenderPerspectivesResponse {
  success: boolean;
  images?: {
    [viewName: string]: string;  // Base64-encoded PNG for each view
  };
  error?: string;
  metadata?: {
    total_views: number;
    render_time: number;
    image_size: [number, number];
  };
}
```

#### Example Usage

```python
# Render specific views
result = await client.call_tool("render_perspectives", {
    "scad_file": "complex_model.scad",
    "views": ["front", "isometric", "top"],
    "distance": 150,
    "image_size": [400, 400],
    "variables": {
        "height": 50,
        "width": 30
    }
})

# Access individual images
for view_name, image_data in result.data["images"].items():
    save_image(f"{view_name}.png", image_data)
```

### check_openscad

Verifies OpenSCAD installation and provides system information.

#### Request Schema

```typescript
interface CheckOpenSCADRequest {
  include_paths?: boolean;  // Include searched paths in response
}
```

#### Response Schema

```typescript
interface CheckOpenSCADResponse {
  success: boolean;
  installed: boolean;
  version?: string;          // OpenSCAD version string
  path?: string;            // Path to OpenSCAD executable
  searched_paths?: string[]; // If include_paths is true
  features?: {
    animation: boolean;
    customizer: boolean;
    manifold: boolean;
  };
}
```

#### Example Usage

```python
# Check installation
result = await client.call_tool("check_openscad", {
    "include_paths": true
})

if result.data["installed"]:
    print(f"OpenSCAD {result.data['version']} found at {result.data['path']}")
else:
    print("OpenSCAD not found. Searched paths:")
    for path in result.data["searched_paths"]:
        print(f"  - {path}")
```

## Resources

### server/info

Provides server configuration and runtime information.

#### URI

`resource://server/info`

#### Response Schema

```typescript
interface ServerInfoResource {
  server: {
    name: string;
    version: string;
    transport: string;
    uptime: number;  // Seconds
  };
  openscad: {
    version: string;
    path: string;
    timeout: number;
  };
  capabilities: {
    max_concurrent_renders: number;
    supported_formats: string[];
    available_color_schemes: string[];
  };
  statistics: {
    total_renders: number;
    active_renders: number;
    failed_renders: number;
    average_render_time: number;
  };
}
```

#### Example Usage

```python
# Get server information
async with Client("openscad-mcp-server") as client:
    info = await client.read_resource("resource://server/info")
    print(f"Server: {info['server']['name']} v{info['server']['version']}")
    print(f"OpenSCAD: {info['openscad']['version']}")
    print(f"Active renders: {info['statistics']['active_renders']}")
```

## Data Types

### Pydantic Models

The server uses Pydantic for type validation and serialization:

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class Vector3D(BaseModel):
    """3D vector for positions and directions"""
    x: float
    y: float
    z: float

class ImageSize(BaseModel):
    """Image dimensions"""
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)

class RenderParams(BaseModel):
    """Base rendering parameters"""
    scad_content: Optional[str] = None
    scad_file: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "scad_content": "cube([10, 10, 10]);",
                    "variables": {"size": 20}
                }
            ]
        }
    }

class SingleRenderParams(RenderParams):
    """Parameters for single view rendering"""
    camera_position: Optional[List[float]] = Field(
        default=[30, 30, 30],
        min_items=3,
        max_items=3
    )
    camera_target: Optional[List[float]] = Field(
        default=[0, 0, 0],
        min_items=3,
        max_items=3
    )
    camera_up: Optional[List[float]] = Field(
        default=[0, 0, 1],
        min_items=3,
        max_items=3
    )
    image_size: Optional[List[int]] = Field(
        default=[800, 600],
        min_items=2,
        max_items=2
    )
    color_scheme: Optional[str] = None
    auto_center: Optional[bool] = False

class PerspectiveParams(RenderParams):
    """Parameters for multi-view rendering"""
    distance: Optional[float] = Field(default=100, gt=0)
    image_size: Optional[List[int]] = Field(
        default=[400, 400],
        min_items=2,
        max_items=2
    )
    views: Optional[List[str]] = None
```

## Error Handling

The server implements comprehensive error handling with specific error codes and messages.

### Error Response Format

```typescript
interface ErrorResponse {
  success: false;
  error: string;
  error_code?: string;
  details?: {
    field?: string;
    reason?: string;
    suggestion?: string;
  };
}
```

### Error Codes

| Code | Description | HTTP Status |
|------|-------------|-------------|
| `OPENSCAD_NOT_FOUND` | OpenSCAD executable not found | 503 |
| `INVALID_SCAD_SOURCE` | No valid SCAD content or file provided | 400 |
| `FILE_NOT_FOUND` | Specified SCAD file doesn't exist | 404 |
| `RENDER_TIMEOUT` | Rendering exceeded timeout limit | 504 |
| `RENDER_FAILED` | OpenSCAD rendering failed | 500 |
| `INVALID_PARAMETERS` | Invalid tool parameters | 400 |
| `CONCURRENT_LIMIT` | Max concurrent renders exceeded | 429 |

### Error Examples

```json
// OpenSCAD not found
{
  "success": false,
  "error": "OpenSCAD executable not found",
  "error_code": "OPENSCAD_NOT_FOUND",
  "details": {
    "suggestion": "Install OpenSCAD from https://openscad.org or set OPENSCAD_PATH environment variable"
  }
}

// Invalid parameters
{
  "success": false,
  "error": "Invalid camera position",
  "error_code": "INVALID_PARAMETERS",
  "details": {
    "field": "camera_position",
    "reason": "Must be an array of 3 numbers",
    "suggestion": "Use format [x, y, z], e.g., [30, 30, 30]"
  }
}

// Render timeout
{
  "success": false,
  "error": "Rendering exceeded 30 second timeout",
  "error_code": "RENDER_TIMEOUT",
  "details": {
    "suggestion": "Simplify the model or increase OPENSCAD_TIMEOUT"
  }
}
```

## Examples

### Basic Cube Rendering

```python
# Simple cube with default settings
result = await client.call_tool("render_single", {
    "scad_content": "cube([20, 20, 20]);"
})
```

### Parametric Model with Variables

```python
# Parametric gear model
result = await client.call_tool("render_single", {
    "scad_file": "parametric_gear.scad",
    "variables": {
        "teeth": 24,
        "module": 2,
        "pressure_angle": 20,
        "thickness": 10
    },
    "camera_position": [100, 100, 50],
    "image_size": [1920, 1080]
})
```

### Batch Rendering Multiple Views

```python
# Render all views of a complex model
views_needed = ["front", "back", "left", "right", "top", "bottom", "isometric"]

result = await client.call_tool("render_perspectives", {
    "scad_file": "assembly.scad",
    "views": views_needed,
    "distance": 200,
    "image_size": [512, 512]
})

# Process all images
for view, image_data in result.data["images"].items():
    process_image(view, image_data)
```

### Animation Frame Rendering

```python
# Render animation frames (sequential single renders)
frames = []
for angle in range(0, 360, 10):
    result = await client.call_tool("render_single", {
        "scad_content": f"""
            $vpr = [60, 0, {angle}];
            cube([20, 20, 20]);
        """,
        "image_size": [640, 480]
    })
    frames.append(result.data["image"])

# Create animation from frames
create_gif(frames, "rotation.gif")
```

### Complex Model with Custom Color Scheme

```python
# Advanced rendering with custom settings
result = await client.call_tool("render_single", {
    "scad_content": """
        $fn = 200;  // High resolution
        
        module complex_shape() {
            difference() {
                sphere(r=30);
                for(i = [0:5]) {
                    rotate([0, 0, i*60])
                    translate([25, 0, 0])
                    cylinder(h=60, r=10, center=true);
                }
            }
        }
        
        complex_shape();
    """,
    "camera_position": [80, 60, 40],
    "camera_target": [0, 0, 0],
    "image_size": [2048, 1536],
    "color_scheme": "Tomorrow Night",
    "auto_center": true
})
```

## Performance Considerations

### Optimization Tips

1. **Resolution Control**
   ```scad
   // Use appropriate $fn values
   $fn = 50;  // Low resolution for previews
   $fn = 200; // High resolution for final renders
   ```

2. **Image Size**
   - Previews: 400x400 to 800x600
   - Final renders: 1920x1080 to 4096x4096
   - Batch operations: Keep uniform sizes

3. **Concurrent Rendering**
   ```python
   # Configure max concurrent renders
   os.environ["MCP_RENDER_MAX_CONCURRENT"] = "4"
   ```

4. **Timeout Configuration**
   ```python
   # Increase timeout for complex models
   os.environ["OPENSCAD_TIMEOUT"] = "60"  # 60 seconds
   ```

### Performance Metrics

| Operation | Simple Model | Complex Model | Notes |
|-----------|-------------|---------------|-------|
| Single View | 0.5-1s | 2-5s | Depends on $fn and complexity |
| 8 Perspectives | 4-8s | 16-40s | Parallel rendering helps |
| Base64 Encoding | <0.05s | <0.1s | Negligible overhead |
| File I/O | <0.01s | <0.05s | SSD recommended |

### Memory Usage

- Base server: ~50 MB
- Per render: +10-20 MB
- Peak (4 concurrent): ~130 MB
- Image cache: Not implemented (v1.0.0)

### Scaling Recommendations

1. **Horizontal Scaling**
   - Run multiple server instances
   - Use load balancer for distribution
   - Share file system for .scad files

2. **Vertical Scaling**
   - Increase CPU cores for parallel renders
   - Add RAM for larger models
   - Use GPU acceleration (future feature)

3. **Caching Strategy** (Future)
   - Cache rendered images by hash
   - Implement TTL for cache entries
   - Use Redis for distributed cache

## Rate Limiting

The server implements basic rate limiting:

- Max concurrent renders: 4 (configurable)
- Queue size: 100 requests
- Timeout per render: 30s (configurable)

For production deployments, consider:
- API rate limiting per client
- Request throttling
- Priority queues for different clients

## Logging

The server provides comprehensive logging:

```python
# Log levels
export LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR

# Log format
2025-08-25 12:00:00 INFO: Server started on stdio transport
2025-08-25 12:00:01 DEBUG: Received tool call: render_single
2025-08-25 12:00:02 INFO: Rendering completed in 1.23s
```

## Monitoring

Key metrics to monitor:

- Render success/failure rate
- Average render time
- Queue depth
- Memory usage
- OpenSCAD process count

## Security Considerations

1. **Input Validation**
   - All parameters validated with Pydantic
   - File paths restricted to allowed directories
   - SCAD code size limited

2. **Resource Limits**
   - Timeout protection
   - Memory limits
   - Concurrent render limits

3. **File System Access**
   - Read-only access to SCAD files
   - Temporary files in isolated directory
   - Automatic cleanup of temp files

## Version Compatibility

| Component | Minimum | Recommended | Notes |
|-----------|---------|-------------|-------|
| Python | 3.9 | 3.11+ | Async improvements |
| FastMCP | 2.11.3 | 2.11.3 | Required |
| Pydantic | 2.11.7 | 2.11.7 | Required |
| OpenSCAD | 2019.05 | 2021.01+ | Latest features |

## Migration Guide

### From Other MCP Servers

1. Update tool names in client code
2. Adapt parameter formats
3. Update image processing for base64 format
4. Adjust error handling

### From Direct OpenSCAD CLI

```bash
# Old CLI approach
openscad -o output.png --camera=0,0,0,50,50,30,0 model.scad

# New MCP approach
result = await client.call_tool("render_single", {
    "scad_file": "model.scad",
    "camera_position": [50, 50, 30],
    "camera_target": [0, 0, 0]
})
```

## Support

For issues or questions:
- GitHub Issues: [Report bugs](https://github.com/yourusername/openscad-mcp-server/issues)
- Documentation: [README](./README.md) | [CHANGELOG](./CHANGELOG.md)
- Examples: See `/examples` directory in repository

---

**API Version:** 1.0.0 | **Last Updated:** 2025-08-25