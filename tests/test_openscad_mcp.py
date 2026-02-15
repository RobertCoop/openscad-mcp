"""
Comprehensive test suite for OpenSCAD MCP Server improvements.

Tests cover:
- Parameter validation (parse_list_param, parse_dict_param, parse_image_size_param)
- Directory auto-creation
- Response size optimization (estimate_response_size, save_image_to_file, compress_base64_image, manage_response_size)
- Integration tests for backward compatibility and new features
"""

import pytest
import json
import os
import sys
import tempfile
import base64
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock, call
from typing import Any, Dict, List

# Add parent directory to path to import the server module
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from openscad_mcp.server import (
    parse_list_param,
    parse_dict_param, 
    parse_image_size_param,
    estimate_response_size,
    save_image_to_file,
    compress_base64_image,
    manage_response_size,
    parse_camera_param
)


# ============================================================================
# Test Parameter Parsers
# ============================================================================

class TestParameterParsers:
    """Test flexible parameter parsing functions."""
    
    # ------------------------------------------------------------------------
    # parse_list_param tests
    # ------------------------------------------------------------------------
    
    def test_parse_list_param_with_list(self):
        """Test parse_list_param with native list input."""
        result = parse_list_param(["front", "top", "side"], [])
        assert result == ["front", "top", "side"]
        assert isinstance(result, list)
    
    def test_parse_list_param_with_json_string(self):
        """Test parse_list_param with JSON array string."""
        result = parse_list_param('["front", "top", "side"]', [])
        assert result == ["front", "top", "side"]
        
        # Test with numbers
        result = parse_list_param('[1, 2, 3]', [])
        assert result == [1, 2, 3]
    
    def test_parse_list_param_with_csv_string(self):
        """Test parse_list_param with CSV format string."""
        result = parse_list_param("front,top,side", [])
        assert result == ["front", "top", "side"]
        
        # Test with spaces
        result = parse_list_param("front, top , side", [])
        assert result == ["front", "top", "side"]
        
        # Test with trailing comma
        result = parse_list_param("front,top,", [])
        assert result == ["front", "top"]
    
    def test_parse_list_param_with_single_value(self):
        """Test parse_list_param with single value string."""
        result = parse_list_param("front", [])
        assert result == ["front"]
    
    def test_parse_list_param_with_none(self):
        """Test parse_list_param with None returns default."""
        default = ["default1", "default2"]
        result = parse_list_param(None, default)
        assert result == default
        assert result is default  # Should return the same object
    
    def test_parse_list_param_invalid(self):
        """Test parse_list_param with invalid input raises error."""
        with pytest.raises(ValueError, match="Cannot parse list from type"):
            parse_list_param(12345, [])
        
        with pytest.raises(ValueError, match="Cannot parse list from type"):
            parse_list_param({"key": "value"}, [])
    
    # ------------------------------------------------------------------------
    # parse_dict_param tests
    # ------------------------------------------------------------------------
    
    def test_parse_dict_param_with_dict(self):
        """Test parse_dict_param with native dict input."""
        input_dict = {"x": 10, "y": 20, "name": "test"}
        result = parse_dict_param(input_dict, {})
        assert result == input_dict
        assert isinstance(result, dict)
    
    def test_parse_dict_param_with_json_string(self):
        """Test parse_dict_param with JSON object string."""
        result = parse_dict_param('{"x": 10, "y": 20.5, "active": true}', {})
        assert result == {"x": 10, "y": 20.5, "active": True}
        
        # Test with nested objects
        result = parse_dict_param('{"point": {"x": 1, "y": 2}}', {})
        assert result == {"point": {"x": 1, "y": 2}}
    
    def test_parse_dict_param_with_keyvalue_string(self):
        """Test parse_dict_param with key=value format."""
        result = parse_dict_param("x=10,y=20,name=test", {})
        assert result == {"x": 10, "y": 20, "name": "test"}
        
        # Test with spaces
        result = parse_dict_param("x = 10, y = 20.5 , active = true", {})
        assert result == {"x": 10, "y": 20.5, "active": True}
        
        # Test with boolean values
        result = parse_dict_param("enabled=true,disabled=false", {})
        assert result == {"enabled": True, "disabled": False}
    
    def test_parse_dict_param_type_conversion(self):
        """Test parse_dict_param auto-converts types in key=value format."""
        result = parse_dict_param("int=42,float=3.14,bool=true,string=hello", {})
        assert result["int"] == 42
        assert isinstance(result["int"], int)
        assert result["float"] == 3.14
        assert isinstance(result["float"], float)
        assert result["bool"] is True
        assert isinstance(result["bool"], bool)
        assert result["string"] == "hello"
        assert isinstance(result["string"], str)
    
    def test_parse_dict_param_with_none(self):
        """Test parse_dict_param with None returns default."""
        default = {"default": "value"}
        result = parse_dict_param(None, default)
        assert result == default
        assert result is default
    
    def test_parse_dict_param_invalid(self):
        """Test parse_dict_param with invalid input raises error."""
        with pytest.raises(ValueError, match="Cannot parse dict from type"):
            parse_dict_param(12345, {})
        
        with pytest.raises(ValueError, match="Cannot parse dict from type"):
            parse_dict_param(["list", "item"], {})
    
    def test_parse_dict_param_empty_string(self):
        """Test parse_dict_param with empty object string."""
        result = parse_dict_param("{}", {})
        assert result == {}
    
    # ------------------------------------------------------------------------
    # parse_image_size_param tests
    # ------------------------------------------------------------------------
    
    def test_parse_image_size_param_with_list(self):
        """Test parse_image_size_param with list format."""
        result = parse_image_size_param([800, 600], [])
        assert result == [800, 600]
        
        # Test with float values (should convert to int)
        result = parse_image_size_param([800.5, 600.9], [])
        assert result == [800, 600]
    
    def test_parse_image_size_param_with_string_x(self):
        """Test parse_image_size_param with 'WxH' format."""
        result = parse_image_size_param("800x600", [])
        assert result == [800, 600]
        
        # Test with spaces
        result = parse_image_size_param(" 1920 x 1080 ", [])
        assert result == [1920, 1080]
    
    def test_parse_image_size_param_with_string_comma(self):
        """Test parse_image_size_param with 'W,H' format."""
        result = parse_image_size_param("800,600", [])
        assert result == [800, 600]
        
        # Test with spaces
        result = parse_image_size_param(" 1920 , 1080 ", [])
        assert result == [1920, 1080]
    
    def test_parse_image_size_param_with_tuple(self):
        """Test parse_image_size_param with tuple format."""
        result = parse_image_size_param((800, 600), [])
        assert result == [800, 600]
    
    def test_parse_image_size_param_with_json(self):
        """Test parse_image_size_param with JSON array string."""
        result = parse_image_size_param("[800, 600]", [])
        assert result == [800, 600]
    
    def test_parse_image_size_param_with_none(self):
        """Test parse_image_size_param with None returns default."""
        default = [1024, 768]
        result = parse_image_size_param(None, default)
        assert result == default
    
    def test_parse_image_size_param_invalid(self):
        """Test parse_image_size_param with invalid input raises error."""
        # Wrong number of values
        with pytest.raises(ValueError, match="must have 2 values"):
            parse_image_size_param([800], [])
        
        with pytest.raises(ValueError, match="must have 2 values"):
            parse_image_size_param([800, 600, 400], [])
        
        # Invalid format
        with pytest.raises(ValueError, match="Cannot parse image size"):
            parse_image_size_param("invalid", [])
        
        # Invalid type
        with pytest.raises(ValueError, match="Cannot parse image size"):
            parse_image_size_param({"width": 800}, [])
    
    # ------------------------------------------------------------------------
    # parse_camera_param tests
    # ------------------------------------------------------------------------
    
    def test_parse_camera_param_with_list(self):
        """Test parse_camera_param with list format."""
        result = parse_camera_param([10, 20, 30], [])
        assert result == [10.0, 20.0, 30.0]
        assert all(isinstance(v, float) for v in result)
    
    def test_parse_camera_param_with_dict(self):
        """Test parse_camera_param with dict format."""
        result = parse_camera_param({"x": 10, "y": 20, "z": 30}, [])
        assert result == [10.0, 20.0, 30.0]
    
    def test_parse_camera_param_with_json_list(self):
        """Test parse_camera_param with JSON list string."""
        result = parse_camera_param("[10, 20, 30]", [])
        assert result == [10.0, 20.0, 30.0]
    
    def test_parse_camera_param_with_json_dict(self):
        """Test parse_camera_param with JSON dict string."""
        result = parse_camera_param('{"x": 10, "y": 20, "z": 30}', [])
        assert result == [10.0, 20.0, 30.0]
    
    def test_parse_camera_param_with_none(self):
        """Test parse_camera_param with None returns default."""
        default = [1.0, 2.0, 3.0]
        result = parse_camera_param(None, default)
        assert result == default


# ============================================================================
# Test Directory Management
# ============================================================================

class TestDirectoryManagement:
    """Test directory auto-creation and management."""
    
    def test_temp_directory_creation(self):
        """Test that temp directory is created automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test_temp" / "nested"
            assert not test_dir.exists()
            
            # Should create directory when needed
            test_dir.mkdir(parents=True, exist_ok=True)
            assert test_dir.exists()
            assert test_dir.is_dir()
    
    def test_directory_creation_with_parents(self):
        """Test creating nested directories with parents=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = Path(tmpdir) / "level1" / "level2" / "level3"
            assert not nested_dir.exists()
            
            nested_dir.mkdir(parents=True, exist_ok=True)
            assert nested_dir.exists()
            assert (Path(tmpdir) / "level1").exists()
            assert (Path(tmpdir) / "level1" / "level2").exists()
    
    def test_directory_exists_handling(self):
        """Test that exist_ok=True prevents errors when directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test"
            test_dir.mkdir()
            assert test_dir.exists()
            
            # Should not raise error with exist_ok=True
            test_dir.mkdir(exist_ok=True)
            assert test_dir.exists()
    
    def test_fallback_directory_strategy(self):
        """Test fallback directory strategy when primary fails."""
        fallback_dirs = [
            Path("/tmp/openscad-mcp"),
            Path.home() / ".openscad-mcp" / "tmp",
            Path.cwd() / "tmp"
        ]
        
        # At least one fallback should be writable
        writable = False
        for dir_path in fallback_dirs:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                test_file = dir_path / "test.txt"
                test_file.write_text("test")
                test_file.unlink()
                writable = True
                break
            except (OSError, PermissionError):
                continue
        
        assert writable, "No fallback directory is writable"


# ============================================================================
# Test Response Size Management
# ============================================================================

class TestResponseSizeManagement:
    """Test response size estimation and optimization."""
    
    def test_estimate_response_size_accuracy(self):
        """Test response size estimation for various data structures."""
        # Simple string
        data = "Hello World"
        size = estimate_response_size(data)
        assert size > 0
        assert size == len(json.dumps(data)) // 4
        
        # Base64 data (typical image)
        base64_data = "A" * 10000
        size = estimate_response_size(base64_data)
        assert size == len(json.dumps(base64_data)) // 4
        
        # Complex structure
        complex_data = {
            "images": {
                "front": "A" * 1000,
                "top": "B" * 1000,
                "side": "C" * 1000
            },
            "metadata": {
                "count": 3,
                "format": "png"
            }
        }
        size = estimate_response_size(complex_data)
        assert size > 750  # Should be roughly 3000+ chars / 4
    
    def test_save_image_to_file(self):
        """Test saving base64 image to file."""
        # Create a small test image (1x1 red pixel PNG)
        test_image_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            filename = "test_image.png"
            
            # Save image
            file_path = save_image_to_file(test_image_base64, filename, output_dir)
            
            assert os.path.exists(file_path)
            assert file_path == str(output_dir / filename)
            
            # Verify file content
            with open(file_path, 'rb') as f:
                saved_data = f.read()
            
            assert saved_data == base64.b64decode(test_image_base64)
    
    def test_save_image_to_file_creates_directory(self):
        """Test that save_image_to_file creates directory if it doesn't exist."""
        test_image_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "new_dir" / "nested"
            assert not output_dir.exists()
            
            file_path = save_image_to_file(test_image_base64, "test.png", output_dir)
            
            assert output_dir.exists()
            assert os.path.exists(file_path)
    
    @patch('openscad_mcp.server.Image')
    def test_compress_base64_image(self, mock_image_class):
        """Test base64 image compression."""
        # Mock PIL Image
        mock_image = Mock()
        mock_image_class.open.return_value = mock_image
        
        # Simulate compression by returning smaller data
        original_data = "A" * 10000
        compressed_data = "A" * 7000  # 30% smaller
        
        mock_image.save.side_effect = lambda buffer, **kwargs: buffer.write(base64.b64decode(base64.b64encode(compressed_data.encode())))
        
        test_image_base64 = base64.b64encode(original_data.encode()).decode()
        
        result = compress_base64_image(test_image_base64)
        
        assert mock_image_class.open.called
        assert mock_image.save.called
        save_kwargs = mock_image.save.call_args[1]
        assert save_kwargs['format'] == 'PNG'
        assert save_kwargs['optimize'] is True
    
    def test_manage_response_size_auto_mode(self):
        """Test intelligent response size management in auto mode."""
        # Small response - should keep as base64
        small_images = {
            "front": "A" * 100,
            "top": "B" * 100
        }
        
        result = manage_response_size(small_images, output_format="auto")
        assert isinstance(result, dict)
        # Should return simple dict for backward compatibility
        assert all(isinstance(v, str) for v in result.values())
    
    @patch('openscad_mcp.server.save_image_to_file')
    def test_manage_response_size_force_file_path(self, mock_save):
        """Test forcing file path output format."""
        mock_save.return_value = "/tmp/test.png"
        
        images = {"render": "A" * 1000}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = manage_response_size(
                images, 
                output_format="file_path",
                output_dir=Path(tmpdir)
            )
        
        assert isinstance(result, dict)
        assert "render" in result
        assert result["render"]["type"] == "file_path"
        assert result["render"]["path"] == "/tmp/test.png"
        assert result["render"]["mime_type"] == "image/png"
    
    @patch('openscad_mcp.server.compress_base64_image')
    def test_manage_response_size_force_compressed(self, mock_compress):
        """Test forcing compressed output format."""
        original = "A" * 1000
        compressed = "A" * 700
        mock_compress.return_value = compressed
        
        images = {"render": original}
        
        result = manage_response_size(images, output_format="compressed")
        
        assert isinstance(result, dict)
        assert "render" in result
        assert result["render"]["type"] == "base64_compressed"
        assert result["render"]["data"] == compressed
        assert result["render"]["compression_ratio"] == 0.7
    
    def test_manage_response_size_large_response_handling(self):
        """Test handling of multiple large images exceeding token limit."""
        # Create large images that exceed default limit
        large_images = {
            f"view_{i}": "A" * 20000 for i in range(5)
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should automatically switch to file_path mode
            result = manage_response_size(
                large_images,
                output_format="auto",
                max_size=1000,  # Very small limit to force file mode
                output_dir=Path(tmpdir)
            )
            
            assert isinstance(result, dict)
            # Should have switched to file path mode
            assert all(
                v.get("type") == "file_path"
                for v in result.values()
                if isinstance(v, dict)
            )
    
    def test_manage_response_size_with_list_input(self):
        """Test manage_response_size with list input format."""
        images_list = [
            {"data": "A" * 100},
            {"data": "B" * 100}
        ]
        
        result = manage_response_size(images_list, output_format="base64")
        
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(item["type"] == "base64" for item in result)


# ============================================================================
# Test Integration
# ============================================================================

class TestIntegration:
    """Integration tests for complete workflows."""
    
    @pytest.mark.asyncio
    async def test_render_single_with_flexible_params(self):
        """Test render_single with all flexible parameter formats."""
        from openscad_mcp.server import render_single
        # Access underlying function (FastMCP wraps it as FunctionTool)
        render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single

        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            mock_render.return_value = "base64imagedata"

            # Test with various parameter formats
            result = await render_fn(
                scad_content="cube(10);",
                camera_position='{"x": 10, "y": 20, "z": 30}',  # JSON dict string
                camera_target=[0, 0, 0],  # List
                camera_up=None,  # Use default
                image_size="1024x768",  # String format
                variables="x=10,y=20",  # Key=value format
                auto_center=True
            )
            
            assert result["success"] is True
            assert "data" in result or "path" in result
            
            # Verify parameters were parsed correctly
            call_args = mock_render.call_args[0]
            assert call_args[2] == [10.0, 20.0, 30.0]  # camera_position
            assert call_args[3] == [0, 0, 0]  # camera_target
            assert call_args[5] == [1024, 768]  # image_size
    
    @pytest.mark.asyncio
    async def test_render_single_with_view_keywords(self):
        """Test render_single with view keyword parameter."""
        from openscad_mcp.server import render_single
        render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single

        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            mock_render.return_value = "base64imagedata"

            # Test multiple views
            for view_name in ["front", "top", "isometric"]:
                result = await render_fn(
                    scad_content="sphere(10);",
                    view=view_name,
                    image_size=[800, 600],
                    variables={"radius": 10},
                    output_format="base64"
                )
                
                assert result["success"] is True
                assert "data" in result
                assert result["mime_type"] == "image/png"
    
    @pytest.mark.asyncio
    async def test_render_with_output_format_auto(self):
        """Test automatic output format selection based on size."""
        from openscad_mcp.server import render_single
        render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single

        # Small response
        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            mock_render.return_value = "A" * 100  # Small image

            result = await render_fn(
                scad_content="cube(5);",
                output_format="auto"
            )
            
            assert result["success"] is True
            assert "data" in result  # Should use base64 for small images
    
    @pytest.mark.asyncio
    async def test_render_with_large_response(self):
        """Test handling of large responses with auto mode."""
        from openscad_mcp.server import render_single
        render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single

        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            # Return very large image data
            mock_render.return_value = "A" * 50000
            
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch('openscad_mcp.server.Path') as mock_path:
                    mock_path.return_value = Path(tmpdir)
                    
                    result = await render_fn(
                        scad_content="complex_model();",
                        view="isometric",
                        output_format="auto"
                    )
                    
                    assert result["success"] is True
                    # Should have handled the large response
                    assert "operation_id" in result
    
    def test_backward_compatibility(self):
        """Test that existing code using old API still works."""
        # Old style with lists
        result = parse_list_param(["view1", "view2"], [])
        assert result == ["view1", "view2"]
        
        # Old style with dicts
        result = parse_dict_param({"key": "value"}, {})
        assert result == {"key": "value"}
        
        # Old style with image size
        result = parse_image_size_param([800, 600], [])
        assert result == [800, 600]


# ============================================================================
# Test Error Handling
# ============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_parse_list_param_malformed_json(self):
        """Test parse_list_param with malformed JSON."""
        # Malformed JSON should fall back to CSV parsing
        result = parse_list_param("[front, top", [])
        assert result == ["[front", "top"]  # Treated as CSV
    
    def test_parse_dict_param_malformed_json(self):
        """Test parse_dict_param with malformed JSON."""
        # Malformed JSON should fall back to key=value parsing
        with pytest.raises(ValueError):
            parse_dict_param("{key: value", {})
    
    def test_save_image_invalid_base64(self):
        """Test save_image_to_file with invalid base64."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Failed to save image"):
                save_image_to_file("invalid_base64", "test.png", Path(tmpdir))
    
    def test_compress_image_invalid_data(self):
        """Test compress_base64_image with invalid data."""
        with pytest.raises(ValueError, match="Failed to compress image"):
            compress_base64_image("invalid_image_data")
    
    @pytest.mark.asyncio
    async def test_render_missing_input(self):
        """Test render functions with missing required input."""
        from openscad_mcp.server import render_single
        render_fn = render_single.fn if hasattr(render_single, 'fn') else render_single

        with pytest.raises(ValueError, match="Exactly one of scad_content or scad_file"):
            await render_fn()  # Missing both

        with pytest.raises(ValueError, match="Exactly one of scad_content or scad_file"):
            await render_fn(scad_content="cube();", scad_file="file.scad")  # Both


# ============================================================================
# Test render_scad_to_png Command Construction
# ============================================================================


class TestRenderScadToPngCommand:
    """Test that render_scad_to_png constructs the correct subprocess commands.

    These tests mock subprocess.run and find_openscad to verify that command-line
    arguments are built correctly without needing an actual OpenSCAD installation.
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self, monkeypatch, tmp_path):
        """Set up common mocks for all tests in this class."""
        from openscad_mcp.server import render_scad_to_png
        from openscad_mcp.utils.config import Config, set_config

        # Create a config with a real temp directory and caching disabled
        # (caching would cause later tests to hit cache and skip subprocess)
        from openscad_mcp.utils.config import CacheConfig

        with patch("pathlib.Path.mkdir"):
            config = Config(temp_dir=tmp_path, cache=CacheConfig(enabled=False))
        set_config(config)

        # Mock find_openscad to return a known path
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad",
            lambda: "/usr/bin/openscad",
        )

        # Store tmp_path for individual tests
        self._tmp_path = tmp_path

    def _make_mock_result(self, output_path_str):
        """Create a mock subprocess result that also writes a fake PNG file."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    def test_camera_uses_six_value_format(self):
        """Test that --camera uses the correct 6-value eye+center format."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            # Write a fake PNG to the output path
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(
                scad_content="cube(10);",
                camera_position=[10, 20, 30],
                camera_target=[1, 2, 3],
            )

        cmd = captured_cmd["cmd"]
        camera_args = [a for a in cmd if a.startswith("--camera=")]
        assert len(camera_args) == 1
        # Should be --camera=eye_x,eye_y,eye_z,center_x,center_y,center_z (6 values)
        camera_val = camera_args[0].split("=", 1)[1]
        parts = camera_val.split(",")
        assert len(parts) == 6, f"Expected 6 camera values, got {len(parts)}: {camera_val}"
        assert parts == ["10", "20", "30", "1", "2", "3"]

    def test_d_flags_from_variables(self):
        """Test that -D flags are properly constructed from variables dict."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(
                scad_content="cube(size);",
                variables={
                    "size": 25,
                    "label": "TEST",
                    "enabled": True,
                    "ratio": 3.14,
                },
            )

        cmd = captured_cmd["cmd"]

        # Collect all -D flag pairs
        d_flags = {}
        for i, arg in enumerate(cmd):
            if arg == "-D" and i + 1 < len(cmd):
                d_flags[cmd[i + 1].split("=")[0]] = cmd[i + 1]

        assert "size=25" in d_flags["size"]
        assert 'label="TEST"' in d_flags["label"]
        assert "enabled=true" in d_flags["enabled"]
        assert "ratio=3.14" in d_flags["ratio"]

    def test_hardwarnings_included(self):
        """Test that --hardwarnings is included in the command."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(scad_content="cube(10);")

        cmd = captured_cmd["cmd"]
        assert "--hardwarnings" in cmd

    def test_imgsize_format(self):
        """Test that --imgsize is properly formatted as 'W,H'."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(
                scad_content="cube(10);",
                image_size=[1920, 1080],
            )

        cmd = captured_cmd["cmd"]
        imgsize_idx = cmd.index("--imgsize")
        assert cmd[imgsize_idx + 1] == "1920,1080"

    def test_temp_files_cleaned_up(self):
        """Test that temporary files are cleaned up after rendering."""
        from openscad_mcp.server import render_scad_to_png

        temp_files_seen = {}

        def mock_run(cmd, **kwargs):
            # Find the SCAD input file (last argument)
            scad_file = cmd[-1]
            temp_files_seen["scad"] = scad_file
            # Find the output file
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    temp_files_seen["output"] = cmd[i + 1]
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(scad_content="cube(10);")

        # After render_scad_to_png returns, the TemporaryDirectory context
        # manager should have cleaned up the temp files
        assert "scad" in temp_files_seen
        assert not Path(temp_files_seen["scad"]).exists(), (
            "Temporary SCAD file should be cleaned up"
        )

    def test_timeout_passed_to_subprocess(self):
        """Test that timeout is passed to subprocess.run."""
        from openscad_mcp.server import render_scad_to_png

        captured_kwargs = {}

        def mock_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            return mock_result

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(scad_content="cube(10);")

        assert "timeout" in captured_kwargs
        # Default timeout from RenderingConfig is 300 seconds
        assert captured_kwargs["timeout"] == 300

    def test_autocenter_and_viewall_flags(self):
        """Test that --autocenter and --viewall are added when auto_center=True."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(scad_content="cube(10);", auto_center=True)

        cmd = captured_cmd["cmd"]
        assert "--autocenter" in cmd
        assert "--viewall" in cmd

    def test_colorscheme_flag(self):
        """Test that --colorscheme is set correctly."""
        from openscad_mcp.server import render_scad_to_png

        captured_cmd = {}

        def mock_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return self._make_mock_result(None)

        with patch("subprocess.run", side_effect=mock_run):
            render_scad_to_png(scad_content="cube(10);", color_scheme="Sunset")

        cmd = captured_cmd["cmd"]
        cs_idx = cmd.index("--colorscheme")
        assert cmd[cs_idx + 1] == "Sunset"


# ============================================================================
# Test Security Validations
# ============================================================================


class TestSecurityValidations:
    """Test security validations in render_scad_to_png.

    These tests verify that the actual validation logic in render_scad_to_png
    properly rejects dangerous or malformed inputs. Mocking is limited to
    find_openscad and subprocess so that the real validation code runs.
    """

    @pytest.fixture(autouse=True)
    def setup_mocks(self, monkeypatch, tmp_path):
        """Set up common mocks for security tests."""
        from openscad_mcp.utils.config import Config, SecurityConfig, set_config

        self._tmp_path = tmp_path

        # Mock find_openscad
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad",
            lambda: "/usr/bin/openscad",
        )

        # Store monkeypatch for per-test config changes
        self._monkeypatch = monkeypatch

    def _set_config_with_security(self, allowed_paths=None, max_file_size_mb=10):
        """Helper to set up config with specific security settings."""
        from openscad_mcp.utils.config import Config, SecurityConfig, set_config

        with patch("pathlib.Path.mkdir"):
            config = Config(
                temp_dir=self._tmp_path,
                security=SecurityConfig(
                    allowed_paths=allowed_paths,
                    max_file_size_mb=max_file_size_mb,
                ),
            )
        set_config(config)

    def test_path_traversal_rejected_with_allowed_paths(self):
        """Test that scad_file outside allowed_paths is rejected.

        When allowed_paths is configured, accessing /etc/passwd or any
        path outside the allowed list should raise ValueError.
        """
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security(allowed_paths=["/home/user/scad", "/tmp/openscad"])

        with pytest.raises(ValueError, match="not within allowed paths"):
            render_scad_to_png(scad_file="/etc/passwd")

    def test_path_traversal_dot_dot_rejected(self):
        """Test that path traversal via '../' is rejected when allowed_paths is set."""
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security(allowed_paths=["/home/user/scad"])

        with pytest.raises(ValueError, match="not within allowed paths"):
            render_scad_to_png(scad_file="/home/user/scad/../../etc/passwd")

    def test_allowed_path_is_accepted(self):
        """Test that a file within allowed_paths passes validation.

        The test should get past path validation and fail on the file not
        existing (FileNotFoundError), not on the path check (ValueError).
        """
        from openscad_mcp.server import render_scad_to_png

        allowed_dir = str(self._tmp_path / "scad_files")
        self._set_config_with_security(allowed_paths=[allowed_dir])

        scad_dir = self._tmp_path / "scad_files"
        scad_dir.mkdir(parents=True, exist_ok=True)
        scad_file = scad_dir / "model.scad"
        scad_file.write_text("cube(10);")

        # Should pass path validation but fail at subprocess (which we mock)
        def mock_run(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            return mock_result

        with patch("subprocess.run", side_effect=mock_run):
            # This should NOT raise ValueError about allowed paths
            result = render_scad_to_png(scad_file=str(scad_file))
            assert isinstance(result, str)  # base64 string

    def test_variable_name_injection_rejected(self):
        """Test that variable names with special characters are rejected.

        OpenSCAD variable names must match ^[a-zA-Z_][a-zA-Z0-9_]*$.
        Injection attempts like 'size; rm -rf /' or 'x")+foo(' should be blocked.
        """
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security()

        malicious_names = [
            "size; rm -rf /",
            'x")+foo(',
            "var name",
            "123start",
            "key=value",
            "$(cmd)",
            "a\nb",
            "hello-world",
            "foo.bar",
        ]

        for name in malicious_names:
            with pytest.raises(ValueError, match="Invalid variable name"):
                render_scad_to_png(
                    scad_content="cube(10);",
                    variables={name: 1},
                )

    def test_valid_variable_names_accepted(self):
        """Test that valid OpenSCAD variable names are accepted."""
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security()

        valid_names = ["size", "_private", "myVar2", "CONSTANT", "a", "_", "__double"]

        for name in valid_names:
            # Should pass variable name validation; will fail later at subprocess
            # We mock subprocess to avoid that
            def mock_run(cmd, **kwargs):
                for i, arg in enumerate(cmd):
                    if arg == "-o" and i + 1 < len(cmd):
                        Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                        Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
                mock_result = Mock()
                mock_result.returncode = 0
                mock_result.stdout = ""
                mock_result.stderr = ""
                return mock_result

            with patch("subprocess.run", side_effect=mock_run):
                result = render_scad_to_png(
                    scad_content="cube(10);",
                    variables={name: 42},
                )
                assert isinstance(result, str)

    def test_oversized_scad_content_rejected(self):
        """Test that scad_content exceeding max_file_size_mb is rejected.

        When security.max_file_size_mb is set to a small value, large
        content should be rejected before any rendering occurs.
        """
        from openscad_mcp.server import render_scad_to_png

        # Set max file size to 1 MB
        self._set_config_with_security(max_file_size_mb=1)

        # Create content just over 1 MB
        oversized_content = "// " + "x" * (1 * 1024 * 1024 + 100)

        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            render_scad_to_png(scad_content=oversized_content)

    def test_content_within_size_limit_accepted(self):
        """Test that scad_content within max_file_size_mb is accepted."""
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security(max_file_size_mb=10)

        small_content = "cube(10);"

        def mock_run(cmd, **kwargs):
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[i + 1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            return mock_result

        with patch("subprocess.run", side_effect=mock_run):
            result = render_scad_to_png(scad_content=small_content)
            assert isinstance(result, str)

    def test_no_path_restriction_without_allowed_paths(self):
        """Test that any path is accepted when allowed_paths is None (default)."""
        from openscad_mcp.server import render_scad_to_png

        self._set_config_with_security(allowed_paths=None)

        # Without allowed_paths, any file path should pass the path check.
        # It will fail at a later stage (FileNotFoundError or subprocess),
        # NOT with a ValueError about "not within allowed paths".
        nonexistent = str(self._tmp_path / "nonexistent.scad")
        with pytest.raises(FileNotFoundError, match="SCAD file not found"):
            render_scad_to_png(scad_file=nonexistent)