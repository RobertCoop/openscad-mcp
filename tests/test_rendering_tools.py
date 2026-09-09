"""
Tests for the consolidated ``render`` MCP tool.

Covers quality presets, view presets, error handling, context logging,
include path forwarding, and input validation for mode="views" and
mode="compare".
"""

import json
from unittest.mock import patch

import pytest
from fastmcp.utilities.types import Image as MCPImage

from openscad_mcp.server import (
    DEFAULT_RENDER_VIEWS,
    QUALITY_PRESETS,
    VIEW_PRESETS,
    render,
)

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


def _view_labels(result):
    """Text digests that introduce an image, one per rendered view."""
    return [item for item in result if isinstance(item, str) and item.startswith("View:")]


# ============================================================================
# TestRenderSingleView
# ============================================================================


class TestRenderSingleView:
    """Tests for render(mode="views") with a single view."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(render)

    # -- quality presets -----------------------------------------------------

    async def test_quality_draft(self):
        """Draft quality preset merges $fn/$fa/$fs into variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="draft")

        assert isinstance(result, list)
        # digest, image, metadata
        assert len(result) == 3
        assert isinstance(result[1], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args.kwargs["variables"]
        for key, value in QUALITY_PRESETS["draft"].items():
            assert passed_vars[key] == value

    async def test_quality_high(self):
        """High quality preset merges $fn/$fa/$fs into variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="high")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args.kwargs["variables"]
        for key, value in QUALITY_PRESETS["high"].items():
            assert passed_vars[key] == value

    async def test_quality_normal(self):
        """Normal quality preset adds no extra variables."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", quality="normal")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        passed_vars = mock_render.call_args.kwargs["variables"]
        # normal preset is {}, so no quality keys
        assert "$fn" not in passed_vars
        assert "$fa" not in passed_vars
        assert "$fs" not in passed_vars

    async def test_quality_invalid(self):
        """Invalid quality preset is reported in the metadata."""
        result = await self.fn(scad_content="cube(10);", quality="ultra")

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid quality preset" in metadata["error"]

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
        passed_vars = mock_render.call_args.kwargs["variables"]
        assert passed_vars["$fn"] == 100

    # -- view presets --------------------------------------------------------

    async def test_view_preset_front(self):
        """View preset 'front' sets correct camera parameters."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", views=["front"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        kwargs = mock_render.call_args.kwargs
        expected_pos, expected_target, expected_up = VIEW_PRESETS["front"]
        assert kwargs["camera_position"] == list(expected_pos)
        assert kwargs["camera_target"] == list(expected_target)
        assert kwargs["camera_up"] == list(expected_up)

    async def test_view_preset_invalid(self):
        """Invalid view preset is reported in the metadata."""
        result = await self.fn(scad_content="cube(10);", views=["diagonal"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid view name" in metadata["error"]

    async def test_custom_camera_without_views(self):
        """camera_position without views renders one custom-camera image."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", camera_position=[10, 20, 30])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert metadata["views"] == ["custom"]
        assert mock_render.call_args.kwargs["camera_position"] == [10, 20, 30]

    async def test_auto_fit_used_for_ungrounded_render(self):
        """Ungrounded renders always auto-fit; there is no auto_center switch."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(scad_content="cube(10);", views=["front"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert mock_render.call_args.kwargs["auto_center"] is True
        assert "projection" not in mock_render.call_args.kwargs

    # -- error handling & misc -----------------------------------------------

    async def test_error_handling(self):
        """RuntimeError in render_scad_to_png surfaces as a failed view."""
        with patch(
            "openscad_mcp.server.render_scad_to_png",
            side_effect=RuntimeError("OpenSCAD crashed"),
        ):
            result = await self.fn(scad_content="cube(10);")

        assert isinstance(result, list)
        # no digest and no image: only the metadata survives
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "OpenSCAD crashed" in json.dumps(metadata["failed_views"])

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
        assert mock_render.call_args.kwargs["include_paths"] == ["/some/path"]

    async def test_both_inputs_error(self):
        """Providing both scad_content and scad_file is reported in the metadata."""
        result = await self.fn(
            scad_content="cube(10);",
            scad_file="/some/file.scad",
        )

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Exactly one" in metadata["error"]

    async def test_invalid_mode(self):
        """An unknown mode is rejected."""
        result = await self.fn(scad_content="cube(10);", mode="wireframe")

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "mode must be one of" in metadata["error"]


# ============================================================================
# TestRenderMultipleViews
# ============================================================================


class TestRenderMultipleViews:
    """Tests for render(mode="views") with several perspectives."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(render)

    async def test_default_views(self):
        """Default renders one view (isometric), not every preset.

        Every 800x600 image costs roughly 640 vision tokens; rendering all
        eight presets by default would be ~5000 tokens per call.
        """
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);")

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert metadata["views"] == list(DEFAULT_RENDER_VIEWS) == ["isometric"]
        assert len(metadata["views"]) == 1

    async def test_custom_view_list(self):
        """Custom views list renders only the requested perspectives."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", views=["front", "top"])

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert len(metadata["views"]) == 2
        # Check that the view labels are present in the result list
        labels = _view_labels(result)
        assert any(label.startswith("View: front") for label in labels)
        assert any(label.startswith("View: top") for label in labels)

    async def test_views_as_csv_string(self):
        """A single list entry holding a CSV string is not split."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            result = await self.fn(scad_content="cube(10);", views=["front,top"])

        # With ["front,top"] it stays a list with one element "front,top"
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
        assert len(metadata["views"]) == 2

    async def test_image_tokens_scale_with_view_count(self):
        """image_tokens counts every image the call returns."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64):
            one = await self.fn(scad_content="cube(10);", views=["front"])
            two = await self.fn(scad_content="cube(10);", views=["front", "top"])

        assert _parse_metadata(two)["image_tokens"] == 2 * _parse_metadata(one)["image_tokens"]

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
            projection=None,
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
        assert len(metadata["views"]) == 1
        assert metadata["failed_views"] is not None
        assert len(metadata["failed_views"]) == 1
        assert metadata["success"] is False

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
            passed_vars = call_args.kwargs.get("variables", {})
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
            assert call_args.kwargs.get("include_paths") == ["/lib"]

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
# TestRenderCompare
# ============================================================================


class TestRenderCompare:
    """Tests for render(mode="compare")."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(render)

    async def test_two_contents_mode(self):
        """Providing before and after SCAD content renders both."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                scad_content_after="sphere(10);",
                mode="compare",
            )

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0].startswith("Before:")
        assert isinstance(result[1], MCPImage)
        assert result[2].startswith("After:")
        assert isinstance(result[3], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert set(metadata["before"]) or metadata["before"] == {}
        assert mock_render.call_count == 2
        rendered = {call.kwargs["scad_content"] for call in mock_render.call_args_list}
        assert rendered == {"cube(10);", "sphere(10);"}

    async def test_file_with_variables_mode(self):
        """Providing scad_file + variables + variables_after succeeds."""
        scad_file = self.tmp_path / "model.scad"
        scad_file.write_text("cube(size);")

        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_file=str(scad_file),
                variables={"size": 10},
                variables_after={"size": 20},
                mode="compare",
            )

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0].startswith("Before:")
        assert isinstance(result[1], MCPImage)
        assert result[2].startswith("After:")
        assert isinstance(result[3], MCPImage)
        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        assert mock_render.call_count == 2
        sizes = sorted(call.kwargs["variables"]["size"] for call in mock_render.call_args_list)
        assert sizes == [10, 20]

    async def test_invalid_no_inputs(self):
        """Providing no source returns error."""
        result = await self.fn(mode="compare")

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Exactly one" in metadata["error"]

    async def test_invalid_only_before(self):
        """Providing only the 'before' source without an 'after' returns error."""
        result = await self.fn(scad_content="cube(10);", mode="compare")

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "compare needs variables_after or scad_content_after" in metadata["error"]

    async def test_quality_merging(self):
        """Quality preset variables are merged into render calls."""
        with patch("openscad_mcp.server.render_scad_to_png", return_value=FAKE_B64) as mock_render:
            result = await self.fn(
                scad_content="cube(10);",
                scad_content_after="sphere(10);",
                mode="compare",
                quality="draft",
            )

        metadata = _parse_metadata(result)
        assert metadata["success"] is True
        for call_args in mock_render.call_args_list:
            passed_vars = call_args.kwargs.get("variables", {})
            for key, value in QUALITY_PRESETS["draft"].items():
                assert passed_vars[key] == value

    async def test_error_handling(self):
        """RuntimeError in render_scad_to_png surfaces as error result."""
        with patch(
            "openscad_mcp.server.render_scad_to_png",
            side_effect=RuntimeError("OpenSCAD not found"),
        ):
            result = await self.fn(
                scad_content="cube(10);",
                scad_content_after="sphere(10);",
                mode="compare",
            )

        assert isinstance(result, list)
        assert len(result) == 1
        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "OpenSCAD not found" in metadata["error"]

    async def test_invalid_view(self):
        """Invalid view preset returns error."""
        result = await self.fn(
            scad_content="cube(10);",
            scad_content_after="sphere(10);",
            mode="compare",
            views=["nonexistent"],
        )

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid view name" in metadata["error"]

    async def test_invalid_quality(self):
        """Invalid quality preset returns error."""
        result = await self.fn(
            scad_content="cube(10);",
            scad_content_after="sphere(10);",
            mode="compare",
            quality="ultra",
        )

        metadata = _parse_metadata(result)
        assert metadata["success"] is False
        assert "Invalid quality preset" in metadata["error"]
