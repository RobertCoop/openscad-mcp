"""Printability facts for a mesh in a stated print orientation.

This module measures. It never scores and never returns a verdict, because
every quantity here carries orientation-, tessellation- and threshold-dependent
uncertainty that a single number would hide:

* Overhang area from facet normals is exact at a flat threshold but
  systematically **under**-reports on curved surfaces in proportion to facet
  size (-51% on a sphere at ``$fn=16``, -3% at 256). So the area is reported
  with a bracket evaluated at plus and minus half the mesh's own facet step.
* Feature thickness by inward ray casting reproduces known wall thicknesses
  exactly, but a boolean operation leaves 0.05 mm tessellation slivers that
  make the bare minimum meaningless. So a distribution, a sample count, a
  method name and the location of the minimum are reported, never a lone
  ``min_wall_thickness_mm``.
* The unsupported span of an overhang is exact where the geometry really is a
  bridge and an over-estimate where it is a cantilever, and nothing here can yet
  tell those apart. So the field is called ``max_unsupported_reach_mm`` and the
  word *bridge* does not appear in any output.
* Support volume is a downward-prism estimate that runs about 20% high against
  a slicer. So the method is named in the field that carries it.

Bed contact is defined relative to the **stated** orientation, not the mesh's
own minimum Z, because that is the whole point: the same part reports 12,958
mm2 of overhang one way up and 20 mm2 the other.

Typical use::

    facts = analyze(load_stl("bracket.scad.stl"), orientation=[180, 0, 0])
    for pose in orientation_candidates(tris):
        print(pose["down"], pose["overhang_area_mm2"], pose["bed_contact_area_mm2"])
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .mesh import Triangle

__all__ = [
    "PrintabilityFacts",
    "analyze",
    "orientation_candidates",
]

Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]
Tri = tuple[Vec3, Vec3, Vec3]
Matrix3 = tuple[Vec3, Vec3, Vec3]

_IDENTITY: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

#: Vertices are welded onto this grid when grouping facets into patches.
_WELD = 1e-6
#: Raster pitch, in mm, for the unsupported-reach distance transform.
_REACH_PITCH = 0.5
#: Hard ceiling on raster cells per patch; the pitch is coarsened to fit.
_MAX_RASTER_CELLS = 400_000
#: Reach is measured for at most this many patches, largest area first.
_MAX_REACH_PATCHES = 20
#: Default number of facets sampled for thickness on a large mesh.
_THICKNESS_SAMPLES = 2000
#: Ray origins are pushed this far below the surface before casting.
_RAY_OFFSET = 1e-4


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------


def _normalize_triangles(tris: Iterable[Any]) -> list[Tri]:
    """Accept :class:`~openscad_mcp.mesh.Triangle` objects or plain vertex triples."""
    out: list[Tri] = []
    for tri in tris:
        if isinstance(tri, Triangle):
            out.append((tri.v0, tri.v1, tri.v2))
        else:
            a, b, c = tri
            out.append((a, b, c))
    return out


def _normal_area(tri: Tri) -> tuple[Vec3, float]:
    """Unit outward normal and area of a triangle; ``((0,0,0), 0.0)`` if degenerate."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0:
        return (0.0, 0.0, 0.0), 0.0
    return (nx / length, ny / length, nz / length), length * 0.5


def _unit(v: Vec3) -> Vec3:
    """Normalise a vector.

    Raises:
        ValueError: If the vector has (near) zero length.
    """
    x, y, z = v
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-15:
        raise ValueError(f"direction must be non-zero, got {v!r}")
    return (x / length, y / length, z / length)


def _matmul3(a: Matrix3, b: Matrix3) -> Matrix3:
    """Multiply two 3x3 matrices."""
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _rotation_axis_angle(axis: Vec3, degrees: float) -> Matrix3:
    """Rotation matrix for ``degrees`` about ``axis`` (Rodrigues)."""
    x, y, z = _unit(axis)
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    k = 1.0 - c
    return (
        (c + x * x * k, x * y * k - z * s, x * z * k + y * s),
        (y * x * k + z * s, c + y * y * k, y * z * k - x * s),
        (z * x * k - y * s, z * y * k + x * s, c + z * z * k),
    )


def _rotation_from_to(a: Vec3, b: Vec3) -> Matrix3:
    """Shortest rotation taking unit vector ``a`` onto unit vector ``b``."""
    ax, ay, az = _unit(a)
    bx, by, bz = _unit(b)
    vx, vy, vz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    c = ax * bx + ay * by + az * bz
    if c < -0.999999:
        # Antiparallel: a half turn about any perpendicular axis.
        perp = (1.0, 0.0, 0.0) if abs(ax) < 0.9 else (0.0, 1.0, 0.0)
        axis = (
            ay * perp[2] - az * perp[1],
            az * perp[0] - ax * perp[2],
            ax * perp[1] - ay * perp[0],
        )
        return _rotation_axis_angle(axis, 180.0)
    k = 1.0 / (1.0 + c)
    return (
        (c + vx * vx * k, vx * vy * k - vz, vx * vz * k + vy),
        (vx * vy * k + vz, c + vy * vy * k, vy * vz * k - vx),
        (vx * vz * k - vy, vy * vz * k + vx, c + vz * vz * k),
    )


def _axis_angle_of(matrix: Matrix3) -> tuple[float, Vec3]:
    """Express a rotation matrix as OpenSCAD's ``rotate(a=..., v=...)`` pair."""
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    cos_a = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    angle = math.degrees(math.acos(cos_a))
    if angle < 1e-9:
        return 0.0, (0.0, 0.0, 1.0)
    if angle > 180.0 - 1e-6:
        # Half turn: the axis is the dominant column of (R + I) / 2.
        best = 0
        diag = [matrix[0][0], matrix[1][1], matrix[2][2]]
        for i in (1, 2):
            if diag[i] > diag[best]:
                best = i
        column = (
            (matrix[0][best] + (1.0 if best == 0 else 0.0)),
            (matrix[1][best] + (1.0 if best == 1 else 0.0)),
            (matrix[2][best] + (1.0 if best == 2 else 0.0)),
        )
        return 180.0, _unit(column)
    s = 2.0 * math.sin(math.radians(angle))
    axis = (
        (matrix[2][1] - matrix[1][2]) / s,
        (matrix[0][2] - matrix[2][0]) / s,
        (matrix[1][0] - matrix[0][1]) / s,
    )
    return angle, _unit(axis)


def _apply(matrix: Matrix3, tris: Sequence[Tri]) -> list[Tri]:
    """Rotate every vertex of a triangle list."""
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = matrix
    out: list[Tri] = []
    for tri in tris:
        moved = []
        for x, y, z in tri:
            moved.append(
                (
                    m00 * x + m01 * y + m02 * z,
                    m10 * x + m11 * y + m12 * z,
                    m20 * x + m21 * y + m22 * z,
                )
            )
        out.append((moved[0], moved[1], moved[2]))
    return out


def _drop_to_bed(tris: Sequence[Tri]) -> tuple[list[Tri], float]:
    """Translate a mesh in Z so its lowest point sits on ``z = 0``.

    Returns:
        ``(triangles, drop_mm)`` where ``drop_mm`` is how far the mesh fell.
    """
    z_min = min(p[2] for tri in tris for p in tri)
    if z_min == 0.0:
        return list(tris), 0.0
    out: list[Tri] = []
    for a, b, c in tris:
        out.append(
            (
                (a[0], a[1], a[2] - z_min),
                (b[0], b[1], b[2] - z_min),
                (c[0], c[1], c[2] - z_min),
            )
        )
    return out, -z_min


def _resolve_orientation(orientation: Any) -> tuple[Matrix3, dict[str, Any]]:
    """Turn any accepted orientation spec into a rotation matrix and a report.

    Accepted forms are ``None`` (leave the mesh as modelled), ``[rx, ry, rz]``
    Euler angles in degrees applied X then Y then Z exactly as OpenSCAD's
    ``rotate()`` does, ``{"a": degrees, "v": [x, y, z]}`` for an axis-angle
    rotation, and ``{"down": [x, y, z]}`` naming the model-space direction that
    should end up pointing at the bed.
    """
    if orientation is None:
        return _IDENTITY, {"input": None, "form": "as-modelled"}

    if isinstance(orientation, dict):
        if "down" in orientation:
            down = tuple(float(v) for v in orientation["down"])
            if len(down) != 3:
                raise ValueError(f"orientation down vector needs 3 values, got {orientation!r}")
            matrix = _rotation_from_to(down, (0.0, 0.0, -1.0))  # type: ignore[arg-type]
            return matrix, {"input": dict(orientation), "form": "down"}
        if "a" in orientation:
            axis = tuple(float(v) for v in orientation.get("v", (0.0, 0.0, 1.0)))
            if len(axis) != 3:
                raise ValueError(f"orientation axis needs 3 values, got {orientation!r}")
            matrix = _rotation_axis_angle(axis, float(orientation["a"]))  # type: ignore[arg-type]
            return matrix, {"input": dict(orientation), "form": "axis-angle"}
        raise ValueError(
            f"orientation dict must have 'down' or 'a', got keys {sorted(orientation)}"
        )

    values = [float(v) for v in orientation]
    if len(values) != 3:
        raise ValueError(f"orientation list must have 3 angles, got {orientation!r}")
    rx, ry, rz = values
    matrix = _matmul3(
        _rotation_axis_angle((0.0, 0.0, 1.0), rz),
        _matmul3(
            _rotation_axis_angle((0.0, 1.0, 0.0), ry),
            _rotation_axis_angle((1.0, 0.0, 0.0), rx),
        ),
    )
    return matrix, {"input": values, "form": "euler-xyz"}


def _orientation_report(matrix: Matrix3, detail: dict[str, Any], drop: float) -> dict[str, Any]:
    """Describe an applied rotation: the OpenSCAD call and which way is down."""
    angle, axis = _axis_angle_of(matrix)
    # The model-space direction that ends up pointing at the bed is R^T * -Z.
    down = (-matrix[2][0], -matrix[2][1], -matrix[2][2])
    return {
        **detail,
        "rotate": {"a": angle, "v": [axis[0], axis[1], axis[2]]},
        "down": [down[0], down[1], down[2]],
        "drop_to_bed_mm": drop,
    }


# ---------------------------------------------------------------------------
# Facet classification
# ---------------------------------------------------------------------------


@dataclass
class _Facets:
    """Per-facet quantities computed once and reused by every measurement."""

    normals: list[Vec3]
    areas: list[float]
    centroids: list[Vec3]
    z_max: list[float]
    down_deg: list[float]  # angle from straight down, 0 = a flat ceiling
    total_area: float
    bed_area: float
    bed_facets: list[int]


def _classify(tris: Sequence[Tri], first_layer_mm: float) -> _Facets:
    """Compute normals, areas, centroids and bed contact for every facet.

    ``down_deg`` is the angle between the facet's outward normal and straight
    down, so 0 is a flat ceiling and 90 is a vertical wall. An overhang at a
    threshold of ``t`` degrees is a downward facet with ``down_deg < 90 - t``.
    """
    normals: list[Vec3] = []
    areas: list[float] = []
    centroids: list[Vec3] = []
    z_max: list[float] = []
    down_deg: list[float] = []
    bed_facets: list[int] = []
    total_area = 0.0
    bed_area = 0.0

    for index, tri in enumerate(tris):
        normal, area = _normal_area(tri)
        normals.append(normal)
        areas.append(area)
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
        centroids.append(((ax + bx + cx) / 3.0, (ay + by + cy) / 3.0, (az + bz + cz) / 3.0))
        top = az if az > bz else bz
        if cz > top:
            top = cz
        z_max.append(top)
        total_area += area

        nz = normal[2]
        if area == 0.0 or nz >= 0.0:
            down_deg.append(180.0)
            continue
        down_deg.append(math.degrees(math.acos(min(1.0, -nz))))
        if top <= first_layer_mm:
            bed_facets.append(index)
            bed_area += area

    return _Facets(
        normals=normals,
        areas=areas,
        centroids=centroids,
        z_max=z_max,
        down_deg=down_deg,
        total_area=total_area,
        bed_area=bed_area,
        bed_facets=bed_facets,
    )


def _overhang_indices(facets: _Facets, overhang_deg: float, first_layer_mm: float) -> list[int]:
    """Facet indices that overhang at this threshold, bed contact excluded."""
    limit = 90.0 - overhang_deg
    out: list[int] = []
    for i, angle in enumerate(facets.down_deg):
        if angle >= limit or facets.areas[i] == 0.0:
            continue
        if facets.z_max[i] <= first_layer_mm:
            continue
        out.append(i)
    return out


def _overhang_area(facets: _Facets, overhang_deg: float, first_layer_mm: float) -> float:
    """Total overhang area at this threshold, bed contact excluded."""
    limit = 90.0 - overhang_deg
    total = 0.0
    for i, angle in enumerate(facets.down_deg):
        if angle >= limit or facets.areas[i] == 0.0:
            continue
        if facets.z_max[i] <= first_layer_mm:
            continue
        total += facets.areas[i]
    return total


def _facet_step_deg(tris: Sequence[Tri], normals: Sequence[Vec3]) -> float:
    """Estimate the mesh's tessellation step from adjacent-facet dihedral angles.

    Angles below 0.05 degrees are coplanar neighbours (the two halves of a
    quad) and angles above 60 are design edges; what is left is the step size
    of whatever curved surfaces the mesh has. The **median** of those is used
    rather than the smallest, because a single near-coplanar pair anywhere on
    the mesh would otherwise collapse the estimate and, with it, the overhang
    bracket. A mesh with no curved surface at all returns 0.0, which is the
    right answer: a faceted box has no tessellation error to bracket.
    """
    inv = 1.0 / _WELD
    edges: dict[tuple[tuple[int, int, int], tuple[int, int, int]], int] = {}
    angles: list[float] = []
    for index, tri in enumerate(tris):
        keys = [(round(p[0] * inv), round(p[1] * inv), round(p[2] * inv)) for p in tri]
        for a, b in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
            edge = (a, b) if a < b else (b, a)
            other = edges.get(edge)
            if other is None:
                edges[edge] = index
                continue
            n1 = normals[other]
            n2 = normals[index]
            dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
            angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
            if 0.05 < angle < 60.0:
                angles.append(angle)
    if not angles:
        return 0.0
    angles.sort()
    return angles[len(angles) // 2]


def _patches(
    tris: Sequence[Tri], facets: _Facets, indices: Sequence[int], min_area: float
) -> list[dict[str, Any]]:
    """Group overhang facets into connected patches by shared welded vertices."""
    if not indices:
        return []
    inv = 1.0 / _WELD
    parent = list(range(len(indices)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    by_vertex: dict[tuple[int, int, int], int] = {}
    for local, facet in enumerate(indices):
        for p in tris[facet]:
            key = (round(p[0] * inv), round(p[1] * inv), round(p[2] * inv))
            seen = by_vertex.get(key)
            if seen is None:
                by_vertex[key] = local
                continue
            ra, rb = find(seen), find(local)
            if ra != rb:
                parent[rb] = ra

    groups: dict[int, list[int]] = {}
    for local in range(len(indices)):
        groups.setdefault(find(local), []).append(local)

    out: list[dict[str, Any]] = []
    for members in groups.values():
        area = math.fsum(facets.areas[indices[m]] for m in members)
        if area < min_area:
            continue
        cx = cy = cz = 0.0
        z_min = math.inf
        worst = 180.0
        for m in members:
            facet = indices[m]
            weight = facets.areas[facet]
            centroid = facets.centroids[facet]
            cx += weight * centroid[0]
            cy += weight * centroid[1]
            cz += weight * centroid[2]
            for p in tris[facet]:
                if p[2] < z_min:
                    z_min = p[2]
            if facets.down_deg[facet] < worst:
                worst = facets.down_deg[facet]
        out.append(
            {
                "facets": [indices[m] for m in members],
                "area_mm2": area,
                "center": [cx / area, cy / area, cz / area],
                "z_min": z_min,
                "worst_deg": worst,
                "facet_count": len(members),
            }
        )
    out.sort(key=lambda p: -p["area_mm2"])
    return out


# ---------------------------------------------------------------------------
# Rasterisation and the unsupported-reach distance transform
# ---------------------------------------------------------------------------


def _point_in_triangle(px: float, py: float, tri2d: tuple[Vec2, Vec2, Vec2]) -> bool:
    """True when a point lies inside or on a 2D triangle."""
    (ax, ay), (bx, by), (cx, cy) = tri2d
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = d1 < 0.0 or d2 < 0.0 or d3 < 0.0
    has_pos = d1 > 0.0 or d2 > 0.0 or d3 > 0.0
    return not (has_neg and has_pos)


def _rasterise(
    tris2d: Sequence[tuple[Vec2, Vec2, Vec2]],
    x0: float,
    y0: float,
    nx: int,
    ny: int,
    pitch: float,
) -> bytearray:
    """Mark the cells whose centre falls inside any of the 2D triangles."""
    mask = bytearray(nx * ny)
    for tri in tris2d:
        tx = [p[0] for p in tri]
        ty = [p[1] for p in tri]
        i0 = max(0, int((min(tx) - x0) / pitch))
        i1 = min(nx - 1, int((max(tx) - x0) / pitch) + 1)
        j0 = max(0, int((min(ty) - y0) / pitch))
        j1 = min(ny - 1, int((max(ty) - y0) / pitch) + 1)
        for j in range(j0, j1 + 1):
            py = y0 + (j + 0.5) * pitch
            row = j * nx
            for i in range(i0, i1 + 1):
                if mask[row + i]:
                    continue
                if _point_in_triangle(x0 + (i + 0.5) * pitch, py, tri):
                    mask[row + i] = 1
    return mask


def _section_segments(tris: Sequence[Tri], z: float) -> list[tuple[Vec2, Vec2]]:
    """Cross-section of a mesh at height ``z`` as unordered 2D segments.

    Edge crossings are interpolated from the lexicographically smaller
    endpoint, so two triangles sharing an edge produce bit-identical points.
    Without that, 30.9% of chains fail to close on real OpenSCAD output.
    """
    segments: list[tuple[Vec2, Vec2]] = []
    for tri in tris:
        z0, z1, z2 = tri[0][2], tri[1][2], tri[2][2]
        if (z0 < z and z1 < z and z2 < z) or (z0 >= z and z1 >= z and z2 >= z):
            continue
        points: list[Vec2] = []
        for k in range(3):
            p = tri[k]
            q = tri[(k + 1) % 3]
            if (p[2] < z) == (q[2] < z):
                continue
            lo, hi = (q, p) if (q[0], q[1], q[2]) < (p[0], p[1], p[2]) else (p, q)
            span = hi[2] - lo[2]
            if span == 0.0:
                continue
            t = (z - lo[2]) / span
            points.append((lo[0] + t * (hi[0] - lo[0]), lo[1] + t * (hi[1] - lo[1])))
        if len(points) == 2 and points[0] != points[1]:
            segments.append((points[0], points[1]))
    return segments


def _spans_at(segments: Sequence[tuple[Vec2, Vec2]], y: float) -> list[tuple[float, float]]:
    """Interior x-spans of a closed cross-section along the scanline ``y``."""
    crossings: list[float] = []
    for (x1, y1), (x2, y2) in segments:
        if (y1 > y) == (y2 > y):
            continue
        crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    if len(crossings) < 2:
        return []
    crossings.sort()
    return [(crossings[i], crossings[i + 1]) for i in range(0, len(crossings) - 1, 2)]


def _chamfer_distance(mask: bytearray, nx: int, ny: int, pitch: float) -> list[float]:
    """Distance from every cell to the nearest set cell, by 3x3 chamfer passes.

    Weights 1 and sqrt(2) make the result exact along the axes and the
    diagonals and at most 8% high in between, which is the safe direction for
    an unsupported span.
    """
    big = float(nx + ny) * 4.0
    dist = [0.0 if mask[i] else big for i in range(nx * ny)]
    diag = math.sqrt(2.0)
    for j in range(ny):
        row = j * nx
        prev = row - nx
        for i in range(nx):
            here = dist[row + i]
            if here == 0.0:
                continue
            if i > 0 and dist[row + i - 1] + 1.0 < here:
                here = dist[row + i - 1] + 1.0
            if j > 0:
                if dist[prev + i] + 1.0 < here:
                    here = dist[prev + i] + 1.0
                if i > 0 and dist[prev + i - 1] + diag < here:
                    here = dist[prev + i - 1] + diag
                if i + 1 < nx and dist[prev + i + 1] + diag < here:
                    here = dist[prev + i + 1] + diag
            dist[row + i] = here
    for j in range(ny - 1, -1, -1):
        row = j * nx
        nxt = row + nx
        for i in range(nx - 1, -1, -1):
            here = dist[row + i]
            if here == 0.0:
                continue
            if i + 1 < nx and dist[row + i + 1] + 1.0 < here:
                here = dist[row + i + 1] + 1.0
            if j + 1 < ny:
                if dist[nxt + i] + 1.0 < here:
                    here = dist[nxt + i] + 1.0
                if i > 0 and dist[nxt + i - 1] + diag < here:
                    here = dist[nxt + i - 1] + diag
                if i + 1 < nx and dist[nxt + i + 1] + diag < here:
                    here = dist[nxt + i + 1] + diag
            dist[row + i] = here
    return [d * pitch for d in dist]


def _patch_reach(
    tris: Sequence[Tri],
    patch: dict[str, Any],
    probe_drop: float,
    section_cache: dict[int, list[tuple[Vec2, Vec2]]],
) -> tuple[float | None, float | None]:
    """Largest distance from the patch to the material that could support it.

    The patch is rasterised in XY. Cells sitting over material one probe depth
    below are seeds of a chamfer distance transform, and the largest distance
    reached by any patch cell is how far the extruder gets from anything solid.

    Returns:
        ``(max_unsupported_reach_mm, max_distance_to_support_mm)``, or
        ``(None, None)`` when nothing below the patch is solid. The reach is
        twice the distance, which is the full span for a patch anchored on
        opposite sides and an over-estimate for a cantilever -- the reason the
        word *bridge* is not used for it.
    """
    tris2d = [
        (
            (tris[f][0][0], tris[f][0][1]),
            (tris[f][1][0], tris[f][1][1]),
            (tris[f][2][0], tris[f][2][1]),
        )
        for f in patch["facets"]
    ]
    xs = [p[0] for tri in tris2d for p in tri]
    ys = [p[1] for tri in tris2d for p in tri]
    pitch = _REACH_PITCH
    margin = 4.0 * pitch
    width = max(xs) - min(xs) + 2.0 * margin
    height = max(ys) - min(ys) + 2.0 * margin
    while (width / pitch + 2.0) * (height / pitch + 2.0) > _MAX_RASTER_CELLS:
        pitch *= 2.0
        margin = 4.0 * pitch
        width = max(xs) - min(xs) + 2.0 * margin
        height = max(ys) - min(ys) + 2.0 * margin
    x0 = min(xs) - margin
    y0 = min(ys) - margin
    nx = max(1, int(width / pitch) + 1)
    ny = max(1, int(height / pitch) + 1)

    z_probe = max(1e-4, patch["z_min"] - probe_drop)
    key = int(round(z_probe / 1e-6))
    segments = section_cache.get(key)
    if segments is None:
        segments = _section_segments(tris, z_probe)
        section_cache[key] = segments

    supported = bytearray(nx * ny)
    any_support = False
    for j in range(ny):
        spans = _spans_at(segments, y0 + (j + 0.5) * pitch)
        if not spans:
            continue
        row = j * nx
        for lo, hi in spans:
            i0 = max(0, int((lo - x0) / pitch))
            i1 = min(nx - 1, int((hi - x0) / pitch) + 1)
            for i in range(i0, i1 + 1):
                x = x0 + (i + 0.5) * pitch
                if lo <= x <= hi:
                    supported[row + i] = 1
                    any_support = True
    if not any_support:
        return None, None

    inside = _rasterise(tris2d, x0, y0, nx, ny, pitch)
    distance = _chamfer_distance(supported, nx, ny, pitch)
    worst = 0.0
    for i in range(nx * ny):
        if inside[i] and not supported[i] and distance[i] > worst:
            worst = distance[i]
    return 2.0 * worst, worst


# ---------------------------------------------------------------------------
# Downward drops (support volume) via an XY bucket grid
# ---------------------------------------------------------------------------


class _XYGrid:
    """Triangles bucketed by their XY footprint, for straight-down queries.

    A support prism only ever looks straight down, so a 2D bucket grid answers
    it with far less work than a 3D ray traversal: a query touches one cell.
    """

    __slots__ = ("cells", "nx", "ny", "pitch", "tris", "x0", "y0")

    def __init__(self, tris: Sequence[Tri], target_per_cell: float = 4.0) -> None:
        self.tris = tris
        xs = [p[0] for tri in tris for p in tri]
        ys = [p[1] for tri in tris for p in tri]
        self.x0, self.y0 = min(xs), min(ys)
        width = max(1e-9, max(xs) - self.x0)
        height = max(1e-9, max(ys) - self.y0)
        cells = max(1.0, len(tris) / target_per_cell)
        self.pitch = max(1e-9, math.sqrt(width * height / cells))
        self.nx = max(1, int(width / self.pitch) + 1)
        self.ny = max(1, int(height / self.pitch) + 1)
        self.cells: dict[int, list[int]] = {}
        for index, tri in enumerate(tris):
            i0 = self._index(min(p[0] for p in tri), 0)
            i1 = self._index(max(p[0] for p in tri), 0)
            j0 = self._index(min(p[1] for p in tri), 1)
            j1 = self._index(max(p[1] for p in tri), 1)
            for j in range(j0, j1 + 1):
                base = j * self.nx
                for i in range(i0, i1 + 1):
                    self.cells.setdefault(base + i, []).append(index)

    def _index(self, value: float, axis: int) -> int:
        origin = self.x0 if axis == 0 else self.y0
        count = self.nx if axis == 0 else self.ny
        return min(count - 1, max(0, int((value - origin) / self.pitch)))

    def candidates(self, x: float, y: float) -> list[int]:
        """Triangle indices whose XY bounding box may cover ``(x, y)``."""
        key = self._index(y, 1) * self.nx + self._index(x, 0)
        return self.cells.get(key, [])

    def drop(self, x: float, y: float, z: float, skip: int) -> float:
        """Distance straight down from ``(x, y, z)`` to the first surface below.

        Only upward-facing facets catch support material. When nothing is
        below, the drop runs to the bed at ``z = 0``.
        """
        best = 0.0
        for index in self.candidates(x, y):
            if index == skip:
                continue
            tri = self.tris[index]
            normal, area = _normal_area(tri)
            if area == 0.0 or normal[2] <= 0.0:
                continue
            tri2d = ((tri[0][0], tri[0][1]), (tri[1][0], tri[1][1]), (tri[2][0], tri[2][1]))
            if not _point_in_triangle(x, y, tri2d):
                continue
            # Plane through the facet: n . (p - a) = 0, solved for z.
            hit = (
                tri[0][2] - (normal[0] * (x - tri[0][0]) + normal[1] * (y - tri[0][1])) / normal[2]
            )
            if hit < z - 1e-9 and hit > best:
                best = hit
        return z - best


def _support_estimate(
    tris: Sequence[Tri], facets: _Facets, indices: Sequence[int], pitch: float
) -> tuple[float, float]:
    """Downward-prism support volume and the footprint it lands on.

    Every overhang facet is given a solid prism from its own area straight down
    to the first upward-facing surface beneath it, or to the bed. That runs
    about 20% high against what a slicer actually deposits, which is why the
    number ships with its method named beside it.
    """
    if not indices:
        return 0.0, 0.0
    grid = _XYGrid(tris)
    volume = 0.0
    for facet in indices:
        cx, cy, cz = facets.centroids[facet]
        drop = grid.drop(cx, cy, cz, facet)
        volume += facets.areas[facet] * (-facets.normals[facet][2]) * drop

    tris2d = [
        (
            (tris[f][0][0], tris[f][0][1]),
            (tris[f][1][0], tris[f][1][1]),
            (tris[f][2][0], tris[f][2][1]),
        )
        for f in indices
    ]
    xs = [p[0] for tri in tris2d for p in tri]
    ys = [p[1] for tri in tris2d for p in tri]
    cell = max(pitch, 1e-6)
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    while (width / cell + 2.0) * (height / cell + 2.0) > _MAX_RASTER_CELLS:
        cell *= 2.0
    nx = max(1, int(width / cell) + 2)
    ny = max(1, int(height / cell) + 2)
    mask = _rasterise(tris2d, min(xs) - cell, min(ys) - cell, nx, ny, cell)
    footprint = sum(mask) * cell * cell
    return volume, footprint


# ---------------------------------------------------------------------------
# Thickness by inward ray casting
# ---------------------------------------------------------------------------


def _sample_facets(areas: Sequence[float], limit: int) -> list[int]:
    """Pick up to ``limit`` facet indices with probability proportional to area.

    Systematic sampling on the cumulative-area axis: deterministic, repeatable,
    and unbiased in area, so a large flat face is not sampled once while a
    thousand sliver facets dominate the distribution.
    """
    count = len(areas)
    if count <= limit:
        return [i for i in range(count) if areas[i] > 0.0]
    total = math.fsum(areas)
    if total <= 0.0:
        return []
    step = total / limit
    picked: list[int] = []
    seen: set[int] = set()
    cumulative = 0.0
    target = step * 0.5
    for index, area in enumerate(areas):
        cumulative += area
        while target < cumulative and len(picked) < limit:
            if index not in seen and area > 0.0:
                seen.add(index)
                picked.append(index)
            target += step
    return picked


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile of an already sorted sequence."""
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _thickness(
    tris: Sequence[Tri],
    facets: _Facets,
    nozzle_mm: float,
    cap_mm: float | None,
    samples: int,
) -> dict[str, Any]:
    """Wall thickness distribution by casting a ray inward from each facet.

    A ray leaves the facet centroid along the inward normal and the first face
    it meets that is turned away from it -- the exit face, ``n . d > 0`` -- ends
    the measurement. That filter and no other: the tempting extra grazing-angle
    filter fixes one mesh (0.157 mm to 3.695 against a true 4.0) and breaks
    another, raising a real 2 mm gear-root wall to 3.0.

    The minimum is reported with its location but is never the headline. A
    boolean operation leaves tessellation slivers, and on real parts those
    bottom out around 0.05 mm with no physical meaning.
    """
    try:
        from .geom import Mesh, ray_cast
    except ImportError:  # pragma: no cover - geom ships alongside this module
        return {
            "method": "ray",
            "available": False,
            "note": "thickness needs openscad_mcp.geom, which could not be imported",
        }

    mesh = Mesh(list(tris))
    indices = _sample_facets(facets.areas, samples)
    values: list[float] = []
    areas: list[float] = []
    locations: list[Vec3] = []
    beyond_cap = 0

    for facet in indices:
        normal = facets.normals[facet]
        area = facets.areas[facet]
        if area == 0.0:
            continue
        direction = (-normal[0], -normal[1], -normal[2])
        cx, cy, cz = facets.centroids[facet]
        origin = (
            cx + direction[0] * _RAY_OFFSET,
            cy + direction[1] * _RAY_OFFSET,
            cz + direction[2] * _RAY_OFFSET,
        )
        found = None
        for hit in ray_cast(mesh, origin, direction, max_distance=cap_mm):
            if (
                hit.normal[0] * direction[0]
                + hit.normal[1] * direction[1]
                + hit.normal[2] * direction[2]
            ) > 0.0:
                found = hit.t
                break
        if found is None:
            beyond_cap += 1
            continue
        values.append(found + _RAY_OFFSET)
        areas.append(area)
        locations.append((cx, cy, cz))

    if not values:
        if cap_mm is not None and beyond_cap:
            note = (
                f"every sampled wall is thicker than the {cap_mm} mm cap, which "
                "is the screening pass answering that nothing is thin"
            )
        else:
            note = "no inward ray found an exit face; the mesh may be open or inverted"
        empty: dict[str, Any] = {
            "method": "ray",
            "available": True,
            "samples": len(indices),
            "measured": 0,
            "note": note,
        }
        if cap_mm is not None:
            empty["capped_at_mm"] = cap_mm
            empty["samples_beyond_cap"] = beyond_cap
        return empty

    order = sorted(range(len(values)), key=lambda i: values[i])
    ordered = [values[i] for i in order]
    thin_area = 0.0
    thin_facets = 0
    for value, area in zip(values, areas, strict=True):
        if value < nozzle_mm:
            thin_area += area
            if area >= 1.0:
                thin_facets += 1

    result: dict[str, Any] = {
        "method": "ray",
        "available": True,
        "min": ordered[0],
        "p01": _percentile(ordered, 0.01),
        "p05": _percentile(ordered, 0.05),
        "median": _percentile(ordered, 0.5),
        "min_location": list(locations[order[0]]),
        "samples": len(indices),
        "measured": len(values),
        "area_below_nozzle_mm2": thin_area,
        "facets_below_nozzle_with_area_ge_1mm2": thin_facets,
        "nozzle_mm": nozzle_mm,
    }
    if cap_mm is not None:
        result["capped_at_mm"] = cap_mm
        result["samples_beyond_cap"] = beyond_cap
    return result


# ---------------------------------------------------------------------------
# Islands (opt-in, needs a layer height)
# ---------------------------------------------------------------------------


def _oriented_segments(tris: Sequence[Tri], z: float) -> list[tuple[Vec2, Vec2]]:
    """Cross-section segments directed so that material lies to their left."""
    segments: list[tuple[Vec2, Vec2]] = []
    for tri in tris:
        z0, z1, z2 = tri[0][2], tri[1][2], tri[2][2]
        if (z0 < z and z1 < z and z2 < z) or (z0 >= z and z1 >= z and z2 >= z):
            continue
        points: list[Vec2] = []
        for k in range(3):
            p = tri[k]
            q = tri[(k + 1) % 3]
            if (p[2] < z) == (q[2] < z):
                continue
            lo, hi = (q, p) if (q[0], q[1], q[2]) < (p[0], p[1], p[2]) else (p, q)
            span = hi[2] - lo[2]
            if span == 0.0:
                continue
            t = (z - lo[2]) / span
            points.append((lo[0] + t * (hi[0] - lo[0]), lo[1] + t * (hi[1] - lo[1])))
        if len(points) != 2 or points[0] == points[1]:
            continue
        normal, area = _normal_area(tri)
        if area == 0.0:
            continue
        # The in-plane direction with material on the left is (-ny, nx).
        dx = points[1][0] - points[0][0]
        dy = points[1][1] - points[0][1]
        if dx * (-normal[1]) + dy * normal[0] < 0.0:
            points = [points[1], points[0]]
        segments.append((points[0], points[1]))
    return segments


def _chain(
    segments: Sequence[tuple[Vec2, Vec2]], tol: float = 1e-7
) -> tuple[list[list[Vec2]], int]:
    """Chain directed segments into loops.

    Returns:
        ``(loops, unclosed)`` where ``unclosed`` counts chains that ran out of
        segments before returning to their start. With canonical edge
        interpolation upstream that count is zero on well-formed meshes.
    """
    inv = 1.0 / tol

    def key(p: Vec2) -> tuple[int, int]:
        return (round(p[0] * inv), round(p[1] * inv))

    by_start: dict[tuple[int, int], list[int]] = {}
    for index, segment in enumerate(segments):
        by_start.setdefault(key(segment[0]), []).append(index)

    used = [False] * len(segments)
    loops: list[list[Vec2]] = []
    unclosed = 0
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        loop = [segments[start][0]]
        current = segments[start][1]
        closed = False
        for _ in range(len(segments) + 1):
            if key(current) == key(loop[0]):
                closed = True
                break
            nxt = None
            for candidate in by_start.get(key(current), ()):
                if not used[candidate]:
                    nxt = candidate
                    break
            if nxt is None:
                break
            used[nxt] = True
            loop.append(current)
            current = segments[nxt][1]
        if not closed:
            unclosed += 1
        if len(loop) >= 3:
            loops.append(loop)
    return loops, unclosed


def _loop_area(loop: Sequence[Vec2]) -> float:
    """Signed shoelace area; positive is an outer contour, negative a hole."""
    total = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def _loop_bbox(loop: Sequence[Vec2]) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of a loop as ``(x_min, y_min, x_max, y_max)``."""
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return (min(xs), min(ys), max(xs), max(ys))


def _point_in_loop(point: Vec2, loop: Sequence[Vec2]) -> bool:
    """Crossing-number point-in-polygon test."""
    x, y = point
    inside = False
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
            inside = not inside
    return inside


def _representative_points(loop: Sequence[Vec2], inset: float = 0.1) -> list[Vec2]:
    """Points that are genuinely inside a loop, including near its boundary.

    The polygon centroid is not one of these: on a C-shaped contour it sits in
    the notch, outside the material. What works is the midpoint of the widest
    interior span on a scanline, plus each edge's midpoint pushed inward, which
    catches a loop whose only support is under one arm.
    """
    points: list[Vec2] = []
    ys = [p[1] for p in loop]
    y = (min(ys) + max(ys)) * 0.5
    crossings: list[float] = []
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    crossings.sort()
    widest = 0.0
    for i in range(0, len(crossings) - 1, 2):
        span = crossings[i + 1] - crossings[i]
        if span > widest:
            widest = span
            points = [((crossings[i] + crossings[i + 1]) * 0.5, y)]

    step = max(1, n // 32)
    for i in range(0, n, step):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        # Material is on the left of a positively wound loop.
        points.append(
            (
                (x1 + x2) * 0.5 - dy / length * inset,
                (y1 + y2) * 0.5 + dx / length * inset,
            )
        )
    return points


def _islands(
    tris: Sequence[Tri], layer_height_mm: float, min_area: float, limit: int = 20
) -> dict[str, Any]:
    """Find contours that appear with nothing beneath them on the layer below.

    A slicer prints those into thin air. The most common cause is a genuinely
    floating body; the second is a tab or a boss whose support the modeller
    forgot. Layer 0 is never an island: the bed is under it.
    """
    z_max = max(p[2] for tri in tris for p in tri)
    layer_count = max(1, int(math.ceil(z_max / layer_height_mm)))
    buckets: dict[int, list[Tri]] = {}
    for tri in tris:
        zs = (tri[0][2], tri[1][2], tri[2][2])
        lo = max(0, int(min(zs) / layer_height_mm) - 1)
        hi = min(layer_count - 1, int(max(zs) / layer_height_mm) + 1)
        for k in range(lo, hi + 1):
            buckets.setdefault(k, []).append(tri)

    found: list[dict[str, Any]] = []
    unclosed_total = 0
    loop_total = 0
    previous: list[tuple[list[Vec2], tuple[float, float, float, float]]] = []
    for k in range(layer_count):
        z = (k + 0.5) * layer_height_mm
        loops, unclosed = _chain(_oriented_segments(buckets.get(k, ()), z))
        unclosed_total += unclosed
        loop_total += len(loops) + unclosed
        current = [(loop, _loop_bbox(loop)) for loop in loops]
        if k > 0:
            for loop in loops:
                area = _loop_area(loop)
                if area <= min_area:
                    continue
                supported = False
                probes = _representative_points(loop)
                for point in probes:
                    crossings = 0
                    for below, (bx0, by0, bx1, by1) in previous:
                        if not (bx0 <= point[0] <= bx1 and by0 <= point[1] <= by1):
                            continue
                        if _point_in_loop(point, below):
                            crossings += 1
                    if crossings % 2 == 1:
                        supported = True
                        break
                if supported:
                    continue
                # Report a point that is genuinely inside the contour, so the
                # centre can be used to navigate straight to the island.
                if probes:
                    centre = [probes[0][0], probes[0][1]]
                else:
                    centre = [
                        sum(p[0] for p in loop) / len(loop),
                        sum(p[1] for p in loop) / len(loop),
                    ]
                found.append({"z": z, "layer": k, "area_mm2": area, "center": centre})
        previous = current

    return {
        "count": len(found),
        "layer_height_mm": layer_height_mm,
        "layer_count": layer_count,
        "unclosed_loop_fraction": (unclosed_total / loop_total) if loop_total else 0.0,
        "list": found[:limit],
        "list_truncated": max(0, len(found) - limit),
    }


# ---------------------------------------------------------------------------
# Public results
# ---------------------------------------------------------------------------


@dataclass
class PrintabilityFacts:
    """Everything measured about a part in one stated orientation.

    Every field is a measurement with a named method. There is deliberately no
    score, no grade and no pass/fail: the thresholds that would produce one
    belong to the printer and the print, not to the geometry.
    """

    orientation: dict[str, Any]
    triangle_count: int
    bbox_min: Vec3
    bbox_max: Vec3
    height_mm: float
    drop_to_bed_mm: float
    total_area_mm2: float
    bed_contact_area_mm2: float
    overhang: dict[str, Any]
    support_estimate: dict[str, Any]
    support_footprint_area_mm2: float
    thickness: dict[str, Any] | None = None
    islands: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self, detailed: bool = True) -> dict[str, Any]:
        """Return a JSON-safe dict.

        Args:
            detailed: When True, every patch and every island is listed at full
                precision. When False, floats are rounded to 4 decimals, the
                patch list is cut to the ten largest and per-patch facet index
                lists are dropped.
        """
        ndigits = None if detailed else 4

        def r(value: float) -> float:
            return value if ndigits is None else round(value, ndigits)

        def rv(vec: Sequence[float]) -> list[float]:
            return [float(v) if ndigits is None else round(float(v), ndigits) for v in vec]

        patches = list(self.overhang.get("patches", []))
        if not detailed:
            patches = patches[:10]
        patch_dicts = []
        for patch in patches:
            entry = {
                "area_mm2": r(patch["area_mm2"]),
                "center": rv(patch["center"]),
                "z_min": r(patch["z_min"]),
                "worst_deg": r(patch["worst_deg"]),
                "facet_count": patch["facet_count"],
            }
            if patch.get("max_unsupported_reach_mm") is not None:
                entry["max_unsupported_reach_mm"] = r(patch["max_unsupported_reach_mm"])
                entry["max_distance_to_support_mm"] = r(patch["max_distance_to_support_mm"])
            patch_dicts.append(entry)

        overhang = {
            "threshold_deg": self.overhang["threshold_deg"],
            "area_mm2": r(self.overhang["area_mm2"]),
            "bracket": [r(v) for v in self.overhang["bracket"]],
            "facet_step_deg": r(self.overhang["facet_step_deg"]),
            "fraction_of_surface": r(self.overhang["fraction_of_surface"]),
            "patch_count": self.overhang["patch_count"],
            "patches": patch_dicts,
        }
        if not detailed and len(self.overhang.get("patches", [])) > len(patch_dicts):
            overhang["patches_omitted"] = len(self.overhang["patches"]) - len(patch_dicts)

        result: dict[str, Any] = {
            "orientation": self.orientation,
            "triangle_count": self.triangle_count,
            "bbox_min": rv(self.bbox_min),
            "bbox_max": rv(self.bbox_max),
            "height_mm": r(self.height_mm),
            "drop_to_bed_mm": r(self.drop_to_bed_mm),
            "total_area_mm2": r(self.total_area_mm2),
            "bed_contact_area_mm2": r(self.bed_contact_area_mm2),
            "overhang": overhang,
            "support_estimate": {
                "volume_mm3": r(self.support_estimate["volume_mm3"]),
                "method": self.support_estimate["method"],
            },
            "support_footprint_area_mm2": r(self.support_footprint_area_mm2),
            "notes": list(self.notes),
        }
        if self.thickness is not None:
            result["thickness"] = {
                k: (r(v) if isinstance(v, float) else v) for k, v in self.thickness.items()
            }
            if "min_location" in self.thickness:
                result["thickness"]["min_location"] = rv(self.thickness["min_location"])
        if self.islands is not None:
            islands = dict(self.islands)
            entries = islands.get("list", [])
            if not detailed:
                islands["list"] = [
                    {
                        "z": r(e["z"]),
                        "layer": e["layer"],
                        "area_mm2": r(e["area_mm2"]),
                        "center": rv(e["center"]),
                    }
                    for e in entries
                ]
                islands["unclosed_loop_fraction"] = r(islands["unclosed_loop_fraction"])
            result["islands"] = islands
        return result


def analyze(
    tris: Iterable[Any],
    orientation: list[float] | dict[str, Any] | None = None,
    overhang_deg: float = 45.0,
    nozzle_mm: float = 0.4,
    layer_height_mm: float | None = None,
    first_layer_mm: float = 0.2,
    min_patch_area_mm2: float = 1.0,
    thickness: bool = True,
    thickness_cap_mm: float | None = None,
    *,
    thickness_samples: int = _THICKNESS_SAMPLES,
) -> PrintabilityFacts:
    """Measure a mesh as it would be printed in one stated orientation.

    The mesh is rotated by ``orientation`` and then dropped so its lowest point
    rests on ``z = 0``, and every number that follows is relative to that pose.
    That matters more than any other choice here: the same plate reports 12,958
    mm2 of overhang printed one way up and 20 mm2 printed the other, and a bed
    contact test against the mesh's own minimum Z would have found neither.

    Args:
        tris: Triangles as vertex triples or :class:`~openscad_mcp.mesh.Triangle`.
        orientation: ``None`` for as-modelled, ``[rx, ry, rz]`` degrees applied
            X then Y then Z like OpenSCAD's ``rotate()``, ``{"a": deg, "v": [...]}``
            for an axis-angle rotation, or ``{"down": [x, y, z]}`` naming the
            model-space direction that should face the bed.
        overhang_deg: Overhang threshold measured from vertical. A facet counts
            when its normal is within ``90 - overhang_deg`` of straight down, so
            45 excludes a true 45 degree chamfer and 44 includes it.
        nozzle_mm: Nozzle diameter, used as the thin-feature threshold and the
            support footprint raster pitch.
        layer_height_mm: Layer height. Islands are only measured when this is
            given: the slicer is the most expensive and most fragile piece here.
        first_layer_mm: Facets whose highest point is within this of the bed
            count as bed contact rather than as overhang.
        min_patch_area_mm2: Overhang patches smaller than this are not reported.
        thickness: Whether to measure wall thickness at all.
        thickness_cap_mm: Cap the inward rays at this distance, turning the
            measurement into a cheap screening pass. Three times the nozzle
            diameter is the useful setting; anything thicker is reported only
            as "beyond the cap".
        thickness_samples: How many facets to sample for thickness, chosen with
            probability proportional to area.

    Returns:
        A :class:`PrintabilityFacts`.

    Raises:
        ValueError: If the mesh is empty or the orientation cannot be read.
    """
    triangles = _normalize_triangles(tris)
    if not triangles:
        raise ValueError("cannot analyse an empty mesh")
    if layer_height_mm is not None and layer_height_mm <= 0:
        raise ValueError(f"layer_height_mm must be positive, got {layer_height_mm}")
    if nozzle_mm <= 0:
        raise ValueError(f"nozzle_mm must be positive, got {nozzle_mm}")

    matrix, detail = _resolve_orientation(orientation)
    if matrix != _IDENTITY:
        triangles = _apply(matrix, triangles)
    triangles, drop = _drop_to_bed(triangles)

    xs = [p[0] for tri in triangles for p in tri]
    ys = [p[1] for tri in triangles for p in tri]
    zs = [p[2] for tri in triangles for p in tri]
    bbox_min = (min(xs), min(ys), min(zs))
    bbox_max = (max(xs), max(ys), max(zs))

    facets = _classify(triangles, first_layer_mm)
    indices = _overhang_indices(facets, overhang_deg, first_layer_mm)
    area = math.fsum(facets.areas[i] for i in indices)

    step = _facet_step_deg(triangles, facets.normals)
    if step > 0.0:
        lo = _overhang_area(facets, overhang_deg + step * 0.5, first_layer_mm)
        hi = _overhang_area(facets, overhang_deg - step * 0.5, first_layer_mm)
        bracket = [min(lo, hi), max(lo, hi)]
    else:
        bracket = [area, area]

    patches = _patches(triangles, facets, indices, min_patch_area_mm2)
    probe = layer_height_mm if layer_height_mm else 0.1
    section_cache: dict[int, list[tuple[Vec2, Vec2]]] = {}
    for rank, patch in enumerate(patches):
        if rank >= _MAX_REACH_PATCHES:
            patch["max_unsupported_reach_mm"] = None
            patch["max_distance_to_support_mm"] = None
            continue
        reach, distance = _patch_reach(triangles, patch, probe, section_cache)
        patch["max_unsupported_reach_mm"] = reach
        patch["max_distance_to_support_mm"] = distance

    support_volume, footprint = _support_estimate(triangles, facets, indices, nozzle_mm)

    notes = [
        "overhang area under-reports on curved surfaces in proportion to facet "
        "size; the bracket is evaluated at plus and minus half the mesh's own facet step",
        "max_unsupported_reach_mm is exact for a patch anchored on opposite "
        "sides and an over-estimate for a cantilever; it is not a bridge length",
    ]
    if len(patches) > _MAX_REACH_PATCHES:
        notes.append(
            f"unsupported reach measured for the {_MAX_REACH_PATCHES} largest "
            f"patches only, of {len(patches)}"
        )

    thickness_result = None
    if thickness:
        thickness_result = _thickness(
            triangles, facets, nozzle_mm, thickness_cap_mm, thickness_samples
        )
        notes.append(
            "the thickness minimum is a single facet and may be a tessellation "
            "sliver; read p01/p05 and min_location before acting on it"
        )

    islands_result = None
    if layer_height_mm is not None:
        islands_result = _islands(
            triangles, layer_height_mm, min_area=max(0.05, nozzle_mm * nozzle_mm)
        )

    return PrintabilityFacts(
        orientation=_orientation_report(matrix, detail, drop),
        triangle_count=len(triangles),
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        height_mm=bbox_max[2] - bbox_min[2],
        drop_to_bed_mm=drop,
        total_area_mm2=facets.total_area,
        bed_contact_area_mm2=facets.bed_area,
        overhang={
            "threshold_deg": overhang_deg,
            "area_mm2": area,
            "bracket": bracket,
            "facet_step_deg": step,
            "fraction_of_surface": (area / facets.total_area) if facets.total_area else 0.0,
            "patch_count": len(patches),
            "patches": patches,
        },
        support_estimate={"volume_mm3": support_volume, "method": "prism"},
        support_footprint_area_mm2=footprint,
        thickness=thickness_result,
        islands=islands_result,
        notes=notes,
    )


def _candidate_directions(count: int) -> list[Vec3]:
    """The canonical build directions: six face normals, then eight corners."""
    dirs: list[Vec3] = [
        (0.0, 0.0, -1.0),
        (0.0, 0.0, 1.0),
        (-1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    s = 1.0 / math.sqrt(3.0)
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                dirs.append((sx * s, sy * s, sz * s))
    if count >= len(dirs):
        return dirs
    return dirs[: max(1, count)]


def orientation_candidates(
    tris: Iterable[Any],
    candidates: int = 14,
    min_bed_contact_mm2: float | None = None,
    overhang_deg: float = 45.0,
    first_layer_mm: float = 0.2,
) -> list[dict[str, Any]]:
    """Score canonical build orientations without re-exporting the model.

    Each candidate names a model-space direction that goes down onto the bed.
    The triangle list is rotated in place, which reproduces a real re-export
    through OpenSCAD to within 0.01 mm2 of overhang area, at a fraction of the
    cost.

    Candidates whose bed contact is degenerate are rejected outright, and this
    is not optional: ranking on overhang area alone promoted a 62 mm bracket
    balanced on a single corner to second place. The floor is the larger of
    ``min_bed_contact_mm2`` and one percent of the best candidate's bed contact.

    There is deliberately no ``best`` key. Height, support volume, surface
    finish and where the seam lands all matter, and only the caller knows which
    of those is binding.

    Args:
        tris: Triangles as vertex triples or :class:`~openscad_mcp.mesh.Triangle`.
        candidates: How many directions to try, from a list of 14: the six axis
            directions first, then the eight body diagonals.
        min_bed_contact_mm2: An absolute floor on bed contact, in mm2.
        overhang_deg: Overhang threshold measured from vertical.
        first_layer_mm: Bed contact band thickness.

    Returns:
        One dict per candidate, accepted ones first and sorted by overhang area
        then height, each with ``down``, ``rotate``, ``overhang_area_mm2``,
        ``bed_contact_area_mm2``, ``height_mm``, ``drop_to_bed_mm``,
        ``support_volume_mm3`` and, when rejected, ``rejected_reason``.

    Raises:
        ValueError: If the mesh is empty.
    """
    triangles = _normalize_triangles(tris)
    if not triangles:
        raise ValueError("cannot rank orientations for an empty mesh")

    rows: list[dict[str, Any]] = []
    for direction in _candidate_directions(candidates):
        matrix = _rotation_from_to(direction, (0.0, 0.0, -1.0))
        rotated, drop = _drop_to_bed(_apply(matrix, triangles))
        facets = _classify(rotated, first_layer_mm)
        indices = _overhang_indices(facets, overhang_deg, first_layer_mm)
        overhang_area = math.fsum(facets.areas[i] for i in indices)
        zs = [p[2] for tri in rotated for p in tri]
        volume, _footprint = _support_estimate(rotated, facets, indices, 1.0)
        angle, axis = _axis_angle_of(matrix)
        rows.append(
            {
                "down": [direction[0], direction[1], direction[2]],
                "rotate": {"a": angle, "v": [axis[0], axis[1], axis[2]]},
                "overhang_area_mm2": overhang_area,
                "bed_contact_area_mm2": facets.bed_area,
                "height_mm": max(zs) - min(zs),
                "drop_to_bed_mm": drop,
                "support_volume_mm3": volume,
            }
        )

    best_contact = max(row["bed_contact_area_mm2"] for row in rows)
    floor = 0.01 * best_contact
    if min_bed_contact_mm2 is not None:
        floor = max(floor, min_bed_contact_mm2)
    for row in rows:
        if row["bed_contact_area_mm2"] < floor:
            row["rejected_reason"] = (
                f"bed contact {row['bed_contact_area_mm2']:.3f} mm2 is below the "
                f"floor of {floor:.3f} mm2; the part would balance on an edge or a corner"
            )

    rows.sort(
        key=lambda row: (
            "rejected_reason" in row,
            row["overhang_area_mm2"],
            row["height_mm"],
        )
    )
    return rows
