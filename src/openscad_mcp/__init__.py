"""
OpenSCAD MCP Server - A Model Context Protocol server for OpenSCAD rendering.

This package provides MCP tools and resources for rendering 3D models using OpenSCAD.
"""

from .server import mcp
from .types import (
    ColorScheme,
    ImageSize,
    OpenSCADInfo,
    RenderOperation,
    RenderParams,
    PerspectiveParams,
    RenderResult,
    SingleRenderParams,
    TurntableParams,
    ServerInfo,
    Vector3D,
)

__version__ = "0.1.0"
__all__ = [
    "mcp",
    "ColorScheme",
    "ImageSize",
    "OpenSCADInfo",
    "RenderOperation",
    "RenderParams",
    "PerspectiveParams",
    "RenderResult",
    "SingleRenderParams",
    "TurntableParams",
    "ServerInfo",
    "Vector3D",
]