"""
Pytest fixtures for OpenSCAD MCP Server tests.

Provides reusable test fixtures including:
- Sample base64 images
- Temporary directories
- Mock configurations
- Common test data
"""

import pytest
import base64
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock


@pytest.fixture
def sample_base64_image() -> str:
    """
    Provide a small test image in base64 format.
    
    This is a 1x1 red pixel PNG for testing image handling functions.
    
    Returns:
        Base64-encoded PNG image string
    """
    # 1x1 red pixel PNG (smallest valid PNG)
    return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


@pytest.fixture
def large_base64_image() -> str:
    """
    Provide a large test image for size testing.
    
    Simulates a large image by repeating data to test size limits
    and compression functionality.
    
    Returns:
        Large base64 string simulating a big image
    """
    # Generate large base64 string (simulate ~100KB image)
    base_pattern = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    return base_pattern * 2000  # Approximately 100KB


@pytest.fixture
def medium_base64_image() -> str:
    """
    Provide a medium-sized test image.
    
    Returns:
        Medium base64 string (~10KB)
    """
    base_pattern = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    return base_pattern * 200


@pytest.fixture
def temp_test_dir(tmp_path) -> Path:
    """
    Provide a temporary directory for testing.
    
    Creates a nested structure for testing directory creation.
    
    Args:
        tmp_path: Pytest's tmp_path fixture
        
    Returns:
        Path object to temporary test directory
    """
    test_dir = tmp_path / "test_renders"
    test_dir.mkdir(exist_ok=True)
    return test_dir


@pytest.fixture
def mock_config() -> Mock:
    """
    Mock configuration object for testing.
    
    Provides a mock config with common settings used in tests.
    
    Returns:
        Mock object configured like the server's config
    """
    mock = Mock()
    mock.temp_dir = "/tmp/.openscad-mcp/tmp"
    mock.server = Mock()
    mock.server.version = "1.0.0"
    mock.server.transport = "stdio"
    mock.server.host = "localhost"
    mock.server.port = 8080
    mock.rendering = Mock()
    mock.rendering.max_concurrent = 4
    mock.rendering.queue_size = 10
    mock.cache = Mock()
    mock.cache.enabled = True
    mock.openscad_path = None
    return mock


@pytest.fixture
def sample_scad_content() -> str:
    """
    Provide sample OpenSCAD code for testing.
    
    Returns:
        Simple OpenSCAD code string
    """
    return """
// Sample OpenSCAD model for testing
$fn = 50;

module test_model(size = 10) {
    difference() {
        cube([size, size, size], center = true);
        sphere(r = size * 0.6);
    }
}

test_model(size = 20);
"""


@pytest.fixture
def complex_scad_content() -> str:
    """
    Provide complex OpenSCAD code for testing.
    
    Includes variables, modules, and complex geometry.
    
    Returns:
        Complex OpenSCAD code string
    """
    return """
// Complex model with parameters
width = 100;
height = 50;
depth = 30;
hole_radius = 10;

module complex_part() {
    difference() {
        // Main body
        hull() {
            cube([width, depth, 5]);
            translate([width/2, depth/2, height])
                cylinder(r = width/3, h = 5);
        }
        
        // Holes
        for (x = [20:20:width-20]) {
            for (y = [10:10:depth-10]) {
                translate([x, y, -1])
                    cylinder(r = hole_radius/2, h = height + 2);
            }
        }
    }
}

complex_part();
"""


@pytest.fixture
def camera_positions() -> Dict[str, Dict[str, Any]]:
    """
    Provide common camera positions for testing.
    
    Returns:
        Dictionary of view names to camera parameters
    """
    return {
        "front": {
            "position": [0, -100, 0],
            "target": [0, 0, 0],
            "up": [0, 0, 1]
        },
        "top": {
            "position": [0, 0, 100],
            "target": [0, 0, 0],
            "up": [0, 1, 0]
        },
        "isometric": {
            "position": [100, 100, 100],
            "target": [0, 0, 0],
            "up": [0, 0, 1]
        },
        "custom": {
            "position": [50, -50, 75],
            "target": [10, 10, 10],
            "up": [0, 0, 1]
        }
    }


@pytest.fixture
def test_variables() -> Dict[str, Any]:
    """
    Provide test variables for OpenSCAD rendering.
    
    Returns:
        Dictionary of variable names to values
    """
    return {
        "size": 25,
        "thickness": 2.5,
        "enable_holes": True,
        "label": "TEST",
        "count": 5
    }


@pytest.fixture
def mock_subprocess_result() -> Mock:
    """
    Mock subprocess result for OpenSCAD execution.
    
    Returns:
        Mock CompletedProcess object
    """
    mock = Mock()
    mock.returncode = 0
    mock.stdout = "OpenSCAD 2021.01"
    mock.stderr = ""
    return mock


@pytest.fixture
def mock_context() -> Mock:
    """
    Mock MCP context for testing.
    
    Provides async mock methods for logging.
    
    Returns:
        Mock Context object with async methods
    """
    from unittest.mock import AsyncMock
    
    mock = Mock()
    mock.info = AsyncMock()
    mock.warning = AsyncMock()
    mock.error = AsyncMock()
    mock.debug = AsyncMock()
    return mock


@pytest.fixture
def output_formats() -> list:
    """
    List of supported output formats.
    
    Returns:
        List of output format strings
    """
    return ["auto", "base64", "file_path", "compressed"]


@pytest.fixture
def color_schemes() -> list:
    """
    List of OpenSCAD color schemes for testing.
    
    Returns:
        List of color scheme names
    """
    return [
        "Cornfield",
        "Metallic", 
        "Sunset",
        "Starnight",
        "BeforeDawn",
        "Nature",
        "DeepOcean"
    ]


@pytest.fixture
def invalid_inputs() -> Dict[str, Any]:
    """
    Collection of invalid inputs for negative testing.
    
    Returns:
        Dictionary of parameter names to invalid values
    """
    return {
        "invalid_json": "{key: value",  # Missing quotes
        "invalid_base64": "not-base64-data!@#$",
        "invalid_list": "not[a]list",
        "invalid_dict": "not{a}dict",
        "invalid_image_size": "800x600x400",  # Too many dimensions
        "invalid_camera": [1, 2],  # Too few coordinates
        "invalid_number": "not_a_number"
    }


@pytest.fixture
def mock_pil_image():
    """
    Mock PIL Image for compression tests.
    
    Returns:
        Mock Image object
    """
    mock = Mock()
    mock.save = Mock()
    mock.format = "PNG"
    mock.size = (800, 600)
    return mock


@pytest.fixture
def performance_test_data() -> Dict[str, Any]:
    """
    Data for performance testing.
    
    Returns:
        Dictionary with large datasets for performance tests
    """
    return {
        "large_list": ["item" + str(i) for i in range(10000)],
        "large_dict": {f"key_{i}": f"value_{i}" for i in range(5000)},
        "many_images": {f"view_{i}": "A" * 10000 for i in range(20)},
        "complex_json": {
            "nested": {
                "level": {
                    "data": ["item"] * 1000
                }
            } for _ in range(100)
        }
    }


@pytest.fixture(autouse=True)
def reset_environment(monkeypatch):
    """
    Reset environment for each test.
    
    Ensures tests don't interfere with each other.
    
    Args:
        monkeypatch: Pytest's monkeypatch fixture
    """
    # Clear any OpenSCAD-related environment variables
    monkeypatch.setenv("OPENSCAD_PATH", "", prepend=False)
    
    # Ensure clean temp directory
    import tempfile
    import shutil
    temp_base = Path(tempfile.gettempdir()) / "openscad-mcp-test"
    if temp_base.exists():
        shutil.rmtree(temp_base, ignore_errors=True)
    temp_base.mkdir(exist_ok=True)
    
    yield
    
    # Cleanup after test
    if temp_base.exists():
        shutil.rmtree(temp_base, ignore_errors=True)


@pytest.fixture
def mock_openscad_executable(monkeypatch):
    """
    Mock OpenSCAD executable for testing without actual installation.
    
    Args:
        monkeypatch: Pytest's monkeypatch fixture
        
    Returns:
        Path to mock executable
    """
    def mock_run(*args, **kwargs):
        mock = Mock()
        mock.returncode = 0
        mock.stdout = "OpenSCAD version 2021.01"
        mock.stderr = ""
        return mock
    
    monkeypatch.setattr("subprocess.run", mock_run)
    return "/usr/bin/openscad"


# Pytest configuration markers
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests"
    )
    config.addinivalue_line(
        "markers", "performance: Performance tests"
    )
    config.addinivalue_line(
        "markers", "slow: Slow running tests"
    )