"""
Type definitions and schemas for OpenSCAD MCP Server.

This module contains all Pydantic models, enums, and type definitions
used throughout the application for validation and serialization.
"""

from enum import Enum
from typing import Any, List, Tuple
import json

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================================
# Enums
# ============================================================================


class ColorScheme(str, Enum):
    """OpenSCAD color schemes."""

    CORNFIELD = "Cornfield"
    SUNSET = "Sunset"
    METALLIC = "Metallic"
    STARNIGHT = "Starnight"
    BEFORE_DAWN = "BeforeDawn"
    NATURE = "Nature"
    DEEP_OCEAN = "DeepOcean"
    TOMORROW = "Tomorrow"
    TOMORROW_NIGHT = "Tomorrow Night"
    MONOTONE = "Monotone"


class TransportType(str, Enum):
    """MCP transport types."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


# ============================================================================
# Base Models
# ============================================================================


class Vector3D(BaseModel):
    """3D vector for positions and directions."""

    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")
    z: float = Field(..., description="Z coordinate")

    @model_validator(mode="before")
    @classmethod
    def parse_vector_input(cls, data: Any) -> Any:
        """Parse various input formats for Vector3D.

        Handles:
        - Dict format: {"x": 1, "y": 2, "z": 3}
        - List format: [1, 2, 3]
        - String representation of list: "[1, 2, 3]"
        - String representation of dict: '{"x": 1, "y": 2, "z": 3}'
        """
        # If it's already a dict with x, y, z keys, return as is
        if isinstance(data, dict) and "x" in data and "y" in data and "z" in data:
            return data

        # If it's a string, try to parse it as JSON
        if isinstance(data, str):
            try:
                # Remove any whitespace and try to parse
                data = data.strip()
                parsed = json.loads(data)

                # If parsed result is a list
                if isinstance(parsed, list) and len(parsed) == 3:
                    return {"x": parsed[0], "y": parsed[1], "z": parsed[2]}
                # If parsed result is a dict
                elif isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                # If JSON parsing fails, it might be a malformed string
                raise ValueError(f"Cannot parse '{data}' as a valid Vector3D")

        # If it's a list or tuple with 3 elements
        if isinstance(data, (list, tuple)) and len(data) == 3:
            return {"x": data[0], "y": data[1], "z": data[2]}

        # If none of the above, return as is and let Pydantic handle it
        return data

    def to_tuple(self) -> Tuple[float, float, float]:
        """Convert to tuple format."""
        return (self.x, self.y, self.z)

    @classmethod
    def from_tuple(cls, values: Tuple[float, float, float]) -> "Vector3D":
        """Create from tuple."""
        return cls(x=values[0], y=values[1], z=values[2])


class ImageSize(BaseModel):
    """Image dimensions."""

    width: int = Field(800, ge=1, le=4096, description="Image width in pixels")
    height: int = Field(600, ge=1, le=4096, description="Image height in pixels")

    @model_validator(mode="before")
    @classmethod
    def parse_image_size_input(cls, data: Any) -> Any:
        """Parse various input formats for ImageSize.

        Handles:
        - Dict format: {"width": 800, "height": 600}
        - List format: [800, 600]
        - String representation of list: "[800, 600]"
        - String representation of dict: '{"width": 800, "height": 600}'
        """
        # If it's already a dict with width/height keys, return as is
        if isinstance(data, dict) and "width" in data and "height" in data:
            return data

        # If it's a string, try to parse it as JSON
        if isinstance(data, str):
            try:
                # Remove any whitespace and try to parse
                data = data.strip()
                parsed = json.loads(data)

                # If parsed result is a list with 2 elements
                if isinstance(parsed, list) and len(parsed) == 2:
                    return {"width": parsed[0], "height": parsed[1]}
                # If parsed result is a dict
                elif isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                # If JSON parsing fails, it might be a malformed string
                raise ValueError(f"Cannot parse '{data}' as a valid ImageSize")

        # If it's a list or tuple with 2 elements
        if isinstance(data, (list, tuple)) and len(data) == 2:
            return {"width": data[0], "height": data[1]}

        # If none of the above, return as is and let Pydantic handle it
        return data

    def to_tuple(self) -> Tuple[int, int]:
        """Convert to tuple format."""
        return (self.width, self.height)

    @classmethod
    def from_tuple(cls, values: Tuple[int, int]) -> "ImageSize":
        """Create from tuple."""
        return cls(width=values[0], height=values[1])

    @field_validator("width", "height")
    @classmethod
    def validate_size(cls, v: int, info) -> int:
        """Validate image dimensions."""
        if v > 4096:
            raise ValueError(f"{info.field_name} exceeds maximum of 4096 pixels")
        return v

    @model_validator(mode="after")
    def validate_total_pixels(self) -> "ImageSize":
        """Validate total pixel count."""
        total_pixels = self.width * self.height
        if total_pixels > 16777216:  # 4K limit
            raise ValueError(f"Total pixels ({total_pixels}) exceeds 4K limit (16777216)")
        return self


# ============================================================================
# Server Information
# ============================================================================


class OpenSCADInfo(BaseModel):
    """Information about OpenSCAD installation."""

    installed: bool = Field(..., description="Whether OpenSCAD is installed")
    version: str | None = Field(None, description="OpenSCAD version")
    path: str | None = Field(None, description="Path to OpenSCAD executable")
    searched_paths: List[str] | None = Field(None, description="Paths that were searched")


class ServerInfo(BaseModel):
    """Server configuration and capabilities."""

    version: str = Field(..., description="Server version")
    openscad_version: str | None = Field(None, description="OpenSCAD version")
    openscad_path: str | None = Field(None, description="Path to OpenSCAD")
    imagemagick_available: bool = Field(False, description="Whether ImageMagick is available")
    max_concurrent_renders: int = Field(..., description="Maximum concurrent renders")
    active_operations: int = Field(..., description="Currently active operations")
    cache_enabled: bool = Field(..., description="Whether caching is enabled")
    supported_formats: List[str] = Field(
        default_factory=lambda: ["png"], description="Supported output formats"
    )
