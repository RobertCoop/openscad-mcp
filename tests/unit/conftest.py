"""
Pytest fixtures for unit tests.

Provides fixtures specific to unit testing, including mock environment
variables, temporary directories, sample configurations, and config reset.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from openscad_mcp.utils.config import set_config


@pytest.fixture
def mock_env_vars(monkeypatch):
    """
    Set up mock environment variables for config loading tests.

    Provides a complete set of environment variables matching what
    Config.from_env() expects, so that test_config_from_env can
    verify that all values are picked up correctly.
    """
    monkeypatch.setenv("OPENSCAD_PATH", "/usr/bin/openscad")
    monkeypatch.setenv("MCP_TEMP_DIR", "/tmp/test-mcp")
    monkeypatch.setenv("MCP_MAX_CONCURRENT_RENDERS", "10")
    monkeypatch.setenv("MCP_RENDER_TIMEOUT", "600")
    monkeypatch.setenv("MCP_CACHE_ENABLED", "true")
    monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")


@pytest.fixture
def temp_dir(tmp_path):
    """
    Provide a temporary directory for tests that reference ``temp_dir``.

    The main conftest.py defines ``temp_test_dir`` (which creates a
    ``test_renders`` subdirectory), but several tests in test_config.py
    use the simpler ``temp_dir`` name.  This fixture delegates to
    pytest's built-in ``tmp_path``.

    Returns:
        Path object to the temporary directory
    """
    return tmp_path


@pytest.fixture
def sample_yaml_config(tmp_path):
    """
    Create a sample YAML configuration file for testing Config.from_yaml.

    The values written here must match the assertions in
    ``TestConfig.test_config_from_yaml``.

    Returns:
        Path to the YAML file
    """
    config_data = {
        "server": {
            "name": "Test OpenSCAD Server",
            "version": "0.2.0",
            "transport": "stdio",
            "host": "localhost",
            "port": 9000,
        },
        "rendering": {
            "max_concurrent": 10,
            "timeout_seconds": 600,
            "default_color_scheme": "Sunset",
        },
        "cache": {
            "enabled": True,
            "max_size_mb": 1000,
            "ttl_hours": 48,
        },
        "security": {
            "rate_limit": 100,
            "max_file_size_mb": 20,
        },
        "logging": {
            "level": "DEBUG",
        },
    }

    yaml_file = tmp_path / "test_config.yaml"
    with open(yaml_file, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False)

    return yaml_file


@pytest.fixture
def reset_config():
    """
    Reset the global ``_config`` singleton before and after each test.

    This ensures that tests manipulating the global config via
    ``set_config`` / ``get_config`` do not leak state to other tests.
    """
    set_config(None)
    yield
    set_config(None)
