"""Tests for the dependency-free mesh analyser in ``openscad_mcp.mesh``.

Meshes are generated in code, so none of these tests require OpenSCAD. The one
test that does shell out to OpenSCAD is skipped when the binary is absent.
"""

import math
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

from openscad_mcp.mesh import (
    MATERIAL_DENSITIES,
    ComponentStats,
    MeshStats,
    Triangle,
    analyze_polygons,
    analyze_stl,
    analyze_svg,
    analyze_triangles,
    load_stl,
    load_svg_polygons,
    mass_from_volume,
)

# ---------------------------------------------------------------------------
# Mesh builders
# ---------------------------------------------------------------------------


def make_box(origin=(0.0, 0.0, 0.0), size=1.0, invert=False):
    """Return the 12 triangles of an axis-aligned box with outward normals.

    ``size`` may be a scalar or an ``(sx, sy, sz)`` tuple. With ``invert=True``
    the winding is reversed, which is how an internal cavity's shell looks.
    """
    if isinstance(size, int | float):
        sx = sy = sz = float(size)
    else:
        sx, sy, sz = (float(v) for v in size)
    x0, y0, z0 = (float(v) for v in origin)
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz

    p0 = (x0, y0, z0)
    p1 = (x1, y0, z0)
    p2 = (x1, y1, z0)
    p3 = (x0, y1, z0)
    p4 = (x0, y0, z1)
    p5 = (x1, y0, z1)
    p6 = (x1, y1, z1)
    p7 = (x0, y1, z1)

    tris = [
        (p0, p2, p1),
        (p0, p3, p2),  # bottom (-z)
        (p4, p5, p6),
        (p4, p6, p7),  # top (+z)
        (p0, p1, p5),
        (p0, p5, p4),  # front (-y)
        (p2, p3, p7),
        (p2, p7, p6),  # back (+y)
        (p0, p4, p7),
        (p0, p7, p3),  # left (-x)
        (p1, p2, p6),
        (p1, p6, p5),  # right (+x)
    ]
    if invert:
        tris = [(a, c, b) for a, b, c in tris]
    return tris


def write_ascii_stl(path: Path, tris, name="test"):
    """Write triangles as an ASCII STL, mimicking OpenSCAD's output shape."""
    lines = [f"solid {name}"]
    for a, b, c in tris:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for v in (a, b, c):
            lines.append("      vertex {:.10g} {:.10g} {:.10g}".format(*v))
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n")
    return path


def write_binary_stl(path: Path, tris, header=b"binary test stl"):
    """Write triangles as a binary STL using struct."""
    with open(path, "wb") as fh:
        fh.write(header.ljust(80, b"\0")[:80])
        fh.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            fh.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for v in (a, b, c):
                fh.write(struct.pack("<3f", *v))
            fh.write(struct.pack("<H", 0))
    return path


# ---------------------------------------------------------------------------
# Basic geometry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnitCube:
    """A unit cube exercises every basic quantity with known answers."""

    def test_volume_and_area(self):
        stats = analyze_triangles(make_box())
        assert stats.triangle_count == 12
        assert stats.vertex_count == 8
        assert stats.volume == pytest.approx(1.0)
        assert stats.surface_area == pytest.approx(6.0)

    def test_topology(self):
        stats = analyze_triangles(make_box())
        assert stats.is_watertight is True
        assert stats.open_edge_count == 0
        assert stats.non_manifold_edge_count == 0
        assert stats.degenerate_triangle_count == 0
        assert stats.is_manifold is True

    def test_components(self):
        stats = analyze_triangles(make_box())
        assert len(stats.components) == 1
        assert stats.solid_count == 1
        assert stats.cavity_count == 0
        comp = stats.components[0]
        assert isinstance(comp, ComponentStats)
        assert comp.index == 0
        assert comp.triangle_count == 12
        assert comp.is_closed is True
        assert comp.is_cavity is False
        assert comp.centroid == pytest.approx((0.5, 0.5, 0.5))

    def test_bounding_box(self):
        stats = analyze_triangles(make_box(origin=(-2.0, 1.0, 0.0), size=(4.0, 2.0, 6.0)))
        assert stats.bbox_min == pytest.approx((-2.0, 1.0, 0.0))
        assert stats.bbox_max == pytest.approx((2.0, 3.0, 6.0))
        assert stats.dimensions == pytest.approx((4.0, 2.0, 6.0))
        assert stats.center == pytest.approx((0.0, 2.0, 3.0))
        assert stats.volume == pytest.approx(48.0)

    def test_volume_is_translation_invariant(self):
        far = analyze_triangles(make_box(origin=(1000.0, -500.0, 250.0), size=2.0))
        assert far.volume == pytest.approx(8.0)
        assert far.components[0].centroid == pytest.approx((1001.0, -499.0, 251.0))

    def test_accepts_triangle_objects(self):
        tris = [Triangle.from_tuple(t) for t in make_box()]
        stats = analyze_triangles(tris)
        assert stats.volume == pytest.approx(1.0)
        assert stats.triangle_count == 12


@pytest.mark.unit
class TestTriangleHelper:
    """The Triangle convenience wrapper."""

    def test_area_and_normal(self):
        tri = Triangle((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        assert tri.area == pytest.approx(0.5)
        assert tri.normal == pytest.approx((0.0, 0.0, 1.0))
        assert tri.as_tuple() == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    def test_degenerate_normal_is_zero(self):
        tri = Triangle((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
        assert tri.area == pytest.approx(0.0)
        assert tri.normal == (0.0, 0.0, 0.0)

    def test_signed_volume_flips_with_winding(self):
        tri = Triangle((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        flipped = Triangle(tri.v0, tri.v2, tri.v1)
        assert tri.signed_volume == pytest.approx(-flipped.signed_volume)


# ---------------------------------------------------------------------------
# Components, cavities, topology defects
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComponents:
    """Component splitting, cavity detection and defect counting."""

    def test_hollow_box_reports_cavity(self):
        outer = make_box(origin=(0.0, 0.0, 0.0), size=10.0)
        inner = make_box(origin=(2.0, 2.0, 2.0), size=6.0, invert=True)
        stats = analyze_triangles(outer + inner)

        assert len(stats.components) == 2
        assert stats.solid_count == 1
        assert stats.cavity_count == 1
        assert stats.volume == pytest.approx(1000.0 - 216.0)
        assert stats.is_watertight is True
        assert stats.surface_area == pytest.approx(6 * 100 + 6 * 36)

        shell, cavity = stats.components
        assert shell.volume == pytest.approx(1000.0)
        assert cavity.volume == pytest.approx(-216.0)
        assert cavity.is_cavity is True
        assert shell.is_cavity is False
        assert cavity.centroid == pytest.approx((5.0, 5.0, 5.0))

    def test_two_disjoint_cubes(self):
        stats = analyze_triangles(make_box(size=2.0) + make_box(origin=(10.0, 0.0, 0.0), size=3.0))
        assert len(stats.components) == 2
        assert stats.solid_count == 2
        assert stats.cavity_count == 0
        assert stats.volume == pytest.approx(8.0 + 27.0)
        # Sorted by |volume| descending.
        assert stats.components[0].volume == pytest.approx(27.0)
        assert stats.components[1].volume == pytest.approx(8.0)
        assert stats.components[0].index == 0
        assert stats.components[1].index == 1
        assert all(c.is_closed for c in stats.components)

    def test_open_triangle_is_not_watertight(self):
        tris = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))]
        stats = analyze_triangles(tris)
        assert stats.triangle_count == 1
        assert stats.open_edge_count == 3
        assert stats.non_manifold_edge_count == 0
        assert stats.is_watertight is False
        assert stats.is_manifold is False
        assert stats.components[0].is_closed is False
        assert stats.surface_area == pytest.approx(0.5)

    def test_cube_with_missing_face_has_open_edges(self):
        tris = make_box()[2:]  # drop the two bottom triangles
        stats = analyze_triangles(tris)
        assert stats.triangle_count == 10
        assert stats.open_edge_count == 4
        assert stats.is_watertight is False

    def test_non_manifold_edge_detected(self):
        # Three triangles sharing one edge.
        e0 = (0.0, 0.0, 0.0)
        e1 = (1.0, 0.0, 0.0)
        tris = [
            (e0, e1, (0.0, 1.0, 0.0)),
            (e0, e1, (0.0, 0.0, 1.0)),
            (e0, e1, (0.0, -1.0, 1.0)),
        ]
        stats = analyze_triangles(tris)
        assert stats.non_manifold_edge_count == 1
        assert stats.is_manifold is False

    def test_degenerate_triangles_counted_and_excluded(self):
        tris = list(make_box())
        tris.append(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)))  # collinear
        tris.append(((5.0, 5.0, 5.0), (5.0, 5.0, 5.0), (5.0, 5.0, 5.0)))  # repeated vertex
        stats = analyze_triangles(tris)
        assert stats.triangle_count == 14
        assert stats.degenerate_triangle_count == 2
        assert stats.volume == pytest.approx(1.0)
        assert stats.surface_area == pytest.approx(6.0)
        assert stats.is_watertight is True
        assert sum(c.triangle_count for c in stats.components) == 12

    def test_welding_merges_near_duplicate_vertices(self):
        tris = []
        for a, b, c in make_box():
            jitter = 1e-9
            tris.append(
                (
                    (a[0] + jitter, a[1], a[2]),
                    (b[0], b[1] + jitter, b[2]),
                    (c[0], c[1], c[2] + jitter),
                )
            )
        stats = analyze_triangles(tris, weld_tolerance=1e-6)
        assert stats.vertex_count == 8
        assert stats.is_watertight is True

    def test_empty_mesh(self):
        stats = analyze_triangles([])
        assert stats.triangle_count == 0
        assert stats.vertex_count == 0
        assert stats.volume == 0.0
        assert stats.components == []
        assert stats.is_watertight is False

    def test_invalid_weld_tolerance(self):
        with pytest.raises(ValueError, match="weld_tolerance"):
            analyze_triangles(make_box(), weld_tolerance=0.0)

    def test_invalid_triangle_shape(self):
        with pytest.raises(ValueError, match="3 vertices"):
            analyze_triangles([((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))])
        with pytest.raises(ValueError, match="3 coordinates"):
            analyze_triangles([((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))])

    def test_flat_sheet_is_neither_solid_nor_cavity(self):
        tris = [
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
            ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
        ]
        stats = analyze_triangles(tris)
        assert stats.solid_count == 0
        assert stats.cavity_count == 0
        assert stats.volume == pytest.approx(0.0)
        # Falls back to the vertex mean when there is no volume to weight by.
        assert stats.components[0].centroid == pytest.approx((0.5, 0.5, 0.0))


@pytest.mark.unit
class TestToDict:
    """The JSON-safe serialisation used to hand numbers to an assistant."""

    def _many_bodies(self):
        tris = []
        for i in range(5):
            tris.extend(make_box(origin=(20.0 * i, 0.0, 0.0), size=float(i + 1)))
        return analyze_triangles(tris)

    def test_detailed_includes_all_components(self):
        stats = self._many_bodies()
        payload = stats.to_dict(detailed=True)
        assert len(payload["components"]) == 5
        assert payload["component_count"] == 5
        assert "components_omitted" not in payload
        assert payload["volume"] == pytest.approx(sum((i + 1) ** 3 for i in range(5)))

    def test_compact_truncates_and_rounds(self):
        stats = self._many_bodies()
        payload = stats.to_dict(detailed=False)
        assert len(payload["components"]) == 3
        assert payload["components_omitted"] == 2
        assert payload["components"][0]["volume"] == pytest.approx(125.0)
        for value in payload["dimensions"]:
            assert round(value, 4) == value

    def test_rounding_applied(self):
        stats = analyze_triangles(make_box(size=1.0 / 3.0))
        compact = stats.to_dict(detailed=False)
        assert compact["volume"] == round(stats.volume, 4)
        assert compact["dimensions"][0] == round(stats.dimensions[0], 4)
        detailed = stats.to_dict(detailed=True)
        assert detailed["volume"] == stats.volume

    def test_payload_is_json_safe(self):
        import json

        stats = self._many_bodies()
        assert json.loads(json.dumps(stats.to_dict()))["triangle_count"] == 60

    def test_component_dimensions(self):
        stats = analyze_triangles(make_box(size=(1.0, 2.0, 3.0)))
        assert stats.components[0].dimensions == pytest.approx((1.0, 2.0, 3.0))


# ---------------------------------------------------------------------------
# STL loading
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStlLoading:
    """ASCII and binary STL round trips and error handling."""

    def test_ascii_round_trip(self, tmp_path):
        path = write_ascii_stl(tmp_path / "cube.stl", make_box(size=4.0))
        tris = load_stl(path)
        assert len(tris) == 12
        stats = analyze_stl(path)
        assert stats.volume == pytest.approx(64.0)
        assert stats.is_watertight is True

    def test_binary_round_trip(self, tmp_path):
        original = make_box(origin=(-1.0, -1.0, -1.0), size=2.0)
        path = write_binary_stl(tmp_path / "cube.bin.stl", original)
        tris = load_stl(path)
        assert len(tris) == 12
        for loaded, expected in zip(tris, original, strict=False):
            for lv, ev in zip(loaded, expected, strict=False):
                assert lv == pytest.approx(ev, abs=1e-6)
        stats = analyze_stl(path)
        assert stats.volume == pytest.approx(8.0, abs=1e-4)
        assert stats.is_watertight is True

    def test_binary_header_starting_with_solid_is_detected(self, tmp_path):
        path = write_binary_stl(tmp_path / "tricky.stl", make_box(), header=b"solid tricky")
        tris = load_stl(path)
        assert len(tris) == 12
        assert analyze_stl(path).volume == pytest.approx(1.0, abs=1e-5)

    def test_ascii_scientific_notation(self, tmp_path):
        path = tmp_path / "sci.stl"
        path.write_text(
            "solid sci\n"
            "  facet normal 0 0 1\n"
            "    outer loop\n"
            "      vertex 0.0e+00 -1.5E-03 1e2\n"
            "      vertex 1.0000000e+00 0 1E2\n"
            "      vertex 0 1.0e0 100\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid sci\n"
        )
        tris = load_stl(path)
        assert len(tris) == 1
        assert tris[0][0] == pytest.approx((0.0, -0.0015, 100.0))
        assert tris[0][1] == pytest.approx((1.0, 0.0, 100.0))
        assert tris[0][2] == pytest.approx((0.0, 1.0, 100.0))

    def test_ascii_with_crlf_and_indentation(self, tmp_path):
        path = tmp_path / "crlf.stl"
        body = "\r\n".join(
            [
                "solid s",
                "\tfacet normal 0 0 0",
                "\t\touter loop",
                "\t\t\tvertex 0 0 0",
                "\t\t\tvertex 1 0 0",
                "\t\t\tvertex 0 1 0",
                "\t\tendloop",
                "\tendfacet",
                "endsolid s",
            ]
        )
        path.write_bytes(body.encode())
        assert len(load_stl(path)) == 1

    def test_empty_file_raises(self, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(b"   \n")
        with pytest.raises(ValueError, match="Empty STL"):
            load_stl(path)

    def test_truncated_ascii_raises(self, tmp_path):
        path = tmp_path / "trunc.stl"
        path.write_text(
            "solid s\n  facet normal 0 0 0\n    outer loop\n      vertex 0 0 0\n"
            "      vertex 1 0 0\n"
        )
        with pytest.raises(ValueError, match="Truncated ASCII STL"):
            load_stl(path)

    def test_malformed_vertex_raises(self, tmp_path):
        path = tmp_path / "bad.stl"
        path.write_text("solid s\n facet normal 0 0 0\n  outer loop\n   vertex a b c\n")
        with pytest.raises(ValueError, match="Malformed vertex"):
            load_stl(path)

    def test_truncated_binary_raises(self, tmp_path):
        path = tmp_path / "trunc.bin.stl"
        path.write_bytes(b"\0" * 80 + struct.pack("<I", 100) + b"\0" * 50)
        with pytest.raises(ValueError, match="Truncated binary STL"):
            load_stl(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_stl(tmp_path / "nope.stl")

    def test_binary_stl_too_short(self, tmp_path):
        path = tmp_path / "short.stl"
        path.write_bytes(b"\x01" * 20)
        with pytest.raises(ValueError, match="too short"):
            load_stl(path)


# ---------------------------------------------------------------------------
# SVG loading and 2D analysis
# ---------------------------------------------------------------------------

OPENSCAD_SVG = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.0//EN" "http://www.w3.org/TR/SVG/DTD/svg10.dtd">
<svg width="10mm" height="10mm" viewBox="0 0 10 10"
     xmlns="http://www.w3.org/2000/svg" version="1.1">
<title>OpenSCAD Model</title>
<path d="M 0,-0 L 10,-0 L 10,-10 L 0,-10 z M 2,-2 L 2,-8 L 8,-8 L 8,-2 z"/>
</svg>
"""


@pytest.mark.unit
class TestSvg:
    """Parsing the SVG that OpenSCAD writes for 2D exports."""

    def test_parses_subpaths_and_unflips_y(self, tmp_path):
        path = tmp_path / "square.svg"
        path.write_text(OPENSCAD_SVG)
        polys = load_svg_polygons(path)
        assert len(polys) == 2
        outer, hole = polys
        assert outer == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        assert hole == [(2.0, 2.0), (2.0, 8.0), (8.0, 8.0), (8.0, 2.0)]
        # Y must be positive after un-flipping, matching model coordinates.
        assert all(y >= 0 for _x, y in outer)

    def test_area_subtracts_hole(self, tmp_path):
        path = tmp_path / "square.svg"
        path.write_text(OPENSCAD_SVG)
        stats = analyze_svg(path)
        assert stats.polygon_count == 2
        assert stats.hole_count == 1
        assert stats.area == pytest.approx(100.0 - 36.0)
        assert stats.perimeter == pytest.approx(40.0 + 24.0)
        assert stats.bbox_min == pytest.approx((0.0, 0.0))
        assert stats.bbox_max == pytest.approx((10.0, 10.0))
        assert stats.dimensions == pytest.approx((10.0, 10.0))
        assert stats.centroid == pytest.approx((5.0, 5.0))

    def test_space_separated_and_uppercase_z(self, tmp_path):
        path = tmp_path / "spaces.svg"
        path.write_text('<svg><path d="M 0 0 L 4 0 L 4 -3 L 0 -3 Z"/></svg>')
        polys = load_svg_polygons(path)
        assert polys == [[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]]
        assert analyze_polygons(polys).area == pytest.approx(12.0)

    def test_implicit_lineto_after_moveto(self, tmp_path):
        path = tmp_path / "implicit.svg"
        path.write_text("<svg><path d='M0,0 2,0 2,-2 0,-2 z'/></svg>")
        polys = load_svg_polygons(path)
        assert len(polys) == 1
        assert len(polys[0]) == 4
        assert analyze_polygons(polys).area == pytest.approx(4.0)

    def test_duplicated_closing_point_dropped(self, tmp_path):
        path = tmp_path / "dup.svg"
        path.write_text('<svg><path d="M 0,0 L 2,0 L 2,-2 L 0,-2 L 0,0 z"/></svg>')
        polys = load_svg_polygons(path)
        assert polys[0] == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]

    def test_scientific_notation_and_multiple_paths(self, tmp_path):
        path = tmp_path / "multi.svg"
        path.write_text(
            '<svg><path d="M 0,0 L 1e1,0 L 1e1,-1e1 L 0,-1e1 z"/>'
            '<path d="M 20,0 L 25,0 L 25,-5 L 20,-5 z"/></svg>'
        )
        stats = analyze_svg(path)
        assert stats.polygon_count == 2
        assert stats.hole_count == 0
        assert stats.area == pytest.approx(100.0 + 25.0)

    def test_curve_command_rejected(self, tmp_path):
        path = tmp_path / "curve.svg"
        path.write_text('<svg><path d="M 0,0 C 1,1 2,2 3,3"/></svg>')
        with pytest.raises(ValueError, match="Unsupported SVG path command"):
            load_svg_polygons(path)

    def test_incomplete_coordinates_rejected(self, tmp_path):
        path = tmp_path / "bad.svg"
        path.write_text('<svg><path d="M 0,0 L 5"/></svg>')
        with pytest.raises(ValueError, match="Incomplete coordinate list"):
            load_svg_polygons(path)

    def test_svg_without_paths(self, tmp_path):
        path = tmp_path / "none.svg"
        path.write_text("<svg><title>empty</title></svg>")
        assert load_svg_polygons(path) == []
        stats = analyze_svg(path)
        assert stats.area == 0.0
        assert stats.polygon_count == 0


@pytest.mark.unit
class TestPolygonStats:
    """Shoelace area, winding and degenerate input."""

    def test_winding_independent(self):
        ccw = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        cw = list(reversed(ccw))
        assert analyze_polygons([ccw]).area == pytest.approx(4.0)
        assert analyze_polygons([cw]).area == pytest.approx(4.0)

    def test_hole_detected_regardless_of_outer_winding(self):
        outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        hole = [(2.0, 2.0), (2.0, 4.0), (4.0, 4.0), (4.0, 2.0)]
        forward = analyze_polygons([outer, hole])
        reversed_pair = analyze_polygons([list(reversed(outer)), list(reversed(hole))])
        assert forward.hole_count == 1
        assert reversed_pair.hole_count == 1
        assert forward.area == pytest.approx(96.0)
        assert reversed_pair.area == pytest.approx(96.0)

    def test_empty_input(self):
        stats = analyze_polygons([])
        assert stats.area == 0.0
        assert stats.perimeter == 0.0
        assert stats.polygon_count == 0
        assert stats.centroid == (0.0, 0.0)

    def test_degenerate_polygon_ignored_in_count(self):
        stats = analyze_polygons([[(0.0, 0.0), (1.0, 0.0)], [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]])
        assert stats.polygon_count == 1
        assert stats.area == pytest.approx(0.5)

    def test_to_dict_rounds(self):
        stats = analyze_polygons([[(0.0, 0.0), (1.0 / 3.0, 0.0), (0.0, 1.0 / 3.0)]])
        payload = stats.to_dict(ndigits=4)
        assert payload["area"] == round(stats.area, 4)
        assert payload["polygon_count"] == 1


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMass:
    """Volume to mass conversion."""

    def test_cubic_centimetre_of_pla(self):
        assert mass_from_volume(1000.0, MATERIAL_DENSITIES["PLA"]) == pytest.approx(1.24)

    def test_ten_mm_cube_of_steel(self):
        stats = analyze_triangles(make_box(size=10.0))
        assert stats.volume == pytest.approx(1000.0)
        assert stats.mass(MATERIAL_DENSITIES["steel"]) == pytest.approx(7.85)

    def test_zero_volume(self):
        assert mass_from_volume(0.0, 1.24) == 0.0

    def test_invalid_density(self):
        with pytest.raises(ValueError, match="density_g_cm3"):
            mass_from_volume(1000.0, 0.0)

    def test_density_table_is_plausible(self):
        assert set(MATERIAL_DENSITIES) >= {"PLA", "PETG", "ABS", "ASA", "TPU", "aluminum"}
        for name, density in MATERIAL_DENSITIES.items():
            assert 0.5 < density < 20.0, name


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_analyzes_20k_triangles_quickly():
    """A 20k triangle mesh must analyse in well under two seconds."""
    tris = []
    for i in range(12):
        for j in range(12):
            for k in range(12):
                tris.extend(make_box(origin=(i * 2.0, j * 2.0, k * 2.0), size=1.0))
    assert len(tris) == 12 * 12 * 12 * 12 == 20736

    start = time.perf_counter()
    stats = analyze_triangles(tris)
    elapsed = time.perf_counter() - start

    assert stats.triangle_count == 20736
    assert stats.solid_count == 1728
    assert stats.volume == pytest.approx(1728.0)
    assert stats.is_watertight is True
    assert elapsed < 2.0, f"analysis took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Real OpenSCAD export (skipped when OpenSCAD is absent)
# ---------------------------------------------------------------------------

OPENSCAD_BIN = shutil.which("openscad") or (
    "/bin/openscad" if Path("/bin/openscad").exists() else None
)


@pytest.mark.integration
@pytest.mark.skipif(OPENSCAD_BIN is None, reason="OpenSCAD is not installed")
def test_real_openscad_hollow_cube(tmp_path):
    """A real OpenSCAD export of a cube with an internal void."""
    scad = tmp_path / "hollow.scad"
    scad.write_text("difference() { cube(10, center=true); cube(6, center=true); }\n")
    stl = tmp_path / "hollow.stl"

    result = subprocess.run(
        [OPENSCAD_BIN, "-o", str(stl), str(scad)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not stl.exists():
        pytest.skip(f"OpenSCAD export failed: {result.stderr.strip()[:200]}")

    stats = analyze_stl(stl)
    assert stats.solid_count == 1
    assert stats.cavity_count == 1
    assert abs(stats.volume - (1000.0 - 216.0)) < 1e-6
    assert stats.is_watertight is True
    assert stats.dimensions == pytest.approx((10.0, 10.0, 10.0))
    assert isinstance(stats, MeshStats)
    assert not math.isnan(stats.surface_area)
