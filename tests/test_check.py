"""
Tests for the ``check`` tool and the rule engine over real geometry.

These need OpenSCAD (skipped otherwise): the per-part export path, the
mesh-first classification ladder, contact classification, sweeps, the
full-turn certificate, predicates, probes, rays, check files, and the CLI.
"""

import json
import shutil
from pathlib import Path

import pytest

from openscad_mcp import server
from openscad_mcp.checks import Quality, exit_code, summarize
from openscad_mcp.utils.config import CacheConfig, Config, SecurityConfig, set_config

check_fn = server.check.fn
measure_fn = server.measure.fn
export_fn = server.export_model.fn

HAVE_OPENSCAD = shutil.which("openscad") is not None
needs_openscad = pytest.mark.skipif(not HAVE_OPENSCAD, reason="OpenSCAD not installed")

MODEL = """
GAP = 0.5;
LIFT = 0;
module a() { cube(10); }
module b() { translate([10 + GAP, 0, 0]) cube(10); }
module c() { translate([5, 5, 9.8]) cube(4); }
module post() { translate([20, 0, 0]) cylinder(d = 4, h = 12, $fn = 32); }
module bar() { translate([-2, -2, 5]) cube([14, 4, 2]); }
"""
PARTS = [
    {"name": "a", "code": "a();"},
    {"name": "b", "code": "b();"},
    {"name": "c", "code": "c();"},
]


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "asm.scad").write_text(MODEL)
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


class TestQualityAndSummary:
    def test_quality_bound(self):
        q = Quality(fn=24, curved_radius_mm=16.0)
        d = q.to_dict()
        assert d["fn"] == 24 and d["curved_features"] is True
        assert abs(d["error_bound_mm"] - 0.137) < 0.002
        assert Quality(fn=None).to_dict()["curved_features"] is False

    def test_exit_code_and_summary(self):
        rows = [{"status": "PASS"}, {"status": "FAIL"}, {"status": "UNRESOLVED"}]
        assert exit_code(rows) == 1
        assert exit_code([{"status": "PASS"}, {"status": "UNRESOLVED"}]) == 2
        assert exit_code([{"status": "PASS"}]) == 0
        assert summarize(rows) == {"pass": 1, "fail": 1, "unresolved": 1}


@needs_openscad
class TestCheckReal:
    async def test_interference_ladder(self, project):
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=PARTS, quality=24)
        assert r["success"] is True, r
        by = {tuple(f["subject"]): f for f in r["findings"]}
        assert by[("a", "b")]["state"] == "clear"
        assert by[("a", "b")]["magnitude"]["distance_mm"] == pytest.approx(0.5, abs=1e-6)
        assert by[("a", "c")]["state"] == "interference"
        assert by[("a", "c")]["magnitude"]["penetration_mm"] == pytest.approx(0.2, abs=1e-6)
        assert by[("a", "c")]["status"] == "FAIL"
        assert r["exit_code"] == 1
        assert r["frame"] == "assembly"
        assert r["quality"]["fn"] == 24
        assert r["cache"]["misses"] == 3
        again = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=PARTS, quality=24)
        assert again["cache"]["hits"] == 3

    async def test_flush_contact_is_contact_with_area_and_plane(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0}, kind="static",
        )
        f = r["findings"][0]
        assert f["state"] == "contact"
        assert f["magnitude"]["contact_area_mm2"] == pytest.approx(100.0, rel=1e-6)
        assert f["plane"] == "x = 10.000"
        assert f["status"] == "PASS"

    async def test_sliding_contact_fails_when_touching(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0}, kind="sliding", min_mm=0.2,
        )
        assert r["findings"][0]["status"] == "FAIL"
        r2 = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0.5}, kind="sliding", min_mm=0.2,
        )
        assert r2["findings"][0]["status"] == "PASS"

    async def test_clearance_with_requirement(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="clearance", parts=PARTS[:2], min_mm=1.0
        )
        f = r["findings"][0]
        assert f["status"] == "FAIL"
        assert f["magnitude"]["required_mm"] == 1.0
        assert f["magnitude"]["distance_mm"] == pytest.approx(0.5, abs=1e-6)

    async def test_volume_cross_check(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="interference", parts=[PARTS[0], PARTS[2]],
            volume=True,
        )
        f = r["findings"][0]
        assert f["magnitude"]["intersection_volume_mm3"] == pytest.approx(4 * 4 * 0.2, rel=1e-6)

    async def test_motion_sweep_and_certificate(self, project):
        parts = [{"name": "bar", "code": "bar();"}, {"name": "post", "code": "post();"}]
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="motion", parts=parts, moving="bar",
            axis=[0, 0, 1], center=[0, 0, 0], range=[0, 360], steps=36,
        )
        assert r["success"] is True, r
        f = r["findings"][0]
        # bar spans x in [-2, 12] (radius <= 12.2): it never reaches the post at r=18..22
        assert f["all_angles"]["post"]["can_ever_touch"] is False
        assert f["all_angles"]["post"]["min_gap_mm"] > 5
        assert f["status"] == "PASS"

    async def test_rules_from_check_file_and_cli(self, project, capsys):
        check_file = project / "checks.yaml"
        check_file.write_text(
            "version: 1\n"
            "model: asm.scad\n"
            "quality: {fn: 24}\n"
            "parts:\n"
            "  a: {code: 'a();'}\n"
            "  b: {code: 'b();'}\n"
            "  c: {code: 'c();'}\n"
            "checks:\n"
            "  - {rule: no_intersect, pairs: [[a, b]]}\n"
            "  - {rule: interference, pairs: [[a, c]], why: 'c must not sink into a'}\n"
            "  - {rule: clearance, pairs: [[a, b]], min_mm: 0.4}\n"
            "  - {rule: predicate, expr: 'GAP >= 0.4', why: 'gap floor'}\n"
            "  - {rule: probe, point: [5, 5, 5], expect: SOLID}\n"
            "  - {rule: probe, point: [10.25, 5, 5], expect: AIR}\n"
            "  - {rule: ray, origin: [5, 5, 50], direction: [0, 0, -1], first_hit: c}\n"
        )
        r = await check_fn(check_file=str(check_file), mode="rules")
        assert r["success"] is True, r
        statuses = {(f["rule"], tuple(f["subject"])): f["status"] for f in r["findings"]}
        assert statuses[("interference", ("a", "b"))] == "PASS"
        assert statuses[("interference", ("a", "c"))] == "FAIL"
        assert statuses[("clearance", ("a", "b"))] == "PASS"
        assert statuses[("predicate", ("GAP >= 0.4",))] == "PASS"
        assert statuses[("ray", ("c",))] == "PASS"
        probes = [f for f in r["findings"] if f["rule"] == "probe"]
        assert [p["status"] for p in probes] == ["PASS", "PASS"]
        assert r["exit_code"] == 1

        code = server._cli_check([str(check_file), "--allow", str(project)])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL" in out and "interference" in out

    async def test_probe_mode_and_polyline(self, project):
        r = await measure_fn(
            scad_file=str(project / "asm.scad"), mode="probe", parts=PARTS[:2],
            points=[[5, 5, 5], [10.25, 5, 5]], rays=[[5, 5, 50, 0, 0, -1]],
            polyline=[[-5, 5, 5], [30, 5, 5]],
        )
        assert r["success"] is True, r
        assert r["points"][0]["state"] == "solid" and r["points"][0]["parts"] == ["a"]
        assert r["points"][1]["state"] == "air"
        assert r["rays"][0]["first_hit"]["part"] == "a"
        assert r["rays"][0]["first_hit"]["distance_mm"] == pytest.approx(40.0, abs=1e-6)
        assert r["polyline"]["clear"] is False and r["polyline"]["blocked_by"] == "a"

    async def test_export_parts_bundle_3mf(self, project):
        r = await export_fn(scad_file=str(project / "asm.scad"), parts=PARTS, output_format="3mf",
                            output_path=str(project / "asm.3mf"))
        assert r["success"] is True, r
        assert r["object_count"] == 3
        from openscad_mcp.threemf import read_3mf_summary

        summary = read_3mf_summary(project / "asm.3mf")
        assert sorted(o["name"] for o in summary["objects"]) == ["a", "b", "c"]

    async def test_ghost_parts_excluded_from_probes_but_not_pairs(self, project):
        parts = [PARTS[0], {"name": "c", "code": "c();", "ghost": True}]
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=parts)
        assert r["findings"][0]["state"] == "interference"
        p = await measure_fn(scad_file=str(project / "asm.scad"), mode="probe", parts=parts, points=[[7, 7, 12]])
        assert p["points"][0]["state"] == "air"  # inside the ghost only

    async def test_bad_inputs(self, project):
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="wat", parts=PARTS))["success"] is False
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="motion", parts=PARTS))["success"] is False
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="rules", parts=PARTS))["success"] is False
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=[{"name": "x", "code": "nope();"}, PARTS[0]])
        assert r["success"] is True  # unknown module warns and yields empty geometry
        assert "x" in r.get("empty_parts", [])
