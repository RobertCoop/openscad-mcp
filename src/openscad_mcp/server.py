"""
Main FastMCP server implementation for OpenSCAD rendering.
"""

import asyncio
import base64
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple

from fastmcp import Context, FastMCP
# from fastmcp.types import Tool  # This import is not needed for the current FastMCP version
from PIL import Image
import json

from .types import (
    ColorScheme,
    ImageSize,
    OpenSCADInfo,
    RenderResult,
    ServerInfo,
    Vector3D,
)
from .utils.config import get_config


# Initialize the FastMCP server
mcp = FastMCP("OpenSCAD MCP Server")


# ============================================================================
# Helper Functions
# ============================================================================


def find_openscad() -> Optional[str]:
    """Find OpenSCAD executable on the system."""
    config = get_config()
    
    # Check configured path first
    if config.openscad_path and Path(config.openscad_path).exists():
        return config.openscad_path
    
    # Common OpenSCAD executable names
    candidates = ["openscad", "OpenSCAD", "openscad.exe"]
    
    for cmd in candidates:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, check=False)
            return cmd
        except FileNotFoundError:
            continue
    
    # Check common installation paths
    common_paths = [
        "/usr/bin/openscad",
        "/usr/local/bin/openscad",
        "/snap/bin/openscad",
        "/var/lib/flatpak/exports/bin/org.openscad.OpenSCAD",
        "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
        "C:\\Program Files\\OpenSCAD\\openscad.exe",
        "C:\\Program Files (x86)\\OpenSCAD\\openscad.exe",
    ]
    
    for path in common_paths:
        if Path(path).exists():
            return path
    
    return None


def render_scad_to_png(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    camera_position: List[float] = [70, 70, 70],
    camera_target: List[float] = [0, 0, 0],
    camera_up: List[float] = [0, 0, 1],
    image_size: List[int] = [800, 600],
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
) -> str:
    """
    Render OpenSCAD code or file to PNG and return as base64.
    
    This is a simplified synchronous implementation for the MVP.
    """
    openscad_cmd = find_openscad()
    if not openscad_cmd:
        raise RuntimeError("OpenSCAD not found. Please install OpenSCAD first.")
    
    config = get_config()
    
    # Ensure temp directory exists
    temp_dir_path = Path(config.temp_dir)
    temp_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Create temporary files
    with tempfile.TemporaryDirectory(dir=config.temp_dir) as temp_dir:
        temp_path = Path(temp_dir)
        
        # Handle input source
        if scad_content:
            scad_path = temp_path / "input.scad"
            scad_path.write_text(scad_content)
        elif scad_file:
            scad_path = Path(scad_file)
            if not scad_path.exists():
                raise FileNotFoundError(f"SCAD file not found: {scad_file}")
        else:
            raise ValueError("Either scad_content or scad_file must be provided")
        
        # Output path
        output_path = temp_path / "output.png"
        
        # Build OpenSCAD command
        cmd = [
            openscad_cmd,
            "-o", str(output_path),
            "--imgsize", f"{image_size[0]},{image_size[1]}",
            "--colorscheme", color_scheme,
        ]
        
        # Calculate camera distance
        import math
        dx = camera_position[0] - camera_target[0]
        dy = camera_position[1] - camera_target[1]
        dz = camera_position[2] - camera_target[2]
        distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        # Add camera parameters
        camera_str = (
            f"--camera="
            f"{camera_position[0]},{camera_position[1]},{camera_position[2]},"
            f"{camera_target[0]},{camera_target[1]},{camera_target[2]},"
            f"{distance}"
        )
        cmd.append(camera_str)
        
        if auto_center:
            cmd.append("--autocenter")
            cmd.append("--viewall")
        
        # Add variables
        if variables:
            for key, value in variables.items():
                if isinstance(value, str):
                    val_str = f'"{value}"'
                elif isinstance(value, bool):
                    val_str = "true" if value else "false"
                else:
                    val_str = str(value)
                cmd.extend(["-D", f"{key}={val_str}"])
        
        # Add the SCAD file
        cmd.append(str(scad_path))
        
        # Run OpenSCAD
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            raise RuntimeError(f"OpenSCAD rendering failed: {result.stderr}")
        
        if not output_path.exists():
            raise RuntimeError("OpenSCAD did not produce output file")
        
        # Read and encode the image
        with open(output_path, "rb") as f:
            image_data = f.read()
        
        # Return base64-encoded PNG
        return base64.b64encode(image_data).decode("utf-8")


# ============================================================================
# MCP Tools
# ============================================================================


def parse_camera_param(param: Union[str, List[float], Dict[str, float], None], default: List[float]) -> List[float]:
    """
    Parse camera parameters from various input formats.
    
    Accepts:
    - List of floats: [x, y, z]
    - JSON string: "[x, y, z]" or '{"x": x, "y": y, "z": z}'
    - Dict: {"x": x, "y": y, "z": z}
    - None: returns default
    """
    if param is None:
        return default
    
    # If it's already a list, return it
    if isinstance(param, list):
        if len(param) == 3:
            return [float(v) for v in param]
        else:
            raise ValueError(f"Expected 3 values for camera parameter, got {len(param)}")
    
    # If it's a dict with x, y, z keys
    if isinstance(param, dict):
        if "x" in param and "y" in param and "z" in param:
            return [float(param["x"]), float(param["y"]), float(param["z"])]
        else:
            raise ValueError(f"Dict must have x, y, z keys, got {param.keys()}")
    
    # If it's a string, try to parse as JSON
    if isinstance(param, str):
        try:
            parsed = json.loads(param.strip())
            if isinstance(parsed, list) and len(parsed) == 3:
                return [float(v) for v in parsed]
            elif isinstance(parsed, dict) and all(k in parsed for k in ["x", "y", "z"]):
                return [float(parsed["x"]), float(parsed["y"]), float(parsed["z"])]
            else:
                raise ValueError(f"Parsed value must be a list of 3 numbers or dict with x,y,z keys")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Cannot parse '{param}' as camera parameter: {e}")
    
    raise ValueError(f"Unexpected type for camera parameter: {type(param)}")


def parse_list_param(param: Union[str, List[Any], None], default: List[Any]) -> List[Any]:
    """
    Parse flexible list parameters from various input formats.
    
    Handles:
    - JSON arrays: '["front", "top"]'
    - CSV strings: "front,top"
    - Python lists: ["front", "top"]
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        Parsed list
    """
    if param is None:
        return default
    
    # Already a list
    if isinstance(param, list):
        return param
    
    # String input - try various formats
    if isinstance(param, str):
        param = param.strip()
        
        # Try JSON parsing first
        if param.startswith('['):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, list):
                    return parsed
                else:
                    raise ValueError(f"JSON parsed to {type(parsed)}, expected list")
            except json.JSONDecodeError:
                pass
        
        # Try CSV format
        if ',' in param:
            return [item.strip() for item in param.split(',') if item.strip()]
        
        # Single value
        return [param]
    
    raise ValueError(f"Cannot parse list from type {type(param)}")


def parse_dict_param(param: Union[str, Dict[str, Any], None], default: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse flexible dict parameters from various input formats.
    
    Handles:
    - JSON objects: '{"x": 10, "y": 20}'
    - Key=value strings: "x=10,y=20"
    - Python dicts: {"x": 10}
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        Parsed dictionary
    """
    if param is None:
        return default
    
    # Already a dict
    if isinstance(param, dict):
        return param
    
    # String input - try various formats
    if isinstance(param, str):
        param = param.strip()
        
        # Try JSON parsing first
        if param.startswith('{'):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, dict):
                    return parsed
                else:
                    raise ValueError(f"JSON parsed to {type(parsed)}, expected dict")
            except json.JSONDecodeError:
                pass
        
        # Try key=value format
        if '=' in param:
            result = {}
            pairs = param.split(',')
            for pair in pairs:
                pair = pair.strip()
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Try to parse the value as number or boolean
                    try:
                        # Try integer first
                        if '.' not in value:
                            result[key] = int(value)
                        else:
                            result[key] = float(value)
                    except ValueError:
                        # Check for boolean
                        if value.lower() == 'true':
                            result[key] = True
                        elif value.lower() == 'false':
                            result[key] = False
                        else:
                            # Keep as string
                            result[key] = value
            return result
    
    raise ValueError(f"Cannot parse dict from type {type(param)}")


def parse_image_size_param(param: Union[List[int], str, tuple, None], default: List[int]) -> List[int]:
    """
    Parse flexible image size parameters from various input formats.
    
    Handles:
    - List format: [800, 600]
    - String format: "800x600" or "800,600"
    - Tuple format: (800, 600)
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        List of two integers [width, height]
    """
    if param is None:
        return default
    
    # Already a list
    if isinstance(param, list):
        if len(param) == 2:
            return [int(param[0]), int(param[1])]
        else:
            raise ValueError(f"Image size must have 2 values, got {len(param)}")
    
    # Tuple format
    if isinstance(param, tuple):
        if len(param) == 2:
            return [int(param[0]), int(param[1])]
        else:
            raise ValueError(f"Image size must have 2 values, got {len(param)}")
    
    # String format
    if isinstance(param, str):
        param = param.strip()
        
        # Try JSON format first (handles "[1200, 900]")
        if param.startswith('['):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, list) and len(parsed) == 2:
                    return [int(parsed[0]), int(parsed[1])]
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Try "800x600" format
        if 'x' in param:
            parts = param.split('x')
            if len(parts) == 2:
                return [int(parts[0].strip()), int(parts[1].strip())]
        
        # Try "800,600" format (only if not JSON-like)
        if ',' in param and not param.startswith('['):
            parts = param.split(',')
            if len(parts) == 2:
                return [int(parts[0].strip()), int(parts[1].strip())]
    
    raise ValueError(f"Cannot parse image size from {param}")

def estimate_response_size(data: Any) -> int:
    """
    Estimate the token size of response data.
    
    Uses a rough approximation of 4 characters per token, which is a 
    conservative estimate for base64-encoded data and JSON structures.
    
    Args:
        data: Any JSON-serializable data structure
        
    Returns:
        Estimated size in tokens
    """
    json_str = json.dumps(data)
    # Approximate: 4 characters per token (conservative for base64)
    return len(json_str) // 4


def save_image_to_file(base64_data: str, filename: str, output_dir: Path) -> str:
    """
    Save base64 image to file and return path.
    
    Decodes base64 image data and saves it to a file in the specified directory.
    Creates the directory if it doesn't exist.
    
    Args:
        base64_data: Base64-encoded image data
        filename: Name for the saved file
        output_dir: Directory to save the file in
        
    Returns:
        String path to the saved file
        
    Raises:
        ValueError: If base64 decoding fails
        OSError: If file writing fails
    """
    try:
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / filename
        
        # Decode and save
        image_data = base64.b64decode(base64_data)
        with open(file_path, 'wb') as f:
            f.write(image_data)
        
        return str(file_path)
    except Exception as e:
        raise ValueError(f"Failed to save image to file: {e}")


def compress_base64_image(base64_data: str, quality: int = 85, optimize: bool = True) -> str:
    """
    Compress base64 image to reduce size.
    
    Uses PIL/Pillow to decode, compress, and re-encode the image.
    Maintains PNG format but applies compression and optimization.
    
    Args:
        base64_data: Base64-encoded PNG image
        quality: Compression quality (1-100, ignored for PNG optimize)
        optimize: Whether to apply PNG optimization
        
    Returns:
        Compressed base64-encoded image
        
    Raises:
        ValueError: If image processing fails
    """
    import io
    
    try:
        # Decode base64 to image
        image_data = base64.b64decode(base64_data)
        image = Image.open(io.BytesIO(image_data))
        
        # Compress using PNG optimization
        buffer = io.BytesIO()
        # For PNG, quality parameter doesn't apply, but optimize does
        # We use compress_level for finer control
        save_kwargs = {
            'format': 'PNG',
            'optimize': optimize,
            'compress_level': 9 if quality < 50 else (6 if quality < 85 else 3)
        }
        image.save(buffer, **save_kwargs)
        
        # Re-encode to base64
        buffer.seek(0)
        compressed_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return compressed_data
    except Exception as e:
        raise ValueError(f"Failed to compress image: {e}")


def manage_response_size(
    images: Union[Dict[str, str], List[Dict[str, Any]]], 
    output_format: str = "auto",
    max_size: int = 25000, 
    output_dir: Optional[Path] = None,
    ctx: Optional[Any] = None
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Manage response size for multiple images.
    
    Intelligently handles large image responses by either compressing them,
    saving to files, or keeping as base64 based on size constraints.
    
    Args:
        images: Dictionary of name->base64 or list of image dicts with base64 data
        output_format: "auto" | "base64" | "file_path" | "compressed"
        max_size: Maximum response size in tokens (approx 4 chars per token)
        output_dir: Directory to save images when using file_path format
        ctx: Optional context for logging
        
    Returns:
        Modified images dictionary or list with optimized responses
    """
    config = get_config()
    
    # Set default output directory if not provided
    if output_dir is None:
        output_dir = Path(config.temp_dir) / "renders"
    
    # Handle both dict and list inputs
    is_dict = isinstance(images, dict)
    
    if is_dict:
        working_images = [(k, v) for k, v in images.items()]
    else:
        working_images = [(f"image_{i}", img.get("data", img)) for i, img in enumerate(images)]
    
    # Determine output format if auto
    if output_format == "auto":
        # Estimate current size
        current_size = estimate_response_size(images)
        
        if ctx:
            ctx.info(f"Estimated response size: {current_size} tokens")
        
        if current_size > max_size:
            # Try compression first
            test_compressed = {}
            for name, data in working_images[:1]:  # Test with first image
                try:
                    compressed = compress_base64_image(data)
                    compression_ratio = len(compressed) / len(data)
                    # If we can achieve >30% reduction, use compression
                    if compression_ratio < 0.7:
                        output_format = "compressed"
                        break
                except:
                    pass
            
            # If compression isn't enough, use file paths
            if output_format == "auto":
                output_format = "file_path"
        else:
            output_format = "base64"
        
        if ctx:
            ctx.info(f"Selected output format: {output_format}")
    
    # Process images based on format
    result = {}
    
    for name, base64_data in working_images:
        if output_format == "file_path":
            # Save to file and return path
            filename = f"{name}_{uuid.uuid4().hex[:8]}.png"
            file_path = save_image_to_file(base64_data, filename, output_dir)
            result[name] = {
                "type": "file_path",
                "path": file_path,
                "mime_type": "image/png"
            }
            
        elif output_format == "compressed":
            # Compress and return base64
            try:
                compressed_data = compress_base64_image(base64_data)
                result[name] = {
                    "type": "base64_compressed", 
                    "data": compressed_data,
                    "mime_type": "image/png",
                    "compression_ratio": len(compressed_data) / len(base64_data)
                }
            except Exception as e:
                # Fallback to original if compression fails
                if ctx:
                    ctx.warning(f"Compression failed for {name}: {e}")
                result[name] = {
                    "type": "base64",
                    "data": base64_data,
                    "mime_type": "image/png"
                }
                
        else:  # base64 format
            result[name] = {
                "type": "base64",
                "data": base64_data,
                "mime_type": "image/png"
            }
    
    # Return in original format
    if is_dict:
        # For backwards compatibility, if all are base64, return simple dict
        if all(v["type"] == "base64" for v in result.values()):
            return {k: v["data"] for k, v in result.items()}
        return result
    else:
        return list(result.values())



# View presets for common perspectives with distance=200
VIEW_PRESETS = {
    "front": ([0, -200, 0], [0, 0, 0], [0, 0, 1]),
    "back": ([0, 200, 0], [0, 0, 0], [0, 0, 1]),
    "left": ([-200, 0, 0], [0, 0, 0], [0, 0, 1]),
    "right": ([200, 0, 0], [0, 0, 0], [0, 0, 1]),
    "top": ([0, 0, 200], [0, 0, 0], [0, 1, 0]),
    "bottom": ([0, 0, -200], [0, 0, 0], [0, -1, 0]),
    "isometric": ([200, 200, 200], [0, 0, 0], [0, 0, 1]),
    "dimetric": ([200, 100, 200], [0, 0, 0], [0, 0, 1]),
}


@mcp.tool
async def render_single(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    view: Optional[str] = None,
    camera_position: Union[str, List[float], Dict[str, float], None] = None,
    camera_target: Union[str, List[float], Dict[str, float], None] = None,
    camera_up: Union[str, List[float], Dict[str, float], None] = None,
    image_size: Union[str, List[int], tuple, None] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    output_format: Optional[str] = "auto",
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Render a single view from OpenSCAD code or file.
    
    Args:
        scad_content: OpenSCAD code to render (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)  
        view: Predefined view name ("front", "back", "left", "right", "top", "bottom", "isometric", "dimetric")
        camera_position: Camera position - accepts [x,y,z] list, JSON string "[x,y,z]", or dict {"x":x,"y":y,"z":z} (default: [70, 70, 70])
        camera_target: Camera look-at point - accepts [x,y,z] list, JSON string, or dict (default: [0, 0, 0])
        camera_up: Camera up vector - accepts [x,y,z] list, JSON string, or dict (default: [0, 0, 1])
        image_size: Image dimensions - accepts [width, height] list, JSON string "[width, height]", "widthxheight", or tuple (default: [800, 600])
        color_scheme: OpenSCAD color scheme (default: "Cornfield")
        variables: Variables to pass to OpenSCAD
        auto_center: Auto-center the model
        output_format: Output format - "auto", "base64", "file_path", or "compressed" (default: "auto")
        ctx: MCP context for logging
    
    Returns:
        Dict with base64-encoded PNG image or file path
    """
    if ctx:
        await ctx.info("Starting OpenSCAD render...")
    
    # Validate input
    if bool(scad_content) == bool(scad_file):
        raise ValueError("Exactly one of scad_content or scad_file must be provided")
    
    # If view keyword is provided, use preset camera settings
    if view:
        if view not in VIEW_PRESETS:
            raise ValueError(f"Invalid view name '{view}'. Must be one of: {', '.join(VIEW_PRESETS.keys())}")
        
        # Get preset camera settings
        preset_pos, preset_target, preset_up = VIEW_PRESETS[view]
        
        # Override camera parameters with preset values
        camera_position = list(preset_pos)
        camera_target = list(preset_target)
        camera_up = list(preset_up)
        
        # Auto-center is typically enabled for standard views
        if not auto_center:
            auto_center = True
            
        if ctx:
            await ctx.info(f"Using preset view '{view}' with camera position {camera_position}")
    else:
        # Parse camera parameters with proper defaults
        camera_position = parse_camera_param(camera_position, [70, 70, 70])
        camera_target = parse_camera_param(camera_target, [0, 0, 0])
        camera_up = parse_camera_param(camera_up, [0, 0, 1])
    
    # Parse image size with flexible formats
    image_size = parse_image_size_param(image_size, [800, 600])
    
    # Parse variables with flexible formats
    variables = parse_dict_param(variables, {})
    
    try:
        # Run rendering (simplified synchronous version)
        # In production, this should use asyncio.run_in_executor
        image_data = await asyncio.get_event_loop().run_in_executor(
            None,
            render_scad_to_png,
            scad_content,
            scad_file,
            camera_position,
            camera_target,
            camera_up,
            image_size,
            color_scheme,
            variables,
            auto_center,
        )
        
        if ctx:
            await ctx.info("Rendering completed successfully")
        
        # Apply response size management for single image
        if output_format and output_format != "base64":
            managed_result = manage_response_size(
                {"render": image_data},
                output_format=output_format,
                max_size=20000,
                ctx=ctx
            )
            
            # Check if we got extended format
            if isinstance(managed_result, dict) and "render" in managed_result:
                result_data = managed_result["render"]
                if isinstance(result_data, dict):
                    # Extended format with metadata
                    return {
                        "success": True,
                        **result_data,  # Include type, data/path, mime_type
                        "operation_id": str(uuid.uuid4()),
                        "output_format": output_format if output_format != "auto" else "optimized"
                    }
        
        # Default base64 response (backwards compatible)
        return {
            "success": True,
            "data": image_data,
            "mime_type": "image/png",
            "operation_id": str(uuid.uuid4()),
        }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Rendering failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "operation_id": str(uuid.uuid4()),
        }



@mcp.tool
async def check_openscad(
    include_paths: bool = False,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Verify OpenSCAD installation and return version info.
    
    Args:
        include_paths: Include searched paths in response
        ctx: MCP context for logging
    
    Returns:
        Dict with OpenSCAD installation information
    """
    if ctx:
        await ctx.info("Checking OpenSCAD installation...")
    
    openscad_path = find_openscad()
    
    if not openscad_path:
        searched = [
            "/usr/bin/openscad",
            "/usr/local/bin/openscad",
            "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
            "C:\\Program Files\\OpenSCAD\\openscad.exe",
        ]
        
        return {
            "installed": False,
            "version": None,
            "path": None,
            "searched_paths": searched if include_paths else None,
            "message": "OpenSCAD not found. Please install from https://openscad.org",
        }
    
    # Get version
    try:
        result = subprocess.run(
            [openscad_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        version = result.stdout.strip() if result.stdout else "Unknown"
    except Exception:
        version = "Unknown"
    
    if ctx:
        await ctx.info(f"Found OpenSCAD at {openscad_path}")
    
    return {
        "installed": True,
        "version": version,
        "path": str(openscad_path),
        "message": f"OpenSCAD is installed at {openscad_path}",
    }


# ============================================================================
# MCP Resources
# ============================================================================


@mcp.resource("resource://server/info")
async def get_server_info() -> Dict[str, Any]:
    """Get server configuration and capabilities."""
    config = get_config()
    openscad_info = await check_openscad()
    
    return {
        "version": config.server.version,
        "openscad_version": openscad_info.get("version"),
        "openscad_path": openscad_info.get("path"),
        "imagemagick_available": False,  # Simplified for MVP
        "max_concurrent_renders": config.rendering.max_concurrent,
        "queue_size": config.rendering.queue_size,
        "active_operations": 0,  # Simplified for MVP
        "cache_enabled": config.cache.enabled,
        "supported_formats": ["png"],
    }


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    """Main entry point for the server."""
    import sys
    
    # Check for OpenSCAD on startup
    if not find_openscad():
        print("Warning: OpenSCAD not found. Please install OpenSCAD to use this server.")
        print("Visit https://openscad.org for installation instructions.")
    
    # Run the server
    config = get_config()
    
    if config.server.transport == "stdio":
        mcp.run()
    else:
        # For HTTP/SSE transport
        mcp.run(
            transport=config.server.transport.value,
            host=config.server.host,
            port=config.server.port,
        )


if __name__ == "__main__":
    main()