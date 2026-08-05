"""
Main FastMCP server implementation for OpenSCAD rendering.
"""

import asyncio
import base64
import hashlib
import logging
import os
import platform
import re
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple

from fastmcp import Context, FastMCP
from fastmcp.utilities.types import Image as MCPImage
from PIL import Image as PILImage
import json

logger = logging.getLogger(__name__)

from .types import (
    ColorScheme,
    ImageSize,
    OpenSCADInfo,
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


# ============================================================================
# Render Cache Helpers
# ============================================================================


def _compute_render_cache_key(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    camera_position: Optional[List[float]] = None,
    camera_target: Optional[List[float]] = None,
    camera_up: Optional[List[float]] = None,
    image_size: Optional[List[int]] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    include_paths: Optional[List[str]] = None,
) -> str:
    """Compute a SHA-256 cache key from all rendering parameters.

    When *scad_file* is provided (instead of inline content), the file's
    contents are read and hashed so that changes to the file on disk
    correctly invalidate the cache entry.

    Args:
        scad_content: Inline OpenSCAD source code.
        scad_file: Path to an OpenSCAD file.
        camera_position: Camera eye position [x, y, z].
        camera_target: Camera look-at point [x, y, z].
        camera_up: Camera up vector [x, y, z].
        image_size: Output image dimensions [width, height].
        color_scheme: OpenSCAD colour scheme name.
        variables: OpenSCAD ``-D`` variables.
        auto_center: Whether auto-centre / view-all is enabled.
        include_paths: Extra ``-I`` include directories.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    hasher = hashlib.sha256()

    # Hash the actual SCAD source
    if scad_content:
        hasher.update(scad_content.encode("utf-8"))
    elif scad_file:
        try:
            file_data = Path(scad_file).read_bytes()
            hasher.update(file_data)
        except OSError:
            # If we cannot read the file fall back to hashing the path
            hasher.update(scad_file.encode("utf-8"))

    # Hash deterministic JSON representations of all other parameters
    hasher.update(json.dumps(camera_position, sort_keys=True).encode())
    hasher.update(json.dumps(camera_target, sort_keys=True).encode())
    hasher.update(json.dumps(camera_up, sort_keys=True).encode())
    hasher.update(json.dumps(image_size, sort_keys=True).encode())
    hasher.update(color_scheme.encode("utf-8"))
    hasher.update(json.dumps(variables, sort_keys=True).encode() if variables else b"")
    hasher.update(b"1" if auto_center else b"0")
    hasher.update(json.dumps(include_paths, sort_keys=True).encode() if include_paths else b"")

    return hasher.hexdigest()


def _check_cache(cache_key: str) -> Optional[str]:
    """Return cached base64 PNG if the entry exists and has not expired.

    Args:
        cache_key: Hex digest returned by ``_compute_render_cache_key``.

    Returns:
        Base64-encoded PNG string on cache hit, or ``None`` on miss /
        expiration / disabled cache.
    """
    config = get_config()
    if not config.cache.enabled:
        return None

    cache_file = config.cache.directory / f"{cache_key}.png"
    if not cache_file.exists():
        return None

    # Check TTL
    age_hours = (time.time() - cache_file.stat().st_mtime) / 3600.0
    if age_hours > config.cache.ttl_hours:
        # Expired -- remove stale entry
        try:
            cache_file.unlink()
        except OSError:
            pass
        return None

    try:
        image_data = cache_file.read_bytes()
        return base64.b64encode(image_data).decode("utf-8")
    except OSError:
        return None


def _save_to_cache(cache_key: str, image_data: bytes) -> None:
    """Save raw PNG bytes to the cache and evict oldest entries if needed.

    Args:
        cache_key: Hex digest returned by ``_compute_render_cache_key``.
        image_data: Raw PNG image bytes (not base64).
    """
    config = get_config()
    if not config.cache.enabled:
        return

    config.cache.ensure_cache_directory()
    cache_file = config.cache.directory / f"{cache_key}.png"

    try:
        cache_file.write_bytes(image_data)
    except OSError as exc:
        logger.warning("Failed to write render cache entry: %s", exc)
        return

    # Evict oldest files if the cache exceeds the size limit
    _evict_cache_if_needed()


def _evict_cache_if_needed() -> None:
    """Delete the oldest cache files until total size is within limits."""
    config = get_config()
    if not config.cache.enabled:
        return

    cache_dir = config.cache.directory
    if not cache_dir.exists():
        return

    max_bytes = config.cache.max_size_mb * 1024 * 1024

    # Collect all cache files with their stats
    cache_files: List[Tuple[Path, float, int]] = []
    total_size = 0
    for f in cache_dir.glob("*.png"):
        try:
            stat = f.stat()
            cache_files.append((f, stat.st_mtime, stat.st_size))
            total_size += stat.st_size
        except OSError:
            continue

    if total_size <= max_bytes:
        return

    # Sort oldest first (ascending mtime)
    cache_files.sort(key=lambda t: t[1])

    for file_path, _mtime, file_size in cache_files:
        if total_size <= max_bytes:
            break
        try:
            file_path.unlink()
            total_size -= file_size
        except OSError:
            continue


def render_scad_to_png(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    camera_position: Optional[List[float]] = None,
    camera_target: Optional[List[float]] = None,
    camera_up: Optional[List[float]] = None,
    image_size: Optional[List[int]] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    include_paths: Optional[List[str]] = None,
) -> str:
    """
    Render OpenSCAD code or file to PNG and return as base64.

    Supports render caching (controlled via ``config.cache``) and
    multi-file projects via ``include_paths`` (``-I`` flags).
    """
    if camera_position is None:
        camera_position = [70, 70, 70]
    if camera_target is None:
        camera_target = [0, 0, 0]
    if camera_up is None:
        camera_up = [0, 0, 1]
    if image_size is None:
        image_size = [800, 600]

    openscad_cmd = find_openscad()
    if not openscad_cmd:
        raise RuntimeError("OpenSCAD not found. Please install OpenSCAD first.")

    config = get_config()

    # Security validations
    if scad_file:
        resolved_path = Path(scad_file).resolve()
        if config.security.allowed_paths:
            if not any(
                str(resolved_path).startswith(str(Path(ap).resolve()))
                for ap in config.security.allowed_paths
            ):
                raise ValueError(
                    f"File path '{scad_file}' is not within allowed paths: {config.security.allowed_paths}"
                )

    if scad_content:
        max_bytes = config.security.max_file_size_mb * 1024 * 1024
        if len(scad_content) > max_bytes:
            raise ValueError(
                f"SCAD content size ({len(scad_content)} bytes) exceeds maximum allowed size "
                f"({config.security.max_file_size_mb} MB / {max_bytes} bytes)"
            )

    if variables:
        for key in variables:
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                raise ValueError(
                    f"Invalid variable name '{key}': must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
                )

    # Security: validate include_paths against allowed_paths
    if include_paths and config.security.allowed_paths:
        for inc_path in include_paths:
            resolved_inc = Path(inc_path).resolve()
            if not any(
                str(resolved_inc).startswith(str(Path(ap).resolve()))
                for ap in config.security.allowed_paths
            ):
                raise ValueError(
                    f"Include path '{inc_path}' is not within allowed paths: "
                    f"{config.security.allowed_paths}"
                )

    # --- Cache: check for a cached render ---
    cache_key = _compute_render_cache_key(
        scad_content=scad_content,
        scad_file=scad_file,
        camera_position=camera_position,
        camera_target=camera_target,
        camera_up=camera_up,
        image_size=image_size,
        color_scheme=color_scheme,
        variables=variables,
        auto_center=auto_center,
        include_paths=include_paths,
    )
    cached = _check_cache(cache_key)
    if cached is not None:
        logger.debug("Render cache hit for key %s", cache_key[:12])
        return cached

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
            "--hardwarnings",
            "-o", str(output_path),
            "--imgsize", f"{image_size[0]},{image_size[1]}",
            "--colorscheme", color_scheme,
        ]

        # Add camera parameters (eye + center, 6-value format)
        camera_str = (
            f"--camera="
            f"{camera_position[0]},{camera_position[1]},{camera_position[2]},"
            f"{camera_target[0]},{camera_target[1]},{camera_target[2]}"
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

        # Add include paths for multi-file project support
        if include_paths:
            for inc_path in include_paths:
                cmd.extend(["-I", str(inc_path)])

        # Add the SCAD file
        cmd.append(str(scad_path))

        # Run OpenSCAD
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                timeout=config.rendering.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"OpenSCAD rendering timed out after {config.rendering.timeout_seconds} seconds"
            )

        if result.returncode != 0:
            raise RuntimeError(f"OpenSCAD rendering failed: {result.stderr}")

        if not output_path.exists():
            raise RuntimeError("OpenSCAD did not produce output file")

        # Read the image
        with open(output_path, "rb") as f:
            image_data = f.read()

        # --- Cache: save the rendered image ---
        _save_to_cache(cache_key, image_data)

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

        # Empty or whitespace-only string returns default
        if not param:
            return default

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

        # Empty or whitespace-only string returns default
        if not param:
            return default

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
        image = PILImage.open(io.BytesIO(image_data))
        
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
            logger.info(f"Estimated response size: {current_size} tokens")
        
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
                except Exception:
                    pass
            
            # If compression isn't enough, use file paths
            if output_format == "auto":
                output_format = "file_path"
        else:
            output_format = "base64"
        
        if ctx:
            logger.info(f"Selected output format: {output_format}")
    
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
                    logger.warning(f"Compression failed for {name}: {e}")
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

# Quality presets mapping to OpenSCAD resolution variables
QUALITY_PRESETS = {
    "draft": {"$fn": 8, "$fa": 12, "$fs": 2},
    "normal": {},  # OpenSCAD defaults
    "high": {"$fn": 64, "$fa": 2, "$fs": 0.5},
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
    quality: Optional[str] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
):
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
        quality: Quality preset - "draft" (fast, low detail), "normal" (OpenSCAD defaults), or "high" (slow, high detail). User-provided variables override quality preset values.
        include_paths: Additional include paths for OpenSCAD via -I flags, enabling multi-file project support
        ctx: MCP context for logging

    Returns:
        List containing the rendered PNG image and metadata
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

    # Apply quality preset variables (user-provided variables take precedence)
    if quality:
        if quality not in QUALITY_PRESETS:
            raise ValueError(
                f"Invalid quality preset '{quality}'. "
                f"Must be one of: {', '.join(QUALITY_PRESETS.keys())}"
            )
        quality_vars = QUALITY_PRESETS[quality]
        if quality_vars:
            merged = dict(quality_vars)
            merged.update(variables)
            variables = merged

    try:
        # Run rendering in executor to avoid blocking the event loop
        image_b64 = await asyncio.get_running_loop().run_in_executor(
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
            include_paths,
        )

        if ctx:
            await ctx.info("Rendering completed successfully")

        # Return as MCPImage so FastMCP sends proper ImageContent to clients
        image_bytes = base64.b64decode(image_b64)
        return [
            MCPImage(data=image_bytes, format="png"),
            json.dumps({
                "success": True,
                "operation_id": str(uuid.uuid4()),
            }),
        ]

    except Exception as e:
        if ctx:
            await ctx.error(f"Rendering failed: {str(e)}")
        return [
            json.dumps({
                "success": False,
                "error": str(e),
                "operation_id": str(uuid.uuid4()),
            })
        ]


@mcp.tool()
async def render_perspectives(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    views: Optional[List[str]] = None,
    image_size: Optional[str] = None,
    color_scheme: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    quality: Optional[str] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
):
    """
    Render multiple standard views of an OpenSCAD model in a single call.

    Renders the model from several predefined camera perspectives in parallel,
    returning all images at once. Useful for generating a comprehensive visual
    overview of a 3D model.

    Args:
        scad_content: OpenSCAD code to render (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)
        views: List of view names to render. Valid names: "front", "back", "left",
            "right", "top", "bottom", "isometric". If not specified, renders all
            standard views.
        image_size: Image dimensions - accepts "widthxheight", "width,height",
            "[width, height]", or [width, height] list (default: [800, 600])
        color_scheme: OpenSCAD color scheme (default: "Cornfield")
        variables: Variables to pass to OpenSCAD via -D flags
        quality: Quality preset - "draft" (fast, low detail), "normal" (OpenSCAD
            defaults), or "high" (slow, high detail). User-provided variables
            override quality preset values.
        include_paths: Additional include paths for OpenSCAD via -I flags,
            enabling multi-file project support
        ctx: MCP context for logging

    Returns:
        List of rendered PNG images and metadata
    """
    try:
        # Validate input
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file must be provided"
            )

        # Determine which views to render
        default_views = ["front", "back", "left", "right", "top", "bottom", "isometric"]
        if views is None:
            views = default_views
        else:
            # Parse views if provided as string
            views = parse_list_param(views, default_views)

        # Validate view names
        invalid_views = [v for v in views if v not in VIEW_PRESETS]
        if invalid_views:
            raise ValueError(
                f"Invalid view name(s): {', '.join(invalid_views)}. "
                f"Must be one of: {', '.join(VIEW_PRESETS.keys())}"
            )

        # Parse image size
        parsed_image_size = parse_image_size_param(image_size, [800, 600])

        # Parse variables
        parsed_variables = parse_dict_param(variables, {})

        # Apply quality preset variables (user-provided variables take precedence)
        if quality:
            if quality not in QUALITY_PRESETS:
                raise ValueError(
                    f"Invalid quality preset '{quality}'. "
                    f"Must be one of: {', '.join(QUALITY_PRESETS.keys())}"
                )
            quality_vars = QUALITY_PRESETS[quality]
            if quality_vars:
                merged = dict(quality_vars)
                merged.update(parsed_variables)
                parsed_variables = merged

        # Use provided color scheme or default
        resolved_color_scheme = color_scheme or "Cornfield"

        if ctx:
            await ctx.info(
                f"Rendering {len(views)} perspective(s): {', '.join(views)}"
            )

        # Define render function for a single view
        def _render_view(view_name: str) -> Tuple[str, Any]:
            """Render a single view, returning (view_name, result_or_error)."""
            preset_pos, preset_target, preset_up = VIEW_PRESETS[view_name]
            try:
                image_b64 = render_scad_to_png(
                    scad_content=scad_content,
                    scad_file=scad_file,
                    camera_position=list(preset_pos),
                    camera_target=list(preset_target),
                    camera_up=list(preset_up),
                    image_size=parsed_image_size,
                    color_scheme=resolved_color_scheme,
                    variables=parsed_variables,
                    auto_center=True,
                    include_paths=include_paths,
                )
                return (view_name, {"success": True, "data": image_b64})
            except Exception as e:
                return (view_name, {"success": False, "error": str(e)})

        # Render all views in parallel using asyncio.gather
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, _render_view, view_name)
            for view_name in views
        ]
        results = await asyncio.gather(*tasks)

        # Collect successful renders and errors
        response_items: list = []
        errors = {}
        success_count = 0
        for view_name, result in results:
            if result["success"]:
                image_bytes = base64.b64decode(result["data"])
                response_items.append(f"View: {view_name}")
                response_items.append(MCPImage(data=image_bytes, format="png"))
                success_count += 1
            else:
                errors[view_name] = result["error"]

        if ctx:
            error_count = len(errors)
            msg = f"Rendered {success_count}/{len(views)} view(s) successfully"
            if error_count > 0:
                msg += f" ({error_count} failed)"
            await ctx.info(msg)

        # Add metadata summary
        response_items.append(
            json.dumps({
                "success": len(errors) == 0,
                "count": success_count,
                "errors": errors if errors else None,
            })
        )

        return response_items

    except Exception as e:
        if ctx:
            await ctx.error(f"Render perspectives failed: {str(e)}")
        return [
            json.dumps({
                "success": False,
                "error": str(e),
            })
        ]


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
        version = result.stdout.strip() or result.stderr.strip() or "Unknown"
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
# Export Tool
# ============================================================================


SUPPORTED_EXPORT_FORMATS = {"stl", "3mf", "amf", "off", "dxf", "svg"}


@mcp.tool()
async def export_model(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    output_format: str = "stl",
    output_path: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Export OpenSCAD code or file to STL, 3MF, AMF, OFF, DXF, or SVG.

    Args:
        scad_content: OpenSCAD code to export (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)
        output_format: Export format - "stl", "3mf", "amf", "off", "dxf", or "svg"
            (default: "stl")
        output_path: Path to write the exported file. If not specified, a temp
            directory is used.
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via -I flags
        ctx: MCP context for logging

    Returns:
        Dict with success status, output_path, format, and file_size_bytes
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file must be provided"
            )

        # Validate output format
        fmt = output_format.lower()
        if fmt not in SUPPORTED_EXPORT_FORMATS:
            raise ValueError(
                f"Unsupported format '{output_format}'. "
                f"Must be one of: {', '.join(sorted(SUPPORTED_EXPORT_FORMATS))}"
            )

        config = get_config()

        # Security: validate scad_file path
        if scad_file:
            resolved_path = Path(scad_file).resolve()
            if config.security.allowed_paths:
                if not any(
                    str(resolved_path).startswith(str(Path(ap).resolve()))
                    for ap in config.security.allowed_paths
                ):
                    raise ValueError(
                        f"File path '{scad_file}' is not within allowed paths: "
                        f"{config.security.allowed_paths}"
                    )

        # Security: validate scad_content size
        if scad_content:
            max_bytes = config.security.max_file_size_mb * 1024 * 1024
            if len(scad_content) > max_bytes:
                raise ValueError(
                    f"SCAD content size ({len(scad_content)} bytes) exceeds "
                    f"maximum allowed size "
                    f"({config.security.max_file_size_mb} MB / {max_bytes} bytes)"
                )

        # Security: validate variable names
        if variables:
            for key in variables:
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                    raise ValueError(
                        f"Invalid variable name '{key}': "
                        f"must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
                    )

        openscad_cmd = find_openscad()
        if not openscad_cmd:
            raise RuntimeError(
                "OpenSCAD not found. Please install OpenSCAD first."
            )

        # Ensure temp directory exists
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)

        # Determine output file path
        if output_path:
            final_output = Path(output_path)
            final_output.parent.mkdir(parents=True, exist_ok=True)
        else:
            export_dir = temp_dir_path / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            final_output = export_dir / f"export_{uuid.uuid4().hex[:8]}.{fmt}"

        # Handle input source
        cleanup_temp = False
        if scad_content:
            tmp_input = temp_dir_path / f"input_{uuid.uuid4().hex[:8]}.scad"
            tmp_input.write_text(scad_content)
            scad_input_path = tmp_input
            cleanup_temp = True
        else:
            scad_input_path = Path(scad_file)
            if not scad_input_path.exists():
                raise FileNotFoundError(f"SCAD file not found: {scad_file}")

        # Build command
        cmd = [openscad_cmd, "-o", str(final_output)]

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

        # Add include paths
        if include_paths:
            for inc_path in include_paths:
                cmd.extend(["-I", str(inc_path)])

        cmd.append(str(scad_input_path))

        if ctx:
            await ctx.info(f"Exporting to {fmt}...")

        # Run OpenSCAD in executor
        def _run_export():
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=config.rendering.timeout_seconds,
                )
                return result
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"OpenSCAD export timed out after "
                    f"{config.rendering.timeout_seconds} seconds"
                )

        result = await asyncio.get_running_loop().run_in_executor(
            None, _run_export
        )

        # Clean up temp input file
        if cleanup_temp and scad_input_path.exists():
            scad_input_path.unlink()

        if result.returncode != 0:
            raise RuntimeError(f"OpenSCAD export failed: {result.stderr}")

        if not final_output.exists():
            raise RuntimeError("OpenSCAD did not produce output file")

        file_size = final_output.stat().st_size

        if ctx:
            await ctx.info(
                f"Export complete: {final_output} ({file_size} bytes)"
            )

        return {
            "success": True,
            "output_path": str(final_output),
            "format": fmt,
            "file_size_bytes": file_size,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Export failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


# ============================================================================
# Model Management Tools
# ============================================================================


def _validate_model_name(name: str) -> str:
    """
    Validate and normalize a model file name.

    Ensures the name contains only safe characters (alphanumeric, hyphens,
    underscores, dots) and ends with .scad.

    Args:
        name: The model file name to validate

    Returns:
        The validated and normalized name (with .scad extension)

    Raises:
        ValueError: If the name contains invalid characters or path traversal
    """
    # Reject path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(
            f"Invalid model name '{name}': must not contain path separators "
            f"or '..'"
        )

    # Strip .scad extension for validation, then re-add
    base = name.removesuffix(".scad")

    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', base):
        raise ValueError(
            f"Invalid model name '{name}': must start with alphanumeric and "
            f"contain only alphanumeric, hyphens, and underscores"
        )

    if not name.endswith(".scad"):
        name = name + ".scad"

    return name


def _resolve_workspace(workspace: Optional[str] = None) -> Path:
    """
    Resolve the workspace directory path.

    Uses the provided workspace path or defaults to the configured temp_dir
    models subdirectory. Creates the directory if it does not exist.

    Args:
        workspace: Optional workspace directory path

    Returns:
        Resolved Path to the workspace directory

    Raises:
        ValueError: If the workspace path contains path traversal sequences
    """
    config = get_config()

    if workspace:
        if ".." in workspace:
            raise ValueError(
                "Workspace path must not contain '..'"
            )
        ws = Path(workspace).resolve()
    else:
        ws = Path(config.temp_dir) / "models"

    ws.mkdir(parents=True, exist_ok=True)
    return ws


@mcp.tool()
async def create_model(
    name: str,
    content: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Create a new OpenSCAD model file.

    Args:
        name: File name for the model (alphanumeric, hyphens, underscores;
            .scad extension added automatically if missing)
        content: OpenSCAD source code for the model
        workspace: Directory to save the model in. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, path, and name of the created file
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if file_path.exists():
            raise ValueError(
                f"Model '{name}' already exists at {file_path}. "
                f"Use update_model to modify it."
            )

        file_path.write_text(content)

        if ctx:
            await ctx.info(f"Created model: {file_path}")

        return {
            "success": True,
            "path": str(file_path),
            "name": name,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to create model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def get_model(
    name: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Read an OpenSCAD model file and return its contents.

    Args:
        name: File name of the model to read
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, name, content, path, and size_bytes
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}"
            )

        content = file_path.read_text()
        size = file_path.stat().st_size

        if ctx:
            await ctx.info(f"Read model: {file_path} ({size} bytes)")

        return {
            "success": True,
            "name": name,
            "content": content,
            "path": str(file_path),
            "size_bytes": size,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to read model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def update_model(
    name: str,
    content: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Update an existing OpenSCAD model file with new content.

    The file must already exist. Use create_model to create new files.

    Args:
        name: File name of the model to update
        content: New OpenSCAD source code for the model
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, path, and name of the updated file
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}. "
                f"Use create_model to create it first."
            )

        file_path.write_text(content)

        if ctx:
            await ctx.info(f"Updated model: {file_path}")

        return {
            "success": True,
            "path": str(file_path),
            "name": name,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to update model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def list_models(
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    List all OpenSCAD model files in the workspace directory.

    Args:
        workspace: Directory to list models from. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, list of models (name, path, size_bytes,
        modified), and count
    """
    try:
        ws = _resolve_workspace(workspace)
        models = []

        for scad_file in sorted(ws.glob("*.scad")):
            stat = scad_file.stat()
            models.append({
                "name": scad_file.name,
                "path": str(scad_file),
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })

        if ctx:
            await ctx.info(
                f"Found {len(models)} model(s) in {ws}"
            )

        return {
            "success": True,
            "models": models,
            "count": len(models),
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to list models: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def delete_model(
    name: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Delete an OpenSCAD model file from the workspace.

    The file must exist. Returns the path of the deleted file.

    Args:
        name: File name of the model to delete
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, name, and deleted_path
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}"
            )

        deleted_path = str(file_path)
        file_path.unlink()

        if ctx:
            await ctx.info(f"Deleted model: {deleted_path}")

        return {
            "success": True,
            "name": name,
            "deleted_path": deleted_path,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to delete model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


# ============================================================================
# Validation, Analysis, Libraries, and Comparison Tools
# ============================================================================


def _parse_openscad_stderr(stderr: str) -> Dict[str, List[str]]:
    """
    Parse OpenSCAD stderr output into categorized message lists.

    Scans each line of stderr for ECHO, WARNING, ERROR, and DEPRECATED
    markers and groups them into separate lists.

    Args:
        stderr: Raw stderr output from OpenSCAD

    Returns:
        Dict with keys "errors", "warnings", "echo_output", "deprecated"
    """
    errors = []
    warnings = []
    echo_output = []
    deprecated = []

    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("ECHO:"):
            echo_output.append(stripped[len("ECHO:"):].strip())
        elif "WARNING:" in stripped:
            warnings.append(stripped)
        elif "ERROR:" in stripped:
            errors.append(stripped)
        elif "DEPRECATED:" in stripped:
            deprecated.append(stripped)
        elif stripped.startswith("ERROR"):
            errors.append(stripped)

    return {
        "errors": errors,
        "warnings": warnings,
        "echo_output": echo_output,
        "deprecated": deprecated,
    }


def _parse_stl_vertices(stl_path: Path) -> List[List[float]]:
    """
    Parse vertex coordinates from an STL file (ASCII or binary).

    Detects the STL format automatically and extracts all vertex
    coordinates. For binary STL, reads the 80-byte header and
    triangle count, then iterates facets. For ASCII STL, uses
    regex matching on vertex lines.

    Args:
        stl_path: Path to the STL file to parse

    Returns:
        List of [x, y, z] vertex coordinate lists

    Raises:
        ValueError: If the STL file cannot be parsed
    """
    with open(stl_path, "rb") as f:
        header = f.read(80)

    # Detect ASCII vs binary: ASCII STL starts with "solid"
    is_ascii = header[:5] == b"solid"

    vertices = []

    if is_ascii:
        text = stl_path.read_text(errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("vertex"):
                parts = stripped.split()
                if len(parts) == 4:
                    try:
                        vertices.append([
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3]),
                        ])
                    except ValueError:
                        continue
    else:
        # Binary STL format:
        # 80 bytes header, 4 bytes triangle count,
        # then per triangle: 12 bytes normal + 3x12 bytes vertices
        # + 2 bytes attribute
        with open(stl_path, "rb") as f:
            f.read(80)  # skip header
            count_data = f.read(4)
            if len(count_data) < 4:
                raise ValueError("Invalid binary STL: too short")
            tri_count = struct.unpack("<I", count_data)[0]

            for _ in range(tri_count):
                # Skip normal vector (3 floats = 12 bytes)
                f.read(12)
                # Read 3 vertices (each 3 floats = 12 bytes)
                for _ in range(3):
                    vdata = f.read(12)
                    if len(vdata) < 12:
                        raise ValueError(
                            "Invalid binary STL: unexpected end of file"
                        )
                    x, y, z = struct.unpack("<fff", vdata)
                    vertices.append([x, y, z])
                # Skip attribute byte count (2 bytes)
                f.read(2)

    return vertices


@mcp.tool()
async def validate_scad(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Syntax-check OpenSCAD code without performing a full render.

    Runs OpenSCAD with output directed to /dev/null (NUL on Windows)
    so it only parses and evaluates the code without generating
    geometry output. Much faster than a full render. Captures and
    categorizes ECHO, WARNING, ERROR, and DEPRECATED messages from
    stderr.

    Args:
        scad_content: OpenSCAD code to validate (mutually exclusive
            with scad_file)
        scad_file: Path to OpenSCAD file to validate (mutually
            exclusive with scad_content)
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via
            -I flags
        ctx: MCP context for logging

    Returns:
        Dict with success status, valid flag, errors list, warnings
        list, echo_output list, and deprecated list
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file "
                "must be provided"
            )

        config = get_config()

        # Security: validate scad_file path
        if scad_file:
            resolved_path = Path(scad_file).resolve()
            if config.security.allowed_paths:
                if not any(
                    str(resolved_path).startswith(
                        str(Path(ap).resolve())
                    )
                    for ap in config.security.allowed_paths
                ):
                    raise ValueError(
                        f"File path '{scad_file}' is not within "
                        f"allowed paths: "
                        f"{config.security.allowed_paths}"
                    )

        # Security: validate scad_content size
        if scad_content:
            max_bytes = (
                config.security.max_file_size_mb * 1024 * 1024
            )
            if len(scad_content) > max_bytes:
                raise ValueError(
                    f"SCAD content size ({len(scad_content)} bytes) "
                    f"exceeds maximum allowed size "
                    f"({config.security.max_file_size_mb} MB / "
                    f"{max_bytes} bytes)"
                )

        # Security: validate variable names
        if variables:
            for key in variables:
                if not re.match(
                    r'^[a-zA-Z_][a-zA-Z0-9_]*$', key
                ):
                    raise ValueError(
                        f"Invalid variable name '{key}': "
                        f"must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
                    )

        openscad_cmd = find_openscad()
        if not openscad_cmd:
            raise RuntimeError(
                "OpenSCAD not found. Please install OpenSCAD first."
            )

        # Ensure temp directory exists
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)

        # Handle input source
        cleanup_temp = False
        if scad_content:
            tmp_input = (
                temp_dir_path
                / f"validate_{uuid.uuid4().hex[:8]}.scad"
            )
            tmp_input.write_text(scad_content)
            scad_input_path = tmp_input
            cleanup_temp = True
        else:
            scad_input_path = Path(scad_file)
            if not scad_input_path.exists():
                raise FileNotFoundError(
                    f"SCAD file not found: {scad_file}"
                )

        # Build command: output to /dev/null (NUL on Windows)
        null_output = (
            "NUL" if platform.system() == "Windows"
            else "/dev/null"
        )
        cmd = [
            openscad_cmd,
            "--hardwarnings",
            "-o", null_output,
        ]

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

        # Add include paths
        if include_paths:
            for inc_path in include_paths:
                cmd.extend(["-I", str(inc_path)])

        cmd.append(str(scad_input_path))

        if ctx:
            await ctx.info("Validating OpenSCAD code...")

        # Run OpenSCAD in executor
        def _run_validate():
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=config.rendering.timeout_seconds,
                )
                return result
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"OpenSCAD validation timed out after "
                    f"{config.rendering.timeout_seconds} seconds"
                )

        result = await asyncio.get_running_loop().run_in_executor(
            None, _run_validate
        )

        # Clean up temp input file
        if cleanup_temp and scad_input_path.exists():
            scad_input_path.unlink()

        # Parse stderr for messages
        parsed = _parse_openscad_stderr(result.stderr)

        is_valid = (
            result.returncode == 0 and len(parsed["errors"]) == 0
        )

        if ctx:
            status = "valid" if is_valid else "invalid"
            await ctx.info(
                f"Validation complete: {status} "
                f"({len(parsed['errors'])} error(s), "
                f"{len(parsed['warnings'])} warning(s))"
            )

        return {
            "success": True,
            "valid": is_valid,
            "errors": parsed["errors"],
            "warnings": parsed["warnings"],
            "echo_output": parsed["echo_output"],
            "deprecated": parsed["deprecated"],
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Validation failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def analyze_model(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Extract geometric information from an OpenSCAD model.

    Exports the model to a temporary STL file, then parses vertex
    data to compute bounding box, dimensions, center point, and
    triangle count. The temporary STL is cleaned up after parsing.

    Args:
        scad_content: OpenSCAD code to analyze (mutually exclusive
            with scad_file)
        scad_file: Path to OpenSCAD file to analyze (mutually
            exclusive with scad_content)
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via
            -I flags
        ctx: MCP context for logging

    Returns:
        Dict with success status, bounding_box (min/max xyz),
        dimensions (width/height/depth), center point, and
        triangle_count
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file "
                "must be provided"
            )

        config = get_config()

        # Security: validate scad_file path
        if scad_file:
            resolved_path = Path(scad_file).resolve()
            if config.security.allowed_paths:
                if not any(
                    str(resolved_path).startswith(
                        str(Path(ap).resolve())
                    )
                    for ap in config.security.allowed_paths
                ):
                    raise ValueError(
                        f"File path '{scad_file}' is not within "
                        f"allowed paths: "
                        f"{config.security.allowed_paths}"
                    )

        # Security: validate scad_content size
        if scad_content:
            max_bytes = (
                config.security.max_file_size_mb * 1024 * 1024
            )
            if len(scad_content) > max_bytes:
                raise ValueError(
                    f"SCAD content size ({len(scad_content)} bytes) "
                    f"exceeds maximum allowed size "
                    f"({config.security.max_file_size_mb} MB / "
                    f"{max_bytes} bytes)"
                )

        # Security: validate variable names
        if variables:
            for key in variables:
                if not re.match(
                    r'^[a-zA-Z_][a-zA-Z0-9_]*$', key
                ):
                    raise ValueError(
                        f"Invalid variable name '{key}': "
                        f"must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
                    )

        openscad_cmd = find_openscad()
        if not openscad_cmd:
            raise RuntimeError(
                "OpenSCAD not found. Please install OpenSCAD first."
            )

        # Ensure temp directory exists
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)

        # Handle input source
        cleanup_input = False
        if scad_content:
            tmp_input = (
                temp_dir_path
                / f"analyze_{uuid.uuid4().hex[:8]}.scad"
            )
            tmp_input.write_text(scad_content)
            scad_input_path = tmp_input
            cleanup_input = True
        else:
            scad_input_path = Path(scad_file)
            if not scad_input_path.exists():
                raise FileNotFoundError(
                    f"SCAD file not found: {scad_file}"
                )

        # Create temp STL output path
        stl_output = (
            temp_dir_path
            / f"analyze_{uuid.uuid4().hex[:8]}.stl"
        )

        # Build export command
        cmd = [openscad_cmd, "-o", str(stl_output)]

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

        # Add include paths
        if include_paths:
            for inc_path in include_paths:
                cmd.extend(["-I", str(inc_path)])

        cmd.append(str(scad_input_path))

        if ctx:
            await ctx.info("Analyzing model geometry...")

        # Run OpenSCAD export in executor
        def _run_export():
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=config.rendering.timeout_seconds,
                )
                return result
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"OpenSCAD export timed out after "
                    f"{config.rendering.timeout_seconds} seconds"
                )

        result = await asyncio.get_running_loop().run_in_executor(
            None, _run_export
        )

        # Clean up temp input file
        if cleanup_input and scad_input_path.exists():
            scad_input_path.unlink()

        if result.returncode != 0:
            # Clean up STL if it exists
            if stl_output.exists():
                stl_output.unlink()
            raise RuntimeError(
                f"OpenSCAD export failed: {result.stderr}"
            )

        if not stl_output.exists():
            raise RuntimeError(
                "OpenSCAD did not produce STL output file"
            )

        # Parse STL vertices
        try:
            vertices = _parse_stl_vertices(stl_output)
        finally:
            # Always clean up temp STL
            if stl_output.exists():
                stl_output.unlink()

        if not vertices:
            raise ValueError(
                "No vertices found in exported STL. "
                "The model may be empty."
            )

        # Calculate bounding box
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        zs = [v[2] for v in vertices]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)

        width = max_x - min_x
        height = max_y - min_y
        depth = max_z - min_z

        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        center_z = (min_z + max_z) / 2.0

        # Triangle count = vertices / 3 (each triangle has 3 verts)
        triangle_count = len(vertices) // 3

        if ctx:
            await ctx.info(
                f"Analysis complete: {triangle_count} triangles, "
                f"dimensions {width:.2f} x {height:.2f} x "
                f"{depth:.2f}"
            )

        return {
            "success": True,
            "bounding_box": {
                "min": [min_x, min_y, min_z],
                "max": [max_x, max_y, max_z],
            },
            "dimensions": {
                "width": width,
                "height": height,
                "depth": depth,
            },
            "center": [center_x, center_y, center_z],
            "triangle_count": triangle_count,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Analysis failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def get_libraries(
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Discover installed OpenSCAD libraries on the system.

    Searches standard OpenSCAD library paths for the current
    platform, plus the OPENSCADPATH environment variable. For each
    found library directory, lists subdirectories as libraries and
    reports file counts, README presence, and main entry files.

    This is a read-only operation that does not require OpenSCAD
    to be installed.

    Args:
        ctx: MCP context for logging

    Returns:
        Dict with success status, library_paths searched, and
        libraries list with name, path, file_count, has_readme,
        and main_files for each library
    """
    try:
        # Determine library search paths based on platform
        search_paths = []
        system = platform.system()

        if system == "Linux":
            home = Path.home()
            search_paths.extend([
                home / ".local" / "share" / "OpenSCAD" / "libraries",
                Path("/usr/share/openscad/libraries"),
                Path("/usr/local/share/openscad/libraries"),
            ])
        elif system == "Darwin":
            home = Path.home()
            search_paths.extend([
                home / "Documents" / "OpenSCAD" / "libraries",
                home / "Library" / "Application Support"
                / "OpenSCAD" / "libraries",
            ])
        elif system == "Windows":
            home = Path.home()
            search_paths.extend([
                home / "Documents" / "OpenSCAD" / "libraries",
            ])

        # Check OPENSCADPATH environment variable
        openscad_env = os.environ.get("OPENSCADPATH")
        if openscad_env:
            for p in openscad_env.split(os.pathsep):
                env_path = Path(p.strip())
                if env_path not in search_paths:
                    search_paths.append(env_path)

        if ctx:
            await ctx.info(
                f"Searching {len(search_paths)} library path(s)..."
            )

        # Scan each path for libraries
        found_paths = []
        libraries = []

        for lib_dir in search_paths:
            if not lib_dir.exists() or not lib_dir.is_dir():
                continue

            found_paths.append(str(lib_dir))

            # Each subdirectory is potentially a library
            for entry in sorted(lib_dir.iterdir()):
                if not entry.is_dir():
                    # Also check for top-level .scad files
                    continue

                # Count .scad files in the library
                scad_files = list(entry.rglob("*.scad"))
                file_count = len(scad_files)

                # Check for README files
                readme_names = [
                    "README", "README.md", "README.txt",
                    "readme.md", "readme.txt",
                ]
                has_readme = any(
                    (entry / rn).exists() for rn in readme_names
                )

                # Identify main entry files
                main_file_candidates = [
                    "std.scad", "main.scad", "lib.scad",
                    f"{entry.name}.scad",
                ]
                main_files = [
                    mf for mf in main_file_candidates
                    if (entry / mf).exists()
                ]

                libraries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "file_count": file_count,
                    "has_readme": has_readme,
                    "main_files": main_files,
                })

        if ctx:
            await ctx.info(
                f"Found {len(libraries)} library(ies) in "
                f"{len(found_paths)} path(s)"
            )

        return {
            "success": True,
            "library_paths": found_paths,
            "libraries": libraries,
        }

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Library discovery failed: {str(e)}"
            )
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def compare_renders(
    scad_content_before: Optional[str] = None,
    scad_content_after: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables_before: Optional[Dict[str, Any]] = None,
    variables_after: Optional[Dict[str, Any]] = None,
    view: Optional[str] = "isometric",
    image_size: Optional[str] = None,
    quality: Optional[str] = "draft",
    ctx: Optional[Context] = None,
):
    """
    Render two versions of a model for visual comparison.

    Supports two modes:
    1. Two different SCAD contents: provide scad_content_before and
       scad_content_after.
    2. Same file with different variables: provide scad_file with
       variables_before and variables_after.

    Both versions are rendered in parallel for efficiency. Uses
    the existing render_scad_to_png helper and QUALITY_PRESETS.

    Args:
        scad_content_before: OpenSCAD code for the "before" version
        scad_content_after: OpenSCAD code for the "after" version
        scad_file: Path to OpenSCAD file (used with variable diffs)
        variables_before: Variables for the "before" render
        variables_after: Variables for the "after" render
        view: View preset name (default: "isometric"). Valid names:
            "front", "back", "left", "right", "top", "bottom",
            "isometric", "dimetric"
        image_size: Image dimensions - accepts "widthxheight",
            "width,height", "[width, height]", or [width, height]
            list (default: [800, 600])
        quality: Quality preset - "draft", "normal", or "high"
            (default: "draft")
        ctx: MCP context for logging

    Returns:
        List with before/after images and metadata
    """
    try:
        # Validate input combinations
        has_both_contents = (
            scad_content_before is not None
            and scad_content_after is not None
        )
        has_file_with_vars = (
            scad_file is not None
            and variables_before is not None
            and variables_after is not None
        )

        if not has_both_contents and not has_file_with_vars:
            raise ValueError(
                "Provide either (scad_content_before + "
                "scad_content_after) or (scad_file + "
                "variables_before + variables_after)"
            )

        # Validate view preset
        if view and view not in VIEW_PRESETS:
            raise ValueError(
                f"Invalid view name '{view}'. "
                f"Must be one of: "
                f"{', '.join(VIEW_PRESETS.keys())}"
            )

        # Validate quality preset
        if quality and quality not in QUALITY_PRESETS:
            raise ValueError(
                f"Invalid quality preset '{quality}'. "
                f"Must be one of: "
                f"{', '.join(QUALITY_PRESETS.keys())}"
            )

        # Parse image size
        parsed_image_size = parse_image_size_param(
            image_size, [800, 600]
        )

        # Get camera settings from view preset
        if view:
            preset_pos, preset_target, preset_up = (
                VIEW_PRESETS[view]
            )
            cam_pos = list(preset_pos)
            cam_target = list(preset_target)
            cam_up = list(preset_up)
        else:
            cam_pos = [200, 200, 200]
            cam_target = [0, 0, 0]
            cam_up = [0, 0, 1]

        # Build quality variables
        quality_vars = {}
        if quality:
            quality_vars = dict(QUALITY_PRESETS.get(quality, {}))

        # Prepare before/after render parameters
        if has_both_contents:
            before_content = scad_content_before
            after_content = scad_content_after
            before_file = None
            after_file = None
            before_vars = dict(quality_vars)
            after_vars = dict(quality_vars)
            if variables_before:
                before_vars.update(variables_before)
            if variables_after:
                after_vars.update(variables_after)
        else:
            before_content = None
            after_content = None
            before_file = scad_file
            after_file = scad_file
            before_vars = dict(quality_vars)
            before_vars.update(variables_before)
            after_vars = dict(quality_vars)
            after_vars.update(variables_after)

        if ctx:
            await ctx.info(
                "Rendering before and after versions in parallel..."
            )

        # Define render functions for before and after
        def _render_before():
            return render_scad_to_png(
                scad_content=before_content,
                scad_file=before_file,
                camera_position=cam_pos,
                camera_target=cam_target,
                camera_up=cam_up,
                image_size=parsed_image_size,
                color_scheme="Cornfield",
                variables=before_vars if before_vars else None,
                auto_center=True,
            )

        def _render_after():
            return render_scad_to_png(
                scad_content=after_content,
                scad_file=after_file,
                camera_position=cam_pos,
                camera_target=cam_target,
                camera_up=cam_up,
                image_size=parsed_image_size,
                color_scheme="Cornfield",
                variables=after_vars if after_vars else None,
                auto_center=True,
            )

        # Render both in parallel
        loop = asyncio.get_running_loop()
        before_task = loop.run_in_executor(
            None, _render_before
        )
        after_task = loop.run_in_executor(
            None, _render_after
        )
        before_b64, after_b64 = await asyncio.gather(
            before_task, after_task
        )

        if ctx:
            await ctx.info("Comparison renders completed")

        before_bytes = base64.b64decode(before_b64)
        after_bytes = base64.b64decode(after_b64)

        return [
            "Before:",
            MCPImage(data=before_bytes, format="png"),
            "After:",
            MCPImage(data=after_bytes, format="png"),
            json.dumps({
                "success": True,
                "view": view or "isometric",
                "quality": quality or "draft",
            }),
        ]

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Comparison render failed: {str(e)}"
            )
        return [
            json.dumps({
                "success": False,
                "error": str(e),
            })
        ]


# ============================================================================
# Cache Management Tools
# ============================================================================


@mcp.tool()
async def clear_cache(
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Delete all cached render files and report freed space.

    Removes every ``.png`` file from the configured cache directory.
    Does nothing (and still reports success) when the cache is disabled
    or the directory does not exist.

    Args:
        ctx: MCP context for logging

    Returns:
        Dict with success status, cleared_files count, and freed_bytes
    """
    config = get_config()
    cache_dir = config.cache.directory

    if not cache_dir.exists():
        if ctx:
            await ctx.info("Cache directory does not exist; nothing to clear")
        return {
            "success": True,
            "cleared_files": 0,
            "freed_bytes": 0,
        }

    cleared = 0
    freed = 0
    for f in cache_dir.glob("*.png"):
        try:
            size = f.stat().st_size
            f.unlink()
            cleared += 1
            freed += size
        except OSError as exc:
            logger.warning("Failed to delete cache file %s: %s", f, exc)

    if ctx:
        await ctx.info(
            f"Cleared {cleared} cached file(s), freed {freed} bytes"
        )

    return {
        "success": True,
        "cleared_files": cleared,
        "freed_bytes": freed,
    }


# ============================================================================
# Multi-file Project Tools
# ============================================================================


def _extract_scad_dependencies(file_path: Path) -> List[str]:
    """Parse an OpenSCAD file and return its include/use dependencies.

    Looks for lines matching ``include <...>`` or ``use <...>`` and
    extracts the referenced file path string.

    Args:
        file_path: Path to the ``.scad`` file to parse.

    Returns:
        List of dependency path strings as written in the source.
    """
    deps: List[str] = []
    include_use_re = re.compile(
        r'^\s*(?:include|use)\s*<\s*([^>]+?)\s*>\s*;?\s*$'
    )
    try:
        text = file_path.read_text(errors="replace")
        for line in text.splitlines():
            m = include_use_re.match(line)
            if m:
                deps.append(m.group(1))
    except OSError:
        pass
    return deps


@mcp.tool()
async def get_project_files(
    project_dir: str,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    List all OpenSCAD files in a project directory and map their dependencies.

    Recursively finds every ``.scad`` file under *project_dir*, parses
    each file for ``include`` and ``use`` statements, and returns a
    structured overview of the project's file tree and dependency graph.

    Args:
        project_dir: Root directory of the OpenSCAD project. Validated
            against ``security.allowed_paths`` when configured.
        ctx: MCP context for logging

    Returns:
        Dict with success status, files list (each with name, path,
        size_bytes, modified), and dependencies mapping (relative path
        to list of dependency strings).
    """
    try:
        config = get_config()
        resolved_dir = Path(project_dir).resolve()

        # Security: validate against allowed_paths
        if config.security.allowed_paths:
            if not any(
                str(resolved_dir).startswith(str(Path(ap).resolve()))
                for ap in config.security.allowed_paths
            ):
                raise ValueError(
                    f"Project directory '{project_dir}' is not within "
                    f"allowed paths: {config.security.allowed_paths}"
                )

        if not resolved_dir.exists():
            raise FileNotFoundError(
                f"Project directory not found: {project_dir}"
            )
        if not resolved_dir.is_dir():
            raise ValueError(
                f"Path is not a directory: {project_dir}"
            )

        files_info: List[Dict[str, Any]] = []
        dependencies: Dict[str, List[str]] = {}

        for scad_file in sorted(resolved_dir.rglob("*.scad")):
            try:
                stat = scad_file.stat()
            except OSError:
                continue

            rel = str(scad_file.relative_to(resolved_dir))
            files_info.append({
                "name": scad_file.name,
                "path": str(scad_file),
                "relative_path": rel,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })

            deps = _extract_scad_dependencies(scad_file)
            if deps:
                dependencies[rel] = deps

        if ctx:
            await ctx.info(
                f"Found {len(files_info)} .scad file(s) in {project_dir}"
            )

        return {
            "success": True,
            "files": files_info,
            "dependencies": dependencies,
        }

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Failed to scan project files: {str(e)}"
            )
        return {
            "success": False,
            "error": str(e),
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
        "active_operations": 0,  # Simplified for MVP
        "cache_enabled": config.cache.enabled,
        "supported_formats": ["png", "stl", "3mf", "amf", "off", "dxf", "svg"],
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