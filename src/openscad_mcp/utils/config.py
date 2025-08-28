"""
Configuration management for OpenSCAD MCP Server.

This module provides centralized configuration with environment variable
support, type safety, and sensible defaults.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from ..types import TransportType


class RenderingConfig(BaseModel):
    """Rendering-specific configuration."""

    max_concurrent: int = Field(
        5,
        ge=1,
        le=20,
        description="Maximum concurrent rendering operations",
    )
    queue_size: int = Field(
        100,
        ge=10,
        le=1000,
        description="Maximum queue size",
    )
    timeout_seconds: int = Field(
        300,
        ge=30,
        le=3600,
        description="Render timeout in seconds",
    )
    max_image_width: int = Field(
        4096,
        ge=100,
        le=8192,
        description="Maximum image width",
    )
    max_image_height: int = Field(
        4096,
        ge=100,
        le=8192,
        description="Maximum image height",
    )
    max_animation_frames: int = Field(
        360,
        ge=2,
        le=1000,
        description="Maximum animation frames",
    )
    default_color_scheme: str = Field(
        "Cornfield",
        description="Default OpenSCAD color scheme",
    )


class CacheConfig(BaseModel):
    """Cache configuration."""

    enabled: bool = Field(True, description="Enable caching")
    directory: Path = Field(
        Path.home() / ".cache" / "openscad-mcp",
        description="Cache directory",
    )
    max_size_mb: int = Field(
        500,
        ge=100,
        le=10000,
        description="Maximum cache size in MB",
    )
    ttl_hours: int = Field(
        24,
        ge=1,
        le=168,
        description="Cache time-to-live in hours",
    )

    @field_validator("directory")
    @classmethod
    def ensure_directory(cls, v: Path) -> Path:
        """Ensure cache directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v


class SecurityConfig(BaseModel):
    """Security configuration."""

    rate_limit: int = Field(
        60,
        ge=0,
        le=1000,
        description="Max requests per minute (0 to disable)",
    )
    max_file_size_mb: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum SCAD file size in MB",
    )
    allowed_paths: Optional[list[str]] = Field(
        None,
        description="Allowed paths for file access",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(
        "INFO",
        description="Logging level",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
    )
    file: Optional[Path] = Field(
        None,
        description="Log file path",
    )
    max_size_mb: int = Field(
        100,
        ge=10,
        le=1000,
        description="Maximum log file size in MB",
    )
    rotate_count: int = Field(
        5,
        ge=1,
        le=10,
        description="Number of rotated log files to keep",
    )


class ServerConfig(BaseModel):
    """Server configuration."""

    name: str = Field(
        "OpenSCAD MCP Server",
        description="Server name",
    )
    version: str = Field(
        "0.1.0",
        description="Server version",
    )
    transport: TransportType = Field(
        TransportType.STDIO,
        description="Transport type",
    )
    host: str = Field(
        "localhost",
        description="Host for HTTP/SSE transport",
    )
    port: int = Field(
        8000,
        ge=1024,
        le=65535,
        description="Port for HTTP/SSE transport",
    )


class Config(BaseModel):
    """Main configuration class."""

    # Paths
    openscad_path: Optional[str] = Field(
        None,
        description="Path to OpenSCAD executable",
    )
    imagemagick_path: Optional[str] = Field(
        None,
        description="Path to ImageMagick convert command",
    )
    temp_dir: Path = Field(
        Path.cwd() / ".openscad-mcp" / "tmp",
        description="Temporary file directory",
    )

    # Sub-configurations
    server: ServerConfig = Field(default_factory=ServerConfig)
    rendering: RenderingConfig = Field(default_factory=RenderingConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("temp_dir")
    @classmethod
    def ensure_temp_dir(cls, v: Path) -> Path:
        """Ensure temp directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "Config":
        """
        Load configuration from environment variables.

        Args:
            env_file: Optional path to .env file

        Returns:
            Configured Config instance
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()  # Load from default .env

        # Build configuration from environment
        config_dict = {}

        # Paths
        if openscad := os.getenv("OPENSCAD_PATH"):
            config_dict["openscad_path"] = openscad
        if imagemagick := os.getenv("IMAGEMAGICK_PATH"):
            config_dict["imagemagick_path"] = imagemagick
        if temp_dir := os.getenv("MCP_TEMP_DIR"):
            config_dict["temp_dir"] = Path(temp_dir)

        # Server configuration
        server_config = {}
        if transport := os.getenv("MCP_TRANSPORT"):
            server_config["transport"] = transport
        if host := os.getenv("MCP_HOST"):
            server_config["host"] = host
        if port := os.getenv("MCP_PORT"):
            server_config["port"] = int(port)
        if server_config:
            config_dict["server"] = ServerConfig(**server_config)

        # Rendering configuration
        rendering_config = {}
        if max_concurrent := os.getenv("MCP_MAX_CONCURRENT_RENDERS"):
            rendering_config["max_concurrent"] = int(max_concurrent)
        if queue_size := os.getenv("MCP_QUEUE_SIZE"):
            rendering_config["queue_size"] = int(queue_size)
        if timeout := os.getenv("MCP_RENDER_TIMEOUT"):
            rendering_config["timeout_seconds"] = int(timeout)
        if max_width := os.getenv("MCP_MAX_IMAGE_WIDTH"):
            rendering_config["max_image_width"] = int(max_width)
        if max_height := os.getenv("MCP_MAX_IMAGE_HEIGHT"):
            rendering_config["max_image_height"] = int(max_height)
        if max_frames := os.getenv("MCP_MAX_ANIMATION_FRAMES"):
            rendering_config["max_animation_frames"] = int(max_frames)
        if rendering_config:
            config_dict["rendering"] = RenderingConfig(**rendering_config)

        # Cache configuration
        cache_config = {}
        if cache_enabled := os.getenv("MCP_CACHE_ENABLED"):
            cache_config["enabled"] = cache_enabled.lower() == "true"
        if cache_size := os.getenv("MCP_CACHE_SIZE_MB"):
            cache_config["max_size_mb"] = int(cache_size)
        if cache_ttl := os.getenv("MCP_CACHE_TTL_HOURS"):
            cache_config["ttl_hours"] = int(cache_ttl)
        if cache_config:
            config_dict["cache"] = CacheConfig(**cache_config)

        # Security configuration
        security_config = {}
        if rate_limit := os.getenv("MCP_RATE_LIMIT"):
            security_config["rate_limit"] = int(rate_limit)
        if max_file_size := os.getenv("MCP_MAX_FILE_SIZE_MB"):
            security_config["max_file_size_mb"] = int(max_file_size)
        if security_config:
            config_dict["security"] = SecurityConfig(**security_config)

        # Logging configuration
        logging_config = {}
        if log_level := os.getenv("MCP_LOG_LEVEL"):
            logging_config["level"] = log_level
        if log_file := os.getenv("MCP_LOG_FILE"):
            logging_config["file"] = Path(log_file)
        if logging_config:
            config_dict["logging"] = LoggingConfig(**logging_config)

        return cls(**config_dict)

    @classmethod
    def from_yaml(cls, yaml_file: str) -> "Config":
        """
        Load configuration from YAML file.

        Args:
            yaml_file: Path to YAML configuration file

        Returns:
            Configured Config instance
        """
        with open(yaml_file, "r") as f:
            config_dict = yaml.safe_load(f)
        return cls(**config_dict)

    def to_yaml(self, yaml_file: str) -> None:
        """
        Save configuration to YAML file.

        Args:
            yaml_file: Path to save YAML configuration
        """
        with open(yaml_file, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """
    Get the global configuration instance.

    Returns:
        The global Config instance
    """
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_config(config: Config) -> None:
    """
    Set the global configuration instance.

    Args:
        config: Config instance to set globally
    """
    global _config
    _config = config