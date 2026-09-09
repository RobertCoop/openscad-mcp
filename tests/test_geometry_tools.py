"""
Tests for the Phase 1/2 tools: render (modes), measure (modes), validate
(modes), scad_eval and reference, plus the source wrappers behind them.

Pure-logic tests mock OpenSCAD. A second group runs the real binary when it
is installed (skipped otherwise) because the wrappers depend on verified
2021.01 behaviour: include-inside-module, the ``!`` root modifier, and
``projection(cut=true)`` exiting 1 on a miss.
"""

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from openscad_mcp import server
from openscad_mcp.utils.config import CacheConfig, Config, SecurityConfig, set_config
from openscad_mcp.wrappers import (
    EVAL_MARKER,
    collect_eval_results,
    eval_wrapper,
    format_scad_value,
    parse_echo_values,
    part_wrapper,
    parts_wrapper,
    section_transform,
    section_wrapper,
)

render_fn = server.render.fn
measure_fn = server.measure.fn
validate_fn = server.validate.fn
scad_eval_fn = server.scad_eval.fn
reference_fn = server.reference.fn

HAVE_OPENSCAD = shutil.which("openscad") is not None
needs_openscad = pytest.mark.skipif(not HAVE_OPENSCAD, reason="OpenSCAD not installed")

MODEL = """
include <params.scad>
W = 20; H = 10;
function inner() = W - 2*wall;
module body() { difference() { cube([W, W, H]); translate([wall, wall, wall]) cube([inner(), inner(), H]); } }
module lid() { translate([0, 0, H]) cube([W, W, wall]); }
body();
"""
PARAMS = "wall = 2; clearance = 0.2;\n"


def _meta(items):
    return json.loads([x for x in items if isinstance(x, str)][-1])


def _images(items):
    return [x for x in items if not isinstance(x, str)]


def _texts(items):
    return [x for x in items if isinstance(x, str)][:-1]


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "params.scad").write_text(PARAMS)
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
    return proj


# ---------------------------------------------------------------------------
# Wrappers and echo parsing (no OpenSCAD)
# ---------------------------------------------------------------------------


class TestWrappers:
    def test_format_values(self):
        assert format_scad_value(True) == "true"
        assert format_scad_value(None) == "undef"
        assert format_scad_value('a"b') == '"a\\"b"'
        assert format_scad_value([1, 2.5, "x"]) == '[1, 2.5, "x"]'

    def test_section_transforms(self):
        assert section_transform("z", 3) == "translate([0, 0, -3])"
        assert "rotate" in section_transform("x", 0)
        assert "rotate" in section_transform("y", 0)
        with pytest.raises(ValueError):
            section_transform("w", 0)

    def test_section_wrapper_shape(self, tmp_path):
        text = section_wrapper(tmp_path / "m.scad", "z", 1.5, {"W": 40})
        assert "module __model()" in text
        assert "include <" in text
        assert "W = 40;" in text
        assert "projection(cut = true)" in text

    def test_parts_wrapper_uses_root_modifier_and_ghosts(self, tmp_path):
        text = parts_wrapper(
            tmp_path / "m.scad",
            [{"name": "body", "code": "body()"}, {"name": "lid", "code": "lid();"}],
            ["#111111", "#222222"],
            isolate="lid",
        )
        assert "!union()" in text
        assert '%color("#111111", 0.3) { body(); }' in text
        assert 'color("#222222") { lid(); }' in text

    def test_part_wrapper(self, tmp_path):
        text = part_wrapper(tmp_path / "m.scad", "lid()")
        assert "!union()" in text and "lid();" in text

    def test_eval_wrapper(self, tmp_path):
        text = eval_wrapper(tmp_path / "m.scad", ["W*2", "[W,H]"], {"W": 5})
        assert f'echo("{EVAL_MARKER}", 0, (W*2));' in text
        assert "W = 5;" in text

    def test_parse_echo_values(self):
        assert parse_echo_values("3, 2.5, -1e-7") == [3, 2.5, -1e-7]
        assert parse_echo_values('"a, b", true, undef') == ["a, b", True, None]
        assert parse_echo_values("[1, [2, 3]], []") == [[1, [2, 3]], []]
        assert parse_echo_values("[0 : 2 : 10]") == [{"range": [0, 2, 10]}]
        assert parse_echo_values("1.23457e+8") == [123457000.0]

    def test_collect_eval_results(self):
        lines = [
            f'"{EVAL_MARKER}", 1, [1, 2]',
            f'"{EVAL_MARKER}", 0, 42',
            '"unrelated echo"',
        ]
        out = collect_eval_results(lines, 3)
        assert out[0]["value"] == 42 and out[0]["type"] == "number"
        assert out[1]["value"] == [1, 2] and out[1]["type"] == "vector"
        assert out[2]["evaluated"] is False


class TestParsing:
    def test_parse_parts_forms(self):
        assert server._parse_parts([{"name": "a", "code": "a();"}]) == [{"name": "a", "code": "a();"}]
        assert server._parse_parts({"lid": "lid();"}) == [{"name": "lid", "code": "lid();"}]
        assert server._parse_parts(["body()"]) == [{"name": "body", "code": "body()"}]
        assert server._parse_parts('[{"name":"x","code":"x();"}]')[0]["name"] == "x"
        with pytest.raises(ValueError):
            server._parse_parts([])
        with pytest.raises(ValueError):
            server._parse_parts([{"name": "no code"}])


# ---------------------------------------------------------------------------
# Tool behaviour with a mocked binary
# ---------------------------------------------------------------------------


class TestToolsMocked:
    async def test_render_rejects_bad_mode(self, project):
        out = await render_fn(scad_content="cube(1);", mode="bogus")
        assert _meta(out)["success"] is False

    async def test_reference_tool_and_resources(self):
        pytest.importorskip("openscad_mcp.reference")
        data = await reference_fn(topic="fasteners", query="M3")
        assert data["success"] is True
        assert data["entries"]
        listing = await reference_fn(topic="list")
        assert any(t["topic"] == "fits" for t in listing["topics"])
        bad = await reference_fn(topic="nonsense")
        assert bad["success"] is False
        assert server.mcp.instructions
        assert len(server.mcp.instructions) <= 1500

    async def test_measure_mesh_input_validates_path(self, tmp_path, project):
        outside = tmp_path / "outside.stl"
        outside.write_text("solid a\nendsolid a\n")
        out = await measure_fn(mesh=str(outside))
        assert out["success"] is False
        assert "allowed" in out["error"]

    async def test_measure_mesh_input(self, project):
        stl = project / "cube.stl"
        # unit cube as ASCII STL
        faces = [
            ((0, 0, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 0), (0, 1, 0), (1, 1, 0)),
            ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
            ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
            ((0, 1, 0), (1, 1, 1), (1, 1, 0)), ((0, 1, 0), (0, 1, 1), (1, 1, 1)),
            ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
            ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1)),
        ]
        lines = ["solid cube"]
        for tri in faces:
            lines.append(" facet normal 0 0 0\n  outer loop")
            lines.extend(f"   vertex {x} {y} {z}" for x, y, z in tri)
            lines.append("  endloop\n endfacet")
        lines.append("endsolid cube")
        stl.write_text("\n".join(lines) + "\n")
        out = await measure_fn(mesh=str(stl), mode="mass", material="PLA")
        assert out["success"] is True
        assert abs(out["volume"] - 1.0) < 1e-9
        assert out["is_watertight"] is True
        assert abs(out["mass"]["grams"] - 1.24 / 1000) < 1e-6

    async def test_scad_eval_requires_expressions(self):
        out = await scad_eval_fn(expressions=[])
        assert out["success"] is False

    async def test_validate_rejects_bad_mode(self, project):
        out = await validate_fn(scad_content="cube(1);", mode="nope")
        assert out["success"] is False

    async def test_render_views_with_mocked_binary(self, project):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

        def run(cmd, **kw):
            if "-o" in cmd:
                out = Path(cmd[cmd.index("-o") + 1])
                if out.suffix == ".png":
                    out.write_bytes(png)
            from unittest.mock import Mock

            r = Mock()
            r.returncode, r.stderr, r.stdout = 0, "", ""
            return r

        with patch("subprocess.run", side_effect=run), patch(
            "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
        ), patch(
            "openscad_mcp.server.get_openscad_capabilities",
            lambda path=None: {"installed": True, "version": "2021.01", "probed": True},
        ):
            out = await render_fn(scad_content="cube(1);", views=["front", "top"])
        meta = _meta(out)
        assert meta["success"] is True
        assert meta["views"] == ["front", "top"]
        assert len(_images(out)) == 2
        texts = _texts(out)
        assert texts[0].startswith("View: front")
        assert "unknown" in texts[0]  # auto-fit renders carry no absolute scale


# ---------------------------------------------------------------------------
# Real OpenSCAD
# ---------------------------------------------------------------------------


@needs_openscad
class TestToolsReal:
    async def test_measure_model(self, project):
        out = await measure_fn(scad_file=str(project / "asm.scad"))
        assert out["success"] is True
        assert out["dimensions"] == pytest.approx([20, 20, 10])
        expected = 20 * 20 * 10 - 16 * 16 * 8
        assert out["volume"] == pytest.approx(expected, rel=1e-6)
        assert out["is_watertight"] is True
        assert out["solid_count"] == 1
        assert out["mesh_health"]["manifold"] is True

    async def test_measure_uses_variables_and_cache(self, project):
        a = await measure_fn(scad_file=str(project / "asm.scad"), variables={"W": 30})
        assert a["dimensions"][0] == pytest.approx(30)
        b = await measure_fn(scad_file=str(project / "asm.scad"), variables={"W": 30})
        assert b["volume"] == a["volume"]

    async def test_measure_parts(self, project):
        out = await measure_fn(
            scad_file=str(project / "asm.scad"),
            mode="parts",
            parts=[{"name": "body", "code": "body();"}, {"name": "lid", "code": "lid();"}],
        )
        assert out["success"] is True, out
        names = [p["name"] for p in out["parts"]]
        assert names == ["body", "lid"]
        lid = out["parts"][1]
        assert lid["volume"] == pytest.approx(20 * 20 * 2, rel=1e-6)
        assert out["assembly_bbox"]["size"] == pytest.approx([20, 20, 12])
        assert out["bbox_overlaps"] == []

    async def test_measure_section(self, project):
        out = await measure_fn(scad_file=str(project / "asm.scad"), mode="section", section_offset=5)
        assert out["success"] is True, out
        assert out["area"] == pytest.approx(20 * 20 - 16 * 16, rel=1e-6)
        assert out["polygon_count"] == 2
        assert out["hole_count"] == 1
        missed = await measure_fn(scad_file=str(project / "asm.scad"), mode="section", section_offset=50)
        assert missed["empty_section"] is True

    async def test_measure_2d_model(self, project):
        out = await measure_fn(scad_content="square([10, 5]);")
        assert out["success"] is True, out
        assert out["area"] == pytest.approx(50)

    async def test_measure_mass(self, project):
        out = await measure_fn(scad_content="cube(10);", mode="mass", material="petg")
        assert out["mass"]["grams"] == pytest.approx(1.27, rel=1e-6)

    async def test_render_grounded_and_annotated(self, project):
        out = await render_fn(scad_file=str(project / "asm.scad"), views=["front"], grounded=True, annotate=True)
        meta = _meta(out)
        assert meta["success"] is True, meta
        assert meta["bbox"]["max"] == pytest.approx([20, 20, 10])
        text = _texts(out)[0]
        assert "mm/px" in text
        assert "unknown" not in text
        img = _images(out)[0]
        assert img.data[:4] == b"\x89PNG"

    async def test_render_section(self, project):
        out = await render_fn(scad_file=str(project / "asm.scad"), mode="section", section_offset=5)
        meta = _meta(out)
        assert meta["success"] is True, meta
        assert meta["contours"] == 2
        assert "mm/px" in _texts(out)[0]

    async def test_render_parts_and_isolate(self, project):
        out = await render_fn(
            scad_file=str(project / "asm.scad"),
            mode="parts",
            parts=[{"name": "body", "code": "body();"}, {"name": "lid", "code": "lid();"}],
            isolate="lid",
        )
        meta = _meta(out)
        assert meta["success"] is True, meta
        assert [p["name"] for p in meta["parts"]] == ["body", "lid"]
        assert "body=" in _texts(out)[0] and "(ghost)" in _texts(out)[0]

    async def test_render_compare(self, project):
        out = await render_fn(scad_file=str(project / "asm.scad"), mode="compare", variables_after={"W": 30})
        meta = _meta(out)
        assert meta["success"] is True, meta
        assert len(_images(out)) == 2

    async def test_render_reports_assert_with_image(self, project):
        out = await render_fn(scad_content="module m(){ assert(false, \"nope\"); cube(1); } m();")
        meta = _meta(out)
        assert meta["success"] is False
        assert meta["errors"]
        assert len(_images(out)) == 1

    async def test_validate_geometry(self, project):
        out = await validate_fn(scad_content="cube(5); translate([5,5,0]) cube(5);", mode="geometry")
        assert out["valid"] is False
        codes = {f["code"] for f in out["findings"]}
        assert "non_manifold" in codes or "non_manifold_edges" in codes
        ok = await validate_fn(scad_content="cube(5);", mode="geometry")
        assert ok["valid"] is True, ok

    async def test_validate_predicates(self, project):
        out = await validate_fn(
            scad_file=str(project / "asm.scad"),
            mode="predicates",
            predicates=["W > 10", "inner() == 16", "H == 2*W"],
        )
        assert out["valid"] is False
        assert [r["pass"] for r in out["results"]] == [True, True, False]

    async def test_validate_includes(self, project):
        out = await validate_fn(
            scad_content="include <params.scad>\nuse <nope.scad>\ncube(wall);",
            mode="includes",
            include_paths=[str(project)],
        )
        assert out["valid"] is False
        by_ref = {r["reference"]: r for r in out["references"]}
        assert by_ref["params.scad"]["found"] is True
        assert by_ref["nope.scad"]["found"] is False

    async def test_scad_eval(self, project):
        out = await scad_eval_fn(
            expressions=["W*2", "inner()", "[W, H]", "str(W)", "W > 10", "[0:2:6]"],
            scad_file=str(project / "asm.scad"),
        )
        assert out["success"] is True
        values = [r["value"] for r in out["results"]]
        assert values == [40, 16, [20, 10], "20", True, {"range": [0, 2, 6]}]
        standalone = await scad_eval_fn(expressions=["sqrt(16)", "len([1,2,3])"])
        assert [r["value"] for r in standalone["results"]] == [4, 3]

    async def test_every_resource_reads(self, project):
        resources = await server.mcp.get_resources()
        for uri, res in resources.items():
            assert await res.read(), uri
        templates = await server.mcp.get_resource_templates()
        assert "openscad://reference/{topic}" in templates
