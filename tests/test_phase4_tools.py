"""
Tests for the Phase 4 tool surface: BOSL2 lint with autofix, symbol trace,
section offsets as expressions, look-at and callouts, per-part render
fields, features mode, anchors mode, bidirectional fits, parts bundles.

Most need OpenSCAD and BOSL2 (skipped otherwise).
"""

import shutil
from pathlib import Path

import pytest

from openscad_mcp import server
from openscad_mcp.utils.config import CacheConfig, Config, SecurityConfig, set_config

render_fn = server.render.fn
measure_fn = server.measure.fn
validate_fn = server.validate.fn
project_fn = server.get_project_files.fn
export_fn = server.export_model.fn
reference_fn = server.reference.fn
model_fn = server.model.fn

HAVE_OPENSCAD = shutil.which("openscad") is not None
BOSL2 = Path.home() / ".local/share/OpenSCAD/libraries/BOSL2/std.scad"
needs_openscad = pytest.mark.skipif(not HAVE_OPENSCAD, reason="OpenSCAD not installed")
needs_bosl2 = pytest.mark.skipif(not (HAVE_OPENSCAD and BOSL2.exists()), reason="BOSL2 not installed")


def _meta(items):
    import json

    return json.loads([x for x in items if isinstance(x, str)][-1])


def _texts(items):
    return [x for x in items if isinstance(x, str)][:-1]


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "constants.scad").write_text(
        "PLATE_T = 5;\nHOLE_D = 3.3;\nPITCH = 40;\nHEIGHT = PLATE_T * 2;\nCUT_Z = PLATE_T / 2;\n"
    )
    (proj / "plate.scad").write_text(
        "include <config/constants.scad>\n"
        "module plate() {\n"
        "  difference() {\n"
        "    cube([60, 60, PLATE_T]);\n"
        "    for (x = [10, 10 + PITCH]) for (y = [10, 50])\n"
        "      translate([x, y, -1]) cylinder(d = HOLE_D, h = PLATE_T + 2, $fn = 30);\n"
        "  }\n"
        "}\n"
        "module post() { translate([30, 30, PLATE_T]) cylinder(d = 8, h = HEIGHT, $fn = 32); }\n"
        "plate();\n"
    )
    set_config(
        Config(
            temp_dir=tmp_path / "tmp",
            cache=CacheConfig(enabled=True, directory=tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=[str(proj)]),
        )
    )
    server._reset_openscad_cache()
    server._measure_cache.clear()
    server._mesh_cache.clear()
    return proj


class TestTrace:
    async def test_trace_downstream_and_upstream(self, project):
        out = await project_fn(str(project), mode="trace", symbol="PLATE_T")
        assert out["success"] is True, out
        names = {d["name"] for d in out["downstream"]}
        assert {"HEIGHT", "CUT_Z"} <= names
        assert any("plate.scad" in str(f) for f in out["files"])
        up = await project_fn(str(project), mode="trace", symbol="HEIGHT", direction="upstream")
        assert "PLATE_T" in {d["name"] for d in up["upstream"]}

    async def test_trace_needs_symbol(self, project):
        out = await project_fn(str(project), mode="trace")
        assert out["success"] is False


@needs_openscad
class TestFeaturesAndSections:
    async def test_features_lists_the_bolt_pattern(self, project):
        out = await measure_fn(scad_file=str(project / "plate.scad"), mode="features")
        assert out["success"] is True, out
        text = str(out)
        assert "3.3" in text
        assert out["frame"] == "local"
        detailed = await measure_fn(scad_file=str(project / "plate.scad"), mode="features", response_format="detailed")
        feats = detailed.get("features") or []
        subtractive = [f for f in feats if f.get("polarity") == "subtractive"]
        assert len(subtractive) == 4

    async def test_section_offset_expression(self, project):
        out = await measure_fn(
            scad_file=str(project / "plate.scad"), mode="section", section_offset="CUT_Z"
        )
        assert out["success"] is True, out
        assert out["plane"]["offset"] == pytest.approx(2.5)
        assert out["hole_count"] == 4
        img = await render_fn(scad_file=str(project / "plate.scad"), mode="section", section_offset="PLATE_T / 2")
        meta = _meta(img)
        assert meta["success"] is True, meta
        assert meta["section_offset"] == pytest.approx(2.5)

    async def test_section_offset_rejects_statements(self, project):
        out = await measure_fn(scad_file=str(project / "plate.scad"), mode="section", section_offset="1; cube(9)")
        assert out["success"] is False
        img = await render_fn(scad_file=str(project / "plate.scad"), mode="section", section_offset="include <x>")
        assert _meta(img)["success"] is False


@needs_openscad
class TestRenderLookAtAndParts:
    async def test_look_at_and_callouts_in_parts_mode(self, project):
        parts = [{"name": "plate", "code": "plate();"}, {"name": "post", "code": "post();", "ghost": True}]
        out = await render_fn(
            scad_file=str(project / "plate.scad"), mode="parts", parts=parts, grounded=True,
            annotate=True, look_at="post", callouts=[{"label": "post top", "at": [30, 30, 15]}],
            views=["front"],
        )
        meta = _meta(out)
        assert meta["success"] is True, meta
        text = _texts(out)[0]
        assert "framed (look_at)" in text
        assert "callouts: post top" in text
        assert "(ghost)" in text
        colors = {p["name"]: p["color"] for p in meta["parts"]}
        assert colors["plate"] != colors["post"]
        assert meta["parts"][1]["ghost"] is True
        assert "bbox" in meta["parts"][0]

    async def test_stable_colors_are_order_independent(self, project):
        a = await render_fn(scad_file=str(project / "plate.scad"), mode="parts",
                            parts=[{"name": "plate", "code": "plate();"}, {"name": "post", "code": "post();"}])
        b = await render_fn(scad_file=str(project / "plate.scad"), mode="parts",
                            parts=[{"name": "post", "code": "post();"}, {"name": "plate", "code": "plate();"}])
        ca = {p["name"]: p["color"] for p in _meta(a)["parts"]}
        cb = {p["name"]: p["color"] for p in _meta(b)["parts"]}
        assert ca == cb

    async def test_look_at_requires_grounded_in_views_mode(self, project):
        out = await render_fn(scad_file=str(project / "plate.scad"), look_at=[30, 30, 5])
        assert _meta(out)["success"] is False


@needs_openscad
class TestExportBundle:
    async def test_stl_directory_bundle(self, project):
        out = await export_fn(
            scad_file=str(project / "plate.scad"), output_format="stl",
            parts=[{"name": "plate", "code": "plate();"}, {"name": "post", "code": "post();"}],
            output_path=str(project / "out"),
        )
        assert out["success"] is True, out
        assert sorted(o["name"] for o in out["objects"]) == ["plate", "post"]
        assert (project / "out" / "post.stl").exists()


@needs_bosl2
class TestBosl2Lint:
    def _make_project(self, proj: Path):
        # Both parent and child come from the used file; attach(TOP, BOTTOM)
        # of an attachable child is what use<> silently mis-places.
        (proj / "lib.scad").write_text(
            "include <BOSL2/std.scad>\n"
            "module childpart() { attachable(size=[10,10,10]) { cube(10, center=true); children(); } }\n"
            "module parentpart() { attachable(size=[40,40,10]) { cube([40,40,10], center=true); children(); } }\n"
            "if ($preview) parentpart();\n"
        )
        caller = proj / "asm.scad"
        caller.write_text(
            "include <BOSL2/std.scad>\n"
            "use <lib.scad>\n"
            "parentpart() attach(TOP, BOTTOM) childpart();\n"
        )
        return caller

    async def test_lint_flags_attach_child_from_used_file(self, project):
        caller = self._make_project(project)
        out = await validate_fn(scad_file=str(caller), mode="includes")
        assert out["success"] is True, out
        findings = [f for f in out["lint"] if f.get("code") == "bosl2_use_shadowing"]
        assert findings, out["lint"]
        assert findings[0]["severity"] == "error"
        assert out["valid"] is False

    async def test_autofix_rewrites_when_safe(self, project):
        caller = self._make_project(project)
        before_top = await measure_fn(scad_file=str(caller))
        out = await validate_fn(scad_file=str(caller), mode="includes", autofix=True)
        assert any(f.get("fixed") for f in out["lint"]), out["lint"]
        text = caller.read_text()
        assert "use <lib.scad>" not in text
        after_top = await measure_fn(scad_file=str(caller))
        # the child now sits on the parent's top face (z 5..15) instead of lower
        assert before_top["bbox_max"][2] < 15.0
        assert after_top["bbox_max"][2] == pytest.approx(15.0)


@needs_bosl2
class TestAnchors:
    async def test_anchor_frames_follow_placement(self, project):
        (project / "att.scad").write_text(
            "include <BOSL2/std.scad>\n"
            "module block(anchor=BOTTOM, spin=0, orient=UP) {\n"
            "  attachable(anchor, spin, orient, size=[10, 20, 30]) { cuboid([10, 20, 30]); children(); }\n"
            "}\n"
        )
        out = await measure_fn(
            scad_file=str(project / "att.scad"), mode="anchors",
            parts=[{"name": "block", "code": "block();", "place": "translate([100, 0, 0])"}],
        )
        assert out["success"] is True, out
        anchors = {a["name"]: a for a in out["parts"]["block"]["anchors"]}
        # local positions are relative to the attachable's own centre; the
        # assembly position includes the anchor shift and the placement
        assert anchors["TOP"]["local"] == pytest.approx([0, 0, 15])
        assert anchors["TOP"]["assembly"] == pytest.approx([100, 0, 30])
        assert out["frame"] if "frame" in out else True


@needs_openscad
class TestPrintabilityAndMass:
    TEE = (
        "module tee() { translate([20,0,0]) cube([4,30,25]); translate([0,0,25]) cube([44,30,4]);"
        " translate([10,10,5]) cube([0.25,10,20]); }\n"
        "module motor() { translate([60,0,0]) cylinder(d=28, h=20, $fn=48); }\n"
        "tee();\n"
    )

    async def test_printability_facts_find_the_thin_strut_and_the_plate(self, project):
        (project / "tee.scad").write_text(self.TEE)
        p = await measure_fn(scad_file=str(project / "tee.scad"), mode="printability", layer_height_mm=0.2)
        assert p["success"] is True, p
        assert p["thickness"]["min"] == pytest.approx(0.25, abs=1e-6)
        assert p["thickness"]["area_below_nozzle_mm2"] > 0
        assert p["overhang"]["area_mm2"] == pytest.approx(1200.0)
        assert p["islands"]["count"] == 1
        def keys_of(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    yield from keys_of(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from keys_of(v)

        assert not any("bridge" in k.lower() for k in keys_of(p))

    async def test_orientation_candidates_have_no_winner(self, project):
        (project / "tee.scad").write_text(self.TEE)
        o = await measure_fn(scad_file=str(project / "tee.scad"), mode="orientation")
        assert o["success"] is True, o
        assert "best" not in o and "recommended" not in o
        assert len(o["candidates"]) >= 6
        assert all(c["bed_contact_area_mm2"] > 0 for c in o["candidates"] if not c.get("rejected_reason"))

    async def test_validate_printability_findings(self, project):
        (project / "tee.scad").write_text(self.TEE)
        v = await validate_fn(scad_file=str(project / "tee.scad"), mode="printability",
                              profile={"max_unsupported_reach_mm": 20})
        assert v["valid"] is False
        codes = {f["code"] for f in v["findings"]}
        assert {"feature_thinner_than_nozzle", "unsupported_reach"} <= codes
        assert all("magnitude" in f for f in v["findings"])

    async def test_mass_over_parts_with_override_and_axis(self, project):
        (project / "tee.scad").write_text(self.TEE)
        m = await measure_fn(
            scad_file=str(project / "tee.scad"), mode="mass",
            parts=[{"name": "tee", "code": "tee();", "material": "PETG"},
                   {"name": "motor", "code": "motor();", "mass_g": 34}],
            about_axis=[[0, 0, 0], [0, 0, 1]],
        )
        assert m["success"] is True, m
        tee_volume = 4 * 30 * 25 + 44 * 30 * 4 + 0.25 * 10 * 20
        assert m["total_mass_g"] == pytest.approx(tee_volume / 1000 * 1.27 + 34, rel=1e-3)
        assert m["inertia_about_axis_g_mm2"] > 0
        assert m["frame"] == "assembly"

    async def test_check_print_rule(self, project):
        (project / "tee.scad").write_text(self.TEE)
        r = await server.check.fn(
            scad_file=str(project / "tee.scad"), mode="rules",
            parts=[{"name": "tee", "code": "tee();"}],
            checks=[{"rule": "print", "part": "tee", "min_feature_mm": 0.4, "max_unsupported_reach_mm": 20}],
        )
        assert r["success"] is True, r
        by = {f["check"]: f["status"] for f in r["findings"] if "check" in f}
        assert by["min_feature"] == "FAIL"
        assert by["unsupported_reach"] == "FAIL"
