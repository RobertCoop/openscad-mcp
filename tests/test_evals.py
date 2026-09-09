"""Tests for the eval harness under evals/.

The harness lives outside the installed package, so it is imported by path.
Tests that need the OpenSCAD binary skip when it is not installed.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import run as eval_run  # noqa: E402
import scorers  # noqa: E402

FIXTURES_DIR = EVALS_DIR / "fixtures"
OPENSCAD = scorers.find_openscad()
requires_openscad = pytest.mark.skipif(OPENSCAD is None, reason="OpenSCAD is not installed")


# --------------------------------------------------------------------------- #
# Synthetic STL helpers
# --------------------------------------------------------------------------- #

_BOX_FACES = (
    (0, 3, 2),
    (0, 2, 1),
    (4, 5, 6),
    (4, 6, 7),
    (0, 1, 5),
    (0, 5, 4),
    (3, 7, 6),
    (3, 6, 2),
    (0, 4, 7),
    (0, 7, 3),
    (1, 2, 6),
    (1, 6, 5),
)


def box_triangles(origin=(0.0, 0.0, 0.0), size=(1.0, 1.0, 1.0), flip=False):
    """Twelve outward-facing triangles for an axis-aligned box."""
    x0, y0, z0 = origin
    x1, y1, z1 = (origin[i] + size[i] for i in range(3))
    corners = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    tris = []
    for a, b, c in _BOX_FACES:
        face = (corners[a], corners[b], corners[c])
        tris.append((face[0], face[2], face[1]) if flip else face)
    return tris


def ascii_stl(triangles, name="test") -> str:
    lines = [f"solid {name}"]
    for tri in triangles:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for vertex in tri:
            lines.append("      vertex {} {} {}".format(*vertex))
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def binary_stl(triangles) -> bytes:
    blob = b"\0" * 80 + struct.pack("<I", len(triangles))
    for tri in triangles:
        values = [0.0, 0.0, 0.0]
        for vertex in tri:
            values.extend(vertex)
        blob += struct.pack("<12fH", *values, 0)
    return blob


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestFixtures:
    def test_tasks_load(self):
        tasks = eval_run.load_tasks(FIXTURES_DIR)
        assert len(tasks) >= 15

    def test_ids_are_unique_and_match_directory(self):
        tasks = eval_run.load_tasks(FIXTURES_DIR)
        ids = [task["id"] for task in tasks]
        assert len(ids) == len(set(ids))
        for task in tasks:
            assert task["id"] == Path(task["_dir"]).name

    def test_tasks_are_valid(self):
        for task in eval_run.load_tasks(FIXTURES_DIR):
            assert scorers.validate_task(task) == [], task["id"]

    def test_every_check_type_is_implemented(self):
        used = {
            check["type"] for task in eval_run.load_tasks(FIXTURES_DIR) for check in task["checks"]
        }
        assert used <= scorers.CHECK_TYPES
        implemented = (
            set(scorers._MESH_CHECKS) | set(scorers._PROFILE_CHECKS) | {"predicate", "interference"}
        )
        assert implemented == scorers.CHECK_TYPES

    def test_reference_solutions_exist(self):
        for task in eval_run.load_tasks(FIXTURES_DIR):
            assert eval_run.reference_path(task).is_file(), task["id"]

    def test_tags_are_known(self):
        allowed = {"single-part", "assembly", "fit", "2d"}
        for task in eval_run.load_tasks(FIXTURES_DIR):
            assert task["tags"], task["id"]
            assert set(task["tags"]) <= allowed, task["id"]

    def test_prompts_name_the_symbols_checks_depend_on(self):
        """A predicate or interference check is only fair if the prompt asks for the name."""
        for task in eval_run.load_tasks(FIXTURES_DIR):
            prompt = task["prompt"]
            for check in task["checks"]:
                if check["type"] == "predicate":
                    assert check["scad"] in prompt, (task["id"], check["scad"])
                if check["type"] == "interference":
                    for part in check["parts"]:
                        module = part.split("(")[0].strip()
                        assert module in prompt, (task["id"], module)

    def test_coverage_of_task_kinds(self):
        tags = [tag for task in eval_run.load_tasks(FIXTURES_DIR) for tag in task["tags"]]
        for expected in ("single-part", "assembly", "fit", "2d"):
            assert expected in tags


# --------------------------------------------------------------------------- #
# Mesh scorers
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestMeshScorers:
    def test_parse_ascii_stl(self):
        tris = scorers.parse_stl_text(ascii_stl(box_triangles()))
        assert len(tris) == 12

    def test_parse_binary_stl(self, tmp_path):
        path = tmp_path / "box.stl"
        path.write_bytes(binary_stl(box_triangles(size=(2.0, 3.0, 4.0))))
        tris = scorers.parse_stl(path)
        assert len(tris) == 12
        assert scorers.dimensions(tris) == (2.0, 3.0, 4.0)

    def test_bounding_box(self):
        tris = scorers.parse_stl_text(
            ascii_stl(box_triangles(origin=(-1.0, 2.0, 0.5), size=(3.0, 4.0, 5.0)))
        )
        low, high = scorers.bounding_box(tris)
        assert low == (-1.0, 2.0, 0.5)
        assert high == (2.0, 6.0, 5.5)
        assert scorers.dimensions(tris) == (3.0, 4.0, 5.0)

    def test_volume_of_a_cube(self):
        tris = scorers.parse_stl_text(ascii_stl(box_triangles(size=(2.0, 3.0, 4.0))))
        assert scorers.signed_volume(tris) == pytest.approx(24.0)

    def test_volume_is_translation_invariant(self):
        moved = scorers.parse_stl_text(
            ascii_stl(box_triangles(origin=(10.0, -7.0, 3.0), size=(2.0, 2.0, 2.0)))
        )
        assert scorers.signed_volume(moved) == pytest.approx(8.0)

    def test_inverted_shell_has_negative_volume(self):
        tris = scorers.parse_stl_text(ascii_stl(box_triangles(size=(2.0, 2.0, 2.0), flip=True)))
        assert scorers.signed_volume(tris) == pytest.approx(-8.0)

    def test_surface_area_of_a_cube(self):
        tris = scorers.parse_stl_text(ascii_stl(box_triangles(size=(2.0, 2.0, 2.0))))
        assert scorers.surface_area(tris) == pytest.approx(24.0)

    def test_two_components(self):
        tris = box_triangles() + box_triangles(origin=(10.0, 0.0, 0.0))
        parsed = scorers.parse_stl_text(ascii_stl(tris))
        components = scorers.connected_components(parsed)
        assert len(components) == 2
        assert all(len(component) == 12 for component in components)

    def test_single_component(self):
        parsed = scorers.parse_stl_text(ascii_stl(box_triangles()))
        assert len(scorers.connected_components(parsed)) == 1

    def test_watertight(self):
        parsed = scorers.parse_stl_text(ascii_stl(box_triangles()))
        watertight, stats = scorers.is_watertight(parsed)
        assert watertight
        assert stats == {"edges": 18, "boundary_edges": 0, "nonmanifold_edges": 0}

    def test_open_mesh_is_not_watertight(self):
        parsed = scorers.parse_stl_text(ascii_stl(box_triangles()[:-1]))
        watertight, stats = scorers.is_watertight(parsed)
        assert not watertight
        assert stats["boundary_edges"] == 3

    def test_analyze_mesh_counts_solids_and_cavities(self):
        tris = (
            box_triangles(size=(10.0, 10.0, 10.0))
            + box_triangles(origin=(2.0, 2.0, 2.0), size=(4.0, 4.0, 4.0), flip=True)
            + box_triangles(origin=(20.0, 0.0, 0.0), size=(2.0, 2.0, 2.0))
        )
        report = scorers.analyze_mesh(scorers.parse_stl_text(ascii_stl(tris)))
        assert report.solid_count == 2
        assert report.cavity_count == 1
        assert report.volume == pytest.approx(1000.0 - 64.0 + 8.0)

    def test_empty_mesh(self):
        report = scorers.analyze_mesh([])
        assert report.triangles == 0
        assert not report.watertight
        assert report.solid_count == 0


# --------------------------------------------------------------------------- #
# Individual checks, without OpenSCAD
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestChecks:
    @pytest.fixture
    def cube_report(self):
        return scorers.analyze_mesh(
            scorers.parse_stl_text(ascii_stl(box_triangles(size=(10.0, 20.0, 30.0))))
        )

    def test_bbox_pass_and_fail(self, cube_report):
        good = scorers.check_bbox({"type": "bbox", "expected": [10, 20, 30]}, cube_report)
        bad = scorers.check_bbox(
            {"type": "bbox", "expected": [10, 20, 31], "tol_mm": 0.2}, cube_report
        )
        assert good["pass"] and good["actual"] == [10.0, 20.0, 30.0]
        assert not bad["pass"]

    def test_bbox_tolerance_boundary(self, cube_report):
        edge = scorers.check_bbox(
            {"type": "bbox", "expected": [10.2, 20, 30], "tol_mm": 0.2}, cube_report
        )
        assert edge["pass"]

    def test_volume_pct_and_absolute_tolerances(self, cube_report):
        assert scorers.check_volume({"expected": 6000.0, "tol_pct": 1}, cube_report)["pass"]
        assert not scorers.check_volume({"expected": 5000.0, "tol_pct": 1}, cube_report)["pass"]
        assert scorers.check_volume({"expected": 5990.0, "tol_mm3": 20}, cube_report)["pass"]

    def test_watertight_and_counts(self, cube_report):
        assert scorers.check_watertight({}, cube_report)["pass"]
        assert scorers.check_solid_count({"expected": 1}, cube_report)["pass"]
        assert not scorers.check_solid_count({"expected": 2}, cube_report)["pass"]
        assert scorers.check_cavity_count({"expected": 0}, cube_report)["pass"]

    def test_check_result_shape(self, cube_report):
        result = scorers.check_bbox({"expected": [10, 20, 30]}, cube_report)
        assert set(result) == {"type", "pass", "expected", "actual", "detail"}
        assert isinstance(result["pass"], bool)

    def test_predicate_parsing(self):
        values = scorers._parse_echo_values(
            'ECHO: "__EVAL__", 0, 3.4\nECHO: "__EVAL__", 1, [1, 2, 3]\nnoise\n'
        )
        assert values == {0: "3.4", 1: "[1, 2, 3]"}
        assert scorers.check_predicate({"scad": "d", "expected": 3.4}, values, 0)["pass"]
        assert scorers.check_predicate(
            {"scad": "v", "expected": [1, 2, 3], "tol_mm": 0.01}, values, 1
        )["pass"]

    def test_predicate_missing_variable_fails(self):
        result = scorers.check_predicate({"scad": "gone", "expected": 1.0}, {}, 0)
        assert not result["pass"]
        assert "not defined" in result["detail"]

    def test_predicate_shape_mismatch_fails(self):
        values = {0: "3.4"}
        result = scorers.check_predicate({"scad": "v", "expected": [1, 2]}, values, 0)
        assert not result["pass"]

    def test_predicate_non_numeric_fails(self):
        values = {0: '"a string"'}
        assert not scorers.check_predicate({"scad": "s", "expected": 1.0}, values, 0)["pass"]

    def test_missing_candidate_scores_zero(self, tmp_path):
        task = json.loads((FIXTURES_DIR / "plate_simple" / "task.json").read_text())
        result = scorers.score_task(task, tmp_path / "nope.scad")
        assert result["passed"] == 0
        assert result["total"] == len(task["checks"])
        assert result["pass"] is False
        assert result["error"] == "candidate file missing"

    def test_validate_task_reports_problems(self):
        problems = scorers.validate_task(
            {"id": "x", "prompt": "p", "reference_scad": "r.scad", "checks": [{"type": "nope"}]}
        )
        assert problems == ["unknown check type 'nope'"]
        assert "missing 'id'" in scorers.validate_task({"checks": []})


# --------------------------------------------------------------------------- #
# SVG scorers
# --------------------------------------------------------------------------- #

SAMPLE_SVG = """<?xml version="1.0" standalone="no"?>
<svg width="40mm" height="20mm" viewBox="0 -20 40 20" version="1.1">
<path d="
M 40,-20 L 0,-20 L 0,-0 L 40,-0 z
M 15,-5 L 15,-15 L 25,-15 L 25,-5 z
" stroke="black" fill="lightgray"/>
</svg>
"""


@pytest.mark.unit
class TestProfileScorers:
    def test_parse_contours(self):
        contours = scorers.parse_svg_contours(SAMPLE_SVG)
        assert len(contours) == 2
        assert contours[0][0] == (40.0, 20.0)

    def test_area_subtracts_holes(self, tmp_path):
        path = tmp_path / "plate.svg"
        path.write_text(SAMPLE_SVG)
        report = scorers.analyze_svg(path)
        assert report.dims == (40.0, 20.0)
        assert report.area == pytest.approx(40 * 20 - 10 * 10)

    def test_polygon_area_sign(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        assert scorers.polygon_area(square) == pytest.approx(100.0)
        assert scorers.polygon_area(list(reversed(square))) == pytest.approx(-100.0)

    def test_profile_checks(self, tmp_path):
        path = tmp_path / "plate.svg"
        path.write_text(SAMPLE_SVG)
        report = scorers.analyze_svg(path)
        assert scorers.check_bbox_2d({"expected": [40, 20], "tol_mm": 0.2}, report)["pass"]
        assert not scorers.check_bbox_2d({"expected": [40, 25], "tol_mm": 0.2}, report)["pass"]
        assert scorers.check_area_2d({"expected": 700.0, "tol_pct": 1}, report)["pass"]
        assert not scorers.check_area_2d({"expected": 800.0, "tol_pct": 1}, report)["pass"]

    def test_empty_svg(self, tmp_path):
        path = tmp_path / "empty.svg"
        path.write_text("<svg></svg>")
        report = scorers.analyze_svg(path)
        assert report.contours == 0
        assert report.area == 0.0


# --------------------------------------------------------------------------- #
# Comparison statistics
# --------------------------------------------------------------------------- #


def make_report(label, passes):
    """Build a minimal score report: ``passes`` maps task id to a pass/fail bool."""
    results = []
    for task_id, ok in passes.items():
        total = 3
        passed = 3 if ok else 1
        results.append(
            {
                "id": task_id,
                "tags": [],
                "candidate": f"{task_id}.scad",
                "checks": [],
                "error": None,
                "total": total,
                "passed": passed,
                "pass": ok,
            }
        )
    tasks_passed = sum(1 for row in results if row["pass"])
    return {
        "schema": 1,
        "label": label,
        "results": results,
        "summary": {
            "tasks": len(results),
            "tasks_passed": tasks_passed,
            "task_pass_rate": tasks_passed / len(results) if results else 0.0,
            "checks": 3 * len(results),
            "checks_passed": sum(row["passed"] for row in results),
            "check_pass_rate": 0.0,
        },
    }


@pytest.mark.unit
class TestCompare:
    def test_sign_test_values(self):
        assert eval_run.sign_test(0, 0) == 1.0
        assert eval_run.sign_test(3, 3) == 1.0
        assert eval_run.sign_test(5, 0) == pytest.approx(0.0625)
        assert eval_run.sign_test(6, 0) == pytest.approx(0.03125)
        assert eval_run.sign_test(0, 6) == pytest.approx(0.03125)
        assert eval_run.sign_test(10, 1) == pytest.approx(2 * 12 / 2048)

    def test_minimum_detectable_flips(self):
        assert eval_run.min_discordant_for_significance() == 6
        assert eval_run.min_discordant_for_significance(alpha=0.01) == 8

    def test_compare_paired_counts(self, tmp_path):
        a = {f"t{i}": i < 4 for i in range(10)}  # 4 passes
        b = dict(a)
        for i in range(4, 10):
            b[f"t{i}"] = True  # B fixes six tasks A failed
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        path_a.write_text(json.dumps(make_report("A", a)))
        path_b.write_text(json.dumps(make_report("B", b)))

        result = eval_run.compare(json.loads(path_a.read_text()), json.loads(path_b.read_text()))
        assert result["tasks"] == 10
        assert result["a_pass_rate"] == pytest.approx(0.4)
        assert result["b_pass_rate"] == pytest.approx(1.0)
        assert result["difference"] == pytest.approx(0.6)
        assert result["both_pass"] == 4
        assert result["a_only"] == 0
        assert result["b_only"] == 6
        assert result["discordant"] == 6
        assert result["p_value"] == pytest.approx(0.03125)

    def test_compare_with_mixed_flips_is_not_significant(self):
        a = {"t1": True, "t2": True, "t3": False, "t4": False, "t5": False}
        b = {"t1": False, "t2": True, "t3": True, "t4": True, "t5": False}
        result = eval_run.compare(make_report("A", a), make_report("B", b))
        assert result["a_only"] == 1
        assert result["b_only"] == 2
        assert result["p_value"] > 0.05

    def test_compare_reports_missing_tasks(self):
        result = eval_run.compare(
            make_report("A", {"t1": True, "t2": True}), make_report("B", {"t1": True})
        )
        assert result["tasks"] == 1
        assert result["missing_in_b"] == ["t2"]

    def test_compare_table_mentions_caveat(self):
        table = eval_run.format_compare_table(
            eval_run.compare(make_report("A", {"t1": True}), make_report("B", {"t1": False}))
        )
        assert "sign test p-value" in table
        assert "minimum detectable difference" in table


# --------------------------------------------------------------------------- #
# End to end, needs OpenSCAD
# --------------------------------------------------------------------------- #


@requires_openscad
@pytest.mark.slow
@pytest.mark.integration
class TestReferenceSelfTest:
    def test_reference_solutions_score_100_percent(self):
        report = eval_run.score_directory("", FIXTURES_DIR, use_reference=True, jobs=4)
        failures = [
            (row["id"], [check for check in row["checks"] if not check["pass"]])
            for row in report["results"]
            if not row["pass"]
        ]
        assert failures == [], failures
        assert report["summary"]["task_pass_rate"] == 1.0
        assert report["summary"]["check_pass_rate"] == 1.0

    def test_reference_command_exits_zero(self, capsys):
        assert eval_run.main(["reference"]) == 0
        assert "tasks passed" in capsys.readouterr().out

    def test_interference_detects_an_overlap(self, tmp_path):
        source = tmp_path / "overlap.scad"
        source.write_text(
            "module a() { cube([10, 10, 10]); }\n"
            "module b() { translate([5, 0, 0]) cube([10, 10, 10]); }\n"
        )
        result = scorers.check_interference(
            {"parts": ["a();", "b();"], "max_overlap_mm3": 0.0}, source, tmp_path
        )
        assert not result["pass"]
        assert result["actual"] == pytest.approx(500.0)

    def test_interference_accepts_disjoint_parts(self, tmp_path):
        source = tmp_path / "apart.scad"
        source.write_text(
            "module a() { cube([10, 10, 10]); }\n"
            "module b() { translate([50, 0, 0]) cube([10, 10, 10]); }\n"
        )
        result = scorers.check_interference(
            {"parts": ["a();", "b();"], "max_overlap_mm3": 0.0}, source, tmp_path
        )
        assert result["pass"]
        assert result["actual"] == 0.0

    def test_measure_scad_reports_geometry(self, tmp_path):
        source = tmp_path / "cube.scad"
        source.write_text("cube([10, 20, 30]);\n")
        report = scorers.measure_scad(source)
        assert report["ok"]
        assert report["bbox"] == [10.0, 20.0, 30.0]
        assert report["volume"] == pytest.approx(6000.0)
        assert report["watertight"] is True

    def test_broken_candidate_fails_without_crashing(self, tmp_path):
        task = json.loads((FIXTURES_DIR / "plate_simple" / "task.json").read_text())
        broken = tmp_path / "plate_simple.scad"
        broken.write_text("cube([60, 40,\n")  # syntax error
        result = scorers.score_task(task, broken)
        assert result["pass"] is False
        assert result["passed"] == 0
        assert result["error"]

    def test_wrong_dimension_is_caught(self, tmp_path):
        task = json.loads((FIXTURES_DIR / "plate_simple" / "task.json").read_text())
        candidate = tmp_path / "plate_simple.scad"
        candidate.write_text("cube([62, 40, 5]);\n")
        result = scorers.score_task(task, candidate)
        failed = {check["type"] for check in result["checks"] if not check["pass"]}
        assert failed == {"bbox", "volume"}
