"""Tests for the CSG-dump feature extractor."""

from __future__ import annotations

import math
import shutil
import textwrap

import pytest

from openscad_mcp import csgfeatures as cf

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures: hand-written CSG dumps
# ---------------------------------------------------------------------------

# A plate with, in order: a plain hole, a hole placed by translate+rotate,
# a hull of two cylinders (a rounded slot), a stub, a non-uniformly scaled
# cylinder, a cone, a rotate_extrude, a linear_extrude of a circle, and an
# additive boss outside the difference.
MIXED_CSG = """
group() {
	difference() {
		cube(size = [40, 40, 10], center = false);
		multmatrix([[1, 0, 0, 10], [0, 1, 0, 10], [0, 0, 1, -1], [0, 0, 0, 1]]) {
			cylinder($fn = 24, $fa = 12, $fs = 2, h = 12, r1 = 2.5, r2 = 2.5, center = false);
		}
		translate([30, 10, 12]) {
			rotate([180, 0, 0]) {
				cylinder(h = 6, d = 4, center = false);
			}
		}
		hull() {
			translate([5, 30, -1]) { cylinder($fn = 16, h = 12, r = 1.5, center = false); }
			translate([15, 30, -1]) { cylinder($fn = 16, h = 12, r = 1.5, center = false); }
		}
		translate([30, 30, 9.9]) { cylinder($fn = 32, h = 0.05, r = 3, center = false); }
		scale([2, 1, 1]) {
			translate([2, 20, -1]) { cylinder($fn = 24, h = 12, r = 1, center = false); }
		}
		translate([20, 20, -1]) {
			cylinder($fn = 24, h = 12, r1 = 2, r2 = 3, center = false);
		}
		rotate_extrude(angle = 360, $fn = 48) {
			translate([5, 0]) { square(size = [2, 4], center = false); }
		}
		linear_extrude(height = 12, center = false, scale = [1, 1]) {
			translate([35, 35]) { circle($fn = 20, r = 2); }
		}
	}
	translate([0, 0, 10]) {
		cylinder($fn = 36, h = 5, r = 4, center = false);
	}
}
"""

# difference inside a difference: the inner subtraction is a net addition.
NESTED_CSG = """
group() {
	difference() {
		cube(size = [20, 20, 10], center = false);
		difference() {
			translate([10, 10, -1]) { cylinder($fn = 24, h = 12, r = 4, center = false); }
			translate([10, 10, -1]) { cylinder($fn = 24, h = 12, r = 1.5, center = false); }
		}
	}
	intersection() {
		cube(size = [5, 5, 5], center = false);
		translate([2, 2, 0]) { cylinder($fn = 12, h = 5, r = 1, center = false); }
	}
}
"""

# Two plates, one 0.6 mm out of line, one hole with no partner at all.
PLATE_A_CSG = """
group() {
	difference() {
		multmatrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, -2.5], [0, 0, 0, 1]]) {
			cube(size = [40, 40, 5], center = true);
		}
		group() {
			multmatrix([[1, 0, 0, -15], [0, 1, 0, 0], [0, 0, 1, -2.5], [0, 0, 0, 1]]) {
				cylinder($fn = 48, h = 7, r1 = 1.7, r2 = 1.7, center = true);
			}
			multmatrix([[1, 0, 0, 15], [0, 1, 0, 0], [0, 0, 1, -2.5], [0, 0, 0, 1]]) {
				cylinder($fn = 48, h = 7, r1 = 1.7, r2 = 1.7, center = true);
			}
			multmatrix([[1, 0, 0, 0], [0, 1, 0, 15], [0, 0, 1, -2.5], [0, 0, 0, 1]]) {
				cylinder($fn = 48, h = 7, r1 = 1.5, r2 = 1.5, center = true);
			}
		}
	}
}
"""

PLATE_B_CSG = """
group() {
	difference() {
		multmatrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 2.5], [0, 0, 0, 1]]) {
			cube(size = [40, 40, 5], center = true);
		}
		group() {
			multmatrix([[1, 0, 0, -14.4], [0, 1, 0, 0], [0, 0, 1, 2.5], [0, 0, 0, 1]]) {
				cylinder($fn = 48, h = 7, r1 = 1.25, r2 = 1.25, center = true);
			}
			multmatrix([[1, 0, 0, 15], [0, 1, 0, 0], [0, 0, 1, 2.5], [0, 0, 0, 1]]) {
				cylinder($fn = 48, h = 7, r1 = 1.25, r2 = 1.25, center = true);
			}
		}
	}
}
"""

# Four M4 tap-drill holes on a 41 mm bolt circle, drilled down from z = +6.
BOLT_CIRCLE_CSG = """
group() {
	difference() {
		multmatrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]) {
			cube(size = [60, 60, 5], center = true);
		}
		group() {
%s
		}
	}
}
""" % "\n".join(
    "			multmatrix([[1, 0, 0, %.6f], [0, 1, 0, %.6f], [0, 0, 1, -1], [0, 0, 0, 1]]) {\n"
    "				cylinder($fn = 30, h = 7, r1 = 1.65, r2 = 1.65, center = false);\n"
    "			}"
    % (
        20.5 * math.cos(math.radians(angle)),
        20.5 * math.sin(math.radians(angle)),
    )
    for angle in (45, 135, 225, 315)
)

BOLT_CIRCLE_SCAD = textwrap.dedent(
    """
    difference() {
        cube([40, 40, 5]);
        for (a = [45:90:315]) rotate([0, 0, a]) translate([41 / 2, 0, -1])
            cylinder(d = 3.3, h = 7, $fn = 30);
        translate([20, 20, -1]) cylinder(d = 20, h = 7);
    }
    """
)


@pytest.fixture(scope="module")
def mixed() -> cf.FeatureSet:
    return cf.extract_features(MIXED_CSG)


def by_diameter(featureset: cf.FeatureSet, diameter: float, index: int = 0) -> cf.CylFeature:
    """Return the ``index``-th feature with the given diameter, in dump order."""
    matches = [f for f in featureset.features if round(f.nominal_d_mm, 3) == diameter]
    assert matches, f"no feature with d={diameter} in {featureset.counts}"
    return matches[index]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_csg_builds_the_tree() -> None:
    root = cf.parse_csg(MIXED_CSG)
    assert root.kind == "root"
    assert [c.kind for c in root.children] == ["group"]
    top = root.children[0]
    assert [c.kind for c in top.children] == ["difference", "translate"]
    difference = top.children[0]
    kinds = [c.kind for c in difference.children]
    assert kinds[0] == "cube"
    assert "hull" in kinds
    assert "rotate_extrude" in kinds
    assert "linear_extrude" in kinds


def test_parse_csg_reads_arguments_and_transforms() -> None:
    root = cf.parse_csg(MIXED_CSG)
    difference = root.children[0].children[0]
    cylinder = difference.children[1].children[0]
    assert cylinder.kind == "cylinder"
    assert cylinder.args["r1"] == 2.5
    assert cylinder.args["$fn"] == 24
    assert cylinder.args["center"] is False
    multmatrix = difference.children[1]
    assert multmatrix.transform is not None
    assert cf._apply_point(multmatrix.transform, (0.0, 0.0, 0.0)) == (10.0, 10.0, -1.0)


def test_parse_csg_handles_comments_and_modifiers() -> None:
    text = """
    group() { // a comment with a ( paren
        %color([1, 0, 0]) { cube(size = [1, 1, 1], center = false); }
        /* block ) comment */
        cylinder(h = 4, r = 1, center = false);
    }
    """
    root = cf.parse_csg(text)
    group = root.children[0]
    assert [c.kind for c in group.children] == ["color", "cylinder"]
    assert group.children[0].modifier == "%"
    features = cf.extract_features(text)
    assert features.counts["skipped_modifier"] == 1


def test_parse_csg_supports_rotate_scale_mirror() -> None:
    root = cf.parse_csg(
        "group() { rotate(a = 90, v = [0, 0, 1]) { translate([1, 0, 0]) "
        "{ scale([2, 2, 2]) { mirror([1, 0, 0]) { cube(size = [1, 1, 1]); } } } } }"
    )
    matrix = cf.IDENTITY
    node = root.children[0].children[0]
    while node.transform is not None:
        matrix = cf._compose(matrix, node.transform)
        node = node.children[0]
    # mirror -> (-1,0,0), scale -> (-2,0,0), translate -> (-1,0,0), rotate 90 Z -> (0,-1,0)
    point = cf._apply_point(matrix, (1.0, 0.0, 0.0))
    assert point == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)


# ---------------------------------------------------------------------------
# Extraction: polarity, world axes, masking
# ---------------------------------------------------------------------------


def test_extract_counts(mixed: cf.FeatureSet) -> None:
    assert mixed.counts["features"] == 4
    assert mixed.counts["subtractive"] == 3
    assert mixed.counts["additive"] == 1
    assert mixed.counts["masked_hull"] == 2
    assert mixed.counts["dropped_stub"] == 1
    assert mixed.counts["unresolved"] == 3


def test_plain_hole_world_axis_and_entry(mixed: cf.FeatureSet) -> None:
    hole = by_diameter(mixed, 5.0)
    assert hole.polarity == "subtractive"
    assert hole.entry == pytest.approx((10.0, 10.0, 11.0))
    assert hole.exit == pytest.approx((10.0, 10.0, -1.0))
    assert hole.axis_point == pytest.approx(hole.entry)
    assert hole.axis_dir == pytest.approx((0.0, 0.0, -1.0))
    assert hole.length_mm == pytest.approx(12.0)
    assert hole.segments == 24
    assert hole.effective_min_d_mm == pytest.approx(5.0 * math.cos(math.pi / 24))
    assert hole.undersize_mm == pytest.approx(5.0 - hole.effective_min_d_mm)


def test_rotated_hole_keeps_entry_at_the_top(mixed: cf.FeatureSet) -> None:
    # translate([30,10,12]) rotate([180,0,0]) cylinder(h=6, d=4) runs 12 -> 6.
    hole = by_diameter(mixed, 4.0)
    assert hole.entry == pytest.approx((30.0, 10.0, 12.0))
    assert hole.exit == pytest.approx((30.0, 10.0, 6.0))
    assert hole.axis_dir == pytest.approx((0.0, 0.0, -1.0))
    assert hole.length_mm == pytest.approx(6.0)
    # No $fn: ceil(max(min(360/12, r*2*pi/2), 5)) with r = 2 -> 7 fragments.
    assert hole.segments == 7


def test_additive_boss_outside_the_difference(mixed: cf.FeatureSet) -> None:
    boss = by_diameter(mixed, 8.0)
    assert boss.polarity == "additive"
    assert boss.entry == pytest.approx((0.0, 0.0, 15.0))
    assert boss.length_mm == pytest.approx(5.0)


def test_linear_extrude_of_a_circle_resolves_to_a_cylinder(mixed: cf.FeatureSet) -> None:
    feature = by_diameter(mixed, 4.0, index=1)
    assert feature.polarity == "subtractive"
    assert feature.nominal_d_mm == pytest.approx(4.0)
    assert feature.entry == pytest.approx((35.0, 35.0, 12.0))
    assert feature.axis_dir == pytest.approx((0.0, 0.0, -1.0))
    assert feature.segments == 20


def test_hull_children_are_masked_by_default_and_recoverable(mixed: cf.FeatureSet) -> None:
    assert all(round(f.nominal_d_mm, 3) != 3.0 for f in mixed.features)
    unmasked = cf.extract_features(MIXED_CSG, mask_hull=False)
    slot = [f for f in unmasked.features if round(f.nominal_d_mm, 3) == 3.0]
    assert len(slot) == 2
    assert all("hull" in f.path for f in slot)
    assert unmasked.counts["masked_hull"] == 0


def test_stub_dropping_is_tunable() -> None:
    kept = cf.extract_features(MIXED_CSG, stub_ratio=0.0)
    stub = [f for f in kept.features if round(f.nominal_d_mm, 3) == 6.0]
    assert len(stub) == 1
    assert stub[0].length_mm == pytest.approx(0.05)
    assert kept.counts["dropped_stub"] == 0


def test_unresolved_reports_ellipse_cone_and_revolve(mixed: cf.FeatureSet) -> None:
    kinds = {u["kind"]: u for u in mixed.unresolved}
    assert set(kinds) == {"elliptical_feature", "cone", "revolve"}
    ellipse = kinds["elliptical_feature"]
    assert ellipse["op"] == "cylinder"
    assert ellipse["count"] == 1
    assert "1.0000 x 2.0000" in ellipse["detail"]
    assert "subtractive" in ellipse["detail"]
    assert kinds["cone"]["reason"] == "d1 != d2"
    assert kinds["revolve"]["op"] == "rotate_extrude"


def test_unresolved_aggregates_repeats() -> None:
    text = "group() { difference() { cube(size = [9, 9, 9]); group() { %s } } }" % (
        "".join(
            "translate([%d, 0, -1]) { cylinder($fn = 24, h = 11, r1 = 1, r2 = 2); }" % x
            for x in (1, 3, 5)
        )
    )
    unresolved = cf.extract_features(text).unresolved
    assert len(unresolved) == 1
    assert unresolved[0]["kind"] == "cone"
    assert unresolved[0]["count"] == 3


def test_nested_difference_flips_polarity_back() -> None:
    featureset = cf.extract_features(NESTED_CSG)
    polarity = {round(f.nominal_d_mm, 3): f.polarity for f in featureset.features}
    assert polarity[8.0] == "subtractive"
    assert polarity[3.0] == "additive"
    assert polarity[2.0] == "additive"
    inner = [f for f in featureset.features if round(f.nominal_d_mm, 3) == 3.0][0]
    assert inner.path == ["difference[1]", "difference[1]"]


def test_intersection_children_stay_additive() -> None:
    featureset = cf.extract_features(NESTED_CSG)
    feature = [f for f in featureset.features if round(f.nominal_d_mm, 3) == 2.0][0]
    assert feature.polarity == "additive"
    assert feature.path == ["intersection[1]"]


# ---------------------------------------------------------------------------
# Grouping and description
# ---------------------------------------------------------------------------


def test_group_features_collapses_a_bolt_circle() -> None:
    featureset = cf.extract_features(BOLT_CIRCLE_CSG)
    groups = cf.group_features(featureset.features)
    assert len(groups) == 1
    group = groups[0]
    assert group.count == 4
    assert group.nominal_d_mm == pytest.approx(3.3)
    assert group.length_mm == pytest.approx(7.0)
    assert group.polarity == "subtractive"
    circle = group.bolt_circle
    assert circle is not None
    assert circle["radius_mm"] == pytest.approx(20.5, abs=1e-3)
    assert circle["center"] == pytest.approx((0.0, 0.0, 6.0), abs=1e-3)
    assert circle["angles_deg"] == pytest.approx([45.0, 135.0, 225.0, 315.0], abs=1e-3)
    assert circle["evenly_spaced"] is True


def test_group_features_separates_diameters_and_directions() -> None:
    featureset = cf.extract_features(MIXED_CSG)
    groups = cf.group_features(featureset.features)
    assert len(groups) == 4
    assert [g.count for g in groups] == [1, 1, 1, 1]
    assert {round(g.nominal_d_mm, 2) for g in groups} == {8.0, 5.0, 4.0}


def test_three_holes_in_a_row_are_not_a_bolt_circle() -> None:
    text = "group() { difference() { cube(size = [40, 10, 5]); group() { %s } } }" % (
        "".join(
            "translate([%d, 5, -1]) { cylinder($fn = 24, h = 7, r = 1.6); }" % x
            for x in (10, 20, 30)
        )
    )
    groups = cf.group_features(cf.extract_features(text).features)
    assert len(groups) == 1
    assert groups[0].count == 3
    assert groups[0].bolt_circle is None


def test_describe_group_format() -> None:
    groups = cf.group_features(cf.extract_features(BOLT_CIRCLE_CSG).features)
    assert (
        cf.describe_group(groups[0])
        == "4x D3.30 7.0 mm deep from z=+6.0 along -Z, bolt circle r=20.5 at 45/135/225/315 deg"
    )


def test_describe_group_uses_blind_and_through_when_known() -> None:
    groups = cf.group_features(cf.extract_features(BOLT_CIRCLE_CSG).features)
    group = groups[0]
    for feature in group.features:
        feature.through = False
    assert cf.describe_group(group).startswith("4x D3.30 blind 7.0 mm from z=+6.0 along -Z")
    for feature in group.features:
        feature.through = True
    assert cf.describe_group(group).startswith("4x D3.30 through 7.0 mm")
    for feature in group.features:
        feature.through = None


def test_describe_group_names_bosses_and_off_axis_directions() -> None:
    featureset = cf.extract_features(MIXED_CSG)
    groups = {round(g.nominal_d_mm, 2): g for g in cf.group_features(featureset.features)}
    assert cf.describe_group(groups[8.0]) == "1x D8.00 boss 5.0 mm deep from z=+15.0 along -Z"
    tilted = cf.extract_features(
        "group() { difference() { cube(size = [20, 20, 20], center = true); "
        "rotate([0, 30, 0]) { cylinder($fn = 24, h = 30, r = 3, center = true); } } }"
    )
    text = cf.describe_group(cf.group_features(tilted.features)[0])
    assert text.startswith("1x D6.00 30.0 mm deep from (")
    assert text.endswith("along (-0.500, 0.000, -0.866)")


# ---------------------------------------------------------------------------
# Fit classification
# ---------------------------------------------------------------------------


def test_fit_candidates_names_an_m4_tap_drill() -> None:
    candidates = cf.fit_candidates(3.3)
    assert candidates[0]["match"] == "M4 tap drill"
    assert candidates[0]["nominal_mm"] == pytest.approx(3.3)
    assert candidates[0]["delta_mm"] == pytest.approx(0.0)
    assert candidates[0]["role"] == "threaded / self-tapping"
    m3 = [c for c in candidates if c["match"].startswith("M3") and c["role"] == "clearance"]
    assert {c["match"] for c in m3} == {"M3 close clearance", "M3 medium clearance"}


def test_fit_candidates_orders_by_distance_and_limits() -> None:
    candidates = cf.fit_candidates(3.4)
    assert candidates[0]["match"] == "M3 medium clearance"
    assert len(candidates) <= 3
    assert [abs(c["delta_mm"]) for c in candidates] == sorted(
        abs(c["delta_mm"]) for c in candidates
    )


def test_fit_candidates_uses_the_fits_table_for_plain_bores() -> None:
    candidates = cf.fit_candidates(8.2)
    rod = [c for c in candidates if c["match"].startswith("8 mm rod")]
    assert rod, candidates
    assert "running fit" in rod[0]["role"]
    assert rod[0]["nominal_mm"] == pytest.approx(8.0)
    pocket = cf.fit_candidates(22.05)
    assert any("608 bearing pocket" in c["match"] for c in pocket)


def test_fit_candidates_can_be_empty() -> None:
    assert cf.fit_candidates(17.3) == []


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_to_dict_concise_and_detailed() -> None:
    featureset = cf.extract_features(MIXED_CSG)
    concise = cf.to_dict(featureset, detailed=False)
    assert set(concise) == {"counts", "patterns", "descriptions", "unresolved"}
    assert "features" not in concise
    assert len(concise["patterns"]) == len(concise["descriptions"]) == 4
    row = concise["patterns"][0]
    assert {"count", "d_mm", "length_mm", "axis_dir", "fit_candidates", "description"} <= set(row)
    assert concise["counts"]["masked_hull"] == 2
    detailed = cf.to_dict(featureset, detailed=True)
    assert len(detailed["features"]) == 4
    assert set(detailed["features"][0]) >= {"entry", "exit", "segments", "path", "polarity"}


# ---------------------------------------------------------------------------
# Transforms and alignment
# ---------------------------------------------------------------------------


def test_transform_features_moves_and_scales() -> None:
    featureset = cf.extract_features(BOLT_CIRCLE_CSG)
    moved = cf.transform_features(
        featureset.features, cf._compose(cf._translation((0.0, 0.0, 10.0)), cf.IDENTITY)
    )
    assert moved[0].entry == pytest.approx(
        (featureset.features[0].entry[0], featureset.features[0].entry[1], 16.0)
    )
    assert moved[0].nominal_d_mm == pytest.approx(3.3)
    scaled = cf.transform_features(featureset.features, cf._scaling((2.0, 2.0, 2.0)))
    assert scaled[0].nominal_d_mm == pytest.approx(6.6)
    assert scaled[0].length_mm == pytest.approx(14.0)
    assert scaled[0].undersize_mm == pytest.approx(2 * featureset.features[0].undersize_mm)


def test_transform_features_rejects_non_uniform_scale() -> None:
    featureset = cf.extract_features(BOLT_CIRCLE_CSG)
    with pytest.raises(ValueError, match="non-uniformly"):
        cf.transform_features(featureset.features, cf._scaling((2.0, 1.0, 1.0)))


@pytest.fixture(scope="module")
def alignment() -> dict:
    parts = {
        "plateA": cf.extract_features(PLATE_A_CSG).features,
        "plateB": cf.extract_features(PLATE_B_CSG).features,
    }
    return cf.align_features(parts)


def test_align_features_finds_the_shared_axis(alignment: dict) -> None:
    assert alignment["counts"]["axes"] == 1
    axis = alignment["axes"][0]
    assert axis["axis_dir"] == pytest.approx([0.0, 0.0, 1.0])
    assert axis["through_point"] == pytest.approx([15.0, 0.0, 0.0])
    assert axis["parts"] == ["plateA", "plateB"]
    assert axis["diameters_mm"] == pytest.approx([3.4, 2.5])
    assert axis["reading"] == "M3 clearance / M3 tap stack through plateA, plateB"
    assert [f["part"] for f in axis["features"]] == ["plateA", "plateB"]


def test_align_features_reports_the_06_mm_offset(alignment: dict) -> None:
    assert alignment["counts"]["misaligned"] == 1
    pair = alignment["misaligned"][0]
    assert pair["offset_mm"] == pytest.approx(0.6, abs=1e-6)
    assert {pair["a"]["part"], pair["b"]["part"]} == {"plateA", "plateB"}
    assert sorted([pair["a"]["d"], pair["b"]["d"]]) == pytest.approx([2.5, 3.4])


def test_align_features_reports_orphans(alignment: dict) -> None:
    assert alignment["counts"]["orphans"] == 1
    orphan = alignment["orphans"][0]
    assert orphan["part"] == "plateA"
    assert orphan["d"] == pytest.approx(3.0)
    assert orphan["entry"] == pytest.approx([0.0, 15.0, 1.0])


def test_align_features_tolerance_absorbs_the_offset() -> None:
    parts = {
        "plateA": cf.extract_features(PLATE_A_CSG).features,
        "plateB": cf.extract_features(PLATE_B_CSG).features,
    }
    loose = cf.align_features(parts, tolerance_mm=1.0)
    assert loose["counts"]["axes"] == 2
    assert loose["counts"]["misaligned"] == 0
    assert loose["counts"]["orphans"] == 1


def test_align_features_ignores_far_apart_holes() -> None:
    parts = {
        "plateA": cf.extract_features(PLATE_A_CSG).features,
        "plateB": cf.extract_features(PLATE_B_CSG).features,
    }
    strict = cf.align_features(parts, near_miss_mm=0.3)
    assert strict["counts"]["misaligned"] == 0
    # Both halves of the 0.6 mm pair now count as orphans, plus plateA's lone hole.
    assert strict["counts"]["orphans"] == 3


def test_align_features_handles_a_single_part() -> None:
    result = cf.align_features({"plateA": cf.extract_features(PLATE_A_CSG).features})
    assert result["axes"] == []
    assert result["counts"]["orphans"] == 3


# ---------------------------------------------------------------------------
# Round trip through OpenSCAD (skipped when it is not installed)
# ---------------------------------------------------------------------------

_OPENSCAD = shutil.which("openscad")
requires_openscad = pytest.mark.skipif(_OPENSCAD is None, reason="OpenSCAD is not installed")


@pytest.mark.integration
@requires_openscad
def test_extract_from_scad_round_trip(tmp_path) -> None:
    scad = tmp_path / "bolt_circle.scad"
    scad.write_text(BOLT_CIRCLE_SCAD)
    featureset = cf.extract_from_scad(scad, openscad=_OPENSCAD or "openscad")
    assert featureset.counts["subtractive"] == 5
    assert featureset.counts["additive"] == 0
    assert featureset.unresolved == []

    groups = cf.group_features(featureset.features)
    bolt = [g for g in groups if g.count == 4][0]
    assert bolt.nominal_d_mm == pytest.approx(3.3, abs=1e-4)
    assert bolt.length_mm == pytest.approx(7.0)
    assert bolt.features[0].segments == 30
    assert bolt.effective_min_d_mm == pytest.approx(3.2819, abs=1e-4)
    assert bolt.undersize_mm == pytest.approx(3.3 - 3.2819, abs=1e-4)
    assert bolt.bolt_circle is not None
    assert bolt.bolt_circle["radius_mm"] == pytest.approx(20.5, abs=1e-3)
    assert bolt.bolt_circle["angles_deg"] == pytest.approx([45.0, 135.0, 225.0, 315.0], abs=1e-2)
    assert cf.describe_group(bolt) == (
        "4x D3.30 7.0 mm deep from z=+6.0 along -Z, bolt circle r=20.5 at 45/135/225/315 deg"
    )

    big = [g for g in groups if g.count == 1][0]
    assert big.nominal_d_mm == pytest.approx(20.0, abs=1e-4)
    assert cf.fit_candidates(big.nominal_d_mm) == []


@pytest.mark.integration
@requires_openscad
def test_extract_from_scad_raises_on_bad_input(tmp_path) -> None:
    scad = tmp_path / "broken.scad"
    scad.write_text("difference() { cube([1,1,1]);\n")
    with pytest.raises(RuntimeError, match="csg export failed"):
        cf.extract_from_scad(scad, openscad=_OPENSCAD or "openscad")
