"""
Tests for the assembly model (check-file grammar, frames, wrapper bodies),
the consolidated ``model`` tool, and the per-part export service.

OpenSCAD is mocked here; geometric rules are covered in test_check.py.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openscad_mcp import server
from openscad_mcp.assembly import (
    Assembly,
    AssemblyError,
    Part,
    assembly_digest,
    load_check_file,
    pairs_for,
    parse_assembly,
    parse_parts,
)
from openscad_mcp.utils.config import CacheConfig, Config, SecurityConfig, set_config

model_fn = server.model.fn

CHECK_YAML = """
version: 1
frames:
  head: {parent: world, lift: "translate([0, 0, HEAD_LIFT_Z])"}
  sub:  {parent: head, lift: "rotate([0, 0, 90])"}
quality: {fn: 48}
parts:
  - {name: base, code: "turntable_base();", place: "translate(BASE_POS)"}
  - {name: platform, code: "turntable_platform();", place: "translate(P)", frame: sub, material: PETG}
  - {name: motor, code: "StepMotor28BYJ();", mass_g: 34, ghost: true, printed: false,
     motion: {type: rotate, axis: [0, 0, 1], center: [64, 0, 0], range: [0, 360]}}
checks:
  - {rule: no_intersect, pairs: all}
  - {rule: contact, pairs: [[base, platform]], kind: sliding, min_gap_mm: 0.2}
  - {rule: predicate, expr: "A < B", why: "a must stay below b"}
  - {rule: probe, point: [0, -40, 0.55], expect: SOLID}
"""


class TestAssemblyModel:
    def test_check_file_parses_and_composes_frames(self):
        asm = load_check_file(CHECK_YAML)
        assert [p.name for p in asm.parts] == ["base", "platform", "motor"]
        assert asm.fn == 48
        assert asm.quality_variables() == {"$fn": 48}
        plat = asm.part("platform")
        assert asm.placement_expr(plat) == "translate([0, 0, HEAD_LIFT_Z]) rotate([0, 0, 90]) translate(P)"
        assert asm.part_body(plat).startswith("!union() {")
        assert "turntable_platform();" in asm.part_statement(plat)
        assert asm.part_statement(asm.part("base")) == "translate(BASE_POS) { turntable_base(); }"

    def test_no_intersect_is_interference(self):
        asm = load_check_file(CHECK_YAML)
        assert asm.checks[0]["rule"] == "interference"

    def test_pairs_and_ghosts(self):
        asm = load_check_file(CHECK_YAML)
        assert pairs_for(asm, "all") == [("base", "platform"), ("base", "motor"), ("platform", "motor")]
        assert pairs_for(asm, "all", include_ghosts=False) == [("base", "platform")]
        assert pairs_for(asm, [["base", "motor"]]) == [("base", "motor")]
        with pytest.raises(AssemblyError):
            pairs_for(asm, [["base", "nope"]])

    def test_json_check_file(self):
        data = {"parts": [{"name": "a", "code": "a();"}], "checks": [{"rule": "clearance", "min_mm": 1}]}
        asm = load_check_file(json.dumps(data))
        assert asm.parts[0].name == "a"
        assert asm.checks[0]["min_mm"] == 1

    def test_parse_parts_forms(self):
        assert [p.name for p in parse_parts(["lid()", {"name": "body", "code": "body();"}])] == ["lid", "body"]
        assert parse_parts({"lid": "lid();", "b": {"code": "b();", "place": "up(3)"}})[1].place == "up(3)"
        assert parse_parts('[{"name":"x","code":"x();"}]')[0].code == "x();"

    @pytest.mark.parametrize(
        "bad",
        [
            [{"name": "x", "code": "include <a>"}],
            [{"name": "1x", "code": "x();"}],
            [{"name": "x", "code": "x(); y();"}],
            [{"name": "x", "code": "x();", "place": "translate(P); cube(1)"}],
            [{"name": "x", "code": "x();"}, {"name": "x", "code": "y();"}],
            [],
        ],
    )
    def test_invalid_parts_rejected(self, bad):
        with pytest.raises(AssemblyError):
            parse_parts(bad)

    def test_unknown_frame_and_cycle(self):
        with pytest.raises(AssemblyError):
            parse_assembly({"parts": [{"name": "a", "code": "a();", "frame": "nope"}]})
        with pytest.raises(AssemblyError):
            parse_assembly({
                "frames": {"f": {"parent": "g"}, "g": {"parent": "f"}},
                "parts": [{"name": "a", "code": "a();", "frame": "f"}],
            })

    def test_unknown_rule_and_bad_pair(self):
        with pytest.raises(AssemblyError):
            parse_assembly({"parts": [{"name": "a", "code": "a();"}], "checks": [{"rule": "bogus"}]})
        with pytest.raises(AssemblyError):
            parse_assembly({"parts": [{"name": "a", "code": "a();"}], "checks": [{"rule": "clearance", "pairs": [["a", "zz"]]}]})

    def test_digest_is_stable(self):
        a = load_check_file(CHECK_YAML)
        b = load_check_file(CHECK_YAML)
        assert assembly_digest(a) == assembly_digest(b)
        assert a.to_dict()["parts"][2]["ghost"] is True

    def test_explode_in_statement(self):
        asm = Assembly(parts=[Part(name="lid", code="lid();", explode=[0, 0, 35])])
        stmt = asm.part_statement(asm.part("lid"), explode=True)
        assert stmt.startswith("translate([0.0, 0.0, 35.0])")


@pytest.fixture
def env(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    set_config(
        Config(
            temp_dir=tmp_path / "tmp",
            cache=CacheConfig(enabled=True, directory=tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=[str(proj)]),
        )
    )
    server._reset_openscad_cache()
    return proj


class TestModelTool:
    async def test_crud_cycle_with_etags(self, env):
        created = await model_fn(action="create", name="box", content="cube(1);", workspace=str(env))
        assert created["success"] is True and created["name"] == "box.scad"
        got = await model_fn(action="get", name="box", workspace=str(env))
        assert got["content"] == "cube(1);" and got["etag"] == created["etag"]
        listed = await model_fn(action="list", workspace=str(env))
        assert listed["count"] == 1
        updated = await model_fn(action="update", name="box", content="cube(2);", workspace=str(env))
        assert updated["etag"] != created["etag"]
        dup = await model_fn(action="create", name="box", content="x", workspace=str(env))
        assert dup["success"] is False
        deleted = await model_fn(action="delete", name="box", workspace=str(env))
        assert deleted["success"] is True
        assert (await model_fn(action="get", name="box", workspace=str(env)))["success"] is False

    async def test_bad_action_and_missing_args(self, env):
        assert (await model_fn(action="explode", name="x"))["success"] is False
        assert (await model_fn(action="create", name="x", workspace=str(env)))["success"] is False

    async def test_workspace_outside_allowed_paths(self, env, tmp_path):
        out = await model_fn(action="list", workspace=str(tmp_path / "elsewhere"))
        assert out["success"] is False
        assert "allowed" in out["error"]

    async def test_template_requires_catalog_entry(self, env):
        pytest.importorskip("openscad_mcp.parts_catalog")
        out = await model_fn(action="create", name="m", template="part:no-such-part", workspace=str(env))
        assert out["success"] is False


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CUBE_STL = "\n".join(
    ["solid c"]
    + [
        f" facet normal 0 0 0\n  outer loop\n   vertex {a}\n   vertex {b}\n   vertex {c}\n  endloop\n endfacet"
        for a, b, c in [
            ("0 0 0", "1 1 0", "1 0 0"), ("0 0 0", "0 1 0", "1 1 0"),
            ("0 0 1", "1 0 1", "1 1 1"), ("0 0 1", "1 1 1", "0 1 1"),
            ("0 0 0", "1 0 0", "1 0 1"), ("0 0 0", "1 0 1", "0 0 1"),
            ("0 1 0", "1 1 1", "1 1 0"), ("0 1 0", "0 1 1", "1 1 1"),
            ("0 0 0", "0 0 1", "0 1 1"), ("0 0 0", "0 1 1", "0 1 0"),
            ("1 0 0", "1 1 0", "1 1 1"), ("1 0 0", "1 1 1", "1 0 1"),
        ]
    ]
    + ["endsolid c", ""]
)


def _mock_openscad(calls):
    """A subprocess.run stand-in that writes a unit cube STL for every -o target."""

    def run(cmd, **kw):
        calls.append(cmd)
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            if str(out) not in ("/dev/null", "NUL"):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(PNG) if out.suffix == ".png" else out.write_text(CUBE_STL)
        if "-d" in cmd:
            Path(cmd[cmd.index("-d") + 1]).write_text(f"{cmd[cmd.index('-o') + 1]}: \\\n\t{cmd[-1]}\n")
        r = Mock()
        r.returncode, r.stderr, r.stdout = 0, "   Simple: yes\n", ""
        return r

    return run


class TestExportService:
    async def test_parts_export_parallel_and_cached(self, env, monkeypatch):
        monkeypatch.setattr("openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(
            "openscad_mcp.server.get_openscad_capabilities",
            lambda path=None: {"installed": True, "version": "2021.01", "probed": True},
        )
        model = env / "asm.scad"
        model.write_text("module a(){cube(1);} module b(){cube(1);}\n")
        asm = parse_assembly({"parts": [{"name": "a", "code": "a();"}, {"name": "b", "code": "b();", "place": "translate([5,0,0])"}]})
        calls = []
        with patch("subprocess.run", side_effect=_mock_openscad(calls)):
            with server._ModelSource(None, str(model), "t") as src:
                first = await server._export_parts(src, asm, {}, None)
            with server._ModelSource(None, str(model), "t") as src:
                second = await server._export_parts(src, asm, {}, None)
        assert set(first) == {"a", "b"}
        assert all(not e.cached for e in first.values())
        assert all(e.cached for e in second.values())
        assert first["a"].key != first["b"].key
        assert first["b"].stl_path.exists()
        # Two exports on the first pass, none on the second
        exports = [c for c in calls if "-o" in c and str(c[c.index("-o") + 1]).endswith(".stl")]
        assert len(exports) == 2
        # Wrapper programs were written to the temp dir, not the project
        assert not list(env.glob("wrapper-*"))
        assert first["a"].diagnostics.mesh_health()["manifold"] is True

    async def test_cache_key_changes_with_place_and_quality(self, env, monkeypatch):
        monkeypatch.setattr("openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad")
        monkeypatch.setattr(
            "openscad_mcp.server.get_openscad_capabilities",
            lambda path=None: {"installed": True, "version": "2021.01", "probed": True},
        )
        model = env / "asm.scad"
        model.write_text("module a(){cube(1);}\n")
        with server._ModelSource(None, str(model), "t") as src:
            a1 = parse_assembly({"parts": [{"name": "a", "code": "a();"}]})
            a2 = parse_assembly({"parts": [{"name": "a", "code": "a();", "place": "up(1)"}]})
            a3 = parse_assembly({"parts": [{"name": "a", "code": "a();"}], "quality": {"fn": 64}})
            k1 = server._part_cache_key(src, a1, a1.parts[0], {}, None)
            k2 = server._part_cache_key(src, a2, a2.parts[0], {}, None)
            k3 = server._part_cache_key(src, a3, a3.parts[0], {}, None)
            k4 = server._part_cache_key(src, a1, a1.parts[0], {"W": 2}, None)
        assert len({k1, k2, k3, k4}) == 4

    def test_quality_argument_forms(self):
        assert server._quality_to_variables(48) == {"$fn": 48}
        assert server._quality_to_variables("draft") == {"$fn": 8, "$fa": 12, "$fs": 2}
        assert server._quality_to_variables({"fn": 32}) == {"$fn": 32}
        with pytest.raises(ValueError):
            server._quality_to_variables("ultra")


class TestCliCheck:
    def test_cli_reports_error_for_missing_file(self, env, capsys):
        code = server._cli_check([str(env / "missing.yaml")])
        assert code == 3
        assert "ERROR" in capsys.readouterr().out
