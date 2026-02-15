"""
OpenSCAD MCP Server - A Model Context Protocol server for OpenSCAD rendering.

This package provides MCP tools and resources for rendering 3D models using OpenSCAD.
"""

from .server import mcp
from .types import (
    ColorScheme,
    ImageSize,
    OpenSCADInfo,
    ServerInfo,
    TransportType,
    Vector3D,
)

__version__ = "0.2.0"
__all__ = [
    "mcp",
    "ColorScheme",
    "ImageSize",
    "OpenSCADInfo",
    "ServerInfo",
    "TransportType",
    "Vector3D",
]
