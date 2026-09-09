"""Tests for openscad_mcp.camera: projection math, framing and annotation."""

from __future__ import annotations

import io
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from openscad_mcp.camera import (
    PALETTE,
    VIEW_HEIGHT_FACTOR,
    OrthoCamera,
    annotate,
    bbox_corners,
    choose_scale_bar,
    fit_camera,
    part_color,
    spatial_digest,
)

CUBE_MIN = (-5.0, -5.0, -5.0)
CUBE_MAX = (5.0, 5.0, 5.0)

# The six standard views as the server defines them, plus isometric.
STANDARD_VIEWS = {
    "front": ((0, -200, 0), (0, 0, 0), (0, 0, 1)),
    "back": ((0, 200, 0), (0, 0, 0), (0, 0, 1)),
    "left": ((-200, 0, 0), (0, 0, 0), (0, 0, 1)),
    "right": ((200, 0, 0), (0, 0, 0), (0, 0, 1)),
    "top": ((0, 0, 200), (0, 0, 0), (0, 1, 0)),
    "bottom": ((0, 0, -200), (0, 0, 0), (0, -1, 0)),
    "isometric": ((200, 200, 200), (0, 0, 0), (0, 0, 1)),
}


def _spans(camera: OrthoCamera, bbox_min, bbox_max) -> tuple[float, float]:
    """Pixel width and height covered by a projected bounding box."""
    pts = [camera.project(c) for c in bbox_corners(bbox_min, bbox_max)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys))


def _solid_png(size: tuple[int, int], color=(200, 210, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.unit
class TestProjection:
    def test_view_height_factor_matches_openscad(self):
        assert abs(VIEW_HEIGHT_FACTOR - 0.397825) < 1e-6

    def test_cube_spans_150_px_at_distance_100(self):
        """A 10 mm cube at distance 100 is ~150 px tall in a 600 px image."""
        camera = OrthoCamera(
            eye=(0, -100, 0), center=(0, 0, 0), up=(0, 0, 1), image_size=(800, 600)
        )
        span_x, span_y = _spans(camera, CUBE_MIN, CUBE_MAX)
        assert span_x == pytest.approx(150, abs=2)
        assert span_y == pytest.approx(150, abs=2)

    def test_span_halves_at_double_distance(self):
        camera = OrthoCamera(eye=(0, -200, 0), image_size=(800, 600))
        assert _spans(camera, CUBE_MIN, CUBE_MAX)[1] == pytest.approx(76, abs=2)

    def test_scale_is_keyed_to_height_not_width(self):
        """800x300 at distance 100 gives the same 76 px as 800x600 at 200."""
        camera = OrthoCamera(eye=(0, -100, 0), image_size=(800, 300))
        assert _spans(camera, CUBE_MIN, CUBE_MAX)[1] == pytest.approx(76, abs=2)

        wide = OrthoCamera(eye=(0, -100, 0), image_size=(1600, 600))
        narrow = OrthoCamera(eye=(0, -100, 0), image_size=(400, 600))
        assert wide.mm_per_px == pytest.approx(narrow.mm_per_px)
        assert wide.view_width_mm == pytest.approx(4 * narrow.view_width_mm)

    def test_center_lands_on_image_center(self):
        camera = OrthoCamera(eye=(30, 40, 50), center=(1, 2, 3), image_size=(800, 600))
        assert camera.project((1, 2, 3)) == pytest.approx((400.0, 300.0))

    def test_pixel_y_grows_downward(self):
        camera = OrthoCamera(eye=(0, -100, 0), up=(0, 0, 1), image_size=(800, 600))
        high = camera.project((0, 0, 5))
        low = camera.project((0, 0, -5))
        assert high[1] < low[1]
        right = camera.project((5, 0, 0))
        assert right[0] > 400.0

    def test_mm_per_px_matches_formula(self):
        camera = OrthoCamera(eye=(0, -100, 0), image_size=(800, 600))
        assert camera.mm_per_px == pytest.approx(VIEW_HEIGHT_FACTOR * 100 / 600)
        assert camera.view_height_mm == pytest.approx(39.7825, abs=1e-3)

    def test_degenerate_up_does_not_raise(self):
        """Top view with a +Z up vector must fall back, not divide by zero."""
        camera = OrthoCamera(eye=(0, 0, 100), center=(0, 0, 0), up=(0, 0, 1))
        x, y = camera.project((5, 5, 0))
        assert math.isfinite(x) and math.isfinite(y)

    def test_zero_distance_is_clamped(self):
        camera = OrthoCamera(eye=(0, 0, 0), center=(0, 0, 0))
        assert camera.distance > 0
        assert all(math.isfinite(v) for v in camera.project((1, 1, 1)))

    def test_invalid_image_size_rejected(self):
        with pytest.raises(ValueError):
            OrthoCamera(eye=(0, -10, 0), image_size=(0, 600))

    def test_depth_orders_points_along_gaze(self):
        camera = OrthoCamera(eye=(0, -100, 0), image_size=(800, 600))
        assert camera.depth((0, 5, 0)) > camera.depth((0, -5, 0))


@pytest.mark.unit
class TestFitCamera:
    def test_isometric_fit_keeps_corners_inside_with_margin(self):
        camera = fit_camera((0, 0, 0), (10, 10, 10), (1, 1, 1), (0, 0, 1), (800, 600), margin=1.15)
        pts = [camera.project(c) for c in bbox_corners((0, 0, 0), (10, 10, 10))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert min(xs) > 0 and max(xs) < 800
        assert min(ys) > 0 and max(ys) < 600
        # Margin: the object must not fill the frame edge to edge.
        assert (max(ys) - min(ys)) < 600 / 1.05
        # And it should still be large enough to be useful.
        assert (max(ys) - min(ys)) > 600 * 0.4

    def test_distance_follows_bounding_sphere_formula(self):
        diameter = math.sqrt(3) * 10
        camera = fit_camera((0, 0, 0), (10, 10, 10), (0, -1, 0), (0, 0, 1), (800, 600))
        expected = diameter * 1.15 / VIEW_HEIGHT_FACTOR
        assert camera.distance == pytest.approx(expected)
        assert camera.center == pytest.approx((5.0, 5.0, 5.0))

    def test_tall_narrow_image_widens_the_view(self):
        """When width < height, the horizontal extent is the limiting one."""
        camera = fit_camera((0, 0, 0), (10, 10, 10), (1, 1, 1), (0, 0, 1), (300, 600))
        assert camera.view_width_mm >= math.sqrt(3) * 10 * 1.15 - 1e-6
        pts = [camera.project(c) for c in bbox_corners((0, 0, 0), (10, 10, 10))]
        assert min(p[0] for p in pts) > 0
        assert max(p[0] for p in pts) < 300

    def test_eye_lies_along_requested_direction(self):
        camera = fit_camera((0, 0, 0), (2, 2, 2), (0, -1, 0), (0, 0, 1), (800, 600))
        assert camera.eye[0] == pytest.approx(1.0)
        assert camera.eye[2] == pytest.approx(1.0)
        assert camera.eye[1] < 0

    def test_degenerate_bbox_still_yields_usable_camera(self):
        camera = fit_camera((0, 0, 0), (0, 0, 0), (1, 1, 1), (0, 0, 1), (100, 100))
        assert camera.distance >= 1e-3
        assert all(math.isfinite(v) for v in camera.project((0, 0, 0)))


@pytest.mark.unit
class TestViewDirectionWords:
    @pytest.mark.parametrize(
        "view,expected",
        [
            ("front", "looking along +Y from -Y (front view), +Z up"),
            ("back", "looking along -Y from +Y (back view), +Z up"),
            ("left", "looking along +X from -X (left view), +Z up"),
            ("right", "looking along -X from +X (right view), +Z up"),
            ("top", "looking down along -Z from +Z (top view), +Y up"),
            ("bottom", "looking up along +Z from -Z (bottom view), -Y up"),
        ],
    )
    def test_six_axis_views(self, view, expected):
        eye, center, up = STANDARD_VIEWS[view]
        assert OrthoCamera(eye=eye, center=center, up=up).view_direction_words() == expected

    def test_isometric(self):
        eye, center, up = STANDARD_VIEWS["isometric"]
        words = OrthoCamera(eye=eye, center=center, up=up).view_direction_words()
        assert words == "looking from +X+Y+Z toward the center (isometric), +Z up"

    def test_off_axis_view_is_described_without_a_preset_name(self):
        words = OrthoCamera(eye=(200, 100, 200), center=(0, 0, 0)).view_direction_words()
        assert words.startswith("looking from +X+Y+Z toward the center")
        assert "isometric" not in words

    def test_offset_center_uses_relative_direction(self):
        camera = OrthoCamera(eye=(10, -90, 10), center=(10, 10, 10), up=(0, 0, 1))
        assert camera.view_direction_words() == "looking along +Y from -Y (front view), +Z up"


@pytest.mark.unit
class TestScaleBar:
    def test_nice_length_for_typical_render(self):
        length_mm, length_px = choose_scale_bar(0.0663, 800)
        fraction = length_px / 800
        assert 0.15 <= fraction <= 0.30
        mantissa = length_mm / (10 ** math.floor(math.log10(length_mm)))
        assert round(mantissa, 6) in (1.0, 2.0, 5.0)
        assert length_mm == pytest.approx(10.0)

    @pytest.mark.parametrize("mm_per_px", [0.001, 0.0663, 0.5, 3.7, 120.0])
    @pytest.mark.parametrize("width", [100, 640, 800, 1920])
    def test_always_nice_and_within_window(self, mm_per_px, width):
        length_mm, length_px = choose_scale_bar(mm_per_px, width)
        mantissa = length_mm / (10 ** math.floor(math.log10(length_mm)))
        assert round(mantissa, 6) in (1.0, 2.0, 5.0)
        assert 0.15 <= length_px / width <= 0.30

    def test_zero_scale_is_safe(self):
        assert choose_scale_bar(0.0, 800) == (0.0, 0.0)


@pytest.mark.unit
class TestPalette:
    def test_ten_distinct_colors(self):
        assert len(PALETTE) == 10
        assert len(set(PALETTE)) == 10

    def test_entries_are_openscad_compatible_hex(self):
        for color in PALETTE:
            assert color.startswith("#") and len(color) == 7
            int(color[1:], 16)

    def test_part_color_cycles(self):
        assert part_color(0) == PALETTE[0]
        assert part_color(9) == PALETTE[9]
        assert part_color(10) == PALETTE[0]
        assert part_color(13) == PALETTE[3]
        assert part_color(-1) == PALETTE[9]


@pytest.mark.unit
class TestAnnotate:
    @pytest.fixture
    def camera(self):
        return OrthoCamera(eye=(0, -100, 0), center=(0, 0, 0), up=(0, 0, 1), image_size=(800, 600))

    @pytest.mark.parametrize("scale_bar", [True, False])
    @pytest.mark.parametrize("axes", [True, False])
    @pytest.mark.parametrize("dimensions", [True, False])
    @pytest.mark.parametrize("with_bbox", [True, False])
    def test_all_option_combinations(self, camera, scale_bar, axes, dimensions, with_bbox):
        source = _solid_png(camera.image_size)
        out = annotate(
            source,
            camera,
            bbox_min=CUBE_MIN if with_bbox else None,
            bbox_max=CUBE_MAX if with_bbox else None,
            label="front" if with_bbox else None,
            scale_bar=scale_bar,
            axes=axes,
            dimensions=dimensions,
        )
        assert out.startswith(b"\x89PNG\r\n\x1a\n")
        image = Image.open(io.BytesIO(out))
        image.load()
        assert image.format == "PNG"
        assert image.size == camera.image_size

    def test_annotation_changes_pixels(self, camera):
        source = _solid_png(camera.image_size)
        out = annotate(source, camera, bbox_min=CUBE_MIN, bbox_max=CUBE_MAX, label="front")
        assert out != source
        before = Image.open(io.BytesIO(source)).convert("RGB")
        after = Image.open(io.BytesIO(out)).convert("RGB")
        changed = sum(1 for a, b in zip(before.getdata(), after.getdata(), strict=True) if a != b)
        assert changed > 200

    def test_tiny_image_stays_in_bounds(self, camera):
        small = OrthoCamera(eye=(0, -100, 0), center=(0, 0, 0), up=(0, 0, 1), image_size=(100, 100))
        out = annotate(
            _solid_png((100, 100)),
            small,
            bbox_min=CUBE_MIN,
            bbox_max=CUBE_MAX,
            label="tiny",
        )
        image = Image.open(io.BytesIO(out))
        image.load()
        assert image.size == (100, 100)

    @pytest.mark.parametrize("view", sorted(STANDARD_VIEWS))
    def test_every_standard_view_annotates(self, view):
        eye, center, up = STANDARD_VIEWS[view]
        cam = OrthoCamera(eye=eye, center=center, up=up, image_size=(400, 300))
        out = annotate(
            _solid_png((400, 300)), cam, bbox_min=CUBE_MIN, bbox_max=CUBE_MAX, label=view
        )
        assert Image.open(io.BytesIO(out)).size == (400, 300)

    def test_axis_pointing_at_viewer_is_noted(self):
        """In a front view the Y axis points away, so it gets a text note."""
        cam = OrthoCamera(eye=(0, -100, 0), up=(0, 0, 1), image_size=(400, 300))
        plain = Image.open(io.BytesIO(_solid_png((400, 300)))).convert("RGB")
        annotated = Image.open(
            io.BytesIO(annotate(_solid_png((400, 300)), cam, scale_bar=False, dimensions=False))
        ).convert("RGB")
        # Text plate for the note sits below the triad on the left edge.
        region = annotated.crop((0, 0, 200, 160))
        assert list(region.getdata()) != list(plain.crop((0, 0, 200, 160)).getdata())

    def test_image_size_mismatch_is_tolerated(self, camera):
        out = annotate(_solid_png((400, 300)), camera, bbox_min=CUBE_MIN, bbox_max=CUBE_MAX)
        assert Image.open(io.BytesIO(out)).size == (400, 300)

    def test_grayscale_input_is_accepted(self, camera):
        buf = io.BytesIO()
        Image.new("L", camera.image_size, 128).save(buf, format="PNG")
        out = annotate(buf.getvalue(), camera, bbox_min=CUBE_MIN, bbox_max=CUBE_MAX)
        assert Image.open(io.BytesIO(out)).size == camera.image_size


@pytest.mark.unit
class TestSpatialDigest:
    @pytest.fixture
    def camera(self):
        eye, center, up = STANDARD_VIEWS["isometric"]
        return OrthoCamera(eye=eye, center=center, up=up, image_size=(800, 600))

    def test_grounded_digest_reports_scale(self, camera):
        text = spatial_digest(
            camera, bbox_min=(0, 0, 0), bbox_max=(10, 20, 5), view_name="isometric"
        )
        assert "mm/px" in text
        assert "unknown" not in text
        assert "isometric" in text
        assert "units: mm" in text
        assert "10 x 20 x 5 mm" in text
        assert "orthographic" in text
        assert len(text.splitlines()) <= 12

    def test_ungrounded_digest_says_unknown(self, camera):
        text = spatial_digest(camera, grounded=False)
        assert "unknown" in text
        assert "mm/px" not in text
        assert "auto-fit" in text

    def test_digest_without_bbox_is_short(self, camera):
        text = spatial_digest(camera)
        assert "bbox" not in text
        assert len(text.splitlines()) <= 12

    def test_digest_reports_camera_and_direction(self, camera):
        text = spatial_digest(camera, view_name="isometric")
        assert "eye=(200, 200, 200)" in text
        assert "distance=346.41" in text
        assert camera.view_direction_words() in text


@pytest.mark.integration
@pytest.mark.slow
class TestAgainstRealOpenSCAD:
    """Verify the projection model against an actual OpenSCAD render."""

    def _openscad(self) -> str | None:
        return shutil.which("openscad") or (
            "/bin/openscad" if Path("/bin/openscad").exists() else None
        )

    def test_projected_corners_match_rendered_pixels(self, tmp_path):
        binary = self._openscad()
        if binary is None:
            pytest.skip("OpenSCAD is not installed")

        scad = tmp_path / "cube.scad"
        scad.write_text("cube(10, center=true);\n")
        png = tmp_path / "cube.png"
        try:
            result = subprocess.run(
                [
                    binary,
                    "-o",
                    str(png),
                    "--projection=o",
                    "--imgsize=800,600",
                    "--camera=0,-100,0,0,0,0",
                    str(scad),
                ],
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
            pytest.skip(f"OpenSCAD render unavailable: {exc}")
        if result.returncode != 0 or not png.exists():  # pragma: no cover
            pytest.skip(f"OpenSCAD render failed: {result.stderr[-300:]!r}")

        image = Image.open(png).convert("RGB")
        width, height = image.size
        pixels = image.load()
        xs: list[int] = []
        ys: list[int] = []
        for y in range(height):
            background = pixels[0, y]  # the model never touches the left edge
            for x in range(width):
                color = pixels[x, y]
                if max(abs(color[i] - background[i]) for i in range(3)) > 12:
                    xs.append(x)
                    ys.append(y)
        assert xs, "render produced no object pixels"

        camera = OrthoCamera(
            eye=(0, -100, 0), center=(0, 0, 0), up=(0, 0, 1), image_size=(width, height)
        )
        pts = [camera.project(c) for c in bbox_corners(CUBE_MIN, CUBE_MAX)]
        pred_x0 = min(p[0] for p in pts)
        pred_x1 = max(p[0] for p in pts)
        pred_y0 = min(p[1] for p in pts)
        pred_y1 = max(p[1] for p in pts)

        assert min(xs) == pytest.approx(pred_x0, abs=3)
        assert max(xs) == pytest.approx(pred_x1, abs=3)
        assert min(ys) == pytest.approx(pred_y0, abs=3)
        assert max(ys) == pytest.approx(pred_y1, abs=3)
