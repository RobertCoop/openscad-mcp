"""Tests for :mod:`openscad_mcp.printability`.

The fixtures are hand-built closed polyhedra whose overhang area, support
volume and unsupported span are known in closed form, so the assertions are
exact rather than golden. The one tessellated fixture reproduces OpenSCAD's own
sphere construction triangle for triangle, which is what makes the overhang
bracket test meaningful: a textbook UV sphere happens to put its ring
boundaries where the error cancels, and a real OpenSCAD sphere does not.
"""

from __future__ import annotations

import math
import time

import pytest

from openscad_mcp.mesh import analyze_triangles
from openscad_mcp.printability import PrintabilityFacts, analyze, orientation_candidates

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _quad(a, b, c, d):
    """Two triangles covering a planar quad, winding preserved."""
    return [(a, b, c), (a, c, d)]


def box(x0, y0, z0, x1, y1, z1):
    """An axis-aligned box as 12 outward-facing triangles."""
    return (
        _quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
        + _quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
        + _quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
        + _quad((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))
        + _quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))
        + _quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))
    )


def prism(poly_xz, y0, y1):
    """A prism over a convex, counter-clockwise (x, z) polygon, extruded in Y."""
    n = len(poly_xz)
    tris = []
    for i in range(1, n - 1):
        a, b, c = poly_xz[0], poly_xz[i], poly_xz[i + 1]
        tris.append(((a[0], y0, a[1]), (b[0], y0, b[1]), (c[0], y0, c[1])))
    rev = list(reversed(poly_xz))
    for i in range(1, n - 1):
        a, b, c = rev[0], rev[i], rev[i + 1]
        tris.append(((a[0], y1, a[1]), (b[0], y1, b[1]), (c[0], y1, c[1])))
    for i in range(n):
        p, q = poly_xz[i], poly_xz[(i + 1) % n]
        tris += _quad((p[0], y1, p[1]), (q[0], y1, q[1]), (q[0], y0, q[1]), (p[0], y0, p[1]))
    return tris


def prism_from_rects(rects, y0, y1):
    """A prism whose cross-section is a set of edge-matched (x0, x1, z0, z1) rects.

    Rectangles that share a full edge cancel it, so the remaining directed
    edges are the outline and the result is manifold with no T-junctions.
    """
    directed = {}
    caps = []
    for x0, x1, z0, z1 in rects:
        corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
        caps.append(corners)
        for i in range(4):
            directed[(corners[i], corners[(i + 1) % 4])] = True
    boundary = [e for e in directed if (e[1], e[0]) not in directed]

    tris = []
    for c in caps:
        tris.append(((c[0][0], y0, c[0][1]), (c[1][0], y0, c[1][1]), (c[2][0], y0, c[2][1])))
        tris.append(((c[0][0], y0, c[0][1]), (c[2][0], y0, c[2][1]), (c[3][0], y0, c[3][1])))
        r = list(reversed(c))
        tris.append(((r[0][0], y1, r[0][1]), (r[1][0], y1, r[1][1]), (r[2][0], y1, r[2][1])))
        tris.append(((r[0][0], y1, r[0][1]), (r[2][0], y1, r[2][1]), (r[3][0], y1, r[3][1])))
    for p, q in boundary:
        tris += _quad((p[0], y1, p[1]), (q[0], y1, q[1]), (q[0], y0, q[1]), (p[0], y0, p[1]))
    return tris


def openscad_sphere(radius, fn, center=(0.0, 0.0, 0.0)):
    """A sphere tessellated exactly the way OpenSCAD builds one.

    Rings sit at colatitudes ``180 * (i + 0.5) / rings`` and the poles are
    closed by flat polygons. That half-step offset is why a real OpenSCAD
    sphere under-reports overhang by 51% at ``$fn=16`` where a textbook UV
    sphere under-reports by 4%.
    """
    cx, cy, cz = center
    rings = max(2, fn // 2)

    def ring(i):
        phi = math.pi * (i + 0.5) / rings
        r = radius * math.sin(phi)
        z = radius * math.cos(phi)
        return [
            (
                cx + r * math.cos(2 * math.pi * j / fn),
                cy + r * math.sin(2 * math.pi * j / fn),
                cz + z,
            )
            for j in range(fn)
        ]

    bands = [ring(i) for i in range(rings)]
    tris = []
    top, bottom = bands[0], bands[-1]
    for j in range(1, fn - 1):
        tris.append((top[0], top[j], top[j + 1]))
        tris.append((bottom[0], bottom[j + 1], bottom[j]))
    for i in range(rings - 1):
        a, b = bands[i], bands[i + 1]
        for j in range(fn):
            k = (j + 1) % fn
            tris.append((a[j], b[j], b[k]))
            tris.append((a[j], b[k], a[k]))
    return tris


def t_shape():
    """A 44 mm plate on a 20 mm post, extruded 20 mm in Y.

    Bed contact 20 x 20 = 400 mm2. Plate underside 44 x 20 - 20 x 20 = 480 mm2,
    sitting 20 mm above the bed, so a prism support is 480 x 20 = 9600 mm3.
    """
    return prism_from_rects(
        [
            (-10.0, 10.0, 0.0, 20.0),
            (-22.0, -10.0, 20.0, 25.0),
            (-10.0, 10.0, 20.0, 25.0),
            (10.0, 22.0, 20.0, 25.0),
        ],
        0.0,
        20.0,
    )


def arch():
    """A 40 x 20 x 20 block with a 12 mm wide, 8 mm tall tunnel through it in Y.

    The tunnel ceiling is 12 x 20 = 240 mm2 of flat overhang, anchored on both
    sides, so its unsupported reach is exactly the 12 mm tunnel width.
    """
    return prism_from_rects(
        [
            (0.0, 14.0, 0.0, 8.0),
            (26.0, 40.0, 0.0, 8.0),
            (0.0, 14.0, 8.0, 20.0),
            (14.0, 26.0, 8.0, 20.0),
            (26.0, 40.0, 8.0, 20.0),
        ],
        0.0,
        20.0,
    )


def chamfer45():
    """A prism whose only downward face is a true 45 degree slope of 200*sqrt(2) mm2."""
    return prism([(0.0, 0.0), (20.0, 20.0), (0.0, 20.0)], 0.0, 10.0)


CHAMFER_AREA = 200.0 * math.sqrt(2.0)  # 282.842712... mm2


# ---------------------------------------------------------------------------
# Fixture sanity: if these are not closed solids nothing below means anything
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "tris", "volume"),
    [
        ("box", box(0.0, 0.0, 0.0, 10.0, 20.0, 30.0), 6000.0),
        ("t_shape", t_shape(), 20.0 * 20.0 * 20.0 + 44.0 * 20.0 * 5.0),
        ("arch", arch(), 40.0 * 20.0 * 20.0 - 12.0 * 8.0 * 20.0),
        ("chamfer45", chamfer45(), 200.0 * 10.0),
        ("sphere", openscad_sphere(10.0, 32), None),
    ],
)
def test_fixtures_are_closed_solids(name, tris, volume):
    stats = analyze_triangles(tris)
    assert stats.is_watertight, f"{name} is not watertight"
    assert stats.non_manifold_edge_count == 0, f"{name} has non-manifold edges"
    assert stats.volume > 0.0, f"{name} has inverted normals"
    if volume is not None:
        assert stats.volume == pytest.approx(volume, rel=1e-12)


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrientation:
    """Every measurement is relative to a stated pose, and the pose is echoed."""

    def test_as_modelled_is_the_identity(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.orientation["rotate"]["a"] == pytest.approx(0.0)
        assert facts.orientation["down"] == pytest.approx([0.0, 0.0, -1.0])
        assert facts.drop_to_bed_mm == pytest.approx(0.0)

    def test_euler_angles_are_applied_x_then_y_then_z(self):
        facts = analyze(t_shape(), orientation=[180.0, 0.0, 0.0], thickness=False)
        assert facts.orientation["rotate"]["a"] == pytest.approx(180.0)
        assert facts.orientation["form"] == "euler-xyz"
        assert facts.height_mm == pytest.approx(25.0)

    def test_axis_angle_form(self):
        facts = analyze(t_shape(), orientation={"a": 180.0, "v": [1.0, 0.0, 0.0]}, thickness=False)
        assert facts.orientation["form"] == "axis-angle"
        assert facts.bed_contact_area_mm2 == pytest.approx(880.0)

    def test_down_vector_form_names_the_face_that_meets_the_bed(self):
        facts = analyze(t_shape(), orientation={"down": [0.0, 0.0, 1.0]}, thickness=False)
        assert facts.orientation["form"] == "down"
        assert facts.bed_contact_area_mm2 == pytest.approx(880.0)

    def test_the_part_is_dropped_onto_the_bed_and_the_drop_is_reported(self):
        facts = analyze(t_shape(), orientation=[180.0, 0.0, 0.0], thickness=False)
        assert facts.drop_to_bed_mm == pytest.approx(25.0)
        assert facts.bbox_min[2] == pytest.approx(0.0)

    def test_a_part_modelled_below_the_bed_is_lifted_onto_it(self):
        sunk = [tuple((p[0], p[1], p[2] - 40.0) for p in t) for t in t_shape()]
        facts = analyze(sunk, thickness=False)
        assert facts.drop_to_bed_mm == pytest.approx(40.0)
        assert facts.bbox_min[2] == pytest.approx(0.0)

    def test_a_bad_orientation_dict_is_rejected(self):
        with pytest.raises(ValueError, match="'down' or 'a'"):
            analyze(t_shape(), orientation={"tilt": 30.0}, thickness=False)

    def test_a_wrong_length_orientation_list_is_rejected(self):
        with pytest.raises(ValueError, match="3 angles"):
            analyze(t_shape(), orientation=[10.0, 20.0], thickness=False)


# ---------------------------------------------------------------------------
# Overhang
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOverhang:
    """Facet-normal overhang is exact on flat geometry, at every threshold."""

    def test_t_shape_overhang_is_the_plate_underside_minus_the_post(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.overhang["area_mm2"] == pytest.approx(44.0 * 20.0 - 20.0 * 20.0)

    def test_t_shape_bed_contact_is_the_post_footprint(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.bed_contact_area_mm2 == pytest.approx(400.0)

    @pytest.mark.parametrize(
        ("threshold", "expected"),
        [(44.0, CHAMFER_AREA), (45.0, 0.0), (46.0, 0.0)],
    )
    def test_a_true_45_degree_face_at_thresholds_around_45(self, threshold, expected):
        facts = analyze(chamfer45(), overhang_deg=threshold, thickness=False)
        assert facts.overhang["area_mm2"] == pytest.approx(expected, abs=1e-9)

    def test_the_threshold_is_echoed(self):
        facts = analyze(chamfer45(), overhang_deg=40.0, thickness=False)
        assert facts.overhang["threshold_deg"] == 40.0

    def test_a_vertical_wall_is_never_an_overhang(self):
        facts = analyze(box(0.0, 0.0, 0.0, 10.0, 10.0, 10.0), thickness=False)
        assert facts.overhang["area_mm2"] == pytest.approx(0.0)
        assert facts.bed_contact_area_mm2 == pytest.approx(100.0)

    def test_fraction_of_surface_is_reported(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.overhang["fraction_of_surface"] == pytest.approx(
            facts.overhang["area_mm2"] / facts.total_area_mm2
        )


@pytest.mark.unit
class TestBedContactDependsOnOrientation:
    """The point of the module: flip the part and every number changes."""

    def test_flipping_moves_the_bed_contact_and_removes_the_overhang(self):
        upright = analyze(t_shape(), thickness=False)
        flipped = analyze(t_shape(), orientation=[180.0, 0.0, 0.0], thickness=False)

        assert upright.bed_contact_area_mm2 == pytest.approx(400.0)
        assert upright.overhang["area_mm2"] == pytest.approx(480.0)
        assert flipped.bed_contact_area_mm2 == pytest.approx(880.0)
        assert flipped.overhang["area_mm2"] == pytest.approx(0.0)

    def test_the_first_layer_band_decides_what_counts_as_bed_contact(self):
        thin = analyze(t_shape(), first_layer_mm=0.2, thickness=False)
        thick = analyze(t_shape(), first_layer_mm=25.0, thickness=False)
        assert thin.bed_contact_area_mm2 == pytest.approx(400.0)
        # With a 25 mm band the plate underside falls inside it and stops
        # counting as overhang, which is exactly why the band is a parameter.
        assert thick.overhang["area_mm2"] == pytest.approx(0.0)

    def test_total_surface_area_is_invariant_under_rotation(self):
        upright = analyze(t_shape(), thickness=False)
        flipped = analyze(t_shape(), orientation=[180.0, 0.0, 0.0], thickness=False)
        assert upright.total_area_mm2 == pytest.approx(flipped.total_area_mm2)


@pytest.mark.unit
class TestOverhangBracket:
    """Curved surfaces under-report; the bracket has to contain the truth."""

    def test_flat_geometry_gets_a_degenerate_bracket(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.overhang["facet_step_deg"] == pytest.approx(0.0)
        lo, hi = facts.overhang["bracket"]
        assert lo == pytest.approx(facts.overhang["area_mm2"])
        assert hi == pytest.approx(facts.overhang["area_mm2"])

    @pytest.mark.parametrize("fn", [16, 32, 64, 128])
    def test_a_sphere_under_reports_and_the_bracket_still_contains_the_truth(self, fn):
        radius = 20.0
        analytic = 2.0 * math.pi * radius**2 * (1.0 - math.cos(math.radians(45.0)))
        facts = analyze(
            openscad_sphere(radius, fn, center=(0.0, 0.0, radius + 5.0)), thickness=False
        )
        area = facts.overhang["area_mm2"]
        lo, hi = facts.overhang["bracket"]
        assert area < analytic, "the facet-normal measure should under-report a sphere"
        assert lo <= analytic <= hi, f"$fn={fn}: {analytic} outside [{lo}, {hi}]"

    def test_the_facet_step_tracks_the_tessellation(self):
        coarse = analyze(openscad_sphere(20.0, 32), thickness=False)
        fine = analyze(openscad_sphere(20.0, 128), thickness=False)
        assert coarse.overhang["facet_step_deg"] == pytest.approx(360.0 / 32.0, rel=0.02)
        assert fine.overhang["facet_step_deg"] == pytest.approx(360.0 / 128.0, rel=0.02)

    def test_the_under_report_shrinks_as_the_mesh_refines(self):
        radius = 20.0
        analytic = 2.0 * math.pi * radius**2 * (1.0 - math.cos(math.radians(45.0)))
        errors = []
        for fn in (16, 32, 64, 128):
            facts = analyze(
                openscad_sphere(radius, fn, center=(0.0, 0.0, radius + 5.0)), thickness=False
            )
            errors.append(abs(facts.overhang["area_mm2"] - analytic) / analytic)
        assert errors == sorted(errors, reverse=True)
        assert errors[0] > 0.4  # the documented -51% at $fn=16


# ---------------------------------------------------------------------------
# Patches and unsupported reach
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPatches:
    """Connected overhang facets, and how far they get from anything solid."""

    def test_the_t_plate_has_two_separate_undersides(self):
        facts = analyze(t_shape(), thickness=False)
        patches = facts.overhang["patches"]
        assert len(patches) == 2
        for patch in patches:
            assert patch["area_mm2"] == pytest.approx(240.0)
            assert patch["z_min"] == pytest.approx(20.0)
            assert patch["worst_deg"] == pytest.approx(0.0)

    def test_a_tunnel_ceiling_reach_is_the_tunnel_width(self):
        facts = analyze(arch(), thickness=False)
        patches = facts.overhang["patches"]
        assert len(patches) == 1
        assert patches[0]["area_mm2"] == pytest.approx(240.0)
        assert patches[0]["max_unsupported_reach_mm"] == pytest.approx(12.0, abs=0.6)
        assert patches[0]["max_distance_to_support_mm"] == pytest.approx(6.0, abs=0.3)

    def test_no_field_is_ever_named_bridge(self):
        # The measure is exact for a patch anchored on both sides and wrong for
        # a cantilever, so the word stays out of the schema. A note may explain
        # that; a key may not imply it.
        facts = analyze(arch(), layer_height_mm=0.2, thickness=True)

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)

        assert not [k for k in keys(facts.to_dict(detailed=True)) if "bridge" in k.lower()]

    def test_small_patches_are_filtered_out(self):
        loose = analyze(t_shape(), min_patch_area_mm2=1.0, thickness=False)
        strict = analyze(t_shape(), min_patch_area_mm2=1000.0, thickness=False)
        assert loose.overhang["patch_count"] == 2
        assert strict.overhang["patch_count"] == 0
        # Filtering the patch list does not change the measured area.
        assert loose.overhang["area_mm2"] == pytest.approx(strict.overhang["area_mm2"])

    def test_patch_centres_sit_on_the_underside(self):
        facts = analyze(arch(), thickness=False)
        centre = facts.overhang["patches"][0]["center"]
        assert centre[2] == pytest.approx(8.0)
        assert centre[0] == pytest.approx(20.0)

    def test_a_fully_floating_patch_has_no_reach_to_report(self):
        floating = box(-10.0, -10.0, 0.0, 10.0, 10.0, 2.0) + box(40.0, -5.0, 10.0, 50.0, 5.0, 14.0)
        facts = analyze(floating, thickness=False)
        detached = [p for p in facts.overhang["patches"] if p["center"][0] > 30.0]
        assert detached, "the floating box underside should be an overhang patch"
        assert detached[0]["max_unsupported_reach_mm"] is None


# ---------------------------------------------------------------------------
# Support estimate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSupportEstimate:
    """A downward prism under every overhang facet, with its method named."""

    def test_the_analytic_t_support_volume(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.support_estimate["volume_mm3"] == pytest.approx(480.0 * 20.0, rel=1e-9)
        assert facts.support_estimate["method"] == "prism"

    def test_a_tunnel_drops_only_to_its_own_floor(self):
        facts = analyze(arch(), thickness=False)
        assert facts.support_estimate["volume_mm3"] == pytest.approx(240.0 * 8.0, rel=1e-9)

    def test_the_footprint_is_the_projected_area_of_the_overhangs(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.support_footprint_area_mm2 == pytest.approx(480.0, rel=0.05)

    def test_nothing_overhanging_means_nothing_to_support(self):
        facts = analyze(box(0.0, 0.0, 0.0, 10.0, 10.0, 10.0), thickness=False)
        assert facts.support_estimate["volume_mm3"] == pytest.approx(0.0)
        assert facts.support_footprint_area_mm2 == pytest.approx(0.0)

    def test_a_facet_over_a_lower_shelf_stops_at_the_shelf(self):
        # The plate underside now lands on a 10 mm shelf instead of the bed.
        stepped = prism_from_rects(
            [
                (-10.0, 10.0, 0.0, 20.0),
                (-22.0, -10.0, 20.0, 25.0),
                (-10.0, 10.0, 20.0, 25.0),
                (10.0, 22.0, 20.0, 25.0),
                (-22.0, -10.0, 0.0, 10.0),
            ],
            0.0,
            20.0,
        )
        facts = analyze(stepped, thickness=False)
        # Left underside drops 10 mm onto the shelf, right underside drops 20.
        assert facts.support_estimate["volume_mm3"] == pytest.approx(
            240.0 * 10.0 + 240.0 * 20.0, rel=1e-6
        )


# ---------------------------------------------------------------------------
# Thickness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThickness:
    """Inward ray casting reproduces a known wall exactly."""

    @pytest.mark.parametrize("wall", [0.25, 1.0])
    def test_a_strut_measures_its_own_width(self, wall):
        facts = analyze(box(0.0, 0.0, 0.0, wall, 10.0, 10.0), thickness=True)
        assert facts.thickness["min"] == pytest.approx(wall, abs=1e-6)
        assert facts.thickness["method"] == "ray"

    def test_thin_struts_report_area_below_the_nozzle_and_thick_ones_do_not(self):
        thin = analyze(box(0.0, 0.0, 0.0, 0.25, 10.0, 10.0), nozzle_mm=0.4, thickness=True)
        thick = analyze(box(0.0, 0.0, 0.0, 1.0, 10.0, 10.0), nozzle_mm=0.4, thickness=True)
        assert thin.thickness["area_below_nozzle_mm2"] > 0.0
        assert thin.thickness["facets_below_nozzle_with_area_ge_1mm2"] > 0
        assert thick.thickness["area_below_nozzle_mm2"] == pytest.approx(0.0)
        assert thick.thickness["facets_below_nozzle_with_area_ge_1mm2"] == 0

    def test_the_distribution_and_the_location_are_reported_not_just_the_minimum(self):
        facts = analyze(box(0.0, 0.0, 0.0, 0.25, 10.0, 10.0), thickness=True)
        for key in ("min", "p01", "p05", "median", "min_location", "samples", "method"):
            assert key in facts.thickness
        assert len(facts.thickness["min_location"]) == 3

    def test_a_hollow_shell_measures_its_wall(self):
        outer = openscad_sphere(20.0, 48, center=(0.0, 0.0, 25.0))
        inner = [tuple(reversed(t)) for t in openscad_sphere(18.0, 48, center=(0.0, 0.0, 25.0))]
        facts = analyze(outer + inner, thickness=True)
        assert facts.thickness["median"] == pytest.approx(2.0, abs=0.05)

    def test_capping_the_rays_turns_it_into_a_screening_pass(self):
        facts = analyze(box(0.0, 0.0, 0.0, 0.25, 10.0, 10.0), nozzle_mm=0.4, thickness_cap_mm=1.2)
        assert facts.thickness["capped_at_mm"] == 1.2
        assert facts.thickness["min"] == pytest.approx(0.25, abs=1e-6)
        assert facts.thickness["samples_beyond_cap"] > 0

    def test_a_cap_that_finds_nothing_says_so_rather_than_failing(self):
        facts = analyze(box(0.0, 0.0, 0.0, 20.0, 20.0, 20.0), thickness_cap_mm=1.2)
        assert facts.thickness["measured"] == 0
        assert "cap" in facts.thickness["note"]

    def test_thickness_can_be_skipped_entirely(self):
        facts = analyze(t_shape(), thickness=False)
        assert facts.thickness is None

    def test_sampling_is_deterministic(self):
        tris = openscad_sphere(10.0, 48)
        first = analyze(tris, thickness=True, thickness_samples=200).thickness
        second = analyze(tris, thickness=True, thickness_samples=200).thickness
        assert first["min"] == second["min"]
        assert first["samples"] == second["samples"]


# ---------------------------------------------------------------------------
# Islands
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIslands:
    """The minimal slicer, and the canonical interpolation it depends on."""

    def test_a_floating_cube_above_a_plate_is_exactly_one_island(self):
        tris = box(-10.0, -10.0, 0.0, 10.0, 10.0, 1.0) + box(-2.0, -2.0, 5.0, 2.0, 2.0, 9.0)
        facts = analyze(tris, layer_height_mm=0.2, thickness=False)
        assert facts.islands["count"] == 1
        island = facts.islands["list"][0]
        assert island["z"] == pytest.approx(5.1, abs=1e-9)
        assert island["area_mm2"] == pytest.approx(16.0)
        assert island["center"] == pytest.approx([0.0, 0.0], abs=1e-9)

    def test_a_solid_part_has_no_islands(self):
        assert analyze(arch(), layer_height_mm=0.2, thickness=False).islands["count"] == 0
        assert analyze(t_shape(), layer_height_mm=0.2, thickness=False).islands["count"] == 0

    def test_canonical_interpolation_closes_every_loop_on_a_sphere(self):
        facts = analyze(
            openscad_sphere(10.0, 64, center=(0.0, 0.0, 10.0)),
            layer_height_mm=0.2,
            thickness=False,
        )
        assert facts.islands["unclosed_loop_fraction"] == 0.0

    def test_canonical_interpolation_survives_an_awkward_rotation(self):
        facts = analyze(
            openscad_sphere(10.0, 48, center=(0.0, 0.0, 10.0)),
            orientation=[37.0, 11.0, 63.0],
            layer_height_mm=0.2,
            thickness=False,
        )
        assert facts.islands["unclosed_loop_fraction"] == 0.0
        assert facts.islands["count"] == 0

    def test_islands_are_opt_in_behind_a_layer_height(self):
        assert analyze(t_shape(), thickness=False).islands is None

    def test_a_non_positive_layer_height_is_rejected(self):
        with pytest.raises(ValueError, match="layer_height_mm must be positive"):
            analyze(t_shape(), layer_height_mm=0.0, thickness=False)

    def test_the_island_list_is_capped_and_the_overflow_counted(self):
        stack = box(-20.0, -20.0, 0.0, 20.0, 20.0, 1.0)
        for i in range(25):
            x = -18.0 + i * 1.5
            stack += box(x, -1.0, 5.0, x + 1.0, 1.0, 6.0)
        facts = analyze(stack, layer_height_mm=0.2, thickness=False)
        assert facts.islands["count"] == 25
        assert len(facts.islands["list"]) == 20
        assert facts.islands["list_truncated"] == 5


# ---------------------------------------------------------------------------
# Orientation candidates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrientationCandidates:
    """Ranking poses without re-exporting, with a mandatory bed-contact floor."""

    def test_fourteen_candidates_by_default(self):
        rows = orientation_candidates(t_shape())
        assert len(rows) == 14

    def test_every_row_carries_the_full_fact_set(self):
        row = orientation_candidates(t_shape())[0]
        for key in (
            "down",
            "rotate",
            "overhang_area_mm2",
            "bed_contact_area_mm2",
            "height_mm",
            "drop_to_bed_mm",
            "support_volume_mm3",
        ):
            assert key in row

    def test_there_is_no_best_key(self):
        for row in orientation_candidates(t_shape()):
            assert "best" not in row
            assert "recommended" not in row

    def test_the_winner_is_never_balanced_on_a_corner(self):
        rows = orientation_candidates(t_shape())
        assert "rejected_reason" not in rows[0]
        assert rows[0]["bed_contact_area_mm2"] > 0.0

    def test_body_diagonals_of_a_boxy_part_are_rejected(self):
        rows = orientation_candidates(t_shape())
        rejected = [r for r in rows if "rejected_reason" in r]
        assert len(rejected) == 8
        for row in rejected:
            assert row["bed_contact_area_mm2"] == pytest.approx(0.0)
            assert "balance" in row["rejected_reason"]

    def test_rejected_candidates_sort_after_accepted_ones(self):
        rows = orientation_candidates(t_shape())
        first_rejected = next(i for i, r in enumerate(rows) if "rejected_reason" in r)
        assert all("rejected_reason" not in r for r in rows[:first_rejected])
        assert all("rejected_reason" in r for r in rows[first_rejected:])

    def test_accepted_candidates_sort_by_overhang_then_height(self):
        rows = [r for r in orientation_candidates(t_shape()) if "rejected_reason" not in r]
        keys = [(r["overhang_area_mm2"], r["height_mm"]) for r in rows]
        assert keys == sorted(keys)

    def test_the_flat_as_modelled_pose_is_among_the_candidates(self):
        rows = orientation_candidates(t_shape())
        flat = [r for r in rows if r["down"] == pytest.approx([0.0, 0.0, -1.0])]
        assert len(flat) == 1
        assert flat[0]["overhang_area_mm2"] == pytest.approx(480.0)
        assert flat[0]["bed_contact_area_mm2"] == pytest.approx(400.0)
        assert flat[0]["support_volume_mm3"] == pytest.approx(9600.0, rel=1e-6)

    def test_the_flipped_pose_reproduces_the_analyze_numbers(self):
        rows = orientation_candidates(t_shape())
        flipped = next(r for r in rows if r["down"] == pytest.approx([0.0, 0.0, 1.0]))
        facts = analyze(t_shape(), orientation={"down": [0.0, 0.0, 1.0]}, thickness=False)
        assert flipped["overhang_area_mm2"] == pytest.approx(facts.overhang["area_mm2"])
        assert flipped["bed_contact_area_mm2"] == pytest.approx(facts.bed_contact_area_mm2)
        assert flipped["height_mm"] == pytest.approx(facts.height_mm)

    def test_an_absolute_bed_contact_floor_rejects_more(self):
        loose = orientation_candidates(t_shape())
        strict = orientation_candidates(t_shape(), min_bed_contact_mm2=700.0)
        assert sum("rejected_reason" in r for r in strict) > sum(
            "rejected_reason" in r for r in loose
        )
        assert strict[0]["bed_contact_area_mm2"] >= 700.0

    def test_asking_for_six_candidates_gives_the_axis_directions_only(self):
        rows = orientation_candidates(t_shape(), candidates=6)
        assert len(rows) == 6
        for row in rows:
            assert sorted(abs(v) for v in row["down"]) == pytest.approx([0.0, 0.0, 1.0])

    def test_an_empty_mesh_is_rejected(self):
        with pytest.raises(ValueError, match="empty mesh"):
            orientation_candidates([])


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToDict:
    """The concise form is smaller, not different."""

    def test_detailed_output_is_json_safe_and_complete(self):
        import json

        facts = analyze(arch(), layer_height_mm=0.2, thickness=True)
        data = facts.to_dict(detailed=True)
        json.dumps(data)
        assert data["overhang"]["patch_count"] == 1
        assert data["support_estimate"]["method"] == "prism"
        assert data["islands"]["layer_height_mm"] == 0.2

    def test_concise_output_rounds_and_truncates(self):
        facts = analyze(arch(), thickness=False)
        data = facts.to_dict(detailed=False)
        assert data["overhang"]["area_mm2"] == round(facts.overhang["area_mm2"], 4)
        assert len(data["overhang"]["patches"]) <= 10

    def test_facet_index_lists_never_reach_the_caller(self):
        facts = analyze(t_shape(), thickness=False)
        for form in (True, False):
            for patch in facts.to_dict(detailed=form)["overhang"]["patches"]:
                assert "facets" not in patch

    def test_the_notes_carry_the_caveats(self):
        facts = analyze(arch(), thickness=True)
        joined = " ".join(facts.notes).lower()
        assert "under-report" in joined
        assert "cantilever" in joined
        assert "sliver" in joined

    def test_no_score_or_verdict_field_exists(self):
        data = analyze(arch(), layer_height_mm=0.2, thickness=True).to_dict(detailed=True)
        flat = repr(data).lower()
        for banned in ("score", "verdict", "pass_fail", 'printable":'):
            assert banned not in flat

    def test_the_dataclass_is_returned(self):
        assert isinstance(analyze(t_shape(), thickness=False), PrintabilityFacts)


@pytest.mark.edge
class TestEdgeCases:
    """Empty and awkward input."""

    def test_an_empty_mesh_is_rejected(self):
        with pytest.raises(ValueError, match="empty mesh"):
            analyze([])

    def test_a_non_positive_nozzle_is_rejected(self):
        with pytest.raises(ValueError, match="nozzle_mm must be positive"):
            analyze(t_shape(), nozzle_mm=0.0, thickness=False)

    def test_triangle_objects_are_accepted(self):
        from openscad_mcp.mesh import Triangle

        objects = [Triangle.from_tuple(t) for t in t_shape()]
        assert analyze(objects, thickness=False).overhang["area_mm2"] == pytest.approx(480.0)

    def test_a_single_triangle_does_not_crash(self):
        facts = analyze([((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0))], thickness=False)
        assert facts.triangle_count == 1


@pytest.mark.performance
@pytest.mark.slow
class TestPerformance:
    """A screening pass on a real-sized part has to stay interactive."""

    def test_20k_triangles_overhang_plus_capped_thickness(self):
        outer = openscad_sphere(20.0, 100, center=(0.0, 0.0, 25.0))
        inner = [tuple(reversed(t)) for t in openscad_sphere(18.0, 100, center=(0.0, 0.0, 25.0))]
        tris = outer + inner
        assert 18_000 < len(tris) < 25_000

        start = time.perf_counter()
        facts = analyze(tris, thickness=True, thickness_cap_mm=1.2)
        elapsed = time.perf_counter() - start

        assert facts.triangle_count == len(tris)
        assert elapsed < 3.0, f"analyze took {elapsed:.3f} s on {len(tris)} triangles"

    def test_overhang_alone_is_much_cheaper_than_with_thickness(self):
        tris = openscad_sphere(20.0, 100, center=(0.0, 0.0, 25.0))
        start = time.perf_counter()
        analyze(tris, thickness=False)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"overhang-only took {elapsed:.3f} s on {len(tris)} triangles"
