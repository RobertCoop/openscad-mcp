"""Tests for the computational-geometry kernel in ``openscad_mcp.geom``.

Every mesh here is built in code, so the suite runs without OpenSCAD. The one
test that shells out to OpenSCAD is skipped when the binary is absent; it
checks the kernel's penetration estimate against the real CSG intersection.

The interesting cases are the ones that separate a correct kernel from a
plausible one: two parts sharing a flush face (contact, never interference),
two bars crossing with no vertex of either inside the other (interference,
which no vertex test can see), a point on a symmetry plane (where ray parity
fails), and a ray whose target sits behind its origin.
"""

import math
import shutil
import struct
import subprocess
import sys
import time

# Timing guards catch quadratic regressions; under coverage tracing pure-Python
# loops run several times slower, so the bounds are relaxed when a tracer is on.
from tests.conftest import PERF_SLACK as _PERF_SLACK
from pathlib import Path

import pytest

from openscad_mcp.geom import (
    Mesh,
    aabb_gap,
    apply,
    can_ever_touch,
    classify_pair,
    classify_point,
    compose,
    contact_area,
    contains,
    identity,
    inscribed_polygon_error,
    min_distance,
    penetration_depth,
    polyline_clear,
    ray_cast,
    ray_cast_parts,
    rotation,
    rz_footprint,
    segments_for,
    sweep_rotation,
    sweep_translation,
    translation,
    winding_number,
)

# ---------------------------------------------------------------------------
# Mesh builders
# ---------------------------------------------------------------------------

_BOX_FACES = (
    (0, 3, 2),
    (0, 2, 1),
    (4, 5, 6),
    (4, 6, 7),
    (0, 1, 5),
    (0, 5, 4),
    (1, 2, 6),
    (1, 6, 5),
    (2, 3, 7),
    (2, 7, 6),
    (3, 0, 4),
    (3, 4, 7),
)


def box_triangles(lo, hi):
    """The 12 outward-facing triangles of the axis-aligned box lo..hi."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    return [(v[a], v[b], v[c]) for a, b, c in _BOX_FACES]


def box(lo, hi, name=""):
    """An axis-aligned box as a :class:`Mesh`."""
    return Mesh(box_triangles(lo, hi), name)


def cube10(origin=(0.0, 0.0, 0.0), name=""):
    """A 10 mm cube with its minimum corner at ``origin``."""
    return box(origin, tuple(origin[k] + 10.0 for k in range(3)), name)


def uv_sphere(radius=10.0, center=(0.0, 0.0, 0.0), nu=40, nv=40, name=""):
    """A latitude/longitude sphere, for tests that need a few thousand facets."""
    cx, cy, cz = center

    def point(i, j):
        theta = math.pi * i / nv
        phi = 2.0 * math.pi * j / nu
        st = math.sin(theta)
        return (
            cx + radius * st * math.cos(phi),
            cy + radius * st * math.sin(phi),
            cz + radius * math.cos(theta),
        )

    tris = []
    for i in range(nv):
        for j in range(nu):
            a, b, c, d = point(i, j), point(i, j + 1), point(i + 1, j + 1), point(i + 1, j)
            if i != 0:
                tris.append((a, b, c))
            if i != nv - 1:
                tris.append((a, c, d))
    return Mesh(tris, name)


def faceted_cylinder(radius, height, center=(0.0, 0.0, 0.0), fn=180, rings=18, name=""):
    """A closed cylinder about the z axis with ``fn`` facets and ``rings`` bands.

    Stands in for a real printed part: OpenSCAD's facet counts come from
    curved features like this one, while its flat faces stay two triangles
    wide. That balance is what a hierarchy prunes well, and it is why the
    timings here track the ones measured on real exported parts.
    """
    cx, cy, cz = center
    z0, z1 = cz - height / 2.0, cz + height / 2.0
    tris = []

    def rim(i, z):
        angle = 2.0 * math.pi * i / fn
        return (cx + radius * math.cos(angle), cy + radius * math.sin(angle), z)

    for band in range(rings):
        za = z0 + (z1 - z0) * band / rings
        zb = z0 + (z1 - z0) * (band + 1) / rings
        for i in range(fn):
            a, b, c, d = rim(i, za), rim(i + 1, za), rim(i + 1, zb), rim(i, zb)
            tris.extend([(a, b, c), (a, c, d)])
    for i in range(fn):
        tris.append(((cx, cy, z1), rim(i, z1), rim(i + 1, z1)))
        tris.append(((cx, cy, z0), rim(i + 1, z0), rim(i, z0)))
    return Mesh(tris, name)


def bracket_part(origin, posts, fn=60, rings=8, name=""):
    """A 40x40x4 plate carrying cylindrical posts: a stand-in for a real part.

    Real exported parts are heterogeneous. Flat faces stay two triangles wide
    however big they are, and the facet count comes from curved features, so
    the feature nearest another part is usually small and unique. Uniform test
    solids do not behave like that: two facing spheres or two parallel
    cylinders put thousands of triangle pairs within a hair of the minimum
    distance, none of which a bounding hierarchy can prune, and the same query
    that takes 13 ms between two real 6k-facet parts takes seconds between
    them.
    """
    tris = list(box_triangles(origin, (origin[0] + 40.0, origin[1] + 40.0, origin[2] + 4.0)))
    for px, py, radius in posts:
        post = faceted_cylinder(
            radius, 12.0, (origin[0] + px, origin[1] + py, origin[2] + 10.0), fn=fn, rings=rings
        )
        tris.extend(post.triangles)
    return Mesh(tris, name)


def write_binary_stl(path, tris):
    """Write a binary STL, for the loader round trip."""
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(tris)))
        for tri in tris:
            fh.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for p in tri:
                fh.write(struct.pack("<3f", *p))
            fh.write(struct.pack("<H", 0))


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransforms:
    """Matrix helpers, and the composition order OpenSCAD uses."""

    def test_identity_leaves_points_alone(self):
        assert apply(identity(), (1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)

    def test_translation(self):
        assert apply(translation((1.0, 2.0, 3.0)), (10.0, 0.0, 0.0)) == (11.0, 2.0, 3.0)

    def test_rotation_about_z(self):
        got = apply(rotation((0.0, 0.0, 1.0), 90.0), (1.0, 0.0, 0.0))
        assert got == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)

    def test_rotation_about_a_centre(self):
        # Turning half a circle about (5, 0, 0) sends the origin to (10, 0, 0).
        got = apply(rotation((0.0, 0.0, 1.0), 180.0, (5.0, 0.0, 0.0)), (0.0, 0.0, 0.0))
        assert got == pytest.approx((10.0, 0.0, 0.0), abs=1e-12)

    def test_compose_applies_rightmost_first(self):
        """``translate(t) rotate(a) p`` rotates first, exactly as OpenSCAD nests."""
        m = compose(translation((10.0, 0.0, 0.0)), rotation((0.0, 0.0, 1.0), 90.0))
        assert apply(m, (1.0, 0.0, 0.0)) == pytest.approx((10.0, 1.0, 0.0), abs=1e-12)

        swapped = compose(rotation((0.0, 0.0, 1.0), 90.0), translation((10.0, 0.0, 0.0)))
        assert apply(swapped, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 11.0, 0.0), abs=1e-12)

    def test_compose_of_nothing_is_identity(self):
        assert compose() == identity()

    def test_zero_axis_rejected(self):
        with pytest.raises(ValueError, match="non-zero"):
            rotation((0.0, 0.0, 0.0), 45.0)


# ---------------------------------------------------------------------------
# Mesh basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMesh:
    """Construction, bounds, caching and transformation."""

    def test_bbox_and_count(self):
        m = cube10((1.0, 2.0, 3.0))
        assert m.triangle_count == 12
        assert m.bbox_min == (1.0, 2.0, 3.0)
        assert m.bbox_max == (11.0, 12.0, 13.0)

    def test_empty_mesh(self):
        m = Mesh([], "nothing")
        assert m.triangle_count == 0
        assert m.bbox_min == (0.0, 0.0, 0.0)
        assert m.bvh().root == -1
        assert m.is_watertight() is False

    def test_watertight_census(self):
        closed = cube10()
        assert closed.is_watertight() is True
        open_box = Mesh(closed.triangles[:-2], "open")
        assert open_box.is_watertight() is False

    def test_bvh_is_cached_and_leaf_size_one(self):
        m = cube10()
        first = m.bvh()
        assert first is m.bvh()
        assert first.leaf_size == 1
        assert max(first.count[i] for i in range(len(first.count)) if first.left[i] < 0) == 1

    def test_transformed_matches_a_freshly_built_mesh(self):
        """A refit BVH must answer exactly like one built from scratch."""
        moving = cube10()
        moving.bvh()  # force the refit path
        target = box((30.0, 0.0, 0.0), (40.0, 10.0, 10.0))
        shifted = moving.transformed(translation((15.0, 0.0, 0.0)))
        rebuilt = Mesh(list(shifted.triangles))
        assert min_distance(shifted, target).distance == pytest.approx(
            min_distance(rebuilt, target).distance
        )
        assert shifted.bbox_min == pytest.approx((15.0, 0.0, 0.0))

    def test_refit_rejects_a_different_mesh(self):
        with pytest.raises(ValueError, match="same length"):
            cube10().bvh().refit(box_triangles((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))[:6])

    def test_from_stl_round_trip(self, tmp_path):
        path = tmp_path / "cube.stl"
        write_binary_stl(path, box_triangles((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))
        m = Mesh.from_stl(path)
        assert m.name == "cube"
        assert m.triangle_count == 12
        assert m.bbox_max == pytest.approx((10.0, 10.0, 10.0))

    def test_degenerate_triangles_are_tolerated(self):
        """A zero-area facet must not poison distance, winding or contact."""
        p = (5.0, 5.0, 5.0)
        tris = [*cube10().triangles, (p, p, p), ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))]
        m = Mesh(tris, "with_junk")
        assert winding_number(m, (5.0, 5.0, 5.0)) == pytest.approx(1.0, abs=1e-9)
        other = box((20.0, 0.0, 0.0), (30.0, 10.0, 10.0))
        assert min_distance(m, other).distance == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDistance:
    """Exact surface-to-surface distance."""

    def test_separated_cubes_exact(self):
        a = cube10()
        b = box((15.0, 0.0, 0.0), (25.0, 10.0, 10.0))
        result = min_distance(a, b)
        assert result.distance == pytest.approx(5.0, abs=1e-12)
        assert result.exact is True
        assert result.point_a[0] == pytest.approx(10.0)
        assert result.point_b[0] == pytest.approx(15.0)
        assert 0 <= result.tri_a < a.triangle_count
        assert 0 <= result.tri_b < b.triangle_count

    def test_diagonal_separation(self):
        a = cube10()
        b = box((13.0, 14.0, 0.0), (23.0, 24.0, 10.0))
        assert min_distance(a, b).distance == pytest.approx(5.0, abs=1e-12)

    def test_touching_cubes_are_zero(self):
        assert min_distance(cube10(), cube10((0.0, 0.0, 10.0))).distance == 0.0

    def test_piercing_triangles_are_zero(self):
        """An edge through the middle of a face touches none of its edges.

        This is the case a pure edge-edge/vertex-face distance gets confidently
        wrong: two bars crossing at right angles come back millimetres apart.
        """
        bar_x = box((-50.0, -2.0, -2.0), (50.0, 2.0, 2.0))
        bar_y = box((-1.0, -50.0, -3.0), (1.0, 50.0, 3.0))
        assert min_distance(bar_x, bar_y).distance == 0.0

    def test_upper_bound_short_circuits(self):
        a = cube10()
        b = box((100.0, 0.0, 0.0), (110.0, 10.0, 10.0))
        result = min_distance(a, b, upper_bound=5.0)
        assert result.exact is False
        assert result.distance == 5.0
        assert result.tri_a == -1

    def test_upper_bound_above_the_answer_still_exact(self):
        a = cube10()
        b = box((15.0, 0.0, 0.0), (25.0, 10.0, 10.0))
        result = min_distance(a, b, upper_bound=50.0)
        assert result.exact is True
        assert result.distance == pytest.approx(5.0)

    def test_empty_mesh_is_infinitely_far(self):
        assert min_distance(Mesh([]), cube10()).distance == math.inf

    def test_aabb_gap_is_a_lower_bound(self):
        a = cube10()
        b = box((15.0, 15.0, 0.0), (25.0, 25.0, 10.0))
        gap = aabb_gap(a, b)
        assert gap == pytest.approx(math.hypot(5.0, 5.0))
        assert gap <= min_distance(a, b).distance + 1e-12
        assert aabb_gap(a, cube10((5.0, 5.0, 5.0))) == 0.0


# ---------------------------------------------------------------------------
# Point in solid
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWindingNumber:
    """The point-in-solid primitive."""

    def test_inside_outside_and_on_face(self):
        m = cube10()
        assert winding_number(m, (5.0, 5.0, 5.0)) == pytest.approx(1.0, abs=1e-9)
        assert winding_number(m, (50.0, 5.0, 5.0)) == pytest.approx(0.0, abs=1e-9)
        assert winding_number(m, (5.0, 5.0, 10.0)) == pytest.approx(0.5, abs=1e-9)

    def test_contains_excludes_the_surface(self):
        m = cube10()
        assert contains(m, (5.0, 5.0, 5.0)) is True
        assert contains(m, (5.0, 5.0, 10.001)) is False
        # A point on the face sits exactly at the default threshold, so it only
        # counts as inside once the threshold is relaxed below 0.5.
        assert contains(m, (5.0, 5.0, 10.0), threshold=0.4) is True

    def test_a_vertex_reads_as_on_surface(self):
        assert winding_number(cube10(), (0.0, 0.0, 0.0)) == pytest.approx(0.5)

    def test_open_mesh_gives_a_fractional_value(self):
        """A hole makes the winding number fractional rather than wrong."""
        open_box = Mesh(cube10().triangles[:-2], "open")
        w = winding_number(open_box, (5.0, 5.0, 5.0))
        assert 0.0 < w < 1.0
        assert abs(w - 1.0) > 1e-6

    def test_symmetry_plane_is_not_degenerate(self):
        """Points on a centred cube's y=0 plane, where ray parity breaks down.

        A ray fired along a symmetry plane runs through the shared edges of
        coincident facets and double counts or misses them; the winding number
        never fires a ray, so these points are ordinary interior points.
        """
        cube = box((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0), "cube")
        parts = {"cube": cube}
        for point in [(0.0, 0.0, 0.0), (0.0, 0.0, 2.0), (2.0, 0.0, 0.0), (0.0, 0.0, -4.9)]:
            result = classify_point(parts, point)
            assert result["state"] == "solid", point
            assert result["parts"] == ["cube"]
            assert result["winding"]["cube"] == pytest.approx(1.0, abs=1e-9)

    def test_classify_point_states(self):
        parts = {"a": cube10(), "b": cube10((20.0, 0.0, 0.0))}
        assert classify_point(parts, (5.0, 5.0, 5.0))["state"] == "solid"
        assert classify_point(parts, (15.0, 5.0, 5.0))["state"] == "air"
        assert classify_point(parts, (15.0, 5.0, 5.0))["parts"] == []
        on_face = classify_point(parts, (10.0, 5.0, 5.0))
        assert on_face["state"] == "on_surface"
        assert on_face["parts"] == ["a"]

    def test_classify_point_reports_every_part(self):
        parts = {"a": cube10(), "b": cube10((20.0, 0.0, 0.0))}
        result = classify_point(parts, (5.0, 5.0, 5.0))
        assert set(result["winding"]) == {"a", "b"}
        assert result["winding"]["b"] == 0.0


# ---------------------------------------------------------------------------
# Pair classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyPair:
    """The clear / contact / interference ladder."""

    def test_separated_is_clear(self):
        rel = classify_pair(cube10(), box((15.0, 0.0, 0.0), (25.0, 10.0, 10.0)))
        assert rel.state == "clear"
        assert rel.distance_mm == pytest.approx(5.0)
        assert rel.penetration_mm is None
        assert rel.contact_area_mm2 is None
        assert rel.closest is not None

    def test_face_contact_area_and_plane(self):
        lower = cube10(name="lower")
        upper = cube10((0.0, 0.0, 10.0), name="upper")
        rel = classify_pair(lower, upper)
        assert rel.state == "contact"
        assert rel.distance_mm == pytest.approx(0.0, abs=1e-12)
        assert rel.contact_area_mm2 == pytest.approx(100.0, abs=1e-9)
        assert rel.normal == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)
        assert rel.plane_offset == pytest.approx(10.0)
        assert rel.at == pytest.approx((5.0, 5.0, 10.0), abs=1e-9)

    def test_contact_normal_points_from_a_to_b(self):
        lower = cube10()
        upper = cube10((0.0, 0.0, 10.0))
        assert classify_pair(upper, lower).normal == pytest.approx((0.0, 0.0, -1.0), abs=1e-12)

    def test_flush_contact_is_never_interference(self):
        """The negative control this kernel exists to get right.

        Stack two 10 mm cubes. The upper cube's four vertical walls all reach
        down to z=10 and meet the lower cube's top face there, so a naive
        triangle crossing test sees wall-versus-face crossings everywhere and
        declares interference. Nothing is actually overlapping.
        """
        lower = cube10()
        upper = cube10((0.0, 0.0, 10.0))
        rel = classify_pair(lower, upper)
        assert rel.state == "contact"
        assert rel.penetration_mm is None
        assert penetration_depth(lower, upper)[0] == pytest.approx(0.0, abs=1e-9)

    def test_overlap_is_interference_with_a_witness_inside_both(self):
        a = cube10()
        b = cube10((8.0, 8.0, 8.0))  # 2 mm of overlap on every axis
        rel = classify_pair(a, b)
        assert rel.state == "interference"
        assert rel.distance_mm == 0.0
        assert rel.penetration_mm >= 2.0 - 1e-6
        assert rel.at is not None
        assert contains(a, rel.at)
        assert contains(b, rel.at)

    def test_penetration_depth_direct(self):
        depth, witness = penetration_depth(cube10(), cube10((8.0, 8.0, 8.0)))
        assert depth == pytest.approx(2.0, abs=1e-9)
        assert witness is not None

    def test_edge_touch_is_contact_with_no_area(self):
        a = cube10()
        b = box((10.0, 10.0, 0.0), (20.0, 20.0, 10.0))  # shares only the vertical edge
        rel = classify_pair(a, b)
        assert rel.state == "contact"
        assert rel.contact_area_mm2 == pytest.approx(0.0, abs=1e-9)

    def test_corner_touch_is_contact(self):
        rel = classify_pair(cube10(), cube10((10.0, 10.0, 10.0)))
        assert rel.state == "contact"

    def test_crossing_bars_with_no_vertex_inside_either(self):
        """Interference that no vertex containment test can find.

        Neither bar has a vertex inside the other -- every vertex of each is
        outside the other's cross-section -- yet they occupy the same 2x4x4 mm
        of space. Only the surface crossing sees it.
        """
        bar_x = box((-50.0, -2.0, -2.0), (50.0, 2.0, 2.0), "bar_x")
        bar_y = box((-1.0, -50.0, -3.0), (1.0, 50.0, 3.0), "bar_y")
        for m, other in ((bar_x, bar_y), (bar_y, bar_x)):
            for v in m.vertices():
                assert not contains(other, v), f"{v} should be outside {other.name}"
        assert penetration_depth(bar_x, bar_y)[0] == pytest.approx(0.0, abs=1e-9)

        rel = classify_pair(bar_x, bar_y)
        assert rel.state == "interference"
        assert rel.penetration_mm > 0.0
        assert contains(bar_x, rel.at)
        assert contains(bar_y, rel.at)

    def test_tolerance_turns_a_small_gap_into_contact(self):
        a = cube10()
        b = box((10.5, 0.0, 0.0), (20.5, 10.0, 10.0))
        assert classify_pair(a, b).state == "clear"
        near = classify_pair(a, b, tolerance=1.0)
        assert near.state == "contact"
        assert near.distance_mm == pytest.approx(0.5)

    def test_to_dict_omits_what_does_not_apply(self):
        rel = classify_pair(cube10(), box((15.0, 0.0, 0.0), (25.0, 10.0, 10.0)))
        data = rel.to_dict()
        assert data["state"] == "clear"
        assert "penetration_mm" not in data
        assert "contact_area_mm2" not in data

    def test_contact_area_of_a_partial_overlap(self):
        """Two faces meeting over part of their area report just that part."""
        lower = cube10()
        upper = box((5.0, 5.0, 10.0), (25.0, 25.0, 20.0))
        area, normal, offset, centroid = contact_area(lower, upper)
        assert area == pytest.approx(25.0, abs=1e-9)
        assert normal == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)
        assert offset == pytest.approx(10.0)
        assert centroid == pytest.approx((7.5, 7.5, 10.0), abs=1e-9)

    def test_contact_area_ignores_parallel_faces_that_do_not_meet(self):
        area, normal, _offset, _centroid = contact_area(cube10(), cube10((0.0, 0.0, 10.5)))
        assert area == 0.0
        assert normal is None


# ---------------------------------------------------------------------------
# Rays and corridors
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRays:
    """Ray casting, and the guards that keep it honest."""

    def test_entering_then_exiting(self):
        hits = ray_cast(cube10(), (-5.0, 5.0, 5.0), (1.0, 0.0, 0.0))
        assert [h.t for h in hits] == pytest.approx([5.0, 15.0])
        assert [h.entering for h in hits] == [True, False]
        assert hits[0].normal == pytest.approx((-1.0, 0.0, 0.0), abs=1e-12)
        assert hits[0].point == pytest.approx((0.0, 5.0, 5.0), abs=1e-12)

    def test_a_ray_from_inside_only_exits(self):
        hits = ray_cast(cube10(), (5.0, 5.0, 5.0), (1.0, 0.0, 0.0))
        assert len(hits) == 1
        assert hits[0].entering is False
        assert hits[0].t == pytest.approx(5.0)

    def test_geometry_behind_the_origin_is_not_a_hit(self):
        """The guard that a prototype got backwards, inverting ~10% of answers.

        Moller-Trumbore happily returns a negative parameter for a triangle
        behind the ray. Reading |t| <= eps as "starts on the surface" before
        discarding t < -eps turns every one of those into a phantom hit at the
        origin.
        """
        cube = cube10()
        assert ray_cast(cube, (15.0, 5.0, 5.0), (1.0, 0.0, 0.0)) == []
        backwards = ray_cast(cube, (15.0, 5.0, 5.0), (-1.0, 0.0, 0.0))
        assert [h.t for h in backwards] == pytest.approx([5.0, 15.0])
        assert [h.entering for h in backwards] == [True, False]

    def test_a_ray_starting_on_a_face_reports_only_what_is_ahead(self):
        hits = ray_cast(cube10(), (0.0, 5.0, 5.0), (1.0, 0.0, 0.0))
        assert [h.t for h in hits] == pytest.approx([10.0])

    def test_max_distance_truncates(self):
        hits = ray_cast(cube10(), (-5.0, 5.0, 5.0), (1.0, 0.0, 0.0), max_distance=7.0)
        assert [h.t for h in hits] == pytest.approx([5.0])

    def test_direction_need_not_be_normalised(self):
        hits = ray_cast(cube10(), (-5.0, 5.0, 5.0), (7.0, 0.0, 0.0))
        assert hits[0].t == pytest.approx(5.0)

    def test_zero_direction_rejected(self):
        with pytest.raises(ValueError, match="non-zero"):
            ray_cast(cube10(), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    def test_parts_are_merged_in_order(self):
        parts = {"near": cube10(), "far": cube10((20.0, 0.0, 0.0))}
        hits = ray_cast_parts(parts, (-5.0, 5.0, 5.0), (1.0, 0.0, 0.0))
        assert [h.part for h in hits] == ["near", "near", "far", "far"]
        assert [h.t for h in hits] == pytest.approx([5.0, 15.0, 25.0, 35.0])

    def test_polyline_blocked_reports_the_segment(self):
        wall = box((4.0, -5.0, -5.0), (6.0, 5.0, 5.0), "wall")
        result = polyline_clear({"wall": wall}, [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)])
        assert result["clear"] is False
        assert result["blocked_by"] == "wall"
        assert result["blocked_at_mm"] == pytest.approx(4.0)
        assert result["point"] == pytest.approx((4.0, 0.0, 0.0), abs=1e-12)
        assert result["segments"][0]["clear"] is False

    def test_polyline_blocked_on_a_later_segment(self):
        wall = box((8.0, 8.0, -5.0), (12.0, 12.0, 5.0), "wall")
        points = [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (20.0, 10.0, 0.0)]
        result = polyline_clear({"wall": wall}, points)
        assert result["clear"] is False
        assert result["segments"][0]["clear"] is True
        assert result["segments"][1]["clear"] is False
        assert result["segments"][1]["blocked_at_mm"] == pytest.approx(8.0)
        assert result["blocked_at_mm"] == pytest.approx(18.0)

    def test_polyline_can_route_around_an_obstacle(self):
        wall = box((4.0, -5.0, -5.0), (6.0, 5.0, 5.0), "wall")
        detour = [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (10.0, 10.0, 0.0), (10.0, 0.0, 0.0)]
        result = polyline_clear({"wall": wall}, detour)
        assert result["clear"] is True
        assert result["blocked_by"] is None
        assert result["length_mm"] == pytest.approx(30.0)

    def test_polyline_needs_two_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            polyline_clear({}, [(0.0, 0.0, 0.0)])


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------


def bar_and_post():
    """A radial bar that swings into a post at roughly 33 degrees."""
    bar = box((0.0, -2.0, 0.0), (30.0, 2.0, 10.0), "bar")
    post = box((18.0, 18.0, 0.0), (24.0, 24.0, 10.0), "post")
    return bar, post


@pytest.mark.unit
class TestSweeps:
    """Rigid motion against static parts."""

    def test_rotation_sweep_finds_the_first_contact_angle(self):
        bar, post = bar_and_post()
        steps = sweep_rotation(
            bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 90.0), 19, {"post": post}
        )
        assert len(steps) == 19
        assert steps[0]["angle_deg"] == 0.0
        assert steps[-1]["angle_deg"] == pytest.approx(90.0)
        assert steps[0]["contacts"] == []
        assert steps[0]["min_gap_mm"] == pytest.approx(16.0)
        assert steps[0]["worst_part"] == "post"

        blocked = [s for s in steps if s["contacts"]]
        assert blocked, "the bar must reach the post somewhere in the first quadrant"
        first = blocked[0]["angle_deg"]
        assert 30.0 <= first <= 45.0
        assert blocked[0]["contacts"][0]["state"] == "interference"
        assert blocked[0]["max_penetration_mm"] > 0.0
        assert blocked[0]["worst_part"] == "post"
        # Symmetric about the post's diagonal, and clear again beyond it.
        assert steps[-1]["contacts"] == []

    def test_rotation_sweep_gap_shrinks_toward_contact(self):
        bar, post = bar_and_post()
        steps = sweep_rotation(
            bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 30.0), 7, {"post": post}
        )
        gaps = [s["min_gap_mm"] for s in steps]
        assert gaps == sorted(gaps, reverse=True)
        assert all(s["contacts"] == [] for s in steps)

    def test_rotation_sweep_reports_elapsed_time(self):
        bar, post = bar_and_post()
        steps = sweep_rotation(
            bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 90.0), 4, {"post": post}, rate=45.0
        )
        assert [s["t_s"] for s in steps] == pytest.approx([0.0, 2.0 / 3, 4.0 / 3, 2.0])

    def test_translation_sweep(self):
        moving = cube10(name="slider")
        wall = box((20.0, 0.0, 0.0), (30.0, 10.0, 10.0), "wall")
        steps = sweep_translation(moving, (1.0, 0.0, 0.0), (0.0, 15.0), 4, {"wall": wall})
        assert [s["offset_mm"] for s in steps] == pytest.approx([0.0, 5.0, 10.0, 15.0])
        assert steps[0]["min_gap_mm"] == pytest.approx(10.0)
        assert steps[0]["contacts"] == []
        assert steps[1]["min_gap_mm"] == pytest.approx(5.0)
        assert steps[2]["contacts"][0]["state"] == "contact"
        assert steps[3]["contacts"][0]["state"] == "interference"
        # The overlap is a 5x10x10 slab whose four side walls are coincident
        # with the wall's own. Penetration is measured as the distance from a
        # contained point to the nearer surface, so the deepest such point is
        # 2.5 mm in: a lower bound on the 5 mm of travel, as documented.
        assert steps[3]["max_penetration_mm"] == pytest.approx(2.5, abs=1e-6)

    def test_single_step_sweep_samples_the_start(self):
        bar, post = bar_and_post()
        steps = sweep_rotation(
            bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (10.0, 90.0), 1, {"post": post}
        )
        assert [s["angle_deg"] for s in steps] == [10.0]

    def test_bad_sweep_arguments(self):
        bar, post = bar_and_post()
        with pytest.raises(ValueError, match="steps"):
            sweep_rotation(bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 90.0), 0, {"post": post})
        with pytest.raises(ValueError, match="rate"):
            sweep_rotation(
                bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 90.0), 2, {"post": post}, rate=0.0
            )
        with pytest.raises(ValueError, match="non-zero"):
            sweep_translation(bar, (0.0, 0.0, 0.0), (0.0, 1.0), 2, {"post": post})


@pytest.mark.unit
class TestFootprint:
    """The full-turn clearance certificate."""

    def brute_force_min_gap(self, moving, static, axis, centre, steps=72):
        """Rotate through a whole turn and keep the smallest distance seen."""
        worst = math.inf
        for i in range(steps):
            posed = moving.transformed(rotation(axis, 360.0 * i / steps, centre))
            worst = min(worst, min_distance(posed, static).distance)
            if worst <= 0.0:
                break
        return worst

    def test_certificate_agrees_with_a_brute_force_sweep(self):
        axis, centre = (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)
        bar, near = bar_and_post()
        far = box((30.0, 30.0, 0.0), (36.0, 36.0, 10.0), "far")
        above = box((18.0, 18.0, 20.0), (24.0, 24.0, 30.0), "above")

        prints = {
            name: rz_footprint(m, axis, centre)
            for name, m in (("bar", bar), ("near", near), ("far", far), ("above", above))
        }

        touch, gap = can_ever_touch(prints["bar"], prints["near"])
        assert touch is True
        assert gap == 0.0
        assert self.brute_force_min_gap(bar, near, axis, centre) == 0.0

        for name in ("far", "above"):
            touch, gap = can_ever_touch(prints["bar"], prints[name])
            brute = self.brute_force_min_gap(bar, {"far": far, "above": above}[name], axis, centre)
            assert touch is False, name
            assert gap > 0.0, name
            # The raster over-reports occupancy, so its gap is a lower bound on
            # the real one: conservative, never optimistic.
            assert gap <= brute + 1e-9, name
            assert gap > brute - 1.0, name

    def test_footprint_bounds_cover_the_swept_radius(self):
        bar, _post = bar_and_post()
        fp = rz_footprint(bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        r_min, r_max, z_min, z_max = fp.bounds()
        assert r_min == pytest.approx(0.0)
        assert r_max >= math.hypot(30.0, 2.0)
        assert r_max < math.hypot(30.0, 2.0) + 1.0
        assert z_min == pytest.approx(0.0)
        assert z_max >= 10.0
        assert fp.cell_count > 0

    def test_footprints_must_be_comparable(self):
        axis, centre = (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)
        bar, post = bar_and_post()
        coarse = rz_footprint(bar, axis, centre, r_pitch=1.0, z_pitch=1.0)
        fine = rz_footprint(post, axis, centre, r_pitch=0.5, z_pitch=0.5)
        with pytest.raises(ValueError, match="pitches"):
            can_ever_touch(coarse, fine)
        other_axis = rz_footprint(post, (0.0, 1.0, 0.0), centre, r_pitch=1.0, z_pitch=1.0)
        with pytest.raises(ValueError, match="axis"):
            can_ever_touch(coarse, other_axis)

    def test_every_vertex_lands_in_an_occupied_cell(self):
        """The raster must cover the part, whatever else it also covers.

        A footprint that misses even one cell of real material would turn the
        certificate from conservative into wrong, so this checks the direction
        that matters: everything the mesh occupies is marked.
        """
        axis, centre = (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)
        part = faceted_cylinder(12.0, 20.0, (18.0, 3.0, 5.0), fn=60, rings=6, name="part")
        fp = rz_footprint(part, axis, centre, r_pitch=0.25, z_pitch=0.25)
        for vertex in part.vertices():
            radius = math.hypot(vertex[0], vertex[1])
            cell = (math.floor(radius / 0.25), math.floor(vertex[2] / 0.25))
            assert cell in fp.cells, f"{vertex} -> {cell} missing from the raster"

    def test_a_triangle_straddling_the_axis_reaches_radius_zero(self):
        """The smallest radius on a triangle is not always at a vertex.

        Here the axis passes through the middle of a single large triangle:
        every vertex is 20 mm out and every edge stays 10 mm out, but the
        swept region runs all the way in to the axis.
        """
        tri = ((-20.0, -10.0, 0.0), (20.0, -10.0, 0.0), (0.0, 20.0, 0.0))
        fp = rz_footprint(Mesh([tri], "fan"), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 0.25, 0.25)
        assert (0, 0) in fp.cells
        assert fp.bounds()[0] == pytest.approx(0.0)

    def test_an_edge_passing_the_axis_dips_below_its_endpoints(self):
        """A chord's closest approach is nearer than either of its ends."""
        tri = ((-20.0, 5.0, 0.0), (20.0, 5.0, 0.0), (0.0, 30.0, 0.0))
        fp = rz_footprint(Mesh([tri], "chord"), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 0.25, 0.25)
        r_min = fp.bounds()[0]
        assert r_min == pytest.approx(5.0, abs=0.25)
        assert r_min < math.hypot(20.0, 5.0)

    def test_a_part_reaching_the_axis_is_filled_solid(self):
        """A rod through the axis must come back solid, not hollow.

        The flood fill that marks the interior has to know that there is no
        outside at negative radius. Give it a margin there and it walks past
        the axis into the middle of the part, and the certificate stops seeing
        the part's own bulk.
        """
        rod = faceted_cylinder(20.0, 10.0, (0.0, 0.0, 5.0), fn=60, rings=6, name="rod")
        fp = rz_footprint(rod, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), r_pitch=0.5, z_pitch=0.5)
        # 40 radial cells by 20 axial ones, give or take the boundary row.
        assert fp.cell_count > 40 * 20 * 0.9
        assert (0, 10) in fp.cells, "the middle of the rod is missing"
        assert (20, 10) in fp.cells

    def test_the_space_between_the_axis_and_an_offset_part_stays_empty(self):
        """The fill must not sweep in everything between a part and the axis."""
        part = faceted_cylinder(6.0, 10.0, (20.0, 0.0, 5.0), fn=60, rings=6, name="offset")
        fp = rz_footprint(part, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), r_pitch=0.5, z_pitch=0.5)
        r_min = fp.bounds()[0]
        assert r_min == pytest.approx(14.0, abs=0.5)
        assert not any(i * 0.5 < 13.0 for i, _ in fp.cells)

    def test_bad_footprint_arguments(self):
        bar, _post = bar_and_post()
        with pytest.raises(ValueError, match="non-zero"):
            rz_footprint(bar, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        with pytest.raises(ValueError, match="positive"):
            rz_footprint(bar, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), r_pitch=0.0)


# ---------------------------------------------------------------------------
# Render quality
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuality:
    """Facet count and the error it leaves behind."""

    def test_inscribed_polygon_error(self):
        assert inscribed_polygon_error(16.0, 24) == pytest.approx(0.137, abs=5e-4)
        assert inscribed_polygon_error(16.0, 24) == pytest.approx(
            16.0 * (1.0 - math.cos(math.radians(180.0 / 24)))
        )

    def test_error_falls_with_more_facets(self):
        errors = [inscribed_polygon_error(10.0, fn) for fn in (8, 16, 32, 64)]
        assert errors == sorted(errors, reverse=True)
        assert inscribed_polygon_error(10.0, 2) == 0.0
        assert inscribed_polygon_error(0.0, 24) == 0.0

    def test_segments_for_honours_fn(self):
        assert segments_for(10.0, fn=24) == 24
        assert segments_for(1000.0, fn=6) == 6
        assert segments_for(10.0, fn=1) == 3

    def test_segments_for_uses_the_smaller_of_the_two_limits(self):
        # Small radius: the fragment-size limit bites, with a floor of 5.
        assert segments_for(1.0, fa=12.0, fs=2.0) == 5
        # Large radius: the angle limit bites at 360/fa.
        assert segments_for(100.0, fa=12.0, fs=2.0) == 30
        # Between them the size limit governs: ceil(2*pi*r/fs) = 19 facets.
        assert segments_for(3.0, fa=12.0, fs=1.0) == 19
        assert segments_for(3.0, fa=12.0, fs=1.0) == math.ceil(3.0 * 2 * math.pi / 1.0)

    def test_segments_for_a_vanishing_radius(self):
        assert segments_for(0.0) == 3


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
class TestPerformance:
    """Loose upper bounds, five times the times measured while developing.

    The bounds are deliberately slack so an ordinary slow machine does not
    fail the suite; they exist to catch an algorithmic regression, such as a
    BVH that stops pruning or a query that goes quadratic.
    """

    def test_min_distance_on_two_six_thousand_triangle_meshes(self):
        # Spheres on purpose: a smooth surface is the hard case for the
        # hierarchy, because a whole cap of triangles sits within a hair of
        # the minimum and none of it can be pruned. Real faceted parts of this
        # size answer an order of magnitude faster.
        a = uv_sphere(10.0, (0.0, 0.0, 0.0), nu=56, nv=56, name="a")
        b = uv_sphere(10.0, (40.0, 0.0, 0.0), nu=56, nv=56, name="b")
        assert 5000 < a.triangle_count < 7000
        a.bvh()
        b.bvh()  # the target is the query, not the one-off build

        start = time.perf_counter()
        result = min_distance(a, b)
        elapsed = time.perf_counter() - start

        assert result.distance == pytest.approx(20.0, abs=0.1)
        assert elapsed < (0.25) * _PERF_SLACK, f"min_distance took {elapsed:.3f}s"

    def test_twelve_step_rotation_sweep(self):
        # A realistic sweep: clear for most of the arc, colliding at the end,
        # which is the shape of a "how far can this swing" question.
        moving = bracket_part(
            (10.0, -20.0, 0.0),
            [
                (6.0, 6.0, 3.0),
                (6.0, 20.0, 3.0),
                (6.0, 34.0, 3.0),
                (20.0, 6.0, 2.5),
                (20.0, 20.0, 2.5),
                (20.0, 34.0, 2.5),
            ],
            name="moving",
        )
        static = bracket_part(
            (-20.0, 30.0, 0.0),
            [(10.0, 10.0, 3.0), (20.0, 20.0, 3.0), (30.0, 30.0, 2.0)],
            name="static",
        )
        assert 6000 < moving.triangle_count < 7500
        assert 2500 < static.triangle_count < 3500
        moving.bvh()
        static.bvh()

        start = time.perf_counter()
        steps = sweep_rotation(
            moving, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 36.0), 12, {"static": static}
        )
        elapsed = time.perf_counter() - start

        assert len(steps) == 12
        assert steps[0]["contacts"] == []
        assert any(s["contacts"] for s in steps)
        assert elapsed < (2.5) * _PERF_SLACK, f"12-step sweep took {elapsed:.3f}s"

    def test_rotational_footprint_of_a_large_part(self):
        """A wide part at the default pitch, the case a motion check repeats."""
        part = faceted_cylinder(80.0, 16.0, (0.0, 0.0, 37.0), fn=180, rings=18, name="platform")
        assert 6000 < part.triangle_count < 7500

        start = time.perf_counter()
        fp = rz_footprint(part, (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        elapsed = time.perf_counter() - start

        assert fp.cell_count > 1000
        assert elapsed < (1.5) * _PERF_SLACK, f"rz_footprint took {elapsed:.3f}s"

    def test_winding_number_on_a_multi_part_set(self):
        parts = {
            f"p{i}": uv_sphere(5.0, (12.0 * i, 0.0, 0.0), nu=40, nv=40, name=f"p{i}")
            for i in range(6)
        }
        point = (0.0, 0.0, 0.0)
        start = time.perf_counter()
        for _ in range(20):
            result = classify_point(parts, point)
        elapsed = (time.perf_counter() - start) / 20

        assert result["state"] == "solid"
        assert elapsed < (0.0075) * _PERF_SLACK, f"classify_point took {elapsed * 1000:.2f}ms"


# ---------------------------------------------------------------------------
# Against the real thing
# ---------------------------------------------------------------------------

OPENSCAD_BIN = shutil.which("openscad") or (
    "/bin/openscad" if Path("/bin/openscad").exists() else None
)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(OPENSCAD_BIN is None, reason="OpenSCAD is not installed")
def test_penetration_matches_a_real_csg_intersection(tmp_path):
    """Check the kernel's penetration against OpenSCAD's own intersection.

    Two 10 mm cubes offset by 8 mm overlap in a 2 mm cube. OpenSCAD computes
    that intersection with CGAL; the kernel never sees it, and has to agree.
    """
    from openscad_mcp.mesh import analyze_stl

    scad = tmp_path / "pair.scad"
    scad.write_text("intersection() {\n" "  cube(10);\n" "  translate([8, 8, 8]) cube(10);\n" "}\n")
    stl = tmp_path / "pair.stl"
    result = subprocess.run(
        [OPENSCAD_BIN, "-o", str(stl), str(scad)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not stl.exists():
        pytest.skip(f"OpenSCAD export failed: {result.stderr.strip()[:200]}")

    overlap = analyze_stl(stl)
    assert overlap.volume == pytest.approx(8.0, abs=1e-6)
    thinnest = min(overlap.dimensions)

    relation = classify_pair(cube10(), cube10((8.0, 8.0, 8.0)))
    assert relation.state == "interference"
    assert relation.penetration_mm == pytest.approx(thinnest, abs=1e-6)
    assert contains(cube10(), relation.at)
