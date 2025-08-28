"""
Type definitions and schemas for OpenSCAD MCP Server.

This module contains all Pydantic models, enums, and type definitions
used throughout the application for validation and serialization.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
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


class RenderStatus(str, Enum):
    """Status of a rendering operation."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    """Queue priority levels."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TransportType(str, Enum):
    """MCP transport types."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class PredefinedView(str, Enum):
    """Predefined camera views for rendering."""

    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    ISOMETRIC = "isometric"
    DIMETRIC = "dimetric"


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
# Render Parameters
# ============================================================================


class RenderParams(BaseModel):
    """Base parameters for rendering operations."""

    scad_content: Optional[str] = Field(None, description="OpenSCAD code to render")
    scad_file: Optional[str] = Field(None, description="Path to OpenSCAD file")
    variables: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Variables to pass to OpenSCAD"
    )
    image_size: ImageSize = Field(default_factory=ImageSize, description="Output image size")
    color_scheme: ColorScheme = Field(
        ColorScheme.CORNFIELD, description="OpenSCAD color scheme"
    )
    auto_center: bool = Field(False, description="Auto-center the model")

    @model_validator(mode="after")
    def validate_source(self) -> "RenderParams":
        """Ensure exactly one source is provided."""
        if bool(self.scad_content) == bool(self.scad_file):
            raise ValueError("Exactly one of scad_content or scad_file must be provided")
        return self


class SingleRenderParams(RenderParams):
    """Parameters for single view rendering."""

    view: Optional[Union[PredefinedView, str]] = Field(
        None, 
        description="Predefined view name (front, back, left, right, top, bottom, isometric, dimetric) - overrides camera settings if provided"
    )
    camera_position: Union[Vector3D, List[float], str] = Field(
        default_factory=lambda: Vector3D(x=70, y=70, z=70),
        description="Camera position in 3D space (accepts Vector3D, list [x,y,z], or JSON string)",
    )
    camera_target: Union[Vector3D, List[float], str] = Field(
        default_factory=lambda: Vector3D(x=0, y=0, z=0),
        description="Point camera looks at (accepts Vector3D, list [x,y,z], or JSON string)",
    )
    camera_up: Union[Vector3D, List[float], str] = Field(
        default_factory=lambda: Vector3D(x=0, y=0, z=1),
        description="Camera up vector (accepts Vector3D, list [x,y,z], or JSON string)",
    )

    @field_validator("camera_position", "camera_target", "camera_up", mode="before")
    @classmethod
    def parse_camera_params(cls, v: Any) -> Vector3D:
        """Parse camera parameters into Vector3D objects.
        
        Accepts:
        - Vector3D objects
        - Lists/tuples of 3 floats: [x, y, z]
        - JSON string representation: "[x, y, z]"
        - Dict format: {"x": x, "y": y, "z": z}
        """
        # If it's already a Vector3D, return it
        if isinstance(v, Vector3D):
            return v
        
        # Use Vector3D's validator to handle the conversion
        return Vector3D.model_validate(v)


class PerspectiveParams(RenderParams):
    """Parameters for perspective views rendering."""

    distance: float = Field(200.0, gt=0, description="Camera distance from origin")
    views: Optional[List[str]] = Field(
        None,
        description="Specific views to render (default: all)",
        json_schema_extra={
            "example": ["front", "back", "left", "right", "top", "bottom", "isometric", "dimetric"]
        }
    )


class TurntableParams(RenderParams):
    """Parameters for turntable animation."""

    num_frames: int = Field(36, ge=2, le=360, description="Number of frames to generate")
    radius: float = Field(100.0, gt=0, description="Camera orbit radius")
    height: float = Field(50.0, description="Camera height")
    look_at: Vector3D = Field(
        default_factory=lambda: Vector3D(x=0, y=0, z=0),
        description="Point to look at",
    )
    create_gif: bool = Field(False, description="Create animated GIF (requires ImageMagick)")
    gif_delay: int = Field(100, ge=10, le=1000, description="Delay between frames in milliseconds")


# ============================================================================
# Results
# ============================================================================


class RenderResult(BaseModel):
    """Result from a rendering operation."""

    success: bool = Field(..., description="Whether the operation succeeded")
    operation_id: str = Field(..., description="Unique operation identifier")
    data: Optional[str] = Field(None, description="Base64-encoded image data")
    mime_type: str = Field("image/png", description="MIME type of the result")
    error: Optional[str] = Field(None, description="Error message if failed")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class MultiRenderResult(BaseModel):
    """Result from multiple rendering operations."""

    success: bool = Field(..., description="Whether all operations succeeded")
    operation_id: str = Field(..., description="Unique operation identifier")
    images: Dict[str, str] = Field(
        default_factory=dict, description="Map of view names to base64 image data"
    )
    errors: Dict[str, str] = Field(
        default_factory=dict, description="Map of view names to error messages"
    )
    mime_type: str = Field("image/png", description="MIME type of the results")


class AnimationResult(BaseModel):
    """Result from animation rendering."""

    success: bool = Field(..., description="Whether the operation succeeded")
    operation_id: str = Field(..., description="Unique operation identifier")
    frames: List[str] = Field(default_factory=list, description="List of base64-encoded frames")
    gif_data: Optional[str] = Field(None, description="Base64-encoded GIF data if created")
    errors: List[str] = Field(default_factory=list, description="Any errors encountered")
    mime_type: str = Field("image/png", description="MIME type of frame images")


# ============================================================================
# Validation Results
# ============================================================================


class ValidationResult(BaseModel):
    """Result from input validation."""

    is_valid: bool = Field(..., description="Whether the input is valid")
    errors: List[str] = Field(default_factory=list, description="List of validation errors")
    warnings: List[str] = Field(default_factory=list, description="List of warnings")
    normalized_input: Optional[Dict[str, Any]] = Field(
        None, description="Normalized and validated input"
    )


# ============================================================================
# Operation Management
# ============================================================================


class RenderOperation(BaseModel):
    """Represents a queued rendering operation."""

    operation_id: str = Field(..., description="Unique operation identifier")
    task_type: str = Field(..., description="Type of rendering task")
    params: Dict[str, Any] = Field(..., description="Task parameters")
    priority: Priority = Field(Priority.NORMAL, description="Operation priority")
    status: RenderStatus = Field(RenderStatus.QUEUED, description="Current status")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    started_at: Optional[datetime] = Field(None, description="Start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    progress: int = Field(0, ge=0, le=100, description="Progress percentage")
    result: Optional[Union[RenderResult, MultiRenderResult, AnimationResult]] = Field(
        None, description="Operation result"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class QueueStatus(BaseModel):
    """Status of the render queue."""

    total_queued: int = Field(..., description="Total items in queue")
    active_operations: int = Field(..., description="Currently processing operations")
    completed_today: int = Field(..., description="Operations completed today")
    failed_today: int = Field(..., description="Operations failed today")
    average_wait_time: float = Field(..., description="Average wait time in seconds")
    estimated_wait: float = Field(..., description="Estimated wait for new operation")


# ============================================================================
# Server Information
# ============================================================================


class OpenSCADInfo(BaseModel):
    """Information about OpenSCAD installation."""

    installed: bool = Field(..., description="Whether OpenSCAD is installed")
    version: Optional[str] = Field(None, description="OpenSCAD version")
    path: Optional[str] = Field(None, description="Path to OpenSCAD executable")
    searched_paths: Optional[List[str]] = Field(None, description="Paths that were searched")


class ServerInfo(BaseModel):
    """Server configuration and capabilities."""

    version: str = Field(..., description="Server version")
    openscad_version: Optional[str] = Field(None, description="OpenSCAD version")
    openscad_path: Optional[str] = Field(None, description="Path to OpenSCAD")
    imagemagick_available: bool = Field(False, description="Whether ImageMagick is available")
    max_concurrent_renders: int = Field(..., description="Maximum concurrent renders")
    queue_size: int = Field(..., description="Maximum queue size")
    active_operations: int = Field(..., description="Currently active operations")
    cache_enabled: bool = Field(..., description="Whether caching is enabled")
    supported_formats: List[str] = Field(
        default_factory=lambda: ["png"], description="Supported output formats"
    )


# ============================================================================
# Progress Events
# ============================================================================


class ProgressEvent(BaseModel):
    """Progress update event."""

    operation_id: str = Field(..., description="Operation identifier")
    status: RenderStatus = Field(..., description="Current status")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    message: str = Field(..., description="Progress message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")


# ============================================================================
# Error Types
# ============================================================================


class OpenSCADError(BaseModel):
    """OpenSCAD-specific error information."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")
    suggestions: List[str] = Field(default_factory=list, description="Suggested fixes")


# Type aliases for convenience
CameraPosition = Tuple[float, float, float]
CameraTarget = Tuple[float, float, float]
CameraUp = Tuple[float, float, float]
ImageDimensions = Tuple[int, int]