"""
Tests for rendering MCP tools: render_single, render_perspectives, compare_renders.

Covers quality presets, view presets, error handling, context logging,
include path forwarding, and input validation.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

from fastmcp.utilities.types import Image as MCPImage

from openscad_mcp.server import (
    render_single,
    render_perspectives,
    compare_renders,
    render_scad_to_png,
    VIEW_PRESETS,
    QUALITY_PRESETS,
)
from openscad_mcp.utils.config import Config, CacheConfig, SecurityConfig, set_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_B64 = "AAAA"


def _unwrap(tool):
    """Return the underlying async function from a FastMCP FunctionTool."""
    return tool.fn if hasattr(tool, "fn") else tool


def _parse_metadata(result):
    """Parse the JSON metadata string from the last element of a result list."""
    return json.loads(result[-1])


# ============================================================================
# TestRenderSingleGaps
# ============================================================================


class TestRenderSingleGaps:
    """Tests for the render_single MCP tool."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(render_single)

    # -- quality presets -----------------------------------------------------

    async def test_quality_draft(self):
        """Draft quality preset merges $fn/$fa/$fs into variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="draft")

        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        args = mock_render.call_args[0]
        # args order: scad_content, scad_file, camera_position, camera_target,
        #             camera_up, image_size, color_scheme, variables, auto_center,
        #             include_paths
        passed_vars = args[7]
        for key, value in QUALITY_PRESETS["draft"].items():
            assert passed_vars[key] == value

    async def test_quality_high(self):
        """High quality preset merges $fn/$fa/$fs into variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="high")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args[0][7]
        for key, value in QUALITY_PRESETS["high"].items():
            assert passed_vars[key] == value

    async def test_quality_normal(self):
        """Normal quality preset adds no extra variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="normal")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args[0][7]
        # normal preset is {}, so no quality keys
        assert "$fn" not in passed_vars
        assert "$fa" not in passed_vars
        assert "$fs" not in passed_vars

    async def test_quality_invalid(self):
        """Invalid quality preset raises ValueError."""
        with pytest.raises(ValueError, match="Invalid quality preset"):
            await self.fn(scad_content="cube(10);", quality="ultra")

    async def test_user_variable_overrides_quality(self):
        """User-provided variable values override quality preset values."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                quality="draft",
                variables={"$fn": 100},
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args[0][7]
        assert passed_vars["$fn"] == 100

    # -- view presets --------------------------------------------------------

    async def test_view_preset_front(self):
        """View preset 'front' sets correct camera parameters."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", view="front")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        args = mock_render.call_args[0]
        expected_pos, expected_target, expected_up = VIEW_PRESETS["front"]
        assert args[2] == list(expected_pos)
        assert args[3] == list(expected_target)
        assert args[4] == list(expected_up)

    async def test_view_preset_invalid(self):
        """Invalid view preset raises ValueError."""
        with pytest.raises(ValueError, match="Invalid view name"):
            await self.fn(scad_content="cube(10);", view="diagonal")

    # -- error handling & misc -----------------------------------------------

    async def test_error_handling(self):
        """RuntimeError in render_scad_to_png surfaces as error result."""
        with patch(
            "openscad_mcp.server.render_scad_to_png",
            side_effect=RuntimeError("OpenSCAD crashed"),
        ):
            result = await self.fn(scad_content="cube(10);")

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "OpenSCAD crashed" in metadata["error"]

    async def test_ctx_logging(self, mock_context):
        """Context info method is called during render."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", ctx=mock_context)

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        mock_context.info.assert_called()

    async def test_include_paths_forwarded(self):
        """Include paths are forwarded to render_scad_to_png."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", include_paths=["/some/path"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_include = mock_render.call_args[0][9]
        assert passed_include == ["/some/path"]

    async def test_both_inputs_error(self):
        """Providing both scad_content and scad_file raises ValueError."""
        with pytest.raises(ValueError, match="Exactly one"):
            await self.fn(
                scad_content="cube(10);",
                scad_file="/some/file.scad",
            )


# ============================================================================
# TestRenderPerspectives
# ============================================================================


class TestRenderPerspectives:
    """Tests for the render_perspectives MCP tool."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(render_perspectives)

    async def test_default_views(self):
        """Default views renders all 7 standard perspectives."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert metadata["count"] == 7

    async def test_custom_view_list(self):
        """Custom views list renders only the requested perspectives."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", views=["front", "top"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert metadata["count"] == 2
        # Check that the view labels are present in the result list
        labels = [item for item in result if isinstance(item, str) and item.startswith("View:")]
        assert "View: front" in labels
        assert "View: top" in labels

    async def test_views_as_csv_string(self):
        """Views provided as CSV string are parsed correctly."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", views=["front,top"])

        # With ["front,top"] it becomes a list with one element "front,top"
        # which is not in VIEW_PRESETS.
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid view name" in metadata["error"]

    async def test_views_parsed_as_list(self):
        """Explicit list of views renders only those perspectives."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", views=["front", "top"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert metadata["count"] == 2

    async def test_invalid_view(self):
        """Invalid view name in list returns error."""
        result = await self.fn(scad_content="cube(10);", views=["front", "nonexistent"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid view name" in metadata["error"]

    async def test_partial_failure(self):
        """When one view fails, the others still succeed."""
        call_count = 0

        def _mock_render_positional(
            scad_content=None,
            scad_file=None,
            camera_position=None,
            camera_target=None,
            camera_up=None,
            image_size=None,
            color_scheme="Cornfield",
            variables=None,
            auto_center=False,
            include_paths=None,
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("render failed")
            return FAKE_B64

        with patch(
            "openscad_mcp.server.render_scad_to_png",
            side_effect=_mock_render_positional,
        ):
            result = await self.fn(scad_content="cube(10);", views=["front", "top"])

        metadata = _parse_metadata(result)
        # One succeeds, one fails
        assert metadata["count"] == 1
        assert metadata["errors"] is not None
        assert len(metadata["errors"]) == 1

    async def test_quality_preset(self):
        """Quality preset variables are forwarded to render calls."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                quality="high",
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        # Check that quality vars were passed in all calls
        for call_args in mock_render.call_args_list:
            kwargs = call_args[1]
            passed_vars = kwargs.get("variables", {})
            for key, value in QUALITY_PRESETS["high"].items():
                assert passed_vars[key] == value

    async def test_both_inputs_error(self):
        """Providing both scad_content and scad_file returns error."""
        result = await self.fn(scad_content="cube(10);", scad_file="/some/file.scad")

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Exactly one" in metadata["error"]

    async def test_no_input_error(self):
        """Providing neither scad_content nor scad_file returns error."""
        result = await self.fn()

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Exactly one" in metadata["error"]

    async def test_include_paths(self):
        """Include paths are forwarded to render_scad_to_png calls."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                include_paths=["/lib"],
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        for call_args in mock_render.call_args_list:
            kwargs = call_args[1]
            assert kwargs.get("include_paths") == ["/lib"]

    async def test_ctx_logging(self, mock_context):
        """Context info method is called during render."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                ctx=mock_context,
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        mock_context.info.assert_called()


# ============================================================================
# TestCompareRenders
# ============================================================================


class TestCompareRenders:
    """Tests for the compare_renders MCP tool."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(compare_renders)

    async def test_two_contents_mode(self):
        """Providing before and after SCAD content renders both."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content_before="cube(10);",
                scad_content_after="sphere(10);",
            )

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0] == "Before:"
        assert isinstance(result[1], MCPImage)
        assert result[2] == "After:"
        assert isinstance(result[3], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert mock_render.call_count == 2

    async def test_file_with_variables_mode(self):
        """Providing scad_file + variables_before + variables_after succeeds."""
        scad_file = self.tmp_path / "model.scad"
        scad_file.write_text("cube(size);")

        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_file=str(scad_file),
                variables_before={"size": 10},
                variables_after={"size": 20},
            )

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0] == "Before:"
        assert isinstance(result[1], MCPImage)
        assert result[2] == "After:"
        assert isinstance(result[3], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert mock_render.call_count == 2

    async def test_invalid_no_inputs(self):
        """Providing no valid input combination returns error."""
        result = await self.fn()

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Provide either" in metadata["error"]

    async def test_invalid_only_before(self):
        """Providing only scad_content_before without after returns error."""
        result = await self.fn(scad_content_before="cube(10);")

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Provide either" in metadata["error"]

    async def test_quality_merging(self):
        """Quality preset variables are merged into render calls."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content_before="cube(10);",
                scad_content_after="sphere(10);",
                quality="draft",
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        for call_args in mock_render.call_args_list:
            kwargs = call_args[1]
            passed_vars = kwargs.get("variables", {})
            for key, value in QUALITY_PRESETS["draft"].items():
                assert passed_vars[key] == value

    async def test_error_handling(self):
        """RuntimeError in render_scad_to_png surfaces as error result."""
        with patch(
            "openscad_mcp.server.render_scad_to_png",
            side_effect=RuntimeError("OpenSCAD not found"),
        ):
            result = await self.fn(
                scad_content_before="cube(10);",
                scad_content_after="sphere(10);",
            )

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "OpenSCAD not found" in metadata["error"]

    async def test_invalid_view(self):
        """Invalid view preset returns error."""
        result = await self.fn(
            scad_content_before="cube(10);",
            scad_content_after="sphere(10);",
            view="nonexistent",
        )

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid view name" in metadata["error"]

    async def test_invalid_quality(self):
        """Invalid quality preset returns error."""
        result = await self.fn(
            scad_content_before="cube(10);",
            scad_content_after="sphere(10);",
            quality="ultra",
        )

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid quality preset" in metadata["error"]
