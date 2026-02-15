"""
Pytest fixtures for OpenSCAD MCP Server tests.

Provides reusable test fixtures including:
- Sample base64 images
- Temporary directories
- Mock configurations
- Common test data
"""

import struct

import pytest
import base64
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock

from openscad_mcp.utils.config import Config, CacheConfig, SecurityConfig, set_config


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
    Generate a large but valid base64-encoded PNG image for size testing.

    Uses Pillow to create a real 200x200 red PNG so that any code
    attempting to decode or process the image (e.g. compression, saving)
    will work with valid data.

    Returns:
        Base64-encoded PNG image string (~100KB+)
    """
    from PIL import Image
    import io

    img = Image.new("RGB", (200, 200), color="red")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


@pytest.fixture
def medium_base64_image() -> str:
    """
    Generate a medium-sized valid base64-encoded PNG image.

    Returns:
        Base64-encoded PNG image string (~10KB)
    """
    from PIL import Image
    import io

    img = Image.new("RGB", (50, 50), color="blue")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


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
    monkeypatch.delenv("OPENSCAD_PATH", raising=False)

    # Reset the global config singleton so tests don't get stale cached config
    set_config(None)

    # Ensure clean temp directory
    import tempfile
    import shutil
    temp_base = Path(tempfile.gettempdir()) / "openscad-mcp-test"
    if temp_base.exists():
        shutil.rmtree(temp_base, ignore_errors=True)
    temp_base.mkdir(exist_ok=True)
    
    yield

    # Reset config singleton again after test
    set_config(None)

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


@pytest.fixture
def configured_env(tmp_path, monkeypatch):
    """Config with cache disabled and find_openscad mocked.

    Sets up a real ``Config`` rooted in *tmp_path* with caching disabled
    and patches ``find_openscad`` to return a deterministic path.

    Returns:
        tuple: (tmp_path, Config) for the test to use
    """
    cfg = Config(
        temp_dir=tmp_path,
        cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        security=SecurityConfig(allowed_paths=None),
    )
    set_config(cfg)
    monkeypatch.setattr(
        "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
    )
    return tmp_path, cfg


@pytest.fixture
def configured_env_with_cache(tmp_path, monkeypatch):
    """Config with caching enabled (max_size_mb=1) and find_openscad mocked.

    Returns:
        tuple: (tmp_path, Config, cache_dir) for the test to use
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cfg = Config(
        temp_dir=tmp_path,
        cache=CacheConfig(
            enabled=True,
            directory=cache_dir,
            max_size_mb=100,  # minimum allowed by validator
            ttl_hours=24,
        ),
        security=SecurityConfig(allowed_paths=None),
    )
    set_config(cfg)
    monkeypatch.setattr(
        "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
    )
    return tmp_path, cfg, cache_dir


@pytest.fixture
def mock_subprocess_success():
    """Factory returning a mock_run that writes a fake PNG to the ``-o`` path.

    The returned callable can be used as ``side_effect`` for
    ``subprocess.run`` patches.  It creates a tiny (1x1) valid PNG
    at the output path so that any code checking for the file's
    existence or reading its content will succeed.
    """
    # Minimal valid 1x1 white PNG (67 bytes)
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, format="PNG")
    fake_png_bytes = buf.getvalue()

    def _factory(returncode=0, stderr="", stdout=""):
        def mock_run(cmd, **kwargs):
            # Write fake output to the -o path
            if "-o" in cmd:
                idx = cmd.index("-o")
                out_path = Path(cmd[idx + 1])
                if out_path.suffix == ".png":
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(fake_png_bytes)
                elif out_path.suffix == ".stl":
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    # Write minimal ASCII STL
                    out_path.write_text(
                        "solid test\n"
                        "  facet normal 0 0 1\n"
                        "    outer loop\n"
                        "      vertex 0 0 0\n"
                        "      vertex 10 0 0\n"
                        "      vertex 10 10 5\n"
                        "    endloop\n"
                        "  endfacet\n"
                        "endsolid test\n"
                    )
                elif str(out_path) not in ("/dev/null", "NUL"):
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(b"fake-export-data")

            result = Mock()
            result.returncode = returncode
            result.stderr = stderr
            result.stdout = stdout
            return result

        return mock_run

    return _factory


@pytest.fixture
def ascii_stl_content():
    """Valid ASCII STL string with known bounding box (0-10 x 0-10 x 0-5)."""
    return (
        "solid test\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 10 0 0\n"
        "      vertex 10 10 5\n"
        "    endloop\n"
        "  endfacet\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 10 10 5\n"
        "      vertex 0 10 0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid test\n"
    )


@pytest.fixture
def binary_stl_content():
    """Valid binary STL bytes with 1 triangle.

    Triangle: (0,0,0), (10,0,0), (10,10,5) with normal (0,0,1).
    """
    header = b"\x00" * 80  # 80-byte header
    tri_count = struct.pack("<I", 1)
    normal = struct.pack("<fff", 0.0, 0.0, 1.0)
    v1 = struct.pack("<fff", 0.0, 0.0, 0.0)
    v2 = struct.pack("<fff", 10.0, 0.0, 0.0)
    v3 = struct.pack("<fff", 10.0, 10.0, 5.0)
    attr = struct.pack("<H", 0)
    return header + tri_count + normal + v1 + v2 + v3 + attr


@pytest.fixture
def scad_with_deps():
    """SCAD content with include/use/commented-include statements."""
    return (
        'include <lib/utils.scad>\n'
        'use <lib/shapes.scad>;\n'
        '// include <lib/commented.scad>\n'
        '/* use <lib/block_commented.scad> */\n'
        'cube(10);\n'
    )


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