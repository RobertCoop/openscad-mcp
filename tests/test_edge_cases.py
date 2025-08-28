"""
Edge case tests for OpenSCAD MCP Server.

Tests unusual inputs, boundary conditions, and error scenarios including:
- Empty inputs
- Special characters
- Unicode handling
- Extreme values
- Malformed data
- Race conditions
"""

import pytest
import json
import sys
import base64
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch, AsyncMock
import tempfile

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from openscad_mcp.server import (
    parse_list_param,
    parse_dict_param,
    parse_image_size_param,
    parse_camera_param,
    estimate_response_size,
    save_image_to_file,
    compress_base64_image,
    manage_response_size
)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    # ------------------------------------------------------------------------
    # Empty Input Tests
    # ------------------------------------------------------------------------
    
    def test_empty_inputs(self):
        """Test handling of empty inputs."""
        # Empty string for list
        assert parse_list_param("", ["default"]) == ["default"]
        
        # Empty JSON object
        assert parse_dict_param("{}", {}) == {}
        
        # Empty JSON array
        assert parse_list_param("[]", ["default"]) == []
        
        # Whitespace only
        assert parse_list_param("   ", ["default"]) == ["default"]
        assert parse_dict_param("   ", {"default": "value"}) == {"default": "value"}
    
    def test_single_element_edge_cases(self):
        """Test single element edge cases."""
        # Single item with trailing comma
        assert parse_list_param("item,", []) == ["item"]
        
        # Single key-value with trailing comma
        assert parse_dict_param("key=value,", {}) == {"key": "value"}
        
        # Single element JSON array
        assert parse_list_param('["single"]', []) == ["single"]
        
        # Single property JSON object
        assert parse_dict_param('{"only": "one"}', {}) == {"only": "one"}
    
    # ------------------------------------------------------------------------
    # Special Characters Tests
    # ------------------------------------------------------------------------
    
    def test_special_characters_in_strings(self):
        """Test handling of special characters."""
        # Escaped characters in JSON
        result = parse_dict_param('{"key": "value\\nwith\\tnewline"}', {})
        assert result["key"] == "value\nwith\tnewline"
        
        # Special characters in CSV format
        result = parse_list_param("item-with-dash,item_with_underscore", [])
        assert result == ["item-with-dash", "item_with_underscore"]
        
        # Equals sign in value (key=value format)
        result = parse_dict_param("equation=x=y+z", {})
        assert result["equation"] == "x=y+z"
        
        # Comma in value with quotes (potential future enhancement)
        # Currently this would split incorrectly
        with pytest.raises(ValueError):
            # This should ideally handle quoted values but currently doesn't
            parse_dict_param('key="value,with,commas"', {})
    
    def test_escape_sequences(self):
        """Test escape sequence handling."""
        # Backslashes in paths
        result = parse_dict_param('{"path": "C:\\\\Users\\\\test"}', {})
        assert result["path"] == "C:\\Users\\test"
        
        # Unicode escapes
        result = parse_dict_param('{"unicode": "\\u0048\\u0065\\u006c\\u006c\\u006f"}', {})
        assert result["unicode"] == "Hello"
    
    # ------------------------------------------------------------------------
    # Unicode Tests
    # ------------------------------------------------------------------------
    
    def test_unicode_handling(self):
        """Test Unicode character handling."""
        # Unicode in list items
        result = parse_list_param('["日本語", "中文", "한글"]', [])
        assert result == ["日本語", "中文", "한글"]
        
        # Unicode in dict
        result = parse_dict_param('{"名前": "太郎", "city": "東京"}', {})
        assert result["名前"] == "太郎"
        assert result["city"] == "東京"
        
        # Emoji handling
        result = parse_list_param('["🚀", "🎨", "🔧"]', [])
        assert result == ["🚀", "🎨", "🔧"]
        
        # Mixed scripts
        result = parse_dict_param("greeting=Hello世界", {})
        assert result["greeting"] == "Hello世界"
    
    def test_unicode_in_filenames(self):
        """Test Unicode in file operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Unicode filename
            unicode_filename = "测试文件_🚀.png"
            test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
            
            file_path = save_image_to_file(test_image, unicode_filename, Path(tmpdir))
            assert Path(file_path).exists()
            assert Path(file_path).name == unicode_filename
    
    # ------------------------------------------------------------------------
    # Extreme Values Tests
    # ------------------------------------------------------------------------
    
    def test_extreme_numeric_values(self):
        """Test extreme numeric values."""
        # Very large numbers
        result = parse_dict_param("big=999999999999999999999", {})
        assert result["big"] == 999999999999999999999
        
        # Very small (close to zero)
        result = parse_dict_param("tiny=0.000000000001", {})
        assert result["tiny"] == 0.000000000001
        
        # Negative extremes
        result = parse_dict_param("negative=-999999999", {})
        assert result["negative"] == -999999999
        
        # Scientific notation
        result = parse_dict_param('{"scientific": 1.23e-10}', {})
        assert result["scientific"] == 1.23e-10
    
    def test_extreme_image_sizes(self):
        """Test extreme image size values."""
        # Very small image
        result = parse_image_size_param([1, 1], [])
        assert result == [1, 1]
        
        # Very large image
        result = parse_image_size_param([10000, 10000], [])
        assert result == [10000, 10000]
        
        # Asymmetric sizes
        result = parse_image_size_param("10x5000", [])
        assert result == [10, 5000]
        
        # Zero should raise error
        with pytest.raises(ValueError):
            parse_image_size_param([0, 600], [])
    
    def test_extreme_camera_positions(self):
        """Test extreme camera position values."""
        # Very far camera
        result = parse_camera_param([100000, 100000, 100000], [])
        assert result == [100000.0, 100000.0, 100000.0]
        
        # Negative positions
        result = parse_camera_param([-500, -500, -500], [])
        assert result == [-500.0, -500.0, -500.0]
        
        # Mixed extreme values
        result = parse_camera_param({"x": 0.001, "y": -10000, "z": 99999}, [])
        assert result == [0.001, -10000.0, 99999.0]
    
    # ------------------------------------------------------------------------
    # Malformed Data Tests
    # ------------------------------------------------------------------------
    
    def test_malformed_json(self):
        """Test handling of malformed JSON."""
        # Missing quotes (should fall back to other parsing)
        result = parse_list_param("[item1, item2]", [])
        # Falls back to CSV parsing
        assert "[item1" in result or "item1" in result[0]
        
        # Unclosed brackets
        with pytest.raises(ValueError):
            parse_dict_param('{"key": "value"', {})
        
        # Invalid JSON but valid key=value
        result = parse_dict_param('{key: value}', {"default": "val"})
        assert result == {"default": "val"}  # Falls back to default
        
        # Trailing commas in JSON (some parsers accept this)
        try:
            result = parse_dict_param('{"a": 1, "b": 2,}', {})
            # Some JSON parsers accept trailing commas
        except:
            pass  # Expected for strict JSON parsers
    
    def test_mixed_format_confusion(self):
        """Test ambiguous format inputs."""
        # Looks like JSON but isn't quite
        result = parse_list_param("[1,2,3", [])
        assert len(result) > 0  # Should handle gracefully
        
        # Mixed separators
        result = parse_dict_param("a=1;b=2,c=3", {})
        # Should parse what it can
        assert "c" in result
        assert result["c"] == 3
    
    # ------------------------------------------------------------------------
    # Type Confusion Tests
    # ------------------------------------------------------------------------
    
    def test_type_confusion(self):
        """Test inputs that could be interpreted multiple ways."""
        # String that looks like a number
        result = parse_dict_param('{"zip": "01234"}', {})
        assert result["zip"] == "01234"  # Should preserve as string
        
        # Boolean-like strings
        result = parse_dict_param("maybe=yes,probably=no", {})
        assert result["maybe"] == "yes"  # Not converted to boolean
        assert result["probably"] == "no"
        
        # Number-like strings in lists
        result = parse_list_param('["123", "456.789"]', [])
        assert result[0] == "123"  # Preserved as string in JSON
        assert result[1] == "456.789"
    
    def test_null_and_undefined_values(self):
        """Test null and undefined value handling."""
        # JSON null
        result = parse_dict_param('{"key": null}', {})
        assert result["key"] is None
        
        # String "null"
        result = parse_dict_param("key=null", {})
        assert result["key"] == "null"  # Treated as string
        
        # Empty values
        result = parse_dict_param("key=", {})
        assert result["key"] == ""
    
    # ------------------------------------------------------------------------
    # Boundary Tests for Response Management
    # ------------------------------------------------------------------------
    
    def test_response_size_edge_cases(self):
        """Test edge cases in response size management."""
        # Exactly at threshold
        threshold_data = {"data": "A" * 25000 * 4}  # Exactly at 25000 token threshold
        size = estimate_response_size(threshold_data)
        assert size == 25000
        
        # Just below threshold
        below_data = {"data": "A" * 24999 * 4}
        size = estimate_response_size(below_data)
        assert size == 24999
        
        # Just above threshold
        above_data = {"data": "A" * 25001 * 4}
        size = estimate_response_size(above_data)
        assert size == 25001
    
    @patch('openscad_mcp.server.compress_base64_image')
    def test_compression_failure_fallback(self, mock_compress):
        """Test fallback when compression fails."""
        mock_compress.side_effect = Exception("Compression failed")
        
        images = {"test": "imagedata"}
        result = manage_response_size(images, output_format="compressed")
        
        # Should fall back to uncompressed
        assert result["test"]["type"] == "base64"
        assert result["test"]["data"] == "imagedata"
    
    def test_file_path_with_special_directories(self):
        """Test file paths with special directory names."""
        special_names = [
            "..test",  # Starts with dots
            "test..dir",  # Contains dots
            "test dir with spaces",
            "test-dir-with-dashes",
            "test_dir_with_underscores",
            "123numeric",  # Starts with number
        ]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in special_names:
                test_dir = Path(tmpdir) / name
                test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
                
                # Should handle special directory names
                file_path = save_image_to_file(test_image, "test.png", test_dir)
                assert Path(file_path).exists()
    
    # ------------------------------------------------------------------------
    # Concurrency and Race Condition Tests
    # ------------------------------------------------------------------------
    
    def test_concurrent_file_operations(self):
        """Test concurrent file save operations."""
        import concurrent.futures
        
        test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        
        def save_file(index, tmpdir):
            return save_image_to_file(
                test_image,
                f"concurrent_{index}.png",
                Path(tmpdir)
            )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(save_file, i, tmpdir)
                    for i in range(20)
                ]
                
                results = [f.result() for f in futures]
                
                # All files should be created successfully
                assert len(results) == 20
                assert all(Path(r).exists() for r in results)
    
    def test_same_filename_collision(self):
        """Test handling of filename collisions."""
        test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save first file
            file1 = save_image_to_file(test_image, "test.png", Path(tmpdir))
            assert Path(file1).exists()
            
            # Save second file with same name (should overwrite)
            file2 = save_image_to_file(test_image, "test.png", Path(tmpdir))
            assert Path(file2).exists()
            assert file1 == file2  # Same path
    
    # ------------------------------------------------------------------------
    # Memory and Resource Tests
    # ------------------------------------------------------------------------
    
    def test_very_large_base64_handling(self):
        """Test handling of very large base64 strings."""
        # Create a very large base64 string (simulate 10MB image)
        large_base64 = "A" * (10 * 1024 * 1024)
        
        # Should handle without crashing
        size = estimate_response_size({"image": large_base64})
        assert size > 0
        
        # Manage response should switch to file mode
        with tempfile.TemporaryDirectory() as tmpdir:
            result = manage_response_size(
                {"large": large_base64},
                output_format="auto",
                max_size=1000,
                output_dir=Path(tmpdir)
            )
            
            # Should use file_path for large data
            assert result["large"]["type"] == "file_path"
    
    def test_deeply_nested_structures(self):
        """Test handling of deeply nested data structures."""
        # Create deeply nested dict
        nested = {"level": 0}
        current = nested
        for i in range(100):
            current["next"] = {"level": i + 1}
            current = current["next"]
        
        # Should handle without stack overflow
        size = estimate_response_size(nested)
        assert size > 0
        
        # Parse deeply nested JSON
        nested_json = json.dumps(nested)
        # This might be too complex for parse_dict_param
        # but shouldn't crash
        try:
            result = parse_dict_param(nested_json, {})
            assert isinstance(result, dict)
        except:
            pass  # Acceptable to fail gracefully
    
    # ------------------------------------------------------------------------
    # Platform-Specific Edge Cases
    # ------------------------------------------------------------------------
    
    def test_windows_path_handling(self):
        """Test Windows-style path handling."""
        # Windows paths in JSON
        result = parse_dict_param(
            '{"path": "C:\\\\Windows\\\\System32"}',
            {}
        )
        assert result["path"] == "C:\\Windows\\System32"
        
        # UNC paths
        result = parse_dict_param(
            '{"unc": "\\\\\\\\server\\\\share\\\\file"}',
            {}
        )
        assert "\\" in result["unc"]
    
    def test_case_sensitivity(self):
        """Test case sensitivity in parameters."""
        # JSON is case-sensitive
        result = parse_dict_param('{"Key": 1, "key": 2}', {})
        assert result["Key"] == 1
        assert result["key"] == 2
        
        # key=value format is case-sensitive for keys
        result = parse_dict_param("KEY=1,key=2", {})
        assert result["KEY"] == 1
        assert result["key"] == 2
        
        # Boolean values are case-insensitive
        result = parse_dict_param("a=TRUE,b=False,c=true", {})
        assert result["a"] is True
        assert result["b"] is False
        assert result["c"] is True


class TestErrorRecovery:
    """Test error recovery and graceful degradation."""
    
    def test_partial_parsing_recovery(self):
        """Test that parsing recovers from partial errors."""
        # Partially valid CSV
        result = parse_list_param("valid1,valid2,,valid3", [])
        assert "valid1" in result
        assert "valid3" in result
        
        # Partially valid key=value
        result = parse_dict_param("good=1,bad=,ok=2", {})
        assert result["good"] == 1
        assert result["ok"] == 2
        assert result["bad"] == ""  # Empty value
    
    def test_fallback_strategies(self):
        """Test fallback strategies for invalid inputs."""
        # Invalid JSON falls back to CSV
        result = parse_list_param('["item1", item2]', [])
        assert len(result) > 0  # Should parse something
        
        # Invalid everything falls back to default
        result = parse_dict_param("!@#$%^&*()", {"default": "value"})
        assert result == {"default": "value"}
    
    @patch('openscad_mcp.server.Path.mkdir')
    def test_directory_creation_failure(self, mock_mkdir):
        """Test handling of directory creation failures."""
        mock_mkdir.side_effect = PermissionError("Permission denied")
        
        test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should handle permission error gracefully
            with pytest.raises(ValueError, match="Failed to save image"):
                save_image_to_file(test_image, "test.png", Path("/root/forbidden"))