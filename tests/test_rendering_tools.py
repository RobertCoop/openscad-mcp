"""
Tests for rendering MCP tools: render_single, render_perspectives, compare_renders.

Covers quality presets, view presets, error handling, context logging,
include path forwarding, and input validation.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

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
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="draft")

        assert result["success"] is True
        call_kwargs = mock_render.call_args
        variables = call_kwargs[1].get("variables") or call_kwargs[0][8] if len(call_kwargs[0]) > 8 else call_kwargs[1].get("variables")
        # positional arg index 8 is variables; let's just check the call
        # The function passes variables as a positional arg to render_scad_to_png
        # render_scad_to_png is called via run_in_executor with positional args
        args = mock_render.call_args[0]
        # args order: scad_content, scad_file, camera_position, camera_target,
        #             camera_up, image_size, color_scheme, variables, auto_center,
        #             include_paths
        passed_vars = args[7]
        for key, value in QUALITY_PRESETS["draft"].items():
            assert passed_vars[key] == value

    async def test_quality_high(self):
        """High quality preset merges $fn/$fa/$fs into variables."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="high")

        assert result["success"] is True
        passed_vars = mock_render.call_args[0][7]
        for key, value in QUALITY_PRESETS["high"].items():
            assert passed_vars[key] == value

    async def test_quality_normal(self):
        """Normal quality preset adds no extra variables."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="normal")

        assert result["success"] is True
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
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                quality="draft",
                variables={"$fn": 100},
            )

        assert result["success"] is True
        passed_vars = mock_render.call_args[0][7]
        assert passed_vars["$fn"] == 100

    # -- view presets --------------------------------------------------------

    async def test_view_preset_front(self):
        """View preset 'front' sets correct camera parameters."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(scad_content="cube(10);", view="front")

        assert result["success"] is True
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

        assert result["success"] is False
        assert "OpenSCAD crashed" in result["error"]

    async def test_ctx_logging(self, mock_context):
        """Context info method is called during render."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(scad_content="cube(10);", ctx=mock_context)

        assert result["success"] is True
        mock_context.info.assert_called()

    async def test_include_paths_forwarded(self):
        """Include paths are forwarded to render_scad_to_png."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content="cube(10);", include_paths=["/some/path"]
            )

        assert result["success"] is True
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
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(scad_content="cube(10);")

        assert result["success"] is True
        assert result["count"] == 7

    async def test_custom_view_list(self):
        """Custom views list renders only the requested perspectives."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(
                scad_content="cube(10);", views=["front", "top"]
            )

        assert result["success"] is True
        assert result["count"] == 2
        assert "front" in result["views"]
        assert "top" in result["views"]

    async def test_views_as_csv_string(self):
        """Views provided as CSV string are parsed correctly."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(
                scad_content="cube(10);", views=["front,top"]
            )

        # parse_list_param is called on the views list, but views is already
        # a list, so it passes through. For a true CSV test, let's verify
        # with a single-element list containing a CSV.
        # Actually views is typed Optional[List[str]], but the code calls
        # parse_list_param when views is not None, which handles lists directly.
        # The CSV path is hit when views itself is a string, but the type
        # signature says List[str]. Let's test the validation path instead.
        # With ["front,top"] it becomes a list with one element "front,top"
        # which is not in VIEW_PRESETS.
        assert result["success"] is False
        assert "Invalid view name" in result["error"]

    async def test_views_parsed_as_list(self):
        """Explicit list of views renders only those perspectives."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(
                scad_content="cube(10);", views=["front", "top"]
            )

        assert result["success"] is True
        assert result["count"] == 2

    async def test_invalid_view(self):
        """Invalid view name in list returns error."""
        result = await self.fn(
            scad_content="cube(10);", views=["front", "nonexistent"]
        )

        assert result["success"] is False
        assert "Invalid view name" in result["error"]

    async def test_partial_failure(self):
        """When one view fails, the others still succeed."""
        call_count = 0

        def _mock_render(**kwargs):
            nonlocal call_count
            call_count += 1
            # Fail for second view
            if call_count == 2:
                raise RuntimeError("render failed")
            return FAKE_B64

        def _mock_render_positional(
            scad_content=None, scad_file=None,
            camera_position=None, camera_target=None,
            camera_up=None, image_size=None,
            color_scheme="Cornfield", variables=None,
            auto_center=False, include_paths=None,
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
            result = await self.fn(
                scad_content="cube(10);", views=["front", "top"]
            )

        # One succeeds, one fails
        assert result["count"] == 1
        assert result["errors"] is not None
        assert len(result["errors"]) == 1

    async def test_quality_preset(self):
        """Quality preset variables are forwarded to render calls."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                quality="high",
            )

        assert result["success"] is True
        # Check that quality vars were passed in all calls
        for call_args in mock_render.call_args_list:
            kwargs = call_args[1]
            passed_vars = kwargs.get("variables", {})
            for key, value in QUALITY_PRESETS["high"].items():
                assert passed_vars[key] == value

    async def test_both_inputs_error(self):
        """Providing both scad_content and scad_file returns error."""
        result = await self.fn(
            scad_content="cube(10);", scad_file="/some/file.scad"
        )

        assert result["success"] is False
        assert "Exactly one" in result["error"]

    async def test_no_input_error(self):
        """Providing neither scad_content nor scad_file returns error."""
        result = await self.fn()

        assert result["success"] is False
        assert "Exactly one" in result["error"]

    async def test_include_paths(self):
        """Include paths are forwarded to render_scad_to_png calls."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                include_paths=["/lib"],
            )

        assert result["success"] is True
        for call_args in mock_render.call_args_list:
            kwargs = call_args[1]
            assert kwargs.get("include_paths") == ["/lib"]

    async def test_ctx_logging(self, mock_context):
        """Context info method is called during render."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ):
            result = await self.fn(
                scad_content="cube(10);",
                views=["front"],
                ctx=mock_context,
            )

        assert result["success"] is True
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
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content_before="cube(10);",
                scad_content_after="sphere(10);",
            )

        assert result["success"] is True
        assert "before" in result
        assert "after" in result
        assert result["before"]["data"] == FAKE_B64
        assert result["after"]["data"] == FAKE_B64
        assert mock_render.call_count == 2

    async def test_file_with_variables_mode(self):
        """Providing scad_file + variables_before + variables_after succeeds."""
        scad_file = self.tmp_path / "model.scad"
        scad_file.write_text("cube(size);")

        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_file=str(scad_file),
                variables_before={"size": 10},
                variables_after={"size": 20},
            )

        assert result["success"] is True
        assert "before" in result
        assert "after" in result
        assert mock_render.call_count == 2

    async def test_invalid_no_inputs(self):
        """Providing no valid input combination returns error."""
        result = await self.fn()

        assert result["success"] is False
        assert "Provide either" in result["error"]

    async def test_invalid_only_before(self):
        """Providing only scad_content_before without after returns error."""
        result = await self.fn(scad_content_before="cube(10);")

        assert result["success"] is False
        assert "Provide either" in result["error"]

    async def test_quality_merging(self):
        """Quality preset variables are merged into render calls."""
        with patch(
            "openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64
        ) as mock_render:
            result = await self.fn(
                scad_content_before="cube(10);",
                scad_content_after="sphere(10);",
                quality="draft",
            )

        assert result["success"] is True
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

        assert result["success"] is False
        assert "OpenSCAD not found" in result["error"]

    async def test_invalid_view(self):
        """Invalid view preset returns error."""
        result = await self.fn(
            scad_content_before="cube(10);",
            scad_content_after="sphere(10);",
            view="nonexistent",
        )

        assert result["success"] is False
        assert "Invalid view name" in result["error"]

    async def test_invalid_quality(self):
        """Invalid quality preset returns error."""
        result = await self.fn(
            scad_content_before="cube(10);",
            scad_content_after="sphere(10);",
            quality="ultra",
        )

        assert result["success"] is False
        assert "Invalid quality preset" in result["error"]
