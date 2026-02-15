"""
OpenSCAD MCP Server - A Model Context Protocol server for OpenSCAD rendering.

This package provides MCP tools and resources for rendering 3D models using OpenSCAD.
"""

from importlib.metadata import version as _pkg_version

from .server import mcp
from .types import (
    ColorScheme,
    ImageSize,
    OpenSCADInfo,
    ServerInfo,
    TransportType,
    Vector3D,
)

__version__ = _pkg_version("openscad-mcp")
__all__ = [
    "mcp",
    "ColorScheme",
    "ImageSize",
    "OpenSCADInfo",
    "ServerInfo",
    "TransportType",
    "Vector3D",
]
