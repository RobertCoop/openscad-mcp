"""Pure-Python mesh and 2D-outline analysis for OpenSCAD exports.

This module is deliberately dependency free (standard library only) so it can run
anywhere the MCP server runs. It answers the questions an AI assistant needs exact
numbers for after exporting a model:

* How big is it (bounding box, dimensions, centre)?
* How much material does it use (volume, and therefore mass)?
* Is it printable (watertight, manifold, no degenerate facets)?
* How many separate bodies does it have, and are any of them internal cavities?

Everything is implemented with plain lists/dicts and tight loops. No per-triangle
objects are allocated during analysis, so a ~90k triangle STL is analysed in well
under a second.

Typical use::

    stats = analyze_stl("part.stl")
    grams = mass_from_volume(stats.volume, MATERIAL_DENSITIES["PLA"])
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "MATERIAL_DENSITIES",
    "ComponentStats",
    "MeshStats",
    "Polygon2DStats",
    "Triangle",
    "analyze_polygons",
    "analyze_stl",
    "analyze_svg",
    "analyze_triangles",
    "load_stl",
    "load_svg_polygons",
    "mass_from_volume",
]

Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]
TriangleTuple = tuple[Vec3, Vec3, Vec3]
Polygon2D = list[Vec2]

# A component whose |signed volume| is below this is treated as neither a solid
# nor a cavity (a flat sheet, a zero-thickness shell, numerical noise).
VOLUME_TOLERANCE = 1e-9

# Twice the area of a triangle must exceed this for the triangle to be considered
# non-degenerate. Degenerate triangles are counted, then excluded from topology.
DEGENERATE_AREA_EPSILON = 1e-14

#: Typical densities in g/cm^3. These are representative values for common
#: filaments/materials, not manufacturer-specific figures; real spools vary by a
#: few percent and infill/print settings dominate the final mass anyway.
MATERIAL_DENSITIES: dict[str, float] = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.21,
    "Nylon": 1.14,
    "PC": 1.20,
    "resin": 1.10,
    "aluminum": 2.70,
    "steel": 7.85,
    "brass": 8.5,
}


@dataclass(frozen=True, slots=True)
class Triangle:
    """A single triangle, as a convenience wrapper around three vertices.

    Analysis routines operate on plain ``((x, y, z), (x, y, z), (x, y, z))``
    tuples for speed; this class exists for callers who want named access and the
    common per-triangle quantities. ``analyze_triangles`` accepts either form.
    """

    v0: Vec3
    v1: Vec3
    v2: Vec3

    @classmethod
    def from_tuple(cls, tri: TriangleTuple) -> Triangle:
        """Build a :class:`Triangle` from a 3-tuple of vertices."""
        return cls(tri[0], tri[1], tri[2])

    def as_tuple(self) -> TriangleTuple:
        """Return the triangle as a plain tuple of three vertices."""
        return (self.v0, self.v1, self.v2)

    @property
    def normal(self) -> Vec3:
        """Unit normal following the right-hand rule; ``(0, 0, 0)`` if degenerate."""
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = self.v0, self.v1, self.v2
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length == 0.0:
            return (0.0, 0.0, 0.0)
        return (nx / length, ny / length, nz / length)

    @property
    def area(self) -> float:
        """Triangle area."""
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = self.v0, self.v1, self.v2
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)

    @property
    def signed_volume(self) -> float:
        """Signed volume of the tetrahedron spanned by the origin and this triangle."""
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = self.v0, self.v1, self.v2
        return (
            ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
        ) / 6.0


# ---------------------------------------------------------------------------
# STL loading
# ---------------------------------------------------------------------------


def _parse_ascii_stl(data: bytes) -> list[TriangleTuple]:
    """Parse an ASCII STL payload into vertex triples."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - decode with errors never raises
        raise ValueError(f"Cannot decode ASCII STL: {exc}") from exc

    triangles: list[TriangleTuple] = []
    append = triangles.append
    pending: list[Vec3] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line.startswith("vertex"):
            continue
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"Malformed vertex on line {lineno}: {raw!r}")
        try:
            pending.append((float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError as exc:
            raise ValueError(f"Malformed vertex on line {lineno}: {raw!r}") from exc
        if len(pending) == 3:
            append((pending[0], pending[1], pending[2]))
            pending = []

    if pending:
        raise ValueError(f"Truncated ASCII STL: trailing facet with {len(pending)} vertex/vertices")
    return triangles


def _parse_binary_stl(data: bytes) -> list[TriangleTuple]:
    """Parse a binary STL payload into vertex triples."""
    if len(data) < 84:
        raise ValueError(f"Binary STL too short: {len(data)} bytes")
    (count,) = struct.unpack_from("<I", data, 80)
    expected = 84 + 50 * count
    if len(data) < expected:
        raise ValueError(
            f"Truncated binary STL: header declares {count} triangles "
            f"({expected} bytes) but file is {len(data)} bytes"
        )

    triangles: list[TriangleTuple] = []
    append = triangles.append
    payload = data[84:expected]
    for rec in struct.iter_unpack("<12fH", payload):
        append(((rec[3], rec[4], rec[5]), (rec[6], rec[7], rec[8]), (rec[9], rec[10], rec[11])))
    return triangles


def _looks_like_binary_stl(data: bytes) -> bool:
    """True when the byte length matches the binary STL triangle-count header."""
    if len(data) < 84:
        return False
    (count,) = struct.unpack_from("<I", data, 80)
    return bool(len(data) == 84 + 50 * count)


def load_stl(path: Path | str) -> list[TriangleTuple]:
    """Load an STL file (ASCII or binary) as a list of vertex triples.

    Detection is deliberately defensive: a binary STL may also begin with the
    bytes ``solid``, so the ASCII path is only taken when a ``facet`` keyword is
    present and the byte length does not match the binary triangle-count header.

    Args:
        path: Path to the ``.stl`` file.

    Returns:
        A list of ``((x, y, z), (x, y, z), (x, y, z))`` triangles, in file order.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is empty, truncated, or otherwise malformed.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    if not data.strip():
        raise ValueError(f"Empty STL file: {file_path}")

    head = data[:1024]
    if head[:5].lower() == b"solid":
        if b"facet" in head.lower() and not _looks_like_binary_stl(data):
            return _parse_ascii_stl(data)
        if _looks_like_binary_stl(data):
            return _parse_binary_stl(data)
        if b"facet" in data.lower():
            return _parse_ascii_stl(data)
        raise ValueError(
            f"File starts with 'solid' but contains no facets and is not a "
            f"valid binary STL: {file_path}"
        )
    return _parse_binary_stl(data)


# ---------------------------------------------------------------------------
# SVG loading (OpenSCAD 2D exports)
# ---------------------------------------------------------------------------

_PATH_D_RE = re.compile(r"<path\b[^>]*?\bd\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.IGNORECASE | re.S)
_TOKEN_RE = re.compile(r"([A-Za-z])|([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)")
_SUPPORTED_COMMANDS = frozenset("MmLlHhVvZz")


def _tokenize_path(d: str) -> list[tuple[str, float]]:
    """Split an SVG path ``d`` attribute into (kind, value) tokens.

    ``kind`` is ``"cmd"`` (value is the command's ordinal) or ``"num"``.
    """
    tokens: list[tuple[str, float]] = []
    for match in _TOKEN_RE.finditer(d):
        letter, number = match.group(1), match.group(2)
        if letter is not None:
            tokens.append(("cmd", float(ord(letter))))
        else:
            tokens.append(("num", float(number)))
    return tokens


def _parse_path_data(d: str) -> list[Polygon2D]:
    """Turn one SVG ``d`` attribute into closed polygons in model coordinates."""
    tokens = _tokenize_path(d)
    total = len(tokens)
    polygons: list[Polygon2D] = []
    current: Polygon2D = []
    cx = cy = 0.0
    start_x = start_y = 0.0
    command = ""
    index = 0

    def flush(points: Polygon2D) -> None:
        """Append ``points`` as a polygon, dropping a duplicated closing point."""
        if len(points) >= 3 and (
            abs(points[0][0] - points[-1][0]) < 1e-12 and abs(points[0][1] - points[-1][1]) < 1e-12
        ):
            points = points[:-1]
        if len(points) >= 3:
            polygons.append(points)

    def numbers(at: int, count: int, cmd: str) -> list[float]:
        """Read ``count`` consecutive numeric tokens starting at ``at``."""
        values: list[float] = []
        for pos in range(at, at + count):
            if pos >= total or tokens[pos][0] != "num":
                raise ValueError(f"Incomplete coordinate list for SVG command {cmd!r}")
            values.append(tokens[pos][1])
        return values

    while index < total:
        kind, value = tokens[index]
        if kind == "cmd":
            command = chr(int(value))
            index += 1
            if command not in _SUPPORTED_COMMANDS:
                raise ValueError(
                    f"Unsupported SVG path command {command!r}; only straight-line "
                    "paths (M/L/H/V/Z) produced by OpenSCAD are handled"
                )
            if command in "Zz":
                flush(current)
                current = []
                cx, cy = start_x, start_y
                continue
        elif not command:
            raise ValueError("SVG path data starts with a coordinate, not a command")

        if command in "Mm":
            x, y = numbers(index, 2, command)
            index += 2
            if command == "m":
                x, y = cx + x, cy + y
            flush(current)
            current = [(x, -y)]
            cx, cy = x, y
            start_x, start_y = x, y
            command = "L" if command == "M" else "l"
        elif command in "Ll":
            x, y = numbers(index, 2, command)
            index += 2
            if command == "l":
                x, y = cx + x, cy + y
            cx, cy = x, y
            current.append((x, -y))
        elif command in "Hh":
            (x,) = numbers(index, 1, command)
            index += 1
            cx = cx + x if command == "h" else x
            current.append((cx, -cy))
        else:  # V or v
            (y,) = numbers(index, 1, command)
            index += 1
            cy = cy + y if command == "v" else y
            current.append((cx, -cy))

    flush(current)
    return polygons


def load_svg_polygons(path: Path | str) -> list[Polygon2D]:
    """Load the polygons from an SVG produced by OpenSCAD's 2D export.

    OpenSCAD writes each 2D shape as ``<path d="M x,y L x,y ... z">`` with the Y
    axis flipped (SVG's Y grows downward). The returned coordinates are
    un-flipped, so they match the model's own coordinate system, and each
    subpath is returned as a closed polygon without a duplicated final point.

    Args:
        path: Path to the ``.svg`` file.

    Returns:
        A list of polygons, each a list of ``(x, y)`` points.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If a path uses curve commands or has malformed coordinates.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    polygons: list[Polygon2D] = []
    for match in _PATH_D_RE.finditer(text):
        d = match.group(2) if match.group(2) is not None else match.group(3)
        if d:
            polygons.extend(_parse_path_data(d))
    return polygons


# ---------------------------------------------------------------------------
# Mesh statistics
# ---------------------------------------------------------------------------


@dataclass
class ComponentStats:
    """Statistics for one connected body within a mesh."""

    index: int
    triangle_count: int
    volume: float
    surface_area: float
    bbox_min: Vec3
    bbox_max: Vec3
    centroid: Vec3
    is_closed: bool

    @property
    def dimensions(self) -> Vec3:
        """Size of this component's bounding box."""
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )

    @property
    def is_cavity(self) -> bool:
        """True when the shell faces inward, i.e. it encloses empty space."""
        return self.volume < -VOLUME_TOLERANCE

    def to_dict(self, ndigits: int | None = None) -> dict[str, Any]:
        """Return a JSON-safe dict, optionally rounding floats to ``ndigits``."""
        return {
            "index": self.index,
            "triangle_count": self.triangle_count,
            "volume": _round(self.volume, ndigits),
            "surface_area": _round(self.surface_area, ndigits),
            "bbox_min": _round_vec(self.bbox_min, ndigits),
            "bbox_max": _round_vec(self.bbox_max, ndigits),
            "dimensions": _round_vec(self.dimensions, ndigits),
            "centroid": _round_vec(self.centroid, ndigits),
            "is_closed": self.is_closed,
            "is_cavity": self.is_cavity,
        }


@dataclass
class MeshStats:
    """Whole-mesh geometry and topology summary."""

    triangle_count: int
    vertex_count: int
    bbox_min: Vec3
    bbox_max: Vec3
    dimensions: Vec3
    center: Vec3
    volume: float
    surface_area: float
    components: list[ComponentStats] = field(default_factory=list)
    solid_count: int = 0
    cavity_count: int = 0
    open_edge_count: int = 0
    non_manifold_edge_count: int = 0
    is_watertight: bool = False
    degenerate_triangle_count: int = 0

    @property
    def is_manifold(self) -> bool:
        """True when the mesh has no open and no non-manifold edges."""
        return self.open_edge_count == 0 and self.non_manifold_edge_count == 0

    def mass(self, density_g_cm3: float) -> float:
        """Mass in grams for this mesh's volume at the given density."""
        return mass_from_volume(self.volume, density_g_cm3)

    def to_dict(self, detailed: bool = True) -> dict[str, Any]:
        """Return a JSON-safe dict summary.

        Args:
            detailed: When True, include every component at full precision. When
                False, round floats to 4 decimals and include at most the three
                largest components (``components_omitted`` reports the rest).
        """
        ndigits = None if detailed else 4
        components = self.components if detailed else self.components[:3]
        result: dict[str, Any] = {
            "triangle_count": self.triangle_count,
            "vertex_count": self.vertex_count,
            "bbox_min": _round_vec(self.bbox_min, ndigits),
            "bbox_max": _round_vec(self.bbox_max, ndigits),
            "dimensions": _round_vec(self.dimensions, ndigits),
            "center": _round_vec(self.center, ndigits),
            "volume": _round(self.volume, ndigits),
            "surface_area": _round(self.surface_area, ndigits),
            "solid_count": self.solid_count,
            "cavity_count": self.cavity_count,
            "component_count": len(self.components),
            "open_edge_count": self.open_edge_count,
            "non_manifold_edge_count": self.non_manifold_edge_count,
            "is_watertight": self.is_watertight,
            "is_manifold": self.is_manifold,
            "degenerate_triangle_count": self.degenerate_triangle_count,
            "components": [c.to_dict(ndigits) for c in components],
        }
        if not detailed:
            result["components_omitted"] = max(0, len(self.components) - len(components))
        return result


def _round(value: float, ndigits: int | None) -> float:
    """Round a float when ``ndigits`` is set, otherwise pass it through."""
    return value if ndigits is None else round(value, ndigits)


def _round_vec(vec: Sequence[float], ndigits: int | None) -> list[float]:
    """Round a coordinate tuple to a JSON-safe list."""
    if ndigits is None:
        return [float(v) for v in vec]
    return [round(float(v), ndigits) for v in vec]


def _as_tuples(tris: Iterable[Any]) -> Iterable[TriangleTuple]:
    """Accept either Triangle objects or plain vertex triples."""
    for tri in tris:
        if isinstance(tri, Triangle):
            yield (tri.v0, tri.v1, tri.v2)
        else:
            yield tri


def analyze_triangles(tris: Iterable[Any], weld_tolerance: float = 1e-6) -> MeshStats:
    """Compute geometry and topology statistics for a triangle soup.

    Vertices are welded onto a grid of ``weld_tolerance`` so that separately
    written but coincident vertices (the normal case in STL) share an index.
    Connected bodies are found by union-find over welded vertices; each body's
    signed volume tells solids (positive) from internal cavities (negative), so a
    hollow part reports the correct net material volume.

    Args:
        tris: Triangles as ``((x, y, z), (x, y, z), (x, y, z))`` tuples or
            :class:`Triangle` instances.
        weld_tolerance: Grid size, in model units, used to merge coincident
            vertices. Must be positive.

    Returns:
        A :class:`MeshStats` with components sorted by ``|volume|`` descending.

    Raises:
        ValueError: If ``weld_tolerance`` is not positive or a triangle does not
            have exactly three 3D vertices.
    """
    if weld_tolerance <= 0:
        raise ValueError(f"weld_tolerance must be positive, got {weld_tolerance}")

    inv = 1.0 / weld_tolerance
    vert_index: dict[tuple[int, int, int], int] = {}
    vx_list: list[float] = []
    vy_list: list[float] = []
    vz_list: list[float] = []
    tri_verts: list[int] = []  # flat, 3 entries per triangle

    triangle_count = 0
    for tri in _as_tuples(tris):
        if len(tri) != 3:
            raise ValueError(f"Triangle must have 3 vertices, got {len(tri)}")
        triangle_count += 1
        for point in tri:
            if len(point) != 3:
                raise ValueError(f"Vertex must have 3 coordinates, got {len(point)}")
            x, y, z = point
            key = (round(x * inv), round(y * inv), round(z * inv))
            idx = vert_index.get(key)
            if idx is None:
                idx = len(vx_list)
                vert_index[key] = idx
                vx_list.append(x)
                vy_list.append(y)
                vz_list.append(z)
            tri_verts.append(idx)

    vertex_count = len(vx_list)
    if triangle_count == 0 or vertex_count == 0:
        zero: Vec3 = (0.0, 0.0, 0.0)
        return MeshStats(
            triangle_count=0,
            vertex_count=0,
            bbox_min=zero,
            bbox_max=zero,
            dimensions=zero,
            center=zero,
            volume=0.0,
            surface_area=0.0,
            components=[],
            is_watertight=False,
        )

    min_x = min(vx_list)
    max_x = max(vx_list)
    min_y = min(vy_list)
    max_y = max(vy_list)
    min_z = min(vz_list)
    max_z = max(vz_list)

    # --- per-triangle geometry, edge topology, union-find over vertices --------
    parent = list(range(vertex_count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    edge_counts: dict[tuple[int, int], int] = {}
    good_tris: list[int] = []  # indices into tri_verts / areas / vols (triangle number)
    areas: list[float] = []
    vols: list[float] = []
    degenerate = 0

    sqrt = math.sqrt
    for t in range(triangle_count):
        base = 3 * t
        i0 = tri_verts[base]
        i1 = tri_verts[base + 1]
        i2 = tri_verts[base + 2]
        if i0 in (i1, i2) or i1 == i2:
            degenerate += 1
            continue

        ax = vx_list[i0]
        ay = vy_list[i0]
        az = vz_list[i0]
        bx = vx_list[i1]
        by = vy_list[i1]
        bz = vz_list[i1]
        cx = vx_list[i2]
        cy = vy_list[i2]
        cz = vz_list[i2]

        ux = bx - ax
        uy = by - ay
        uz = bz - az
        wx = cx - ax
        wy = cy - ay
        wz = cz - az
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        double_area = sqrt(nx * nx + ny * ny + nz * nz)
        if double_area <= DEGENERATE_AREA_EPSILON:
            degenerate += 1
            continue

        good_tris.append(t)
        areas.append(0.5 * double_area)
        vols.append(
            (ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)) / 6.0
        )

        for p, q in ((i0, i1), (i1, i2), (i2, i0)):
            edge = (p, q) if p < q else (q, p)
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
            ra = find(p)
            rb = find(q)
            if ra != rb:
                if ra < rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb

    # --- edge topology totals, per component ---------------------------------
    open_edges = 0
    non_manifold_edges = 0
    bad_root: dict[int, int] = {}
    for (p, _q), count in edge_counts.items():
        if count == 2:
            continue
        if count == 1:
            open_edges += 1
        else:
            non_manifold_edges += 1
        root = find(p)
        bad_root[root] = bad_root.get(root, 0) + 1

    # --- accumulate per component --------------------------------------------
    comp_tris: dict[int, int] = {}
    comp_area: dict[int, float] = {}
    comp_vol: dict[int, float] = {}
    comp_cx: dict[int, float] = {}
    comp_cy: dict[int, float] = {}
    comp_cz: dict[int, float] = {}
    comp_bbox: dict[int, list[float]] = {}
    comp_sum: dict[int, list[float]] = {}  # plain vertex sums for the fallback centroid

    total_area = 0.0
    total_volume = 0.0
    for n, t in enumerate(good_tris):
        base = 3 * t
        i0 = tri_verts[base]
        i1 = tri_verts[base + 1]
        i2 = tri_verts[base + 2]
        root = find(i0)
        area = areas[n]
        vol = vols[n]
        total_area += area
        total_volume += vol

        ax = vx_list[i0]
        ay = vy_list[i0]
        az = vz_list[i0]
        bx = vx_list[i1]
        by = vy_list[i1]
        bz = vz_list[i1]
        cx = vx_list[i2]
        cy = vy_list[i2]
        cz = vz_list[i2]

        if root in comp_tris:
            comp_tris[root] += 1
            comp_area[root] += area
            comp_vol[root] += vol
            # Tetra centroid (origin, a, b, c) is (a + b + c) / 4, weighted by volume.
            comp_cx[root] += vol * (ax + bx + cx) * 0.25
            comp_cy[root] += vol * (ay + by + cy) * 0.25
            comp_cz[root] += vol * (az + bz + cz) * 0.25
            box = comp_bbox[root]
            if ax < box[0]:
                box[0] = ax
            if ay < box[1]:
                box[1] = ay
            if az < box[2]:
                box[2] = az
            if ax > box[3]:
                box[3] = ax
            if ay > box[4]:
                box[4] = ay
            if az > box[5]:
                box[5] = az
            acc = comp_sum[root]
            acc[0] += ax + bx + cx
            acc[1] += ay + by + cy
            acc[2] += az + bz + cz
            acc[3] += 3.0
        else:
            comp_tris[root] = 1
            comp_area[root] = area
            comp_vol[root] = vol
            comp_cx[root] = vol * (ax + bx + cx) * 0.25
            comp_cy[root] = vol * (ay + by + cy) * 0.25
            comp_cz[root] = vol * (az + bz + cz) * 0.25
            comp_bbox[root] = [ax, ay, az, ax, ay, az]
            comp_sum[root] = [ax + bx + cx, ay + by + cy, az + bz + cz, 3.0]

        box = comp_bbox[root]
        for px, py, pz in ((bx, by, bz), (cx, cy, cz)):
            if px < box[0]:
                box[0] = px
            if py < box[1]:
                box[1] = py
            if pz < box[2]:
                box[2] = pz
            if px > box[3]:
                box[3] = px
            if py > box[4]:
                box[4] = py
            if pz > box[5]:
                box[5] = pz

    components: list[ComponentStats] = []
    for root, tri_n in comp_tris.items():
        vol = comp_vol[root]
        box = comp_bbox[root]
        if abs(vol) > VOLUME_TOLERANCE:
            centroid = (comp_cx[root] / vol, comp_cy[root] / vol, comp_cz[root] / vol)
        else:
            acc = comp_sum[root]
            n_pts = acc[3] or 1.0
            centroid = (acc[0] / n_pts, acc[1] / n_pts, acc[2] / n_pts)
        components.append(
            ComponentStats(
                index=0,
                triangle_count=tri_n,
                volume=vol,
                surface_area=comp_area[root],
                bbox_min=(box[0], box[1], box[2]),
                bbox_max=(box[3], box[4], box[5]),
                centroid=centroid,
                is_closed=bad_root.get(root, 0) == 0,
            )
        )

    components.sort(key=lambda c: abs(c.volume), reverse=True)
    for position, comp in enumerate(components):
        comp.index = position

    solid_count = sum(1 for c in components if c.volume > VOLUME_TOLERANCE)
    cavity_count = sum(1 for c in components if c.volume < -VOLUME_TOLERANCE)

    return MeshStats(
        triangle_count=triangle_count,
        vertex_count=vertex_count,
        bbox_min=(min_x, min_y, min_z),
        bbox_max=(max_x, max_y, max_z),
        dimensions=(max_x - min_x, max_y - min_y, max_z - min_z),
        center=((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0),
        volume=total_volume,
        surface_area=total_area,
        components=components,
        solid_count=solid_count,
        cavity_count=cavity_count,
        open_edge_count=open_edges,
        non_manifold_edge_count=non_manifold_edges,
        is_watertight=open_edges == 0,
        degenerate_triangle_count=degenerate,
    )


def analyze_stl(path: Path | str, weld_tolerance: float = 1e-6) -> MeshStats:
    """Load an STL file and analyse it.

    Args:
        path: Path to the ``.stl`` file (ASCII or binary).
        weld_tolerance: Grid size used to merge coincident vertices.

    Returns:
        A :class:`MeshStats` for the file's mesh.
    """
    return analyze_triangles(load_stl(path), weld_tolerance=weld_tolerance)


# ---------------------------------------------------------------------------
# 2D polygon statistics
# ---------------------------------------------------------------------------


@dataclass
class Polygon2DStats:
    """Statistics for a 2D outline, e.g. an OpenSCAD SVG/DXF export."""

    area: float
    perimeter: float
    bbox_min: Vec2
    bbox_max: Vec2
    centroid: Vec2
    polygon_count: int
    hole_count: int

    @property
    def dimensions(self) -> Vec2:
        """Width and height of the bounding box."""
        return (self.bbox_max[0] - self.bbox_min[0], self.bbox_max[1] - self.bbox_min[1])

    def to_dict(self, ndigits: int | None = None) -> dict[str, Any]:
        """Return a JSON-safe dict, optionally rounding floats to ``ndigits``."""
        return {
            "area": _round(self.area, ndigits),
            "perimeter": _round(self.perimeter, ndigits),
            "bbox_min": _round_vec(self.bbox_min, ndigits),
            "bbox_max": _round_vec(self.bbox_max, ndigits),
            "dimensions": _round_vec(self.dimensions, ndigits),
            "centroid": _round_vec(self.centroid, ndigits),
            "polygon_count": self.polygon_count,
            "hole_count": self.hole_count,
        }


def analyze_polygons(polys: Sequence[Polygon2D]) -> Polygon2DStats:
    """Compute area, perimeter, bounding box and centroid for 2D polygons.

    Signed (shoelace) areas are used so that subpaths wound opposite to the
    largest outline are treated as holes and subtracted from the net area. The
    reported area is therefore the material area, not the sum of outlines.

    Args:
        polys: Closed polygons as lists of ``(x, y)`` points.

    Returns:
        A :class:`Polygon2DStats`. Empty input yields all-zero statistics.
    """
    signed_areas: list[float] = []
    perimeter = 0.0
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    sum_x = sum_y = 0.0
    point_count = 0
    cx_acc = cy_acc = 0.0

    for poly in polys:
        n = len(poly)
        if n < 3:
            signed_areas.append(0.0)
            continue
        area2 = 0.0
        cx = cy = 0.0
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            cross = x0 * y1 - x1 * y0
            area2 += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
            perimeter += math.hypot(x1 - x0, y1 - y0)
            if x0 < min_x:
                min_x = x0
            if x0 > max_x:
                max_x = x0
            if y0 < min_y:
                min_y = y0
            if y0 > max_y:
                max_y = y0
            sum_x += x0
            sum_y += y0
            point_count += 1
        signed_areas.append(area2 / 2.0)
        cx_acc += cx / 6.0
        cy_acc += cy / 6.0

    if point_count == 0:
        zero: Vec2 = (0.0, 0.0)
        return Polygon2DStats(0.0, 0.0, zero, zero, zero, 0, 0)

    # Orient against the largest outline so holes come out negative.
    largest = max(signed_areas, key=abs)
    orientation = -1.0 if largest < 0 else 1.0
    net_area = orientation * sum(signed_areas)
    hole_count = sum(1 for a in signed_areas if orientation * a < 0)

    if abs(net_area) > 1e-12:
        centroid = (orientation * cx_acc / net_area, orientation * cy_acc / net_area)
    else:
        centroid = (sum_x / point_count, sum_y / point_count)

    return Polygon2DStats(
        area=net_area,
        perimeter=perimeter,
        bbox_min=(min_x, min_y),
        bbox_max=(max_x, max_y),
        centroid=centroid,
        polygon_count=len([p for p in polys if len(p) >= 3]),
        hole_count=hole_count,
    )


def analyze_svg(path: Path | str) -> Polygon2DStats:
    """Load an OpenSCAD SVG export and analyse its outlines.

    Args:
        path: Path to the ``.svg`` file.

    Returns:
        A :class:`Polygon2DStats` in model coordinates (Y already un-flipped).
    """
    return analyze_polygons(load_svg_polygons(path))


# ---------------------------------------------------------------------------
# Material helpers
# ---------------------------------------------------------------------------


def mass_from_volume(volume_mm3: float, density_g_cm3: float) -> float:
    """Convert a volume in cubic millimetres to a mass in grams.

    Args:
        volume_mm3: Volume in mm^3, as reported by :class:`MeshStats`.
        density_g_cm3: Material density in g/cm^3, e.g. from
            :data:`MATERIAL_DENSITIES`.

    Returns:
        Mass in grams for a fully dense (100% infill) part.

    Raises:
        ValueError: If the density is not positive.
    """
    if density_g_cm3 <= 0:
        raise ValueError(f"density_g_cm3 must be positive, got {density_g_cm3}")
    return volume_mm3 / 1000.0 * density_g_cm3
