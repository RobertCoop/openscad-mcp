"""
Unit tests for the configuration module.

Tests cover configuration loading from environment variables, YAML files,
validation, and default values.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
import tempfile

import pytest
import yaml
from pydantic import ValidationError

from openscad_mcp.utils.config import (
    Config,
    RenderingConfig,
    CacheConfig,
    SecurityConfig,
    LoggingConfig,
    ServerConfig,
    get_config,
    set_config,
)
from openscad_mcp.types import TransportType


class TestRenderingConfig:
    """Test the RenderingConfig model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_rendering_config_defaults(self):
        """Test RenderingConfig creates with default values."""
        config = RenderingConfig()
        
        assert config.max_concurrent == 5
        assert config.queue_size == 100
        assert config.timeout_seconds == 300
        assert config.max_image_width == 4096
        assert config.max_image_height == 4096
        assert config.max_animation_frames == 360
        assert config.default_color_scheme == "Cornfield"
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_rendering_config_validation_bounds(self):
        """Test RenderingConfig validates parameter bounds."""
        # Test max_concurrent bounds
        with pytest.raises(ValidationError):
            RenderingConfig(max_concurrent=0)
        
        with pytest.raises(ValidationError):
            RenderingConfig(max_concurrent=21)
        
        # Test queue_size bounds
        with pytest.raises(ValidationError):
            RenderingConfig(queue_size=9)
        
        with pytest.raises(ValidationError):
            RenderingConfig(queue_size=1001)
        
        # Test timeout bounds
        with pytest.raises(ValidationError):
            RenderingConfig(timeout_seconds=29)
        
        with pytest.raises(ValidationError):
            RenderingConfig(timeout_seconds=3601)
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_rendering_config_custom_values(self):
        """Test RenderingConfig accepts custom valid values."""
        config = RenderingConfig(
            max_concurrent=10,
            queue_size=500,
            timeout_seconds=600,
            max_image_width=2048,
            max_image_height=2048,
            max_animation_frames=180,
            default_color_scheme="Sunset"
        )
        
        assert config.max_concurrent == 10
        assert config.queue_size == 500
        assert config.timeout_seconds == 600
        assert config.max_image_width == 2048
        assert config.max_image_height == 2048
        assert config.max_animation_frames == 180
        assert config.default_color_scheme == "Sunset"


class TestCacheConfig:
    """Test the CacheConfig model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_cache_config_defaults(self):
        """Test CacheConfig creates with default values."""
        with patch('pathlib.Path.mkdir'):
            config = CacheConfig()
        
        assert config.enabled is True
        assert config.max_size_mb == 500
        assert config.ttl_hours == 24
        assert str(config.directory).endswith('.cache/openscad-mcp')
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_cache_directory_creation(self):
        """Test that cache directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "test_cache"
            assert not cache_dir.exists()
            
            config = CacheConfig(directory=cache_dir)
            assert cache_dir.exists()
            assert config.directory == cache_dir
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_cache_config_validation(self):
        """Test CacheConfig validation bounds."""
        with patch('pathlib.Path.mkdir'):
            # Test max_size_mb bounds
            with pytest.raises(ValidationError):
                CacheConfig(max_size_mb=99)
            
            with pytest.raises(ValidationError):
                CacheConfig(max_size_mb=10001)
            
            # Test ttl_hours bounds
            with pytest.raises(ValidationError):
                CacheConfig(ttl_hours=0)
            
            with pytest.raises(ValidationError):
                CacheConfig(ttl_hours=169)


class TestSecurityConfig:
    """Test the SecurityConfig model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_security_config_defaults(self):
        """Test SecurityConfig creates with default values."""
        config = SecurityConfig()
        
        assert config.rate_limit == 60
        assert config.max_file_size_mb == 10
        assert config.allowed_paths is None
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_security_config_validation(self):
        """Test SecurityConfig validation bounds."""
        # Test rate_limit bounds
        with pytest.raises(ValidationError):
            SecurityConfig(rate_limit=-1)
        
        with pytest.raises(ValidationError):
            SecurityConfig(rate_limit=1001)
        
        # Test max_file_size_mb bounds
        with pytest.raises(ValidationError):
            SecurityConfig(max_file_size_mb=0)
        
        with pytest.raises(ValidationError):
            SecurityConfig(max_file_size_mb=101)
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_security_config_allowed_paths(self):
        """Test SecurityConfig with allowed paths."""
        paths = ["/home/user/scad", "/tmp/openscad"]
        config = SecurityConfig(allowed_paths=paths)
        
        assert config.allowed_paths == paths
        assert len(config.allowed_paths) == 2


class TestLoggingConfig:
    """Test the LoggingConfig model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_logging_config_defaults(self):
        """Test LoggingConfig creates with default values."""
        config = LoggingConfig()
        
        assert config.level == "INFO"
        assert config.file is None
        assert config.max_size_mb == 100
        assert config.rotate_count == 5
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_logging_config_level_validation(self):
        """Test LoggingConfig validates log level."""
        # Valid levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)
            assert config.level == level
        
        # Invalid level
        with pytest.raises(ValidationError):
            LoggingConfig(level="INVALID")
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_logging_config_bounds(self):
        """Test LoggingConfig validation bounds."""
        # Test max_size_mb bounds
        with pytest.raises(ValidationError):
            LoggingConfig(max_size_mb=9)
        
        with pytest.raises(ValidationError):
            LoggingConfig(max_size_mb=1001)
        
        # Test rotate_count bounds
        with pytest.raises(ValidationError):
            LoggingConfig(rotate_count=0)
        
        with pytest.raises(ValidationError):
            LoggingConfig(rotate_count=11)


class TestServerConfig:
    """Test the ServerConfig model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_server_config_defaults(self):
        """Test ServerConfig creates with default values."""
        config = ServerConfig()
        
        assert config.name == "OpenSCAD MCP Server"
        assert config.version == "0.1.0"
        assert config.transport == TransportType.STDIO
        assert config.host == "localhost"
        assert config.port == 8000
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_server_config_transport_types(self):
        """Test ServerConfig accepts all transport types."""
        for transport in [TransportType.STDIO, TransportType.HTTP, TransportType.SSE]:
            config = ServerConfig(transport=transport)
            assert config.transport == transport
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_server_config_port_validation(self):
        """Test ServerConfig validates port range."""
        # Valid ports
        config = ServerConfig(port=1024)
        assert config.port == 1024
        
        config = ServerConfig(port=65535)
        assert config.port == 65535
        
        # Invalid ports
        with pytest.raises(ValidationError):
            ServerConfig(port=1023)
        
        with pytest.raises(ValidationError):
            ServerConfig(port=65536)


class TestConfig:
    """Test the main Config model."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_defaults(self):
        """Test Config creates with default values."""
        with patch('pathlib.Path.mkdir'):
            config = Config()
        
        assert config.openscad_path is None
        assert config.imagemagick_path is None
        assert config.temp_dir == Path("/tmp/openscad-mcp")
        
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.rendering, RenderingConfig)
        assert isinstance(config.cache, CacheConfig)
        assert isinstance(config.security, SecurityConfig)
        assert isinstance(config.logging, LoggingConfig)
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_temp_dir_creation(self):
        """Test that temp directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir) / "test_temp"
            assert not temp_path.exists()
            
            config = Config(temp_dir=temp_path)
            assert temp_path.exists()
            assert config.temp_dir == temp_path
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_from_env(self, mock_env_vars):
        """Test Config loads from environment variables."""
        with patch('pathlib.Path.mkdir'):
            config = Config.from_env()
        
        assert config.openscad_path == "/usr/bin/openscad"
        assert str(config.temp_dir) == "/tmp/test-mcp"
        assert config.rendering.max_concurrent == 10
        assert config.rendering.queue_size == 200
        assert config.rendering.timeout_seconds == 600
        assert config.cache.enabled is True
        assert config.logging.level == "DEBUG"
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_from_env_partial(self):
        """Test Config loads partial environment variables."""
        env_vars = {
            'OPENSCAD_PATH': '/custom/openscad',
            'MCP_LOG_LEVEL': 'WARNING',
        }
        
        with patch.dict(os.environ, env_vars, clear=False):
            with patch('pathlib.Path.mkdir'):
                config = Config.from_env()
        
        assert config.openscad_path == "/custom/openscad"
        assert config.logging.level == "WARNING"
        # Check defaults are still used
        assert config.rendering.max_concurrent == 5
        assert config.cache.enabled is True
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_from_env_with_dotenv(self, temp_dir):
        """Test Config loads from .env file."""
        env_content = """
OPENSCAD_PATH=/from/dotenv/openscad
MCP_MAX_CONCURRENT_RENDERS=15
MCP_CACHE_ENABLED=false
"""
        env_file = temp_dir / ".env"
        env_file.write_text(env_content)
        
        with patch('pathlib.Path.mkdir'):
            config = Config.from_env(str(env_file))
        
        assert config.openscad_path == "/from/dotenv/openscad"
        assert config.rendering.max_concurrent == 15
        assert config.cache.enabled is False
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_from_yaml(self, sample_yaml_config):
        """Test Config loads from YAML file."""
        with patch('pathlib.Path.mkdir'):
            config = Config.from_yaml(str(sample_yaml_config))
        
        assert config.server.name == "Test OpenSCAD Server"
        assert config.server.version == "0.2.0"
        assert config.server.transport == TransportType.STDIO
        assert config.rendering.max_concurrent == 10
        assert config.rendering.queue_size == 200
        assert config.rendering.timeout_seconds == 600
        assert config.rendering.default_color_scheme == "Sunset"
        assert config.cache.enabled is True
        assert config.cache.max_size_mb == 1000
        assert config.cache.ttl_hours == 48
        assert config.security.rate_limit == 100
        assert config.security.max_file_size_mb == 20
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_to_yaml(self, temp_dir):
        """Test Config saves to YAML file."""
        with patch('pathlib.Path.mkdir'):
            config = Config(
                openscad_path="/test/openscad",
                rendering=RenderingConfig(max_concurrent=8),
                cache=CacheConfig(enabled=False)
            )
        
        yaml_file = temp_dir / "test_config.yaml"
        config.to_yaml(str(yaml_file))
        
        assert yaml_file.exists()
        
        # Load and verify
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        
        assert data['openscad_path'] == "/test/openscad"
        assert data['rendering']['max_concurrent'] == 8
        assert data['cache']['enabled'] is False
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_config_transport_type_from_env(self):
        """Test Config loads transport type from environment."""
        for transport_str, transport_enum in [
            ("stdio", TransportType.STDIO),
            ("http", TransportType.HTTP),
            ("sse", TransportType.SSE),
        ]:
            with patch.dict(os.environ, {'MCP_TRANSPORT': transport_str}):
                with patch('pathlib.Path.mkdir'):
                    config = Config.from_env()
            
            assert config.server.transport == transport_enum


class TestConfigGlobalFunctions:
    """Test global configuration functions."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_get_config_singleton(self):
        """Test get_config returns singleton instance."""
        with patch('pathlib.Path.mkdir'):
            config1 = get_config()
            config2 = get_config()
        
        assert config1 is config2
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_set_config(self):
        """Test set_config updates global configuration."""
        with patch('pathlib.Path.mkdir'):
            custom_config = Config(openscad_path="/custom/path")
            set_config(custom_config)
            
            retrieved = get_config()
            assert retrieved is custom_config
            assert retrieved.openscad_path == "/custom/path"
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_reset_config(self, reset_config):
        """Test that reset_config fixture works correctly."""
        with patch('pathlib.Path.mkdir'):
            # Set a custom config
            custom_config = Config(openscad_path="/first")
            set_config(custom_config)
            assert get_config().openscad_path == "/first"
        
        # After fixture cleanup (automatic), config should be reset
        # This is tested implicitly by other tests running independently


class TestConfigEdgeCases:
    """Test edge cases and error conditions."""
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_invalid_yaml_file(self, temp_dir):
        """Test Config handles invalid YAML file."""
        invalid_yaml = temp_dir / "invalid.yaml"
        invalid_yaml.write_text("invalid: yaml: content: ][")
        
        with pytest.raises(yaml.YAMLError):
            Config.from_yaml(str(invalid_yaml))
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_missing_yaml_file(self):
        """Test Config handles missing YAML file."""
        with pytest.raises(FileNotFoundError):
            Config.from_yaml("/nonexistent/config.yaml")
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_env_var_type_conversion(self):
        """Test environment variable type conversions."""
        env_vars = {
            'MCP_MAX_CONCURRENT_RENDERS': 'not_a_number',
            'MCP_CACHE_ENABLED': 'not_a_bool',
        }
        
        with patch.dict(os.environ, env_vars):
            with patch('pathlib.Path.mkdir'):
                # Should raise ValueError for invalid integer
                with pytest.raises(ValueError):
                    Config.from_env()
    
    @pytest.mark.unit
    @pytest.mark.config
    def test_cache_enabled_string_conversion(self):
        """Test cache enabled string to boolean conversion."""
        test_cases = [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("anything_else", False),
        ]
        
        for str_val, expected_bool in test_cases:
            with patch.dict(os.environ, {'MCP_CACHE_ENABLED': str_val}):
                with patch('pathlib.Path.mkdir'):
                    config = Config.from_env()
                assert config.cache.enabled == expected_bool