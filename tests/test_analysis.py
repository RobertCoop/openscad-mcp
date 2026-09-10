"""Tests for :mod:`openscad_mcp.analysis`.

The unit tests build tiny fake libraries in ``tmp_path``. The integration
tests need a real OpenSCAD and a real BOSL2 and are skipped without them;
they reproduce the attachment bug the linter exists to catch, then prove the
generated rewrite fixes it.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from openscad_mcp.analysis import (
    FALLBACK_PALETTE,
    apply_rewrite,
    assign_colors,
    clear_scan_cache,
    include_closure,
    lint_use_shadowing,
    make_include_safe_copy,
    mask_source,
    plan_use_to_include_rewrite,
    remove_preview_guards,
    resolve_reference,
    scan_file,
    scan_text,
    stable_color,
    trace_symbol,
    validate_expression,
    variable_collisions,
)

# ---------------------------------------------------------------------------
# environment probes
# ---------------------------------------------------------------------------

OPENSCAD = Path("/bin/openscad")
BOSL2 = Path.home() / ".local" / "share" / "OpenSCAD" / "libraries" / "BOSL2"
DEALER_CAD = Path("/home/coop/projects/dealer/hardware/cad")
DEALER_CONSTANTS = Path("/home/coop/projects/dealer/config/hardware_constants.scad")

needs_openscad = pytest.mark.skipif(
    not (OPENSCAD.exists() and (BOSL2 / "std.scad").exists()),
    reason="needs OpenSCAD and BOSL2 installed",
)
needs_dealer = pytest.mark.skipif(not DEALER_CAD.is_dir(), reason="dealer corpus not present")


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Scans are memoised on (path, mtime, size); start every test cold."""
    clear_scan_cache()
    yield
    clear_scan_cache()


def _render(scad: Path, out: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(OPENSCAD), "-o", str(out), str(scad)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _bbox(stl: Path):
    from openscad_mcp.mesh import load_stl

    if not stl.exists() or stl.stat().st_size < 100:
        return None
    points = [v for tri in load_stl(stl) for v in tri]
    return (
        round(min(p[0] for p in points), 2),
        round(min(p[1] for p in points), 2),
        round(min(p[2] for p in points), 2),
        round(max(p[0] for p in points), 2),
        round(max(p[1] for p in points), 2),
        round(max(p[2] for p in points), 2),
    )


# ---------------------------------------------------------------------------
# fixtures: a tiny fake library
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """A library that sets ``$transform``, a part that includes it, callers."""
    (tmp_path / "fakelib.scad").write_text(
        "// pretend BOSL2\n"
        "$transform = 1;\n"
        "$parent_geom = undef;\n"
        '$tags = "";\n'
        "$parent_gear_teeth = undef;\n"
        "$fn = 32;  // builtin, not a shadowing hazard\n"
        "TOP = [0, 0, 1];\n"
        "module attach(anchor) { children(); }\n"
    )
    (tmp_path / "part.scad").write_text(
        "include <fakelib.scad>\n" "W = 1;\n" "module mod() { cube(W); }\n"
    )
    return tmp_path


@pytest.fixture
def caller_attach(fake_project: Path) -> Path:
    path = fake_project / "caller_attach.scad"
    path.write_text("include <fakelib.scad>\n" "use <part.scad>\n" "attach(TOP) mod();\n")
    return path


@pytest.fixture
def caller_plain(fake_project: Path) -> Path:
    path = fake_project / "caller_plain.scad"
    path.write_text("include <fakelib.scad>\nuse <part.scad>\nmod();\n")
    return path


# ---------------------------------------------------------------------------
# lexical layer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaskSource:
    def test_preserves_length_and_lines(self):
        src = 'a = "x{;"; // }\n/* b = 2;\n */ c = 3;\n'
        masked = mask_source(src)
        assert len(masked) == len(src)
        assert masked.count("\n") == src.count("\n")

    def test_blanks_braces_in_comments_and_strings(self):
        masked = mask_source('s = "{{{"; // }}}\nx = 1;\n')
        assert "{" not in masked and "}" not in masked
        assert "x = 1;" in masked

    def test_unterminated_block_comment(self):
        masked = mask_source("x = 1;\n/* never closed\ny = 2;\n")
        assert "x = 1;" in masked
        assert "y = 2;" not in masked


@pytest.mark.unit
class TestStatementScan:
    def test_classifies_file_scope_statements(self):
        scan = scan_text(
            "include <a.scad>\n"
            "use <b.scad>\n"
            "W = 2;\n"
            "$fn = 16;\n"
            "function f(x) = x * 2;\n"
            "module m() { cube(1); }\n"
            "echo(W);\n"
            "m();\n"
            "if (W > 1) { m(); }\n"
        )
        kinds = [s.kind for s in scan.statements]
        assert kinds == [
            "include",
            "use",
            "assign",
            "assign",
            "function",
            "module",
            "echo",
            "instantiation",
            "control",
        ]
        assert scan.includes == [("a.scad", 1)]
        assert scan.uses == [("b.scad", 2)]
        assert scan.modules == {"m": 6}
        assert scan.functions == {"f": 5}
        assert scan.dollar_vars == {"$fn": 4}
        assert scan.assignments["W"] == (3, "2")
        assert [s.line for s in scan.drawing_statements] == [8, 9]

    def test_nested_assignments_are_not_file_scope(self):
        scan = scan_text("module m() { INNER = 3; }\nOUTER = 4;\n")
        assert set(scan.assignments) == {"OUTER"}

    def test_else_branch_belongs_to_the_if(self):
        scan = scan_text("if (a) cube(1); else sphere(1);\n")
        assert len(scan.statements) == 1
        assert scan.statements[0].kind == "control"

    def test_commented_out_include_is_ignored(self):
        scan = scan_text("// include <ghost.scad>\ninclude <real.scad>\n")
        assert scan.includes == [("real.scad", 2)]


@pytest.mark.unit
class TestIncludeGraph:
    def test_closure_follows_include_not_use(self, fake_project: Path):
        closure = include_closure(fake_project / "part.scad")
        names = {p.name for p in closure}
        assert names == {"part.scad", "fakelib.scad"}

        caller = fake_project / "only_use.scad"
        caller.write_text("use <part.scad>\n")
        assert {p.name for p in include_closure(caller)} == {"only_use.scad"}

    def test_resolve_prefers_the_including_files_directory(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "x.scad").write_text("// near\n")
        (tmp_path / "x.scad").write_text("// far\n")
        found = resolve_reference("x.scad", tmp_path / "sub" / "caller.scad")
        assert found == (tmp_path / "sub" / "x.scad").resolve()

    def test_unresolvable_reference_returns_none(self, tmp_path: Path):
        assert resolve_reference("nope_missing.scad", tmp_path / "c.scad") is None


# ---------------------------------------------------------------------------
# capability 1: the lint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLintUseShadowing:
    def test_attach_child_is_an_error(self, caller_attach: Path):
        findings = lint_use_shadowing(caller_attach)
        assert len(findings) == 1
        f = findings[0]
        assert f["code"] == "bosl2_use_shadowing"
        assert f["severity"] == "ERROR"
        assert f["line"] == 2
        assert Path(f["used_file"]).name == "part.scad"
        assert f["call_sites"][0]["construct"] == "attach"
        assert f["call_sites"][0]["module"] == "mod"

    def test_without_a_context_call_it_is_a_warning(self, caller_plain: Path):
        findings = lint_use_shadowing(caller_plain)
        assert len(findings) == 1
        assert findings[0]["severity"] == "WARNING"
        assert findings[0]["call_sites"] == []

    def test_include_produces_no_finding(self, fake_project: Path):
        caller = fake_project / "caller_include.scad"
        caller.write_text("include <fakelib.scad>\ninclude <part.scad>\nattach(TOP) mod();\n")
        assert lint_use_shadowing(caller) == []

    def test_use_of_a_clean_file_produces_no_finding(self, tmp_path: Path):
        (tmp_path / "clean.scad").write_text("K = 3;\nmodule c() { cube(K); }\n")
        caller = tmp_path / "caller.scad"
        caller.write_text("use <clean.scad>\nc();\n")
        assert lint_use_shadowing(caller) == []

    def test_reports_categories_not_names(self, caller_attach: Path):
        categories = lint_use_shadowing(caller_attach)[0]["categories"]
        assert categories == {
            "transform": 1,
            "attachment_context": 2,
            "gear_mating": 1,
        }
        assert sum(categories.values()) == 4  # $fn excluded as a builtin

    def test_message_names_the_categories(self, caller_attach: Path):
        message = lint_use_shadowing(caller_attach)[0]["message"]
        assert "attachment context (2)" in message
        assert "gear mating (1)" in message
        assert "$transform" in message
        assert "CENTER" in message

    def test_fix_hint_describes_the_rewrite(self, caller_attach: Path):
        fix = lint_use_shadowing(caller_attach)[0]["fix"]
        assert fix["action"] == "use_to_include"
        assert fix["replacement"] == "include <part.scad>"
        assert fix["safe"] is True
        assert fix["needs_shadow_copy"] is False
        assert fix["collisions"] == []

    def test_fix_hint_reports_collisions(self, fake_project: Path):
        caller = fake_project / "collide.scad"
        caller.write_text("include <fakelib.scad>\nuse <part.scad>\nW = 9;\nmod();\n")
        fix = lint_use_shadowing(caller)[0]["fix"]
        assert fix["safe"] is False
        assert fix["collisions"] == ["W"]

    def test_recursive_walk_reaches_used_files(self, fake_project: Path):
        (fake_project / "mid.scad").write_text(
            "include <fakelib.scad>\nuse <part.scad>\nmodule mid() { mod(); }\n"
        )
        top = fake_project / "top.scad"
        top.write_text("include <fakelib.scad>\nuse <mid.scad>\nmid();\n")

        deep = lint_use_shadowing(top)
        assert {Path(f["file"]).name for f in deep} == {"top.scad", "mid.scad"}

        shallow = lint_use_shadowing(top, recursive=False)
        assert {Path(f["file"]).name for f in shallow} == {"top.scad"}

    def test_missing_use_target_is_skipped(self, tmp_path: Path):
        caller = tmp_path / "caller.scad"
        caller.write_text("use <does_not_exist.scad>\n")
        assert lint_use_shadowing(caller) == []


# ---------------------------------------------------------------------------
# capability 2: shadow copies, collisions, rewrites
# ---------------------------------------------------------------------------


@pytest.fixture
def drawing_part(tmp_path: Path) -> Path:
    path = tmp_path / "drawing.scad"
    path.write_text(
        "W = 1;\n"
        "module mod() { cube(W); }\n"
        "function f(x) = x + W;\n"
        "mod();\n"
        "if ($preview) { mod(); }\n"
        "translate([0, 0, 5]) mod();\n"
        "echo(W);\n"
    )
    return path


@pytest.mark.unit
class TestShadowCopy:
    def test_strips_instantiations_and_keeps_definitions(self, drawing_part: Path, tmp_path: Path):
        shadow = make_include_safe_copy(drawing_part, tmp_path / "shadow")
        text = shadow.text
        assert "module mod() { cube(W); }" in text
        assert "function f(x) = x + W;" in text
        assert "W = 1;" in text
        assert "echo(W);" in text
        assert "mod();" not in text.replace("module mod() { cube(W); }", "")
        assert "$preview" not in text
        assert "translate" not in text
        assert shadow.kept_modules == ["mod"]
        assert shadow.kept_assignments == ["W"]

    def test_records_what_it_removed(self, drawing_part: Path, tmp_path: Path):
        shadow = make_include_safe_copy(drawing_part, tmp_path / "shadow")
        assert [r["line"] for r in shadow.removed] == [4, 5, 6]
        assert [r["kind"] for r in shadow.removed] == [
            "instantiation",
            "control",
            "instantiation",
        ]

    def test_line_numbers_are_preserved(self, drawing_part: Path, tmp_path: Path):
        shadow = make_include_safe_copy(drawing_part, tmp_path / "shadow")
        original = drawing_part.read_text().splitlines()
        copied = shadow.text.splitlines()
        assert copied[1] == original[1]  # module definition, same line
        assert copied[3].strip() == ""  # instantiation blanked, line kept
        assert len(copied) >= len(original)  # provenance comment appended at end

    def test_writes_to_the_destination(self, drawing_part: Path, tmp_path: Path):
        shadow = make_include_safe_copy(drawing_part, tmp_path / "shadow")
        assert shadow.path.parent == tmp_path / "shadow"
        assert shadow.path.read_text() == shadow.text
        assert "include-safe copy" in shadow.text

    def test_relative_includes_become_absolute(self, tmp_path: Path):
        (tmp_path / "cfg").mkdir()
        (tmp_path / "cfg" / "k.scad").write_text("K = 7;\n")
        (tmp_path / "cad").mkdir()
        part = tmp_path / "cad" / "p.scad"
        part.write_text("include <../cfg/k.scad>\nmodule p() { cube(K); }\np();\n")

        shadow = make_include_safe_copy(part, tmp_path / "shadow")
        assert f"include <{(tmp_path / 'cfg' / 'k.scad').resolve()}>" in shadow.text
        assert shadow.rewritten_refs and "../cfg/k.scad ->" in shadow.rewritten_refs[0]

    def test_library_references_are_left_alone(self, drawing_part: Path, tmp_path: Path):
        part = tmp_path / "libref.scad"
        part.write_text("include <BOSL2/std.scad>\nmodule m() { cube(1); }\nm();\n")
        shadow = make_include_safe_copy(part, tmp_path / "shadow")
        assert "include <BOSL2/std.scad>" in shadow.text
        assert shadow.rewritten_refs == []


@pytest.mark.unit
class TestVariableCollisions:
    def test_finds_shared_file_scope_names(self, tmp_path: Path):
        (tmp_path / "a.scad").write_text("W = 1;\nH = 2;\nmodule a() { cube(W); }\n")
        (tmp_path / "b.scad").write_text("W = 5;\nD = 3;\nmodule b() { cube(W); }\n")
        assert variable_collisions(tmp_path / "a.scad", tmp_path / "b.scad") == ["W"]

    def test_module_local_names_do_not_collide(self, tmp_path: Path):
        (tmp_path / "a.scad").write_text("module a() { W = 1; cube(W); }\n")
        (tmp_path / "b.scad").write_text("W = 5;\n")
        assert variable_collisions(tmp_path / "a.scad", tmp_path / "b.scad") == []


@pytest.mark.unit
class TestRewritePlan:
    def _caller(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "caller.scad"
        path.write_text(body)
        return path

    def test_safe_rewrite_replaces_the_use_line(self, fake_project: Path):
        caller = self._caller(fake_project, "include <fakelib.scad>\nuse <part.scad>\nmod();\n")
        plan = plan_use_to_include_rewrite(caller, fake_project / "part.scad")
        assert plan.safe is True
        assert plan.changed is True
        assert "include <part.scad>" in plan.new_text
        assert "use <part.scad>" not in plan.new_text
        assert plan.collisions == []

    def test_apply_writes_the_file(self, fake_project: Path):
        caller = self._caller(fake_project, "include <fakelib.scad>\nuse <part.scad>\nmod();\n")
        plan = plan_use_to_include_rewrite(caller, fake_project / "part.scad")
        apply_rewrite(plan)
        assert "include <part.scad>" in caller.read_text()

    def test_collision_makes_it_unsafe(self, fake_project: Path):
        caller = self._caller(
            fake_project, "include <fakelib.scad>\nuse <part.scad>\nW = 9;\nmod();\n"
        )
        plan = plan_use_to_include_rewrite(caller, fake_project / "part.scad")
        assert plan.safe is False
        assert plan.collisions == ["W"]
        assert plan.new_text == plan.original_text
        assert any("last assignment wins" in r for r in plan.reasons)

    def test_unsafe_plan_is_never_applied(self, fake_project: Path):
        caller = self._caller(fake_project, "include <fakelib.scad>\nuse <part.scad>\nW = 9;\n")
        plan = plan_use_to_include_rewrite(caller, fake_project / "part.scad")
        before = caller.read_text()
        with pytest.raises(ValueError, match="refusing to apply"):
            apply_rewrite(plan)
        assert caller.read_text() == before

    def test_drawing_used_file_is_unsafe_without_a_shadow_dir(
        self, drawing_part: Path, tmp_path: Path
    ):
        caller = self._caller(tmp_path, "use <drawing.scad>\nmod();\n")
        plan = plan_use_to_include_rewrite(caller, drawing_part)
        assert plan.safe is False
        assert any("top-level instantiation" in r for r in plan.reasons)

    def test_shadow_dir_makes_a_drawing_file_safe(self, drawing_part: Path, tmp_path: Path):
        caller = self._caller(tmp_path, "use <drawing.scad>\nmod();\n")
        plan = plan_use_to_include_rewrite(caller, drawing_part, shadow_dir=tmp_path / "shadow")
        assert plan.safe is True
        assert plan.shadow is not None
        assert str(plan.shadow.path) in plan.new_text
        assert plan.shadow.path.exists()

    def test_settable_guard_is_reported_instead_of_copying(self, tmp_path: Path):
        used = tmp_path / "guarded.scad"
        used.write_text(
            "make_stl = false;\n" "module g() { cube(1); }\n" "if (make_stl) { g(); }\n"
        )
        caller = self._caller(tmp_path, "use <guarded.scad>\ng();\n")
        plan = plan_use_to_include_rewrite(caller, used)
        assert plan.safe is True
        assert plan.shadow is None
        assert plan.set_variables == {"make_stl": False}
        assert "make_stl = false;" in plan.new_text

    def test_preview_guard_is_not_settable(self, tmp_path: Path):
        used = tmp_path / "prev.scad"
        used.write_text("module g() { cube(1); }\nif ($preview) { g(); }\n")
        caller = self._caller(tmp_path, "use <prev.scad>\ng();\n")
        plan = plan_use_to_include_rewrite(caller, used)
        assert plan.safe is False

    def test_missing_use_statement_is_unsafe(self, fake_project: Path):
        caller = self._caller(fake_project, "include <part.scad>\nmod();\n")
        plan = plan_use_to_include_rewrite(caller, fake_project / "part.scad")
        assert plan.safe is False
        assert "no `use <>` statement" in plan.reasons[0]


@pytest.mark.unit
class TestRemovePreviewGuards:
    def test_handles_the_make_stl_or_preview_idiom(self):
        src = (
            "make_stl = false;\n"
            "if (make_stl || $preview) {\n"
            "    Part();\n"
            "}\n"
            "module Part() { cube(1); }\n"
        )
        out = remove_preview_guards(src)
        assert "if (" not in out
        assert "Part();" in out
        assert "module Part() { cube(1); }" in out
        assert len(out.splitlines()) == len(src.splitlines())

    def test_handles_a_braceless_guard(self):
        out = remove_preview_guards("if ($preview) Part();\n")
        assert out.strip() == "Part();"

    def test_drops_the_else_branch(self):
        out = remove_preview_guards("if ($preview) a(); else b();\n")
        assert "a();" in out
        assert "b()" not in out

    def test_leaves_unrelated_conditionals_alone(self):
        src = "if (make_stl) { Part(); }\n"
        assert remove_preview_guards(src) == src

    def test_extra_guard_names_are_honoured(self):
        src = "make_stl = false;\nif (make_stl) { Part(); }\n"
        out = remove_preview_guards(src, guard_names=("$preview", "make_stl"))
        assert "if (" not in out
        assert "Part();" in out

    def test_nested_guards_are_untouched(self):
        src = "module m() { if ($preview) { cube(1); } }\n"
        assert remove_preview_guards(src) == src


# ---------------------------------------------------------------------------
# capability 3: dependency trace
# ---------------------------------------------------------------------------


@pytest.fixture
def constants_project(tmp_path: Path) -> Path:
    (tmp_path / "constants.scad").write_text(
        "// dimensions\n"
        "BASE = 10;\n"
        "WALL = BASE / 2;\n"
        "OUTER = BASE + 2 * WALL;\n"
        "TOTAL = OUTER * 1.5;\n"
        "UNRELATED = 42;\n"
    )
    (tmp_path / "part_a.scad").write_text("include <constants.scad>\nmodule a() { cube(OUTER); }\n")
    (tmp_path / "part_b.scad").write_text(
        "include <constants.scad>\nmodule b() { cube(UNRELATED); }\n"
    )
    return tmp_path


@pytest.mark.unit
class TestTraceSymbol:
    def _files(self, root: Path):
        return sorted(root.glob("*.scad"))

    def test_downstream_depth_ordering(self, constants_project: Path):
        result = trace_symbol("BASE", self._files(constants_project))
        # ordered by depth, then by name, so the result is deterministic
        assert [(r["name"], r["depth"]) for r in result["downstream"]] == [
            ("OUTER", 1),
            ("WALL", 1),
            ("TOTAL", 2),
        ]

    def test_definition_is_located(self, constants_project: Path):
        result = trace_symbol("BASE", self._files(constants_project))
        assert result["definition"]["line"] == 2
        assert result["definition"]["expression"] == "10"
        assert result["definition"]["file"].endswith("constants.scad")

    def test_upstream_direction(self, constants_project: Path):
        result = trace_symbol("TOTAL", self._files(constants_project), direction="upstream")
        assert result["direction"] == "upstream"
        assert [(r["name"], r["depth"]) for r in result["upstream"]] == [
            ("OUTER", 1),
            ("BASE", 2),
            ("WALL", 2),
        ]

    def test_files_reference_the_traced_set(self, constants_project: Path):
        result = trace_symbol("BASE", self._files(constants_project))
        names = {Path(f["file"]).name for f in result["files"]}
        assert "part_a.scad" in names  # uses OUTER, downstream of BASE
        assert "part_b.scad" not in names  # only uses UNRELATED

    def test_unrelated_constant_has_no_downstream(self, constants_project: Path):
        result = trace_symbol("UNRELATED", self._files(constants_project))
        assert result["downstream"] == []
        assert {Path(f["file"]).name for f in result["files"]} == {"part_b.scad"}

    def test_unknown_symbol_is_reported_not_raised(self, constants_project: Path):
        result = trace_symbol("NOPE", self._files(constants_project))
        assert result["definition"] is None
        assert result["downstream"] == []

    def test_module_local_assignments_are_excluded(self, tmp_path: Path):
        (tmp_path / "c.scad").write_text("TOP = 1;\nmodule m() { TOP = 99; INNER = TOP; }\n")
        result = trace_symbol("TOP", [tmp_path / "c.scad"])
        assert result["constants_parsed"] == 1
        assert result["downstream"] == []

    def test_comments_and_strings_do_not_create_constants(self, tmp_path: Path):
        (tmp_path / "c.scad").write_text(
            '// FAKE = 1;\nREAL = 2;\nS = "ALSO = 3;";\n/* HIDDEN = 4; */\n'
        )
        result = trace_symbol("REAL", [tmp_path / "c.scad"])
        assert result["constants_parsed"] == 2  # REAL and S

    def test_bad_direction_raises(self, constants_project: Path):
        with pytest.raises(ValueError, match="direction must be"):
            trace_symbol("BASE", self._files(constants_project), direction="sideways")

    def test_result_is_labelled_lexical(self, constants_project: Path):
        result = trace_symbol("BASE", self._files(constants_project))
        assert "lexical" in result["note"]
        assert "file-scope" in result["note"]


# ---------------------------------------------------------------------------
# capability 4: expression validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateExpression:
    @pytest.mark.parametrize(
        "expr",
        [
            "PINION_BOTTOM_Z + PINION_GEAR_THICKNESS",
            "1",
            "-3.5e2",
            "$fn * 2",
            "a[2]",
            "v[1][0]",
            "max(1, 2)",
            "f(a = 1, b = 2)",
            "x ? y : z",
            "a == b && c != d",
            "a < b || c >= d",
            "(A + B) % 3",
            "2 ^ 8",
            "[1, 2, 3][0]",
            "[0:2:10][1]",
            '"a string"',
            "-(A) / (B % 3)",
            "len(v) - 1",
            "true ? 1 : 0",
        ],
    )
    def test_accepts(self, expr: str):
        assert validate_expression(expr) == " ".join(expr.split())

    @pytest.mark.parametrize(
        "expr",
        [
            "x; cube()",
            "include <evil.scad>",
            "use <evil.scad>",
            'import("x.stl")',
            'surface(file="h.dat")',
            "echo(1)",
            "assert(false)",
            "a { b }",
            "!x",
            "#sphere(1)",
            "`whoami`",
            "1 +",
            "f(",
            "(1",
            "[1, 2",
            "a ? b",
            ", 1",
            "",
            "   ",
            "a @ b",
            "a \\ b",
        ],
    )
    def test_rejects(self, expr: str):
        with pytest.raises(ValueError):
            validate_expression(expr)

    def test_error_points_at_the_offender(self):
        with pytest.raises(ValueError) as excinfo:
            validate_expression("A + ; B")
        message = str(excinfo.value)
        assert "position 4" in message
        assert "^" in message

    def test_normalises_whitespace(self):
        assert validate_expression("A\n  +\tB") == "A + B"

    def test_non_string_is_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_expression(3)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# capability 5: stable colours
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStableColors:
    def test_same_name_same_colour(self):
        assert stable_color("roller") == stable_color("roller")

    def test_colour_comes_from_the_palette(self):
        from openscad_mcp.camera import PALETTE

        assert stable_color("roller") in PALETTE

    def test_custom_palette_is_used(self):
        assert stable_color("roller", palette=["#fff"]) == "#fff"

    def test_taken_colours_are_avoided(self):
        first = stable_color("roller")
        second = stable_color("roller", taken=[first])
        assert second != first

    def test_exhausted_palette_wraps(self):
        palette = ["#a", "#b"]
        assert stable_color("x", palette=palette, taken=palette) in palette

    def test_assignment_is_order_independent(self):
        names = ["base", "platform", "pinion", "motor", "chute"]
        assert assign_colors(names) == assign_colors(list(reversed(names)))

    def test_assignment_is_collision_free(self):
        names = [f"part_{i}" for i in range(len(FALLBACK_PALETTE))]
        colours = assign_colors(names, palette=FALLBACK_PALETTE)
        assert len(set(colours.values())) == len(names)

    def test_overrides_win_and_are_reserved(self):
        colours = assign_colors(["a", "b", "c"], overrides={"a": "#123456"})
        assert colours["a"] == "#123456"
        assert "#123456" not in {colours["b"], colours["c"]}

    def test_every_name_gets_a_colour(self):
        names = ["x", "y", "z"]
        assert set(assign_colors(names)) == set(names)

    def test_fallback_palette_is_available(self):
        assert len(FALLBACK_PALETTE) >= 8
        assert all(c.startswith("#") for c in FALLBACK_PALETTE)


# ---------------------------------------------------------------------------
# integration: real OpenSCAD, real BOSL2
# ---------------------------------------------------------------------------


@needs_openscad
@pytest.mark.integration
@pytest.mark.slow
class TestAttachmentBugReproduction:
    """`use` silently moves an attach() child. The lint catches it, the
    rewrite fixes it, and OpenSCAD agrees on both counts."""

    @pytest.fixture
    def bosl2_project(self, tmp_path: Path) -> Path:
        (tmp_path / "lib.scad").write_text(
            "include <BOSL2/std.scad>\n"
            "module childpart() { attachable(size=[10,10,10])"
            " { cube(10, center=true); children(); } }\n"
            "module parentpart() { attachable(size=[40,40,10])"
            " { cube([40,40,10], center=true); children(); } }\n"
        )
        (tmp_path / "caller_use.scad").write_text(
            "include <BOSL2/std.scad>\n"
            "use <lib.scad>\n"
            "parentpart() attach(TOP, BOTTOM) childpart();\n"
        )
        (tmp_path / "caller_include.scad").write_text(
            "include <BOSL2/std.scad>\n"
            "include <lib.scad>\n"
            "parentpart() attach(TOP, BOTTOM) childpart();\n"
        )
        return tmp_path

    def test_use_misplaces_the_child_and_include_does_not(self, bosl2_project: Path):
        used = _render(bosl2_project / "caller_use.scad", bosl2_project / "use.stl")
        inc = _render(bosl2_project / "caller_include.scad", bosl2_project / "inc.stl")
        assert used.returncode == 0, used.stderr
        assert inc.returncode == 0, inc.stderr

        use_bbox = _bbox(bosl2_project / "use.stl")
        inc_bbox = _bbox(bosl2_project / "inc.stl")
        assert use_bbox != inc_bbox
        # include: the child sits on the parent's top face (z 5..15).
        assert inc_bbox[5] == pytest.approx(15.0)
        # use: $transform reset across the boundary, so it lands lower.
        assert use_bbox[5] < inc_bbox[5]

    def test_lint_flags_exactly_that_pair(self, bosl2_project: Path):
        findings = lint_use_shadowing(bosl2_project / "caller_use.scad")
        assert len(findings) == 1
        assert findings[0]["severity"] == "ERROR"
        assert Path(findings[0]["used_file"]).name == "lib.scad"
        assert findings[0]["categories"]["transform"] == 1
        assert findings[0]["categories"]["attachment_context"] > 20
        assert lint_use_shadowing(bosl2_project / "caller_include.scad") == []

    def test_the_rewrite_restores_the_attachment(self, bosl2_project: Path):
        target = bosl2_project / "caller_fixed.scad"
        shutil.copy(bosl2_project / "caller_use.scad", target)
        plan = plan_use_to_include_rewrite(target, bosl2_project / "lib.scad")
        assert plan.safe is True
        apply_rewrite(plan)

        assert _render(target, bosl2_project / "fixed.stl").returncode == 0
        assert (
            _render(bosl2_project / "caller_include.scad", bosl2_project / "inc.stl").returncode
            == 0
        )
        assert _bbox(bosl2_project / "fixed.stl") == _bbox(bosl2_project / "inc.stl")
        assert lint_use_shadowing(target) == []


@needs_openscad
@needs_dealer
@pytest.mark.integration
@pytest.mark.slow
class TestPreviewGuardSemantics:
    """The dealer's `if (make_stl || $preview) { StepMotor28BYJ(); }` idiom."""

    @pytest.fixture
    def mirrored(self, tmp_path: Path) -> Path:
        source = DEALER_CAD / "StepMotor_28BYJ_48.scad"
        if not source.exists() or not DEALER_CONSTANTS.exists():
            pytest.skip("dealer step motor sources not present")
        (tmp_path / "hardware" / "cad").mkdir(parents=True)
        (tmp_path / "config").mkdir(parents=True)
        (tmp_path / "config" / "hardware_constants.scad").write_text(DEALER_CONSTANTS.read_text())
        (tmp_path / "hardware" / "cad" / "motor.scad").write_text(source.read_text())
        return tmp_path

    def test_guarded_file_exports_nothing_but_unguarded_exports_the_part(self, mirrored: Path):
        cad = mirrored / "hardware" / "cad"
        guarded = cad / "motor.scad"
        unguarded = cad / "motor_unguarded.scad"
        unguarded.write_text(remove_preview_guards(guarded.read_text()))

        _render(guarded, mirrored / "guarded.stl")
        assert _bbox(mirrored / "guarded.stl") is None  # empty at export time

        assert _render(unguarded, mirrored / "unguarded.stl").returncode == 0
        bbox = _bbox(mirrored / "unguarded.stl")
        assert bbox is not None
        assert bbox[3] - bbox[0] > 20  # a 28BYJ-48 sized body

    def test_shadow_copy_is_silent_alone_and_usable_when_included(self, mirrored: Path):
        cad = mirrored / "hardware" / "cad"
        shadow = make_include_safe_copy(cad / "motor.scad", mirrored / "shadow")
        assert shadow.rewritten_refs  # ../../config/... made absolute

        _render(shadow.path, mirrored / "alone.stl")
        assert _bbox(mirrored / "alone.stl") is None

        caller = mirrored / "caller.scad"
        caller.write_text(f"include <{shadow.path}>\nStepMotor28BYJ();\n")
        assert _render(caller, mirrored / "caller.stl").returncode == 0
        assert _bbox(mirrored / "caller.stl") is not None


@needs_dealer
@pytest.mark.integration
class TestDealerCorpus:
    def test_lint_reports_bosl2_categories_quickly(self):
        target = DEALER_CAD / "turntable_assembly.scad"
        if not target.exists():
            pytest.skip("turntable_assembly.scad not present")

        lint_use_shadowing(target)  # warm the BOSL2 include closure
        start = time.perf_counter()
        findings = lint_use_shadowing(target)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"lint took {elapsed:.3f}s"
        assert findings
        assert all(f["severity"] in ("WARNING", "ERROR") for f in findings)
        bosl2 = [f for f in findings if f["categories"].get("attachment_context", 0) > 20]
        assert bosl2, "expected the BOSL2 attachment-context category"
        assert any(f["categories"].get("transform") == 1 for f in findings)

    @pytest.mark.performance
    def test_warm_lint_is_under_100ms(self):
        target = DEALER_CAD / "turntable_assembly.scad"
        if not target.exists():
            pytest.skip("turntable_assembly.scad not present")
        lint_use_shadowing(target)
        start = time.perf_counter()
        lint_use_shadowing(target)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"warm lint took {elapsed:.3f}s"

    def test_trace_across_the_constants_file_and_parts(self):
        if not DEALER_CONSTANTS.exists():
            pytest.skip("hardware_constants.scad not present")
        files = [DEALER_CONSTANTS] + sorted(DEALER_CAD.glob("*.scad"))
        result = trace_symbol("SCANNER_FOCAL_DISTANCE", files)
        assert result["definition"] is not None
        assert result["constants_parsed"] > 100
        assert result["downstream"]
        depths = [r["depth"] for r in result["downstream"]]
        assert depths == sorted(depths)
        assert result["files"]

    def test_scanning_every_part_file_finds_the_render_guard(self):
        guarded = [
            p
            for p in sorted(DEALER_CAD.glob("*.scad"))
            if any(s.kind == "control" for s in scan_file(p).drawing_statements)
        ]
        assert len(guarded) > 10
        for path in guarded:
            assert "make_stl" in scan_file(path).assignments
