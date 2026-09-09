"""Deterministic scorers for the OpenSCAD evaluation harness.

Pure standard-library code.  Nothing here imports ``openscad_mcp``; the harness is
meant to be runnable against any directory of candidate ``.scad`` files without
installing the server package.

The geometry side is intentionally minimal:

* ``parse_stl``      - ASCII and binary STL into a flat triangle list
* ``bounding_box``   - axis aligned min/max
* ``signed_volume``  - divergence theorem over the triangle soup
* ``connected_components`` - union-find over welded triangle edges
* ``is_watertight``  - every undirected edge used exactly twice

Every ``check_*`` function returns a dict with the keys ``type``, ``pass``,
``expected``, ``actual`` and ``detail``.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# OpenSCAD invocation
# --------------------------------------------------------------------------- #

DEFAULT_TIMEOUT = 120
EMPTY_MARKERS = (
    "Current top level object is empty",
    "Top level object is empty",
    "No top level geometry to render",
)
#: Vertices closer than this (mm) are treated as the same point when welding.
WELD_TOLERANCE = 1e-4


class OpenSCADNotFound(RuntimeError):
    """Raised when no ``openscad`` executable can be located."""


def find_openscad() -> str | None:
    """Return the path to the OpenSCAD binary, or ``None`` if unavailable."""
    env = os.environ.get("OPENSCAD_PATH")
    if env:
        if os.path.isfile(env) and os.access(env, os.X_OK):
            return env
        found = shutil.which(env)
        if found:
            return found
    found = shutil.which("openscad")
    if found:
        return found
    for candidate in (
        "/bin/openscad",
        "/usr/bin/openscad",
        "/usr/local/bin/openscad",
        "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def require_openscad() -> str:
    binary = find_openscad()
    if binary is None:
        raise OpenSCADNotFound(
            "OpenSCAD not found. Install it or set OPENSCAD_PATH to the executable."
        )
    return binary


@dataclass
class ExportResult:
    """Outcome of one OpenSCAD export."""

    ok: bool
    path: Path | None
    returncode: int
    stdout: str
    stderr: str

    @property
    def empty_geometry(self) -> bool:
        blob = f"{self.stdout}\n{self.stderr}"
        return any(marker in blob for marker in EMPTY_MARKERS)


def run_openscad(
    scad_path: Path,
    out_path: Path,
    *,
    openscad: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    extra_args: list[str] | None = None,
) -> ExportResult:
    """Run ``openscad -o out_path scad_path`` and report what happened."""
    binary = openscad or require_openscad()
    cmd = [binary, "-o", str(out_path), *(extra_args or []), str(scad_path)]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(scad_path.parent),
        )
    except subprocess.TimeoutExpired:
        return ExportResult(False, None, -1, "", f"OpenSCAD timed out after {timeout}s")
    ok = proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    return ExportResult(ok, out_path if ok else None, proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------- #
# STL parsing and mesh measurement
# --------------------------------------------------------------------------- #

Triangle = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

_VERTEX_RE = re.compile(
    r"vertex\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)",
)


def parse_stl(path: str | Path) -> list[Triangle]:
    """Parse an ASCII or binary STL file into a list of triangles."""
    data = Path(path).read_bytes()
    if _looks_binary(data):
        return _parse_binary_stl(data)
    return _parse_ascii_stl(data.decode("utf-8", errors="replace"))


def parse_stl_text(text: str) -> list[Triangle]:
    """Parse ASCII STL content held in a string (handy for tests)."""
    return _parse_ascii_stl(text)


def _looks_binary(data: bytes) -> bool:
    if len(data) < 84:
        return False
    (count,) = struct.unpack_from("<I", data, 80)
    if 84 + count * 50 == len(data):
        return True
    return not data[:80].lstrip()[:5].lower().startswith(b"solid")


def _parse_binary_stl(data: bytes) -> list[Triangle]:
    (count,) = struct.unpack_from("<I", data, 80)
    tris: list[Triangle] = []
    offset = 84
    for _ in range(count):
        values = struct.unpack_from("<12fH", data, offset)
        offset += 50
        tris.append(
            (
                (values[3], values[4], values[5]),
                (values[6], values[7], values[8]),
                (values[9], values[10], values[11]),
            )
        )
    return tris


def _parse_ascii_stl(text: str) -> list[Triangle]:
    verts = [(float(a), float(b), float(c)) for a, b, c in _VERTEX_RE.findall(text)]
    if len(verts) % 3:
        verts = verts[: len(verts) - len(verts) % 3]
    return [(verts[i], verts[i + 1], verts[i + 2]) for i in range(0, len(verts), 3)]


def bounding_box(
    tris: list[Triangle],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return ``(min_corner, max_corner)`` of the triangle soup."""
    if not tris:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for tri in tris:
        for vertex in tri:
            for axis in range(3):
                value = vertex[axis]
                if value < lo[axis]:
                    lo[axis] = value
                if value > hi[axis]:
                    hi[axis] = value
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def dimensions(tris: list[Triangle]) -> tuple[float, float, float]:
    """Return the ``(width, depth, height)`` of the bounding box."""
    lo, hi = bounding_box(tris)
    return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])


def signed_volume(tris: list[Triangle]) -> float:
    """Signed volume in mm^3 via the divergence theorem (sum of tetrahedra)."""
    total = 0.0
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tris:
        total += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return total / 6.0


def surface_area(tris: list[Triangle]) -> float:
    total = 0.0
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tris:
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        total += 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
    return total


def _weld_key(vertex: tuple[float, float, float]) -> tuple[int, int, int]:
    scale = 1.0 / WELD_TOLERANCE
    return tuple(int(round(component * scale)) for component in vertex)  # type: ignore[return-value]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _edge_map(tris: list[Triangle]) -> tuple[dict[tuple[int, int], list[int]], list[list[int]]]:
    """Map welded undirected edges to the triangles that use them."""
    ids: dict[tuple[int, int, int], int] = {}
    tri_ids: list[list[int]] = []
    for tri in tris:
        indices = []
        for vertex in tri:
            key = _weld_key(vertex)
            indices.append(ids.setdefault(key, len(ids)))
        tri_ids.append(indices)
    edges: dict[tuple[int, int], list[int]] = {}
    for tri_index, (a, b, c) in enumerate(tri_ids):
        for u, v in ((a, b), (b, c), (c, a)):
            if u == v:
                continue
            edges.setdefault((u, v) if u < v else (v, u), []).append(tri_index)
    return edges, tri_ids


def connected_components(tris: list[Triangle]) -> list[list[Triangle]]:
    """Split the mesh into edge-connected shells."""
    if not tris:
        return []
    edges, _ = _edge_map(tris)
    uf = _UnionFind(len(tris))
    for owners in edges.values():
        first = owners[0]
        for other in owners[1:]:
            uf.union(first, other)
    buckets: dict[int, list[Triangle]] = {}
    for index, tri in enumerate(tris):
        buckets.setdefault(uf.find(index), []).append(tri)
    return list(buckets.values())


def is_watertight(tris: list[Triangle]) -> tuple[bool, dict[str, int]]:
    """A mesh is watertight when every welded edge is shared by exactly two faces."""
    if not tris:
        return False, {"edges": 0, "boundary_edges": 0, "nonmanifold_edges": 0}
    edges, _ = _edge_map(tris)
    boundary = sum(1 for owners in edges.values() if len(owners) == 1)
    nonmanifold = sum(1 for owners in edges.values() if len(owners) > 2)
    stats = {
        "edges": len(edges),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
    }
    return boundary == 0 and nonmanifold == 0, stats


@dataclass
class MeshReport:
    """Everything the 3D checks need, computed once per candidate."""

    triangles: int
    dims: tuple[float, float, float]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    volume: float
    watertight: bool
    edge_stats: dict[str, int] = field(default_factory=dict)
    solid_count: int = 0
    cavity_count: int = 0
    component_volumes: list[float] = field(default_factory=list)


def analyze_mesh(tris: list[Triangle]) -> MeshReport:
    """Measure a triangle soup: bbox, volume, watertightness, shells."""
    lo, hi = bounding_box(tris)
    watertight, stats = is_watertight(tris)
    volumes = [signed_volume(component) for component in connected_components(tris)]
    return MeshReport(
        triangles=len(tris),
        dims=(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]),
        bbox_min=lo,
        bbox_max=hi,
        volume=signed_volume(tris),
        watertight=watertight,
        edge_stats=stats,
        solid_count=sum(1 for v in volumes if v > 0),
        cavity_count=sum(1 for v in volumes if v < 0),
        component_volumes=sorted(volumes, reverse=True),
    )


def analyze_stl(path: str | Path) -> MeshReport:
    return analyze_mesh(parse_stl(path))


# --------------------------------------------------------------------------- #
# SVG parsing (2D tasks)
# --------------------------------------------------------------------------- #

_PATH_RE = re.compile(r'<path[^>]*\sd="(.*?)"', re.DOTALL)
_POINT_RE = re.compile(r"(-?[\d.eE+-]+)\s*,\s*(-?[\d.eE+-]+)")


def parse_svg_contours(text: str) -> list[list[tuple[float, float]]]:
    """Extract closed polygons from an OpenSCAD-exported SVG.

    OpenSCAD writes flattened ``M x,y L x,y ... z`` subpaths and flips the Y axis;
    the flip is undone here so coordinates match the model.
    """
    contours: list[list[tuple[float, float]]] = []
    for path_data in _PATH_RE.findall(text):
        for subpath in path_data.split("M")[1:]:
            points = [(float(x), -float(y)) for x, y in _POINT_RE.findall(subpath)]
            if len(points) >= 3:
                contours.append(points)
    return contours


def polygon_area(points: list[tuple[float, float]]) -> float:
    """Signed shoelace area."""
    total = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total / 2.0


@dataclass
class ProfileReport:
    """Measurements of a 2D profile exported to SVG."""

    contours: int
    dims: tuple[float, float]
    bbox_min: tuple[float, float]
    bbox_max: tuple[float, float]
    area: float


def analyze_svg(path: str | Path) -> ProfileReport:
    contours = parse_svg_contours(Path(path).read_text(encoding="utf-8", errors="replace"))
    if not contours:
        return ProfileReport(0, (0.0, 0.0), (0.0, 0.0), (0.0, 0.0), 0.0)
    xs = [x for contour in contours for x, _ in contour]
    ys = [y for contour in contours for _, y in contour]
    area = abs(sum(polygon_area(contour) for contour in contours))
    return ProfileReport(
        contours=len(contours),
        dims=(max(xs) - min(xs), max(ys) - min(ys)),
        bbox_min=(min(xs), min(ys)),
        bbox_max=(max(xs), max(ys)),
        area=area,
    )


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #

CHECK_TYPES = frozenset(
    {
        "bbox",
        "volume",
        "watertight",
        "solid_count",
        "cavity_count",
        "interference",
        "predicate",
        "bbox_2d",
        "area_2d",
    }
)

#: Checks that need the candidate exported to STL.
MESH_CHECK_TYPES = frozenset({"bbox", "volume", "watertight", "solid_count", "cavity_count"})
#: Checks that need the candidate exported to SVG.
PROFILE_CHECK_TYPES = frozenset({"bbox_2d", "area_2d"})


def _result(
    check_type: str,
    passed: bool,
    expected: Any,
    actual: Any,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "type": check_type,
        "pass": bool(passed),
        "expected": expected,
        "actual": actual,
        "detail": detail,
    }


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(float(value), digits)
    if isinstance(value, list | tuple):
        return [_round(item, digits) for item in value]
    return value


def check_bbox(check: dict[str, Any], report: MeshReport) -> dict[str, Any]:
    expected = [float(v) for v in check["expected"]]
    tol = float(check.get("tol_mm", 0.2))
    actual = list(report.dims)
    deltas = [abs(a - e) for a, e in zip(actual, expected, strict=False)]
    passed = len(expected) == 3 and all(d <= tol for d in deltas)
    worst = max(deltas) if deltas else 0.0
    return _result(
        "bbox",
        passed,
        _round(expected),
        _round(actual),
        f"max axis error {worst:.3f} mm (tolerance {tol} mm)",
    )


def check_volume(check: dict[str, Any], report: MeshReport) -> dict[str, Any]:
    expected = float(check["expected"])
    actual = report.volume
    if "tol_mm3" in check:
        tol = float(check["tol_mm3"])
    else:
        tol = abs(expected) * float(check.get("tol_pct", 5.0)) / 100.0
    error = abs(actual - expected)
    pct = (error / abs(expected) * 100.0) if expected else float("inf")
    return _result(
        "volume",
        error <= tol,
        _round(expected, 2),
        _round(actual, 2),
        f"off by {error:.2f} mm^3 ({pct:.2f}%), tolerance {tol:.2f} mm^3",
    )


def check_watertight(check: dict[str, Any], report: MeshReport) -> dict[str, Any]:
    expected = bool(check.get("expected", True))
    stats = report.edge_stats
    detail = (
        f"{stats.get('edges', 0)} edges, {stats.get('boundary_edges', 0)} open, "
        f"{stats.get('nonmanifold_edges', 0)} non-manifold"
    )
    return _result("watertight", report.watertight == expected, expected, report.watertight, detail)


def check_solid_count(check: dict[str, Any], report: MeshReport) -> dict[str, Any]:
    expected = int(check["expected"])
    detail = "component volumes: " + ", ".join(f"{v:.1f}" for v in report.component_volumes)
    return _result(
        "solid_count", report.solid_count == expected, expected, report.solid_count, detail
    )


def check_cavity_count(check: dict[str, Any], report: MeshReport) -> dict[str, Any]:
    expected = int(check["expected"])
    detail = "inward-facing shells count as cavities"
    return _result(
        "cavity_count", report.cavity_count == expected, expected, report.cavity_count, detail
    )


def check_bbox_2d(check: dict[str, Any], report: ProfileReport) -> dict[str, Any]:
    expected = [float(v) for v in check["expected"]]
    tol = float(check.get("tol_mm", 0.2))
    actual = list(report.dims)
    deltas = [abs(a - e) for a, e in zip(actual, expected, strict=False)]
    passed = len(expected) == 2 and all(d <= tol for d in deltas)
    worst = max(deltas) if deltas else 0.0
    return _result(
        "bbox_2d",
        passed,
        _round(expected),
        _round(actual),
        f"max axis error {worst:.3f} mm (tolerance {tol} mm)",
    )


def check_area_2d(check: dict[str, Any], report: ProfileReport) -> dict[str, Any]:
    expected = float(check["expected"])
    tol = (
        float(check["tol_mm2"])
        if "tol_mm2" in check
        else abs(expected) * float(check.get("tol_pct", 5.0)) / 100.0
    )
    error = abs(report.area - expected)
    return _result(
        "area_2d",
        error <= tol,
        _round(expected, 2),
        _round(report.area, 2),
        f"off by {error:.2f} mm^2, tolerance {tol:.2f} mm^2",
    )


def check_interference(
    check: dict[str, Any],
    scad_path: Path,
    workdir: Path,
    *,
    index: int = 0,
    openscad: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Intersect two named parts of the candidate and measure the overlap volume.

    Disjoint parts make OpenSCAD report an empty top level object and exit 1;
    that is the success case and scores as zero overlap.
    """
    parts = list(check.get("parts", []))
    max_overlap = float(check.get("max_overlap_mm3", 0.0))
    if len(parts) < 2:
        return _result("interference", False, max_overlap, None, "needs two part expressions")
    body = "\n".join(f"    {part}" for part in parts)
    harness = workdir / f"interference_{index}.scad"
    harness.write_text(
        f"use <{scad_path.resolve()}>\nintersection() {{\n{body}\n}}\n",
        encoding="utf-8",
    )
    out = workdir / f"interference_{index}.stl"
    export = run_openscad(harness, out, openscad=openscad, timeout=timeout)
    if not export.ok:
        if export.empty_geometry:
            return _result(
                "interference",
                max_overlap >= 0.0,
                max_overlap,
                0.0,
                "parts are disjoint (empty intersection)",
            )
        return _result(
            "interference",
            False,
            max_overlap,
            None,
            f"intersection export failed: {_tail(export.stderr)}",
        )
    overlap = abs(signed_volume(parse_stl(out)))
    return _result(
        "interference",
        overlap <= max_overlap,
        max_overlap,
        _round(overlap, 3),
        f"overlap volume {overlap:.3f} mm^3 between {parts[0]} and {parts[1]}",
    )


_ECHO_RE = re.compile(r'ECHO:\s*"__EVAL__"\s*,\s*(\d+)\s*,\s*(.*)$')


def _parse_echo_values(text: str) -> dict[int, str]:
    values: dict[int, str] = {}
    for line in text.splitlines():
        match = _ECHO_RE.match(line.strip())
        if match:
            values[int(match.group(1))] = match.group(2).strip()
    return values


def _to_number_or_vector(raw: str) -> float | list[float] | None:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        parts = [p.strip() for p in raw[1:-1].split(",") if p.strip()]
        try:
            return [float(p) for p in parts]
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def evaluate_predicates(
    expressions: list[str],
    scad_path: Path,
    workdir: Path,
    *,
    openscad: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict[int, str], str]:
    """Echo each expression from inside the candidate's scope and read them back."""
    if not expressions:
        return {}, ""
    lines = [f"include <{scad_path.resolve()}>"]
    lines += [f'echo("__EVAL__", {i}, {expr});' for i, expr in enumerate(expressions)]
    harness = workdir / "predicates.scad"
    harness.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = workdir / "predicates.echo"
    export = run_openscad(harness, out, openscad=openscad, timeout=timeout)
    text = ""
    if out.exists():
        text = out.read_text(encoding="utf-8", errors="replace")
    text += "\n" + export.stderr + "\n" + export.stdout
    return _parse_echo_values(text), export.stderr


def check_predicate(check: dict[str, Any], values: dict[int, str], index: int) -> dict[str, Any]:
    expression = check.get("scad", "")
    expected = check["expected"]
    raw = values.get(index)
    if raw is None:
        return _result(
            "predicate",
            False,
            expected,
            None,
            f"'{expression}' was not defined or did not evaluate to a value",
        )
    actual = _to_number_or_vector(raw)
    if actual is None:
        return _result("predicate", False, expected, raw, f"'{expression}' is not numeric")
    if "tol_pct" in check and isinstance(expected, int | float):
        tol = abs(float(expected)) * float(check["tol_pct"]) / 100.0
    else:
        tol = float(check.get("tol_mm", 0.01))
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return _result("predicate", False, expected, actual, f"'{expression}' shape mismatch")
        deltas = [abs(a - float(e)) for a, e in zip(actual, expected, strict=False)]
    else:
        if isinstance(actual, list):
            return _result("predicate", False, expected, actual, f"'{expression}' shape mismatch")
        deltas = [abs(actual - float(expected))]
    worst = max(deltas)
    return _result(
        "predicate",
        worst <= tol,
        expected,
        _round(actual, 4),
        f"{expression} = {_round(actual, 4)} (error {worst:.4f}, tolerance {tol})",
    )


def _tail(text: str, limit: int = 240) -> str:
    cleaned = " ".join(text.split())
    return cleaned[-limit:]


# --------------------------------------------------------------------------- #
# Task scoring
# --------------------------------------------------------------------------- #


def validate_task(task: dict[str, Any]) -> list[str]:
    """Return a list of problems with a task definition (empty means valid)."""
    problems: list[str] = []
    for key in ("id", "prompt", "reference_scad", "checks"):
        if not task.get(key):
            problems.append(f"missing '{key}'")
    for check in task.get("checks", []):
        kind = check.get("type")
        if kind not in CHECK_TYPES:
            problems.append(f"unknown check type '{kind}'")
            continue
        needs_expected = {"bbox", "bbox_2d", "volume", "area_2d", "solid_count", "cavity_count"}
        if kind in needs_expected and "expected" not in check:
            problems.append(f"check '{kind}' needs 'expected'")
        if kind == "interference" and len(check.get("parts", [])) < 2:
            problems.append("check 'interference' needs two part expressions")
        if kind == "predicate" and not check.get("scad"):
            problems.append("check 'predicate' needs 'scad'")
    return problems


def measure_scad(
    scad_path: str | Path,
    *,
    openscad: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    two_d: bool = False,
) -> dict[str, Any]:
    """Export a .scad file and report its measurements.

    Used when authoring fixtures: run the reference through this and copy the
    numbers into ``task.json`` instead of guessing them.
    """
    scad_path = Path(scad_path).resolve()
    binary = openscad or require_openscad()
    with tempfile.TemporaryDirectory(prefix="evalmeasure-") as tmp:
        workdir = Path(tmp)
        if two_d:
            export = run_openscad(scad_path, workdir / "out.svg", openscad=binary, timeout=timeout)
            if not export.ok:
                return {"ok": False, "error": _tail(export.stderr)}
            profile = analyze_svg(workdir / "out.svg")
            return {
                "ok": True,
                "contours": profile.contours,
                "bbox_2d": _round(list(profile.dims), 4),
                "area_2d": _round(profile.area, 3),
            }
        export = run_openscad(scad_path, workdir / "out.stl", openscad=binary, timeout=timeout)
        if not export.ok:
            return {"ok": False, "error": _tail(export.stderr)}
        report = analyze_stl(workdir / "out.stl")
        return {
            "ok": True,
            "triangles": report.triangles,
            "bbox": _round(list(report.dims), 4),
            "bbox_min": _round(list(report.bbox_min), 4),
            "bbox_max": _round(list(report.bbox_max), 4),
            "volume": _round(report.volume, 3),
            "watertight": report.watertight,
            "solid_count": report.solid_count,
            "cavity_count": report.cavity_count,
            "component_volumes": _round(report.component_volumes, 3),
        }


def score_task(
    task: dict[str, Any],
    scad_path: str | Path,
    *,
    openscad: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run every check in ``task`` against one candidate ``.scad`` file."""
    checks = task.get("checks", [])
    scad_path = Path(scad_path)
    outcome: dict[str, Any] = {
        "id": task.get("id"),
        "tags": task.get("tags", []),
        "candidate": str(scad_path),
        "checks": [],
        "error": None,
    }
    if not scad_path.is_file():
        outcome["error"] = "candidate file missing"
        outcome["checks"] = [
            _result(check.get("type", "?"), False, check.get("expected"), None, "no candidate file")
            for check in checks
        ]
        return _finalize(outcome)

    binary = openscad or require_openscad()
    scad_path = scad_path.resolve()
    needs_mesh = any(check.get("type") in MESH_CHECK_TYPES for check in checks)
    needs_profile = any(check.get("type") in PROFILE_CHECK_TYPES for check in checks)
    predicate_exprs = [c.get("scad", "") for c in checks if c.get("type") == "predicate"]

    with tempfile.TemporaryDirectory(prefix="evalscore-") as tmp:
        workdir = Path(tmp)
        mesh_report: MeshReport | None = None
        mesh_error = ""
        if needs_mesh:
            export = run_openscad(
                scad_path, workdir / "candidate.stl", openscad=binary, timeout=timeout
            )
            if export.ok:
                mesh_report = analyze_stl(workdir / "candidate.stl")
            else:
                mesh_error = _tail(export.stderr) or f"exit code {export.returncode}"
                outcome["error"] = f"STL export failed: {mesh_error}"

        profile_report: ProfileReport | None = None
        profile_error = ""
        if needs_profile:
            export = run_openscad(
                scad_path, workdir / "candidate.svg", openscad=binary, timeout=timeout
            )
            if export.ok:
                profile_report = analyze_svg(workdir / "candidate.svg")
            else:
                profile_error = _tail(export.stderr) or f"exit code {export.returncode}"
                outcome["error"] = f"SVG export failed: {profile_error}"

        predicate_values: dict[int, str] = {}
        if predicate_exprs:
            predicate_values, _ = evaluate_predicates(
                predicate_exprs, scad_path, workdir, openscad=binary, timeout=timeout
            )

        predicate_index = 0
        interference_index = 0
        for check in checks:
            kind = check.get("type")
            if kind in MESH_CHECK_TYPES:
                if mesh_report is None:
                    outcome["checks"].append(
                        _result(kind, False, check.get("expected"), None, mesh_error)
                    )
                    continue
                outcome["checks"].append(_MESH_CHECKS[kind](check, mesh_report))
            elif kind in PROFILE_CHECK_TYPES:
                if profile_report is None:
                    outcome["checks"].append(
                        _result(kind, False, check.get("expected"), None, profile_error)
                    )
                    continue
                outcome["checks"].append(_PROFILE_CHECKS[kind](check, profile_report))
            elif kind == "predicate":
                outcome["checks"].append(check_predicate(check, predicate_values, predicate_index))
                predicate_index += 1
            elif kind == "interference":
                outcome["checks"].append(
                    check_interference(
                        check,
                        scad_path,
                        workdir,
                        index=interference_index,
                        openscad=binary,
                        timeout=timeout,
                    )
                )
                interference_index += 1
            else:
                outcome["checks"].append(
                    _result(str(kind), False, check.get("expected"), None, "unknown check type")
                )
    return _finalize(outcome)


_MESH_CHECKS = {
    "bbox": check_bbox,
    "volume": check_volume,
    "watertight": check_watertight,
    "solid_count": check_solid_count,
    "cavity_count": check_cavity_count,
}

_PROFILE_CHECKS = {
    "bbox_2d": check_bbox_2d,
    "area_2d": check_area_2d,
}


def _finalize(outcome: dict[str, Any]) -> dict[str, Any]:
    checks = outcome["checks"]
    outcome["total"] = len(checks)
    outcome["passed"] = sum(1 for check in checks if check["pass"])
    outcome["pass"] = bool(checks) and outcome["passed"] == outcome["total"]
    return outcome
