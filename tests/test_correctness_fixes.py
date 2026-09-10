"""
Regression tests for the correctness fixes documented in
docs/research/llm-cad-mcp-landscape-2026-09.md (Phase 0).

Every test here reproduces a case where the server used to be confidently
wrong: a blank image labelled success, a stale cached render after an
include changed, a file read outside allowed_paths, a resource that raised
on every read, a version probe on every render, a thumbnail-sized default
framing, and a seven-image default response.

OpenSCAD is mocked throughout; the mocks write the files OpenSCAD would
(the PNG, the STL, the ``-d`` dependency list) so the surrounding logic is
exercised for real.
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openscad_mcp import server
from openscad_mcp.diagnostics import (
    extract_source_dependencies,
    image_token_estimate,
    parse_deps_file,
    parse_openscad_output,
    unresolved_includes,
)
from openscad_mcp.server import (
    RenderResult,
    _check_cache,
    _extract_scad_dependencies,
    _reset_openscad_cache,
    _wrap_with_memory_limit,
    find_openscad,
    get_openscad_capabilities,
    render_scad_to_png,
)
from openscad_mcp.utils.config import (
    CacheConfig,
    Config,
    SecurityConfig,
    get_render_semaphore,
    set_config,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

render_fn = server.render.fn
export_model_fn = server.export_model.fn
validate_fn = server.validate.fn
measure_fn = server.measure.fn
check_openscad_fn = server.check_openscad.fn
clear_cache_fn = server.clear_cache.fn
get_server_info_fn = server.get_server_info.fn


def _arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _write_outputs(cmd, deps=(), png=PNG, stl=None):
    """Emulate OpenSCAD's side effects for a command line."""
    if "-o" in cmd:
        out = Path(_arg_after(cmd, "-o"))
        if str(out) not in ("/dev/null", "NUL"):
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.suffix == ".png":
                out.write_bytes(png)
            elif stl is not None:
                out.write_text(stl)
            else:
                out.write_text("solid x\nendsolid x\n")
    if "-d" in cmd:
        deps_file = Path(_arg_after(cmd, "-d"))
        target = _arg_after(cmd, "-o")
        lines = [f"{target}: \\"] + [f"\t{d} \\" for d in deps] + [f"\t{cmd[-1]}"]
        deps_file.write_text("\n".join(lines) + "\n")


def _result(returncode=0, stderr="", stdout=""):
    r = Mock()
    r.returncode = returncode
    r.stderr = stderr
    r.stdout = stdout
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Config with caching enabled in tmp_path and a fake OpenSCAD binary."""
    cfg = Config(
        temp_dir=tmp_path / "tmp",
        cache=CacheConfig(enabled=True, directory=tmp_path / "cache"),
        security=SecurityConfig(allowed_paths=[str(tmp_path / "proj")]),
    )
    set_config(cfg)
    _reset_openscad_cache()
    monkeypatch.setattr("openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad")
    monkeypatch.setattr(
        "openscad_mcp.server.get_openscad_capabilities",
        lambda path=None: {
            "installed": True,
            "version": "2021.01",
            "amf_export": True,
            "probed": True,
        },
    )
    (tmp_path / "proj").mkdir()
    return tmp_path


def _meta(items):
    return json.loads([x for x in items if isinstance(x, str)][-1])


# ---------------------------------------------------------------------------
# Diagnostics parser
# ---------------------------------------------------------------------------


class TestDiagnosticsParser:
    def test_assert_failure_at_exit_zero_is_an_error(self):
        stderr = (
            "ERROR: Assertion 'false' failed: \"bad\" in file /tmp/x/input.scad, line 1\n"
            "TRACE: called by 'assert' in file /tmp/x/input.scad, line 1\n"
            "TRACE: called by 'm' in file /tmp/x/input.scad, line 3\n"
        )
        diag = parse_openscad_output(stderr, 0, inline_path="/tmp/x/input.scad")
        assert diag.ok is False
        assert diag.returncode == 0
        assert len(diag.records) == 1
        rec = diag.records[0]
        assert rec.severity == "ERROR"
        assert rec.file == "<inline>"
        assert rec.line == 1
        assert len(rec.trace) == 2
        assert "/tmp/x" not in json.dumps(diag.to_dict())
        assert [h["code"] for h in diag.hints()] == ["assertion_failed"]

    def test_unknown_module_is_a_warning_with_hint(self):
        diag = parse_openscad_output(
            "WARNING: Ignoring unknown module 'rounded_rect' in file a.scad, line 2\n", 0
        )
        assert diag.ok is True
        assert diag.warnings == [
            "WARNING: Ignoring unknown module 'rounded_rect' in file a.scad, line 2"
        ]
        assert diag.hints()[0]["code"] == "unknown_symbol"

    def test_comma_before_in_file_form(self):
        diag = parse_openscad_output("WARNING: something odd, in file b.scad, line 7\n", 0)
        assert diag.records[0].file == "b.scad"
        assert diag.records[0].line == 7
        assert diag.records[0].message == "something odd"

    def test_cgal_statistics_and_mesh_health(self):
        stderr = (
            "   Top level object is a 3D object:\n"
            "   Simple:         no\n"
            "   Vertices:       14\n"
            "   Edges:          23\n"
            "   Facets:         12\n"
            "   Volumes:         3\n"
            "WARNING: Object may not be a valid 2-manifold and may need repair!\n"
        )
        diag = parse_openscad_output(stderr, 0)
        health = diag.mesh_health()
        assert health["manifold"] is False
        assert health["nef_volumes"] == 3
        assert "body" not in json.dumps(health)  # Volumes is never a body count
        assert diag.statistics["Vertices"] == 14
        assert diag.hints()[0]["code"] == "non_manifold"

    def test_missing_statistics_is_unknown_not_failure(self):
        diag = parse_openscad_output("   Facets:          6\n", 0)
        assert diag.mesh_health()["manifold"] is None
        assert diag.ok is True

    def test_empty_top_level_object(self):
        diag = parse_openscad_output("Current top level object is empty.\n", 1)
        assert diag.empty_output is True
        assert diag.ok is False
        assert any(h["code"] == "empty_output" for h in diag.hints())

    def test_nonzero_exit_without_markers_is_surfaced(self):
        diag = parse_openscad_output("Unknown option --bogus\n", 1)
        assert diag.errors == ["ERROR: OpenSCAD exited with status 1: Unknown option --bogus"]

    def test_echo_is_capped(self):
        stderr = "".join(f'ECHO: "line {i}"\n' for i in range(300))
        diag = parse_openscad_output(stderr, 0, echo_max_lines=10)
        assert len(diag.echo_output) == 10
        assert diag.echo_truncated is True
        long = "ECHO: " + "x" * 5000 + "\n"
        diag = parse_openscad_output(long, 0, echo_max_chars=100)
        assert diag.echo_output[0].endswith("...[truncated]")
        assert diag.echo_truncated is True

    def test_echo_containing_in_file_text_is_preserved(self):
        diag = parse_openscad_output('ECHO: "note in file foo, line 3"\n', 0)
        assert diag.echo_output == ['"note in file foo, line 3"']

    def test_unresolved_includes(self):
        diag = parse_openscad_output(
            "WARNING: Can't open include file 'params.scad'.\n"
            "WARNING: Can't open library 'lib/helper.scad'.\n",
            0,
        )
        assert unresolved_includes(diag) == ["params.scad", "lib/helper.scad"]
        assert diag.hints()[0]["code"] == "missing_include"


class TestDependencyParsing:
    def test_deps_file_with_escaped_spaces_and_continuations(self):
        text = (
            "out.png: \\\n"
            "\t/abs/lib\\ dir/params.scad \\\n"
            "\t/abs/mesh.stl \\\n"
            "\tmain.scad\n"
        )
        assert parse_deps_file(text) == ["/abs/lib dir/params.scad", "/abs/mesh.stl", "main.scad"]

    def test_deps_file_windows_drive_letters(self):
        text = "C:\\out\\x.png: \\\n\tC:\\lib\\a.scad \\\n\tmain.scad\n"
        assert parse_deps_file(text) == ["C:\\lib\\a.scad", "main.scad"]

    def test_deps_file_empty(self):
        assert parse_deps_file("") == []
        assert parse_deps_file("garbage") == []

    def test_source_dependency_regex_catches_every_form(self):
        text = (
            "include <a.scad> // trailing comment\n"
            "use <b.scad>; use <c.scad>\n"
            "/* include <commented.scad> */\n"
            "// use <also_commented.scad>\n"
            'import("mesh.stl");\n'
            'surface(file="h.dat", center=true);\n'
            "cube(1);\n"
        )
        assert extract_source_dependencies(text) == [
            "a.scad",
            "b.scad",
            "c.scad",
            "mesh.stl",
            "h.dat",
        ]

    def test_extract_scad_dependencies_reads_file(self, tmp_path):
        f = tmp_path / "m.scad"
        f.write_text("include <x.scad>\n")
        assert _extract_scad_dependencies(f) == ["x.scad"]
        assert _extract_scad_dependencies(tmp_path / "missing.scad") == []

    def test_image_token_estimate(self):
        assert image_token_estimate(800, 600) == 29 * 22
        assert image_token_estimate(1568, 1568) == 56 * 56


# ---------------------------------------------------------------------------
# Render path: diagnostics with the image, framing, clamping
# ---------------------------------------------------------------------------


class TestRenderDiagnostics:
    def test_render_returns_image_and_error_when_exit_zero(self, env):
        """A failed assert exits 0 with a blank PNG; the error must travel with it."""
        stderr = "ERROR: Assertion 'false' failed in file input.scad, line 1\n"

        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, stderr)

        with patch("subprocess.run", side_effect=run):
            result = render_scad_to_png(scad_content="module m(){assert(false);} m();")

        assert isinstance(result, RenderResult)
        assert result.image_b64
        assert result.diagnostics.ok is False
        assert "Assertion" in result.diagnostics.errors[0]

    async def test_render_reports_success_false_with_image(self, env):
        stderr = "ERROR: Assertion 'false' failed in file input.scad, line 1\n"

        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, stderr)

        with patch("subprocess.run", side_effect=run):
            items = await render_fn(scad_content="assert(false); cube(1);")

        assert any(not isinstance(x, str) for x in items), "image must still be returned"
        meta = _meta(items)
        assert meta["success"] is False
        assert meta["errors"]
        assert meta["hints"][0]["code"] == "assertion_failed"
        assert meta["image_tokens"] == image_token_estimate(800, 600)
        assert meta["cached"] is False

    async def test_render_warnings_do_not_flip_success(self, env):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, "WARNING: Ignoring unknown module 'x' in file input.scad, line 1\n")

        with patch("subprocess.run", side_effect=run):
            meta = _meta(await render_fn(scad_content="x(); cube(1);"))
        assert meta["success"] is True
        assert meta["warnings"]

    async def test_default_render_auto_frames(self, env):
        captured = {}

        def run(cmd, **kw):
            captured["cmd"] = cmd
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            await render_fn(scad_content="cube([2,3,1]);")
        assert "--autocenter" in captured["cmd"]
        assert "--viewall" in captured["cmd"]

    async def test_explicit_camera_is_respected(self, env):
        """An explicit eye point reaches OpenSCAD as the viewing direction.

        Ungrounded renders always auto-fit, so --viewall still adjusts the
        distance along that direction; use grounded=true for an absolute
        scale.
        """
        captured = {}

        def run(cmd, **kw):
            captured["cmd"] = cmd
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            await render_fn(scad_content="cube(1);", camera_position=[10, 10, 10])
        camera = [a for a in captured["cmd"] if a.startswith("--camera=")][0]
        assert camera.split("=")[1].split(",")[:3] == ["10.0", "10.0", "10.0"]
        assert "--viewall" in captured["cmd"]

    def test_image_size_clamped_before_cache_key(self, env):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            a = render_scad_to_png(scad_content="cube(1);", image_size=[4000, 3000])
            b = render_scad_to_png(scad_content="cube(1);", image_size=[1568, 1176])
        assert a.image_size == [1568, 1176]
        assert a.cache_key == b.cache_key
        assert b.cached is True

    async def test_default_is_one_view_and_token_total_scales(self, env):
        """One image by default; asking for three costs three times the tokens."""

        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            meta = _meta(await render_fn(scad_content="cube(1);"))
            three = _meta(
                await render_fn(scad_content="cube(1);", views=["front", "top", "isometric"])
            )
        assert meta["views"] == ["isometric"]
        assert meta["image_tokens"] == image_token_estimate(800, 600)
        assert three["views"] == ["front", "top", "isometric"]
        assert three["image_tokens"] == 3 * image_token_estimate(800, 600)

    def test_image_tool_has_no_output_schema(self):
        """A return annotation here would re-enable structured output and
        break ImageContent delivery; keep it explicit."""
        assert server.render.output_schema is None


# ---------------------------------------------------------------------------
# Cache: dependency manifests
# ---------------------------------------------------------------------------


class TestCacheDependencyValidation:
    def test_include_change_invalidates_cache(self, env):
        proj = env / "proj"
        params = proj / "params.scad"
        params.write_text("W = 10;\n")
        main = proj / "main.scad"
        main.write_text("include <params.scad>\ncube(W);\n")
        renders = {"n": 0}

        def run(cmd, **kw):
            renders["n"] += 1
            _write_outputs(cmd, deps=[str(params)], png=PNG + bytes([renders["n"]]))
            return _result()

        with patch("subprocess.run", side_effect=run):
            first = render_scad_to_png(scad_file=str(main))
            second = render_scad_to_png(scad_file=str(main))
            assert second.cached is True
            assert second.image_b64 == first.image_b64
            assert renders["n"] == 1

            params.write_text("W = 40;\n")
            os.utime(params, None)
            third = render_scad_to_png(scad_file=str(main))

        assert third.cached is False
        assert renders["n"] == 2
        assert third.image_b64 != first.image_b64

    def test_same_stat_different_content_is_detected_by_hash(self, env):
        proj = env / "proj"
        params = proj / "params.scad"
        params.write_text("W = 10;\n")
        main = proj / "main.scad"
        main.write_text("include <params.scad>\ncube(W);\n")

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(params)])
            return _result()

        with patch("subprocess.run", side_effect=run):
            render_scad_to_png(scad_file=str(main))
            # Pin the include's mtime into the past so the write-race guard
            # lets the first render be cached, then rewrite it with the same
            # length and restore that mtime: only the content hash can tell.
            past = int(time.time() - 10) * 1_000_000_000
            os.utime(params, ns=(past, past))
            render_scad_to_png(scad_file=str(main))
            hit = render_scad_to_png(scad_file=str(main))
            params.write_text("W = 99;\n")  # same length as "W = 10;"
            os.utime(params, ns=(past, past))
            again = render_scad_to_png(scad_file=str(main))
        assert hit.cached is True
        assert again.cached is False

    def test_missing_include_that_appears_later_invalidates(self, env):
        proj = env / "proj"
        main = proj / "main.scad"
        main.write_text("include <params.scad>\ncube(1);\n")
        stderr = "WARNING: Can't open include file 'params.scad'.\n"

        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, stderr)

        with patch("subprocess.run", side_effect=run):
            first = render_scad_to_png(scad_file=str(main))
            assert first.unresolved_includes == ["params.scad"]
            assert render_scad_to_png(scad_file=str(main)).cached is True
            (proj / "params.scad").write_text("W = 1;\n")
            assert render_scad_to_png(scad_file=str(main)).cached is False

    def test_cached_diagnostics_survive(self, env):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, "WARNING: Ignoring unknown module 'x' in file input.scad, line 1\n")

        with patch("subprocess.run", side_effect=run):
            render_scad_to_png(scad_content="x(); cube(1);")
            hit = render_scad_to_png(scad_content="x(); cube(1);")
        assert hit.cached is True
        assert hit.diagnostics.warnings

    def test_entry_without_manifest_is_a_miss(self, env):
        cfg = server.get_config()
        cfg.cache.ensure_cache_directory()
        (cfg.cache.directory / ("a" * 64 + ".png")).write_bytes(PNG)
        assert _check_cache("a" * 64) is None
        assert not (cfg.cache.directory / ("a" * 64 + ".png")).exists()

    def test_binary_identity_is_part_of_key(self, env, monkeypatch):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            a = render_scad_to_png(scad_content="cube(1);")
            monkeypatch.setattr(
                "openscad_mcp.server.get_openscad_capabilities",
                lambda path=None: {"installed": True, "version": "2025.08.17", "probed": True},
            )
            b = render_scad_to_png(scad_content="cube(1);")
        assert a.cache_key != b.cache_key
        assert b.cached is False

    async def test_clear_cache_removes_manifests(self, env):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            render_scad_to_png(scad_content="cube(1);")
        cache_dir = server.get_config().cache.directory
        assert list(cache_dir.glob("*.json"))
        result = await clear_cache_fn()
        assert result["cleared_files"] == 2  # the PNG and its dependency manifest
        assert not list(cache_dir.glob("*.json"))
        assert not list(cache_dir.glob("*.png"))


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurityClosure:
    def test_render_withholds_output_reading_outside_allowed_paths(self, env):
        secret = env / "secret.txt"
        secret.write_text("[1, 2, 3]\n")

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(secret)])
            return _result(0, "ECHO: [1, 2, 3]\n")

        with (
            patch("subprocess.run", side_effect=run),
            pytest.raises(ValueError, match="outside allowed"),
        ):
            render_scad_to_png(scad_content=f"n=[ include <{secret}> ]; echo(n); cube(1);")

    async def test_validate_withholds_echo_for_outside_read(self, env):
        secret = env / "secret.txt"
        secret.write_text("[8, 6, 3]\n")

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(secret)])
            return _result(0, "ECHO: [8, 6, 3]\n")

        with patch("subprocess.run", side_effect=run):
            result = await validate_fn(scad_content=f"n=[ include <{secret}> ]; echo(n);")
        assert result["success"] is False
        assert "outside allowed" in result["error"]
        assert "echo_output" not in result

    async def test_measure_withholds_geometry_for_outside_read(self, env):
        secret = env / "secret.dat"
        secret.write_text("11 22\n33 44\n")

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(secret)])
            return _result(0, "   Simple: yes\n")

        with patch("subprocess.run", side_effect=run):
            result = await measure_fn(scad_content=f'surface(file="{secret}");')
        assert result["success"] is False
        assert "bbox_min" not in result

    async def test_export_withholds_and_deletes_file_for_outside_read(self, env):
        secret = env / "secret.dat"
        secret.write_text("1 2\n")
        out = env / "proj" / "out.stl"

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(secret)])
            return _result(0)

        with patch("subprocess.run", side_effect=run):
            result = await export_model_fn(
                scad_content=f'surface(file="{secret}");', output_path=str(out)
            )
        assert result["success"] is False
        assert not out.exists()

    def test_library_and_temp_reads_are_allowed(self, env, monkeypatch):
        lib = env / "libs"
        (lib / "BOSL2").mkdir(parents=True)
        std = lib / "BOSL2" / "std.scad"
        std.write_text("// lib\n")
        monkeypatch.setenv("OPENSCADPATH", str(lib))

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(std)])
            return _result()

        with patch("subprocess.run", side_effect=run):
            result = render_scad_to_png(scad_content="include <BOSL2/std.scad>\ncube(1);")
        assert result.dependencies == [str(std)]

    @pytest.mark.parametrize("tool", [validate_fn, measure_fn, export_model_fn])
    async def test_include_paths_validated_in_every_tool(self, env, tool):
        outside = env / "elsewhere"
        outside.mkdir()
        with patch("subprocess.run") as run:
            result = await tool(scad_content="cube(1);", include_paths=[str(outside)])
        assert result["success"] is False
        assert "Include path" in result["error"]
        run.assert_not_called()

    def test_no_allowed_paths_means_no_closure_check(self, env):
        set_config(
            Config(temp_dir=env / "tmp", cache=CacheConfig(enabled=False, directory=env / "c"))
        )
        secret = env / "secret.txt"
        secret.write_text("[1]\n")

        def run(cmd, **kw):
            _write_outputs(cmd, deps=[str(secret)])
            return _result()

        with patch("subprocess.run", side_effect=run):
            assert render_scad_to_png(scad_content="cube(1);").image_b64

    def test_allowed_paths_from_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "MCP_ALLOWED_PATHS", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
        )
        monkeypatch.setenv("MCP_MAX_MEMORY_MB", "1024")
        cfg = Config.from_env()
        assert cfg.security.allowed_paths == [str(tmp_path / "a"), str(tmp_path / "b")]
        assert cfg.security.max_memory_mb == 1024

    def test_memory_limit_wrapper(self, env):
        cfg = server.get_config()
        cmd = ["/usr/bin/openscad", "-o", "x.png", "in.scad"]
        if os.name == "posix":
            wrapped = _wrap_with_memory_limit(cmd)
            assert wrapped[-len(cmd) :] == cmd
            assert "ulimit -v" in wrapped[2]
            assert str(cfg.security.max_memory_mb * 1024) in wrapped[2]
        cfg.security.max_memory_mb = 0
        assert _wrap_with_memory_limit(cmd) == cmd

    def test_timeout_keeps_partial_stderr(self, env):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(
                cmd, 300, stderr="WARNING: slow thing in file input.scad, line 2\n"
            )

        with patch("subprocess.run", side_effect=run), pytest.raises(RuntimeError) as exc:
            render_scad_to_png(scad_content="cube(1);")
        assert "timed out" in str(exc.value)
        assert "slow thing" in str(exc.value)


# ---------------------------------------------------------------------------
# Export / analyze / validate outputs
# ---------------------------------------------------------------------------


class TestMeshHealthAndFormats:
    async def test_export_reports_mesh_health(self, env):
        stderr = (
            "   Simple:         no\n   Volumes:         3\n"
            "WARNING: Object may not be a valid 2-manifold and may need repair!\n"
        )

        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, stderr)

        with patch("subprocess.run", side_effect=run):
            result = await export_model_fn(scad_content="cube(5); translate([5,5,0]) cube(5);")
        assert result["success"] is True
        assert result["mesh_health"]["manifold"] is False
        assert result["warnings"]
        assert result["hints"][0]["code"] == "non_manifold"

    async def test_export_error_at_exit_zero_flips_success(self, env):
        def run(cmd, **kw):
            _write_outputs(cmd)
            return _result(0, "ERROR: The given mesh is not closed!\n")

        with patch("subprocess.run", side_effect=run):
            result = await export_model_fn(scad_content="polyhedron();")
        assert result["success"] is False
        assert result["errors"]
        assert result["output_path"]

    async def test_export_empty_object(self, env):
        def run(cmd, **kw):
            return _result(1, "Current top level object is empty.\n")

        with patch("subprocess.run", side_effect=run):
            result = await export_model_fn(scad_content="difference(){}")
        assert result["success"] is False
        assert result["empty_output"] is True

    @pytest.mark.parametrize("fmt", ["csg", "nef3", "pdf"])
    async def test_new_export_formats_accepted(self, env, fmt):
        captured = {}

        def run(cmd, **kw):
            captured["cmd"] = cmd
            _write_outputs(cmd)
            return _result()

        with patch("subprocess.run", side_effect=run):
            result = await export_model_fn(scad_content="cube(1);", output_format=fmt)
        assert result["success"] is True
        assert _arg_after(captured["cmd"], "-o").endswith(f".{fmt}")
        assert "mesh_health" in result if fmt == "nef3" else "mesh_health" not in result

    async def test_amf_rejected_when_binary_dropped_it(self, env, monkeypatch):
        monkeypatch.setattr(
            "openscad_mcp.server.get_openscad_capabilities",
            lambda path=None: {
                "installed": True,
                "version": "2026.03.01",
                "amf_export": False,
                "probed": True,
            },
        )
        with patch("subprocess.run") as run:
            result = await export_model_fn(scad_content="cube(1);", output_format="amf")
        assert result["success"] is False
        run.assert_not_called()

    async def test_measure_returns_mesh_health_and_warnings(self, env):
        stl = (
            "solid a\nfacet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 2 0 0\nvertex 0 3 1\nendloop\nendfacet\nendsolid a\n"
        )

        def run(cmd, **kw):
            _write_outputs(cmd, stl=stl)
            return _result(
                0, f"   Simple: yes\n   Volumes: 2\nWARNING: hmm in file {cmd[-1]}, line 1\n"
            )

        with patch("subprocess.run", side_effect=run):
            result = await measure_fn(scad_content="cube([2,3,1]);")
        assert result["success"] is True
        assert result["dimensions"] == [2.0, 3.0, 1.0]
        assert result["mesh_health"]["manifold"] is True
        assert result["warnings"] == ["WARNING: hmm in file <inline>, line 1"]

    async def test_validate_reports_locations_and_hints(self, env):
        def run(cmd, **kw):
            name = Path(cmd[-1]).name
            return _result(
                1,
                (
                    "WARNING: Can't open include file 'nope.scad'.\n"
                    f"ERROR: Parser error: syntax error in file {name}, line 3\n"
                    f"Can't parse file '{name}'!\n"
                ),
            )

        with patch("subprocess.run", side_effect=run):
            result = await validate_fn(scad_content="include <nope.scad>\ncube(1);\nfoo(;\n")
        assert result["valid"] is False
        assert result["records"][1]["line"] == 3
        assert result["records"][1]["file"] == "<inline>"
        assert result["unresolved_includes"] == ["nope.scad"]
        assert {h["code"] for h in result["hints"]} == {"missing_include", "syntax_error"}


# ---------------------------------------------------------------------------
# Binary discovery, capabilities, resource
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_find_openscad_is_memoised(self, tmp_path, monkeypatch):
        set_config(Config(temp_dir=tmp_path, cache=CacheConfig(enabled=False, directory=tmp_path)))
        _reset_openscad_cache()
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "openscad":
                return _result(0, "OpenSCAD version 2021.01\n")
            raise FileNotFoundError

        monkeypatch.setattr(Path, "exists", lambda self: False)
        with patch("subprocess.run", side_effect=run):
            assert find_openscad() == "openscad"
            n = len(calls)
            assert find_openscad() == "openscad"
            assert get_openscad_capabilities()["version"] == "2021.01"
        assert len(calls) == n

    def test_newest_candidate_wins(self, tmp_path, monkeypatch):
        set_config(Config(temp_dir=tmp_path, cache=CacheConfig(enabled=False, directory=tmp_path)))
        _reset_openscad_cache()
        versions = {"openscad": "2021.01", "openscad-nightly": "2025.08.17"}

        def run(cmd, **kw):
            if cmd[0] in versions:
                return _result(0, f"OpenSCAD version {versions[cmd[0]]}\n")
            raise FileNotFoundError

        monkeypatch.setattr(Path, "exists", lambda self: False)
        with patch("subprocess.run", side_effect=run):
            assert find_openscad() == "openscad-nightly"
            caps = get_openscad_capabilities()
        assert caps["is_snapshot"] is True
        assert caps["has_manifold_backend"] is True
        assert caps["amf_export"] is True

    def test_version_read_from_stderr(self, tmp_path, monkeypatch):
        """2021.01 prints --version on stderr."""
        set_config(Config(temp_dir=tmp_path, cache=CacheConfig(enabled=False, directory=tmp_path)))
        _reset_openscad_cache()
        monkeypatch.setattr(Path, "exists", lambda self: False)

        def run(cmd, **kw):
            if cmd[0] == "openscad":
                return _result(0, stderr="OpenSCAD version 2021.01\n", stdout="")
            raise FileNotFoundError

        with patch("subprocess.run", side_effect=run):
            assert get_openscad_capabilities()["version"] == "2021.01"

    async def test_check_openscad_reports_capabilities_and_formats(self, env):
        result = await check_openscad_fn()
        assert result["installed"] is True
        assert "csg" in result["supported_export_formats"]
        assert "upgrade_hint" in result

    async def test_server_info_resource_reads(self, env):
        """resource://server/info raised TypeError on every read (it awaited a FunctionTool)."""
        info = await get_server_info_fn()
        assert info["openscad_version"] == "2021.01"
        assert info["path_validation_enabled"] is True
        assert "csg" in info["supported_formats"]

    async def test_every_registered_resource_is_readable(self, env):
        resources = await server.mcp.get_resources()
        assert resources, "expected at least one resource"
        for uri, res in resources.items():
            value = await res.read()
            assert value, uri


class TestConcurrencyPrimitive:
    def test_semaphore_is_recreated_per_loop(self):
        async def get():
            return get_render_semaphore()

        a = asyncio.run(get())
        b = asyncio.run(get())
        assert a is not b


class TestToolSurfaceBudget:
    """The tool schema is paid on every request. Keep it bounded."""

    BUDGET_CHARS = 21_000
    PER_TOOL_CHARS = 3_600

    async def test_total_schema_within_budget(self):
        tools = await server.mcp.get_tools()
        sizes = {
            name: len(json.dumps(t.to_mcp_tool().model_dump(exclude_none=True)))
            for name, t in tools.items()
        }
        total = sum(sizes.values())
        assert total <= self.BUDGET_CHARS, f"tool surface {total} chars: {sizes}"
        for name, size in sizes.items():
            assert size <= self.PER_TOOL_CHARS, f"{name} schema is {size} chars"
