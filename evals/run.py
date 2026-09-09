#!/usr/bin/env python3
"""Command line entry point for the OpenSCAD evaluation harness.

    python evals/run.py score --candidates DIR [--fixtures evals/fixtures] [--out results.json]
    python evals/run.py compare A.json B.json
    python evals/run.py reference
    python evals/run.py measure path/to/model.scad [--2d]

``score`` expects ``DIR`` to hold one ``<task_id>.scad`` per task.  ``reference``
scores the bundled reference solutions and is the harness self-test: it must
report 100%.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scorers  # noqa: E402

DEFAULT_FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESULTS_SCHEMA = 1


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #


def load_tasks(fixtures_dir: str | Path = DEFAULT_FIXTURES) -> list[dict[str, Any]]:
    """Load every ``<fixtures>/<task_id>/task.json``, sorted by id."""
    fixtures_dir = Path(fixtures_dir)
    tasks: list[dict[str, Any]] = []
    for task_file in sorted(fixtures_dir.glob("*/task.json")):
        task = json.loads(task_file.read_text(encoding="utf-8"))
        task["_dir"] = str(task_file.parent)
        tasks.append(task)
    tasks.sort(key=lambda item: str(item.get("id")))
    return tasks


def reference_path(task: dict[str, Any]) -> Path:
    return Path(task["_dir"]) / task.get("reference_scad", "reference.scad")


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def score_directory(
    candidates_dir: str | Path,
    fixtures_dir: str | Path = DEFAULT_FIXTURES,
    *,
    openscad: str | None = None,
    timeout: int = scorers.DEFAULT_TIMEOUT,
    jobs: int = 4,
    use_reference: bool = False,
    label: str = "",
) -> dict[str, Any]:
    """Score every task against a directory of ``<task_id>.scad`` candidates."""
    tasks = load_tasks(fixtures_dir)
    binary = openscad or scorers.require_openscad()
    candidates_dir = Path(candidates_dir)

    def one(task: dict[str, Any]) -> dict[str, Any]:
        path = reference_path(task) if use_reference else candidates_dir / f"{task['id']}.scad"
        return scorers.score_task(task, path, openscad=binary, timeout=timeout)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(one, tasks))

    checks_total = sum(result["total"] for result in results)
    checks_passed = sum(result["passed"] for result in results)
    tasks_passed = sum(1 for result in results if result["pass"])
    return {
        "schema": RESULTS_SCHEMA,
        "label": label or ("reference" if use_reference else str(candidates_dir)),
        "candidates": "reference" if use_reference else str(candidates_dir),
        "fixtures": str(fixtures_dir),
        "openscad": binary,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "tasks": len(results),
            "tasks_passed": tasks_passed,
            "task_pass_rate": tasks_passed / len(results) if results else 0.0,
            "checks": checks_total,
            "checks_passed": checks_passed,
            "check_pass_rate": checks_passed / checks_total if checks_total else 0.0,
        },
        "results": results,
    }


def format_score_table(report: dict[str, Any], verbose: bool = False) -> str:
    """Render a scoring report as a plain text table."""
    rows = report["results"]
    width = max([len(str(row["id"])) for row in rows] + [4]) if rows else 4
    lines = [
        f"{'task'.ljust(width)}  checks  result",
        f"{'-' * width}  ------  ------",
    ]
    for row in rows:
        checks = f"{row['passed']}/{row['total']}"
        verdict = "PASS" if row["pass"] else "FAIL"
        lines.append(f"{str(row['id']).ljust(width)}  {checks.center(6)}  {verdict}")
        if verbose or not row["pass"]:
            for check in row["checks"]:
                if check["pass"] and not verbose:
                    continue
                mark = "ok " if check["pass"] else "BAD"
                lines.append(
                    f"{' ' * width}    {mark} {check['type']}: "
                    f"expected {check['expected']}, got {check['actual']}"
                    + (f" - {check['detail']}" if check["detail"] else "")
                )
            if row.get("error") and not row["pass"]:
                lines.append(f"{' ' * width}    !!! {row['error']}")
    summary = report["summary"]
    lines.append("")
    lines.append(
        f"tasks passed {summary['tasks_passed']}/{summary['tasks']} "
        f"({summary['task_pass_rate'] * 100:.1f}%), "
        f"checks passed {summary['checks_passed']}/{summary['checks']} "
        f"({summary['check_pass_rate'] * 100:.1f}%)"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def sign_test(wins_a: int, wins_b: int) -> float:
    """Two-sided exact binomial (sign) test p-value over discordant pairs."""
    n = wins_a + wins_b
    if n == 0:
        return 1.0
    smaller = min(wins_a, wins_b)
    tail = sum(math.comb(n, k) for k in range(smaller + 1)) / (2**n)
    return min(1.0, 2 * tail)


def compare(report_a: dict[str, Any], report_b: dict[str, Any]) -> dict[str, Any]:
    """Pair two scoring reports by task id and run a sign test on the flips."""
    pass_a = {row["id"]: bool(row["pass"]) for row in report_a["results"]}
    pass_b = {row["id"]: bool(row["pass"]) for row in report_b["results"]}
    checks_a = {row["id"]: (row["passed"], row["total"]) for row in report_a["results"]}
    checks_b = {row["id"]: (row["passed"], row["total"]) for row in report_b["results"]}
    common = sorted(set(pass_a) & set(pass_b))

    per_task = []
    only_a = only_b = both = neither = 0
    for task_id in common:
        a, b = pass_a[task_id], pass_b[task_id]
        if a and b:
            both += 1
        elif a and not b:
            only_a += 1
        elif b and not a:
            only_b += 1
        else:
            neither += 1
        per_task.append(
            {
                "id": task_id,
                "a_pass": a,
                "b_pass": b,
                "a_checks": list(checks_a[task_id]),
                "b_checks": list(checks_b[task_id]),
                "flip": ("B only" if b and not a else "A only" if a and not b else ""),
            }
        )

    n = len(common)
    rate_a = sum(1 for t in common if pass_a[t]) / n if n else 0.0
    rate_b = sum(1 for t in common if pass_b[t]) / n if n else 0.0
    return {
        "tasks": n,
        "a_label": report_a.get("label", "A"),
        "b_label": report_b.get("label", "B"),
        "a_pass_rate": rate_a,
        "b_pass_rate": rate_b,
        "difference": rate_b - rate_a,
        "both_pass": both,
        "neither_pass": neither,
        "a_only": only_a,
        "b_only": only_b,
        "discordant": only_a + only_b,
        "p_value": sign_test(only_a, only_b),
        "per_task": per_task,
        "missing_in_a": sorted(set(pass_b) - set(pass_a)),
        "missing_in_b": sorted(set(pass_a) - set(pass_b)),
    }


def min_discordant_for_significance(alpha: float = 0.05, limit: int = 64) -> int:
    """Smallest one-sided run of flips whose two-sided sign test clears ``alpha``."""
    for n in range(1, limit + 1):
        if sign_test(n, 0) <= alpha:
            return n
    return limit


def format_compare_table(result: dict[str, Any]) -> str:
    """Render a comparison, including the small-sample caveat."""
    label_a = result["a_label"]
    label_b = result["b_label"]
    rows = result["per_task"]
    width = max([len(str(row["id"])) for row in rows] + [4]) if rows else 4
    lines = [
        f"A = {label_a}",
        f"B = {label_b}",
        "",
        f"{'task'.ljust(width)}  {'A':>7}  {'B':>7}  flip",
        f"{'-' * width}  {'-' * 7}  {'-' * 7}  ----",
    ]
    for row in rows:
        a_checks = f"{row['a_checks'][0]}/{row['a_checks'][1]}"
        b_checks = f"{row['b_checks'][0]}/{row['b_checks'][1]}"
        a_cell = f"{'P' if row['a_pass'] else 'F'} {a_checks}"
        b_cell = f"{'P' if row['b_pass'] else 'F'} {b_checks}"
        lines.append(f"{str(row['id']).ljust(width)}  {a_cell:>7}  {b_cell:>7}  {row['flip']}")

    needed = min_discordant_for_significance()
    tasks = result["tasks"]
    mde = f"{needed / tasks * 100:.0f} percentage points" if tasks else "an unknown amount"
    lines += [
        "",
        f"tasks compared          {tasks}",
        f"A pass rate             {result['a_pass_rate'] * 100:.1f}%",
        f"B pass rate             {result['b_pass_rate'] * 100:.1f}%",
        f"paired difference (B-A) {result['difference'] * 100:+.1f} percentage points",
        f"both pass / both fail   {result['both_pass']} / {result['neither_pass']}",
        f"A only / B only         {result['a_only']} / {result['b_only']}",
        f"sign test p-value       {result['p_value']:.4f} (two-sided, exact)",
        "",
        "Caveat: this is a small paired sample. Only tasks that flip between A and B",
        f"carry any information, so with {tasks} tasks you need at least {needed} flips all in",
        "the same direction, and none against, before the two-sided sign test reaches",
        f"p < 0.05. That is a minimum detectable difference of roughly {mde}",
        "in task pass rate. Treat anything smaller as a hint to gather more tasks or",
        "more samples per task, not as evidence that a feature helps.",
    ]
    if result["missing_in_a"]:
        lines.append(f"tasks only in B: {', '.join(result['missing_in_a'])}")
    if result["missing_in_b"]:
        lines.append(f"tasks only in A: {', '.join(result['missing_in_b'])}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--fixtures", default=str(DEFAULT_FIXTURES), help="fixtures directory")
        sp.add_argument("--out", default=None, help="write the JSON report here")
        sp.add_argument("--openscad", default=None, help="path to the openscad binary")
        sp.add_argument("--timeout", type=int, default=scorers.DEFAULT_TIMEOUT)
        sp.add_argument("--jobs", type=int, default=4, help="tasks to score in parallel")
        sp.add_argument(
            "--verbose", action="store_true", help="show every check, not just failures"
        )

    score_parser = sub.add_parser("score", help="score a directory of candidate .scad files")
    score_parser.add_argument(
        "--candidates", required=True, help="directory of <task_id>.scad files"
    )
    score_parser.add_argument("--label", default="", help="name for this candidate set")
    common(score_parser)

    ref_parser = sub.add_parser("reference", help="score the bundled reference solutions")
    common(ref_parser)

    compare_parser = sub.add_parser("compare", help="compare two score reports")
    compare_parser.add_argument("a", help="baseline results.json")
    compare_parser.add_argument("b", help="treatment results.json")
    compare_parser.add_argument("--out", default=None, help="write the comparison JSON here")

    measure_parser = sub.add_parser("measure", help="measure one .scad file (for authoring tasks)")
    measure_parser.add_argument("scad", help="path to a .scad file")
    measure_parser.add_argument(
        "--2d", dest="two_d", action="store_true", help="export SVG instead of STL"
    )
    measure_parser.add_argument("--openscad", default=None)
    measure_parser.add_argument("--timeout", type=int, default=scorers.DEFAULT_TIMEOUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "measure":
        report = scorers.measure_scad(
            args.scad, openscad=args.openscad, timeout=args.timeout, two_d=args.two_d
        )
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    if args.command == "compare":
        report_a = json.loads(Path(args.a).read_text(encoding="utf-8"))
        report_b = json.loads(Path(args.b).read_text(encoding="utf-8"))
        result = compare(report_a, report_b)
        print(format_compare_table(result))
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"\nwrote {args.out}")
        return 0

    try:
        report = score_directory(
            getattr(args, "candidates", ""),
            args.fixtures,
            openscad=args.openscad,
            timeout=args.timeout,
            jobs=args.jobs,
            use_reference=args.command == "reference",
            label=getattr(args, "label", ""),
        )
    except scorers.OpenSCADNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_score_table(report, verbose=args.verbose))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    if args.command == "reference":
        return 0 if report["summary"]["tasks_passed"] == report["summary"]["tasks"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
