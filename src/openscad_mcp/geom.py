"""Computational-geometry kernel for assembly, motion and probe reasoning.

This module answers the questions an AI assistant asks about an *assembly* of
exported meshes, rather than about a single part (:mod:`openscad_mcp.mesh`
covers the single-part statistics). Everything here is standard library only,
so it runs wherever the MCP server runs, and every routine works on plain
tuples so meshes can be transformed and re-queried cheaply.

The four primitives, and the reasoning behind them:

* **Distance** -- exact triangle-to-triangle minimum distance over a pair of
  AABB BVHs with best-first traversal. The BVH uses **one triangle per leaf**:
  measured against real parts, leaf size 1 is 14-35x faster for distance
  queries than leaf size 8, because a bigger leaf forces a quadratic block of
  triangle pairs that the bound cannot prune.
* **Point in solid** -- the *generalized winding number* (Jacobson et al.
  2013). It is the primitive: it needs no ray, so it cannot be fooled by a ray
  that grazes a shared edge, and it degrades gracefully on meshes that are not
  watertight (a fractional value flags a local defect instead of flipping the
  answer). Ray parity is used only as an internal bulk fast path; it fails on
  symmetry planes, where a ray runs along coincident faces.
* **Crossing** -- Moller-Trumbore for rays, and an exact triangle-triangle
  intersection *segment* for surface crossings. Two guards matter. First,
  Moller-Trumbore returns a negative ``t`` for a triangle behind the ray
  origin; ``t < -eps`` is split off as "behind" *before* ``|t| <= eps`` is
  treated as "on the surface", because doing it the other way silently
  inverts roughly one answer in ten. Second, flush (coplanar) contact is never
  interference: a witness point is taken at the midpoint of the exact
  intersection segment, pushed along the inward bisector of the two outward
  normals, and must be contained in *both* solids.
* **Contact** -- always from the distance kernel (``d ~ 0``), never from the
  crossing test, with the area obtained by clipping opposing near-parallel
  coplanar triangles against each other in their shared plane.

All lengths are millimetres and all angles degrees, matching OpenSCAD.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mesh import analyze_triangles, load_stl

__all__ = [
    "BVH",
    "DistanceResult",
    "Footprint",
    "Mat4",
    "Mesh",
    "PairRelation",
    "RayHit",
    "Tri",
    "Vec3",
    "aabb_gap",
    "apply",
    "can_ever_touch",
    "classify_pair",
    "classify_point",
    "compose",
    "contact_area",
    "contains",
    "identity",
    "inscribed_polygon_error",
    "min_distance",
    "penetration_depth",
    "polyline_clear",
    "ray_cast",
    "ray_cast_parts",
    "rotation",
    "rz_footprint",
    "segments_for",
    "sweep_rotation",
    "sweep_translation",
    "translation",
    "winding_number",
]

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]
Mat4 = tuple[tuple[float, ...], ...]  # 4x4, row-major

#: Ray parameters and distances below this are treated as zero.
EPS = 1e-9

#: A triangle whose doubled area is below this is degenerate and is skipped by
#: every routine that needs a normal (winding number, crossing, contact area).
#: Distance queries keep degenerate triangles: the edge-edge pass handles them
#: exactly, and dropping them would silently shrink the surface.
DEGENERATE_EPS = 1e-14

#: A point is "on the surface" when its winding number is this close to 0.5.
SURFACE_BAND = 0.05


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _unit(a: Vec3) -> Vec3 | None:
    """Return ``a`` normalised, or None when it is too short to normalise."""
    length = _length(a)
    if length < 1e-14:
        return None
    return (a[0] / length, a[1] / length, a[2] / length)


def _tri_normal(tri: Tri) -> tuple[Vec3 | None, float]:
    """Unit normal (right-hand rule) and area of a triangle.

    Returns ``(None, 0.0)`` for a degenerate triangle.
    """
    n = _cross(_sub(tri[1], tri[0]), _sub(tri[2], tri[0]))
    doubled = _length(n)
    if doubled <= DEGENERATE_EPS:
        return None, 0.0
    return (n[0] / doubled, n[1] / doubled, n[2] / doubled), doubled * 0.5


# ---------------------------------------------------------------------------
# Rigid transforms
# ---------------------------------------------------------------------------


def identity() -> Mat4:
    """The 4x4 identity matrix."""
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def translation(v: Vec3) -> Mat4:
    """Translation by ``v``, matching OpenSCAD's ``translate(v)``."""
    return (
        (1.0, 0.0, 0.0, float(v[0])),
        (0.0, 1.0, 0.0, float(v[1])),
        (0.0, 0.0, 1.0, float(v[2])),
        (0.0, 0.0, 0.0, 1.0),
    )


def rotation(axis: Vec3, degrees: float, center: Vec3 = (0.0, 0.0, 0.0)) -> Mat4:
    """Rotation of ``degrees`` about ``axis`` through ``center``.

    Right-handed, like OpenSCAD's ``rotate(a, v)``. When ``center`` is not the
    origin the result is ``translate(center) * R * translate(-center)``.

    Raises:
        ValueError: If ``axis`` has (near) zero length.
    """
    unit = _unit(axis)
    if unit is None:
        raise ValueError(f"rotation axis must be non-zero, got {axis!r}")
    x, y, z = unit
    rad = math.radians(degrees)
    c = math.cos(rad)
    s = math.sin(rad)
    t = 1.0 - c
    rot: Mat4 = (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    if center == (0.0, 0.0, 0.0):
        return rot
    back: Vec3 = (-center[0], -center[1], -center[2])
    return compose(translation(center), rot, translation(back))


def _matmul(a: Mat4, b: Mat4) -> Mat4:
    rows = []
    for i in range(4):
        ai = a[i]
        rows.append(
            tuple(
                ai[0] * b[0][j] + ai[1] * b[1][j] + ai[2] * b[2][j] + ai[3] * b[3][j]
                for j in range(4)
            )
        )
    return (rows[0], rows[1], rows[2], rows[3])


def compose(*mats: Mat4) -> Mat4:
    """Compose transforms so that the *rightmost* is applied first.

    This is OpenSCAD's nesting order: ``compose(translation(t), rotation(...))``
    is ``translate(t) rotate(...) child``, i.e. the child is rotated and then
    translated. With no arguments the identity is returned.
    """
    result = identity()
    for m in mats:
        result = _matmul(result, m)
    return result


def apply(m: Mat4, p: Vec3) -> Vec3:
    """Apply a 4x4 transform to a point (perspective divide included)."""
    x, y, z = p
    r0, r1, r2, r3 = m
    ox = r0[0] * x + r0[1] * y + r0[2] * z + r0[3]
    oy = r1[0] * x + r1[1] * y + r1[2] * z + r1[3]
    oz = r2[0] * x + r2[1] * y + r2[2] * z + r2[3]
    w = r3[0] * x + r3[1] * y + r3[2] * z + r3[3]
    if w != 1.0 and abs(w) > 1e-14:
        return (ox / w, oy / w, oz / w)
    return (ox, oy, oz)


# ---------------------------------------------------------------------------
# Bounding volume hierarchy
# ---------------------------------------------------------------------------


class BVH:
    """Median-split AABB hierarchy over a triangle list.

    Nodes are stored in parallel lists rather than objects, and children always
    have a higher index than their parent, which makes a bottom-up refit a
    single reverse pass.

    The default leaf size is one triangle. That is not a micro-optimisation:
    on real parts, distance queries with leaf size 1 ran 14-35x faster than
    with leaf size 8, because every extra triangle in a leaf is tested against
    every triangle in the opposing leaf with no chance of pruning.
    """

    __slots__ = (
        "count",
        "hi",
        "left",
        "leaf_size",
        "lo",
        "order",
        "right",
        "root",
        "start",
        "tris",
    )

    def __init__(self, tris: list[Tri], leaf_size: int = 1) -> None:
        self.tris = tris
        self.leaf_size = max(1, leaf_size)
        self.order: list[int] = list(range(len(tris)))
        self.lo: list[Vec3] = []
        self.hi: list[Vec3] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.start: list[int] = []
        self.count: list[int] = []
        if not tris:
            self.root = -1
            return
        boxes: list[tuple[float, float, float, float, float, float]] = []
        cents: list[Vec3] = []
        for a, b, c in tris:
            boxes.append(
                (
                    min(a[0], b[0], c[0]),
                    min(a[1], b[1], c[1]),
                    min(a[2], b[2], c[2]),
                    max(a[0], b[0], c[0]),
                    max(a[1], b[1], c[1]),
                    max(a[2], b[2], c[2]),
                )
            )
            cents.append(
                ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)
            )
        self.root = self._build(0, len(tris), boxes, cents)

    def _build(
        self,
        s: int,
        e: int,
        boxes: list[tuple[float, float, float, float, float, float]],
        cents: list[Vec3],
    ) -> int:
        order = self.order
        lo_x = lo_y = lo_z = math.inf
        hi_x = hi_y = hi_z = -math.inf
        for i in range(s, e):
            bx = boxes[order[i]]
            if bx[0] < lo_x:
                lo_x = bx[0]
            if bx[1] < lo_y:
                lo_y = bx[1]
            if bx[2] < lo_z:
                lo_z = bx[2]
            if bx[3] > hi_x:
                hi_x = bx[3]
            if bx[4] > hi_y:
                hi_y = bx[4]
            if bx[5] > hi_z:
                hi_z = bx[5]
        idx = len(self.lo)
        self.lo.append((lo_x, lo_y, lo_z))
        self.hi.append((hi_x, hi_y, hi_z))
        self.left.append(-1)
        self.right.append(-1)
        self.start.append(s)
        self.count.append(e - s)
        if e - s <= self.leaf_size:
            return idx
        ext = (hi_x - lo_x, hi_y - lo_y, hi_z - lo_z)
        axis = ext.index(max(ext))
        mid = (s + e) // 2
        seg = order[s:e]
        seg.sort(key=lambda i: cents[i][axis])
        order[s:e] = seg
        self.left[idx] = self._build(s, mid, boxes, cents)
        self.right[idx] = self._build(mid, e, boxes, cents)
        return idx

    def refit(self, tris: list[Tri]) -> BVH:
        """Return a BVH over ``tris`` reusing this tree's topology.

        Only valid when ``tris`` is the same triangle list under a rigid
        transform, which is exactly the sweep case. Bounds are recomputed
        exactly (a parent is the union of its children), so the result is a
        correct BVH; only the split *quality* degrades as the part rotates
        away from the orientation the tree was built in. Building this way is
        O(n) with no sorting, which is what makes a many-step sweep affordable.
        """
        if len(tris) != len(self.tris):
            raise ValueError("refit needs a triangle list of the same length")
        clone = BVH.__new__(BVH)
        clone.tris = tris
        clone.leaf_size = self.leaf_size
        clone.order = self.order
        clone.left = self.left
        clone.right = self.right
        clone.start = self.start
        clone.count = self.count
        clone.root = self.root
        n_nodes = len(self.lo)
        clone.lo = [(0.0, 0.0, 0.0)] * n_nodes
        clone.hi = [(0.0, 0.0, 0.0)] * n_nodes
        if self.root < 0:
            return clone
        order = self.order
        for node in range(n_nodes - 1, -1, -1):
            li = self.left[node]
            if li < 0:
                lo_x = lo_y = lo_z = math.inf
                hi_x = hi_y = hi_z = -math.inf
                s = self.start[node]
                for i in range(s, s + self.count[node]):
                    for p in tris[order[i]]:
                        if p[0] < lo_x:
                            lo_x = p[0]
                        if p[1] < lo_y:
                            lo_y = p[1]
                        if p[2] < lo_z:
                            lo_z = p[2]
                        if p[0] > hi_x:
                            hi_x = p[0]
                        if p[1] > hi_y:
                            hi_y = p[1]
                        if p[2] > hi_z:
                            hi_z = p[2]
            else:
                ri = self.right[node]
                a_lo, a_hi = clone.lo[li], clone.hi[li]
                b_lo, b_hi = clone.lo[ri], clone.hi[ri]
                lo_x = min(a_lo[0], b_lo[0])
                lo_y = min(a_lo[1], b_lo[1])
                lo_z = min(a_lo[2], b_lo[2])
                hi_x = max(a_hi[0], b_hi[0])
                hi_y = max(a_hi[1], b_hi[1])
                hi_z = max(a_hi[2], b_hi[2])
            clone.lo[node] = (lo_x, lo_y, lo_z)
            clone.hi[node] = (hi_x, hi_y, hi_z)
        return clone

    def _gap2(self, i: int, other: BVH, j: int) -> float:
        """Squared gap between node ``i`` of this BVH and node ``j`` of ``other``."""
        a_lo, a_hi = self.lo[i], self.hi[i]
        b_lo, b_hi = other.lo[j], other.hi[j]
        total = 0.0
        for k in range(3):
            d = b_lo[k] - a_hi[k]
            if d < 0.0:
                d = a_lo[k] - b_hi[k]
            if d > 0.0:
                total += d * d
        return total

    def _point_gap2(self, i: int, p: Vec3) -> float:
        """Squared distance from ``p`` to node ``i``'s box (0 when inside)."""
        lo, hi = self.lo[i], self.hi[i]
        total = 0.0
        for k in range(3):
            if p[k] < lo[k]:
                d = lo[k] - p[k]
                total += d * d
            elif p[k] > hi[k]:
                d = p[k] - hi[k]
                total += d * d
        return total


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------


class Mesh:
    """A triangle soup with lazily built acceleration structures.

    A mesh is immutable in practice: :meth:`transformed` returns a new mesh
    rather than moving this one, so a cached BVH and watertightness verdict
    stay valid for the lifetime of the object.
    """

    __slots__ = ("_bbox", "_bvh", "_solid", "_vertices", "_watertight", "name", "triangles")

    def __init__(self, tris: list[Tri], name: str = "") -> None:
        self.triangles: list[Tri] = list(tris)
        self.name = name
        self._bbox: tuple[Vec3, Vec3] | None = None
        self._bvh: BVH | None = None
        self._watertight: bool | None = None
        self._vertices: list[Vec3] | None = None
        self._solid: list[Tri] | None = None

    @classmethod
    def from_stl(cls, path: Path | str, name: str = "") -> Mesh:
        """Load an STL (ASCII or binary) with :func:`openscad_mcp.mesh.load_stl`."""
        tris = load_stl(path)
        return cls(tris, name or Path(path).stem)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Mesh(name={self.name!r}, triangles={len(self.triangles)})"

    @property
    def triangle_count(self) -> int:
        """Number of triangles, including any degenerate ones."""
        return len(self.triangles)

    def _compute_bbox(self) -> tuple[Vec3, Vec3]:
        if self._bbox is None:
            if not self.triangles:
                self._bbox = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
            else:
                lo_x = lo_y = lo_z = math.inf
                hi_x = hi_y = hi_z = -math.inf
                for tri in self.triangles:
                    for p in tri:
                        if p[0] < lo_x:
                            lo_x = p[0]
                        if p[1] < lo_y:
                            lo_y = p[1]
                        if p[2] < lo_z:
                            lo_z = p[2]
                        if p[0] > hi_x:
                            hi_x = p[0]
                        if p[1] > hi_y:
                            hi_y = p[1]
                        if p[2] > hi_z:
                            hi_z = p[2]
                self._bbox = ((lo_x, lo_y, lo_z), (hi_x, hi_y, hi_z))
        return self._bbox

    @property
    def bbox_min(self) -> Vec3:
        """Minimum corner of the axis-aligned bounding box."""
        return self._compute_bbox()[0]

    @property
    def bbox_max(self) -> Vec3:
        """Maximum corner of the axis-aligned bounding box."""
        return self._compute_bbox()[1]

    def bvh(self) -> BVH:
        """The mesh's BVH, built on first use (leaf size 1) and cached."""
        if self._bvh is None:
            self._bvh = BVH(self.triangles, leaf_size=1)
        return self._bvh

    def is_watertight(self) -> bool:
        """True when the edge census finds no open edge.

        Delegates to :func:`openscad_mcp.mesh.analyze_triangles` so there is one
        definition of watertight in the codebase. Cached, because the census
        welds every vertex and is the most expensive query here.
        """
        if self._watertight is None:
            if not self.triangles:
                self._watertight = False
            else:
                self._watertight = analyze_triangles(self.triangles).is_watertight
        return self._watertight

    def solid_triangles(self) -> list[Tri]:
        """Triangles with a non-zero area, cached.

        Degenerate facets are kept in :attr:`triangles` so indices still match
        the file they were loaded from, but anything that needs a normal has to
        skip them. Returns the original list when there is nothing to skip.
        """
        if self._solid is None:
            good = [t for t in self.triangles if _tri_normal(t)[1] > 0.0]
            self._solid = self.triangles if len(good) == len(self.triangles) else good
        return self._solid

    def vertices(self) -> list[Vec3]:
        """Unique vertices (exact tuple equality), cached."""
        if self._vertices is None:
            seen: dict[Vec3, None] = {}
            for tri in self.triangles:
                for p in tri:
                    seen[p] = None
            self._vertices = list(seen)
        return self._vertices

    def transformed(self, m: Mat4) -> Mesh:
        """Return a copy with every vertex through ``m``.

        If this mesh's BVH is already built, the copy gets a refit of it for
        free, so sweeping a part through many poses does not rebuild the tree.
        """
        r0, r1, r2, r3 = m
        a, b, c, d = r0
        e, f, g, h = r1
        i, j, k, ln = r2
        affine = r3 == (0.0, 0.0, 0.0, 1.0)
        out: list[Tri] = []
        for tri in self.triangles:
            moved: list[Vec3] = []
            for p in tri:
                x, y, z = p
                if affine:
                    moved.append(
                        (
                            a * x + b * y + c * z + d,
                            e * x + f * y + g * z + h,
                            i * x + j * y + k * z + ln,
                        )
                    )
                else:
                    moved.append(apply(m, p))
            out.append((moved[0], moved[1], moved[2]))
        clone = Mesh(out, self.name)
        clone._watertight = self._watertight
        if self._bvh is not None:
            clone._bvh = self._bvh.refit(out)
        return clone


# ---------------------------------------------------------------------------
# Exact triangle-triangle distance
# ---------------------------------------------------------------------------


def _seg_seg(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3) -> tuple[float, Vec3, Vec3]:
    """Squared distance between two segments, with the closest points.

    Ericson, *Real-Time Collision Detection*, section 5.1.9.
    """
    d1 = _sub(q1, p1)
    d2 = _sub(q2, p2)
    r = _sub(p1, p2)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    f = _dot(d2, r)
    tiny = 1e-18
    if a <= tiny and e <= tiny:
        return _dot(r, r), p1, p2
    if a <= tiny:
        s = 0.0
        t = min(1.0, max(0.0, f / e))
    else:
        c = _dot(d1, r)
        if e <= tiny:
            t = 0.0
            s = min(1.0, max(0.0, -c / a))
        else:
            b = _dot(d1, d2)
            denom = a * e - b * b
            s = min(1.0, max(0.0, (b * f - c * e) / denom)) if denom != 0.0 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = min(1.0, max(0.0, -c / a))
            elif t > 1.0:
                t = 1.0
                s = min(1.0, max(0.0, (b - c) / a))
    c1 = _add(p1, _mul(d1, s))
    c2 = _add(p2, _mul(d2, t))
    w = _sub(c1, c2)
    return _dot(w, w), c1, c2


def _closest_point_on_tri(p: Vec3, tri: Tri) -> Vec3:
    """Closest point to ``p`` on a triangle (Ericson, section 5.1.5)."""
    a, b, c = tri
    ab = _sub(b, a)
    ac = _sub(c, a)
    ap = _sub(p, a)
    d1 = _dot(ab, ap)
    d2 = _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a
    bp = _sub(p, b)
    d3 = _dot(ab, bp)
    d4 = _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3) if (d1 - d3) != 0.0 else 0.0
        return _add(a, _mul(ab, v))
    cp = _sub(p, c)
    d5 = _dot(ab, cp)
    d6 = _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6) if (d2 - d6) != 0.0 else 0.0
        return _add(a, _mul(ac, w))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        den = (d4 - d3) + (d5 - d6)
        w = (d4 - d3) / den if den != 0.0 else 0.0
        return _add(b, _mul(_sub(c, b), w))
    den = va + vb + vc
    if den == 0.0:
        return a
    v = vb / den
    w = vc / den
    return _add(a, _add(_mul(ab, v), _mul(ac, w)))


def _boxes_overlap(t1: Tri, t2: Tri) -> bool:
    """Do two triangles' axis-aligned boxes overlap? A necessary condition for
    them to intersect, and six comparisons cheaper than finding out properly."""
    for k in range(3):
        if min(t1[0][k], t1[1][k], t1[2][k]) > max(t2[0][k], t2[1][k], t2[2][k]):
            return False
        if min(t2[0][k], t2[1][k], t2[2][k]) > max(t1[0][k], t1[1][k], t1[2][k]):
            return False
    return True


def _tri_tri_dist2(t1: Tri, t2: Tri) -> tuple[float, Vec3, Vec3]:
    """Exact squared distance between two triangles, with closest points.

    For two triangles that do not intersect, the minimum is always attained
    either between a pair of edges or between a vertex and the interior of the
    opposite face, so those two passes are exact.

    Triangles that *do* intersect need the explicit check that follows them.
    It is tempting to assume a crossing shows up as a zero in the edge-edge
    pass, and the prototype this kernel came from did assume it, but an edge
    stabbing clean through the middle of the other face touches none of its
    edges and none of its vertices: the passes then agree on a confidently
    wrong, strictly positive distance. Two bars crossing at right angles are
    reported as millimetres apart. The bounding-box gate keeps the extra test
    off the hot path, where nothing overlaps anyway.
    """
    best = math.inf
    bp: Vec3 = t1[0]
    bq: Vec3 = t2[0]
    for i in range(3):
        p1 = t1[i]
        q1 = t1[(i + 1) % 3]
        for j in range(3):
            d2, c1, c2 = _seg_seg(p1, q1, t2[j], t2[(j + 1) % 3])
            if d2 < best:
                best = d2
                bp, bq = c1, c2
                if best <= 0.0:
                    return 0.0, bp, bq
    for p in t1:
        c = _closest_point_on_tri(p, t2)
        w = _sub(p, c)
        d2 = _dot(w, w)
        if d2 < best:
            best = d2
            bp, bq = p, c
    for p in t2:
        c = _closest_point_on_tri(p, t1)
        w = _sub(p, c)
        d2 = _dot(w, w)
        if d2 < best:
            best = d2
            bp, bq = c, p
    if best > 0.0 and _boxes_overlap(t1, t2):
        crossing = _tri_tri_segment(t1, t2, tol=0.0)
        if crossing is not None:
            return 0.0, crossing[0], crossing[0]
    return best, bp, bq


@dataclass
class DistanceResult:
    """The closest approach between two meshes.

    ``exact`` is False only when an ``upper_bound`` was supplied and nothing
    beat it; then ``distance`` is that bound, the points are meaningless and
    the triangle indices are -1. The real distance is somewhere above the
    bound and was deliberately not computed.
    """

    distance: float
    point_a: Vec3
    point_b: Vec3
    tri_a: int
    tri_b: int
    exact: bool = True


def min_distance(a: Mesh, b: Mesh, upper_bound: float | None = None) -> DistanceResult:
    """Exact minimum surface-to-surface distance between two meshes.

    Best-first traversal over pairs of BVH nodes: the pair whose boxes are
    closest is expanded next, and any pair whose box gap already exceeds the
    incumbent is dropped. The search stops as soon as a crossing (distance 0)
    is found, so an interfering pair is the cheapest case, not the dearest.

    Args:
        a: First mesh.
        b: Second mesh.
        upper_bound: Stop as soon as it is known that the distance exceeds
            this, and report ``exact=False``. Use it for "are these farther
            apart than X" questions, which are much cheaper than the distance.

    Returns:
        A :class:`DistanceResult`. For an empty mesh the distance is infinite
        and the triangle indices are -1.
    """
    ba, bb = a.bvh(), b.bvh()
    if ba.root < 0 or bb.root < 0:
        return DistanceResult(math.inf, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), -1, -1, True)

    limited = upper_bound is not None
    best = (upper_bound * upper_bound) if upper_bound is not None else math.inf
    bp: Vec3 = (0.0, 0.0, 0.0)
    bq: Vec3 = (0.0, 0.0, 0.0)
    tri_a = tri_b = -1
    tris_a, tris_b = ba.tris, bb.tris
    order_a, order_b = ba.order, bb.order

    heap: list[tuple[float, int, int]] = [(ba._gap2(ba.root, bb, bb.root), ba.root, bb.root)]
    while heap:
        gap, i, j = heapq.heappop(heap)
        if gap >= best:
            break
        la, lb = ba.left[i], bb.left[j]
        if la < 0 and lb < 0:
            sa, sb = ba.start[i], bb.start[j]
            for x in range(sa, sa + ba.count[i]):
                ia = order_a[x]
                ta = tris_a[ia]
                for y in range(sb, sb + bb.count[j]):
                    ib = order_b[y]
                    d2, p, q = _tri_tri_dist2(ta, tris_b[ib])
                    if d2 < best:
                        best = d2
                        bp, bq = p, q
                        tri_a, tri_b = ia, ib
                        if best <= 0.0:
                            return DistanceResult(0.0, bp, bq, tri_a, tri_b, True)
            continue
        if lb < 0 or (la >= 0 and ba.count[i] >= bb.count[j]):
            for child in (ba.left[i], ba.right[i]):
                g = ba._gap2(child, bb, j)
                if g < best:
                    heapq.heappush(heap, (g, child, j))
        else:
            for child in (bb.left[j], bb.right[j]):
                g = ba._gap2(i, bb, child)
                if g < best:
                    heapq.heappush(heap, (g, i, child))

    if tri_a < 0 and limited:
        assert upper_bound is not None
        return DistanceResult(upper_bound, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), -1, -1, False)
    return DistanceResult(math.sqrt(best) if best < math.inf else math.inf, bp, bq, tri_a, tri_b)


def _nearest_surface(bvh: BVH, p: Vec3, upper: float = math.inf) -> tuple[float, Vec3, int]:
    """Distance from ``p`` to the surface, with the closest point and triangle.

    ``upper`` prunes the search: if no triangle is closer than ``upper`` the
    result is ``(inf, p, -1)``, which turns "is anything nearer than X" into a
    fraction of the cost of the full query.
    """
    if bvh.root < 0:
        return math.inf, p, -1
    best = math.inf if upper == math.inf else upper * upper
    bpoint = p
    btri = -1
    heap: list[tuple[float, int]] = [(bvh._point_gap2(bvh.root, p), bvh.root)]
    while heap:
        gap, i = heapq.heappop(heap)
        if gap >= best:
            break
        if bvh.left[i] < 0:
            s = bvh.start[i]
            for x in range(s, s + bvh.count[i]):
                idx = bvh.order[x]
                c = _closest_point_on_tri(p, bvh.tris[idx])
                w = _sub(p, c)
                d2 = _dot(w, w)
                if d2 < best:
                    best = d2
                    bpoint = c
                    btri = idx
        else:
            for child in (bvh.left[i], bvh.right[i]):
                g = bvh._point_gap2(child, p)
                if g < best:
                    heapq.heappush(heap, (g, child))
    if btri < 0:
        return math.inf, p, -1
    return math.sqrt(best), bpoint, btri


def aabb_gap(a: Mesh, b: Mesh) -> float:
    """Separation of the two bounding boxes, 0 when they overlap.

    A lower bound on :func:`min_distance`, for pre-filtering pairs in an
    assembly before paying for the exact query.
    """
    a_lo, a_hi = a.bbox_min, a.bbox_max
    b_lo, b_hi = b.bbox_min, b.bbox_max
    total = 0.0
    for k in range(3):
        d = b_lo[k] - a_hi[k]
        if d < 0.0:
            d = a_lo[k] - b_hi[k]
        if d > 0.0:
            total += d * d
    return math.sqrt(total)


# ---------------------------------------------------------------------------
# Point in solid
# ---------------------------------------------------------------------------


def winding_number(mesh: Mesh, point: Vec3) -> float:
    """Generalized winding number of ``mesh`` at ``point``.

    The sum of the signed solid angles the triangles subtend at the point,
    divided by 4*pi (Jacobson, Kavan and Sorkine-Hornung, 2013). For a
    watertight, outward-oriented mesh this is 1.0 strictly inside, 0.0
    outside, and 0.5 on a face. Unlike ray parity it never has to decide what
    a ray grazing a shared edge means, so it is stable on symmetry planes.

    On a mesh that is *not* watertight the value is still defined and still
    continuous away from the surface, but it drifts from the integers near a
    hole: a fractional result is a signal that the mesh has a local defect
    there, not a failure of this function. The value is undefined when the
    point is exactly a vertex of the mesh; that case returns 0.5, i.e. "on the
    surface". Degenerate facets are skipped, so a point that happens to sit on
    one is not mistaken for a vertex.

    Args:
        mesh: The mesh to query.
        point: The point, in the mesh's own coordinates.

    Returns:
        The winding number. Degenerate triangles contribute nothing.
    """
    px, py, pz = point
    lo, hi = mesh.bbox_min, mesh.bbox_max
    if (
        px < lo[0] or px > hi[0] or py < lo[1] or py > hi[1] or pz < lo[2] or pz > hi[2]
    ) and mesh.is_watertight():
        # Exact for a closed surface: every exterior point has winding 0, and
        # everything outside the bounding box is exterior.
        return 0.0

    total = 0.0
    sqrt = math.sqrt
    atan2 = math.atan2
    for tri in mesh.solid_triangles():
        a, b, c = tri
        ax = a[0] - px
        ay = a[1] - py
        az = a[2] - pz
        bx = b[0] - px
        by = b[1] - py
        bz = b[2] - pz
        cx = c[0] - px
        cy = c[1] - py
        cz = c[2] - pz
        la = sqrt(ax * ax + ay * ay + az * az)
        lb = sqrt(bx * bx + by * by + bz * bz)
        lc = sqrt(cx * cx + cy * cy + cz * cz)
        if la < 1e-12 or lb < 1e-12 or lc < 1e-12:
            return 0.5  # the point is a vertex of the mesh
        num = ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        den = (
            la * lb * lc
            + (ax * bx + ay * by + az * bz) * lc
            + (bx * cx + by * cy + bz * cz) * la
            + (ax * cx + ay * cy + az * cz) * lb
        )
        if num == 0.0 and den == 0.0:
            continue
        total += 2.0 * atan2(num, den)
    return total / (4.0 * math.pi)


def contains(mesh: Mesh, point: Vec3, threshold: float = 0.5) -> bool:
    """True when ``point`` is strictly inside ``mesh``.

    Uses the winding number, so the verdict is stable everywhere except on
    the surface itself, where the winding number is exactly the default
    threshold and rounding decides. A point that close to a boundary is
    ambiguous by definition; lower the threshold to count it as inside.
    """
    return winding_number(mesh, point) > threshold


#: Three directions with no special relationship to any axis or diagonal, for
#: the ray-parity fast path. Deliberately not axis aligned: an axis-aligned ray
#: from a point on a symmetry plane runs along coincident facets.
_PROBE_DIRS: tuple[Vec3, ...] = (
    (0.5773502691896258, 0.5773502691896257, 0.5773502691896256),
    (-0.4472135954999579, 0.8944271909999159, 1e-07),
    (0.2672612419124244, -0.5345224838248488, 0.8017837257372732),
)


def _parity_inside(mesh: Mesh, point: Vec3, direction: Vec3) -> bool | None:
    """Ray-parity containment along one direction, or None if unreliable.

    For a watertight mesh, the parity of the forward crossings is exact --
    provided the ray does not pass through an edge or a vertex, where two
    triangles both claim the crossing or neither does. Those cases are
    detected from the barycentric coordinates and reported as unreliable
    rather than guessed at, which is what makes this usable as a fast path
    under the winding number instead of a rival to it.
    """
    bvh = mesh.bvh()
    tris = mesh.triangles
    count = 0
    for idx in _ray_candidates(bvh, point, direction, None):
        res = _ray_tri(point, direction, tris[idx])
        if res is None:
            continue
        t, u, v, det = res
        if t < -EPS:
            continue  # behind the origin
        if t <= EPS:
            return None  # the point is on this surface
        if min(u, v, 1.0 - u - v) < 1e-7 or abs(det) < 1e-12:
            return None  # grazed an edge or a vertex
        count += 1
    return count % 2 == 1


def _inside_fast(mesh: Mesh, point: Vec3) -> bool:
    """Containment for bulk work: ray parity, falling back to the real thing.

    The winding number is O(triangles) per point, a couple of milliseconds on
    a 6k-facet part, and a penetration scan asks the question hundreds of
    times. Ray parity through the BVH answers in tens of microseconds. It is
    only ever a fast path: any ray that grazes an edge, any disagreement
    between directions, and any mesh that is not watertight falls through to
    :func:`contains`, which is the definition.
    """
    lo, hi = mesh.bbox_min, mesh.bbox_max
    if any(point[k] < lo[k] or point[k] > hi[k] for k in range(3)):
        return False if mesh.is_watertight() else contains(mesh, point)
    if mesh.is_watertight():
        votes: list[bool] = []
        for direction in _PROBE_DIRS:
            vote = _parity_inside(mesh, point, direction)
            if vote is None:
                continue
            votes.append(vote)
            if len(votes) == 2 and votes[0] == votes[1]:
                return votes[0]
        if votes and all(v == votes[0] for v in votes):
            return votes[0]
    return contains(mesh, point)


def classify_point(meshes: dict[str, Mesh], point: Vec3) -> dict[str, Any]:
    """Classify a point against a named set of parts.

    Bounding boxes are checked first. For a closed surface that rejection is
    exact rather than a heuristic (winding is identically 0 outside the box),
    so a probe only pays the full cost for the parts whose box contains it.

    Args:
        meshes: Parts by name.
        point: The point to classify.

    Returns:
        ``{"state": "solid" | "air" | "on_surface", "parts": [names],
        "winding": {name: value}}``. ``parts`` lists the parts responsible for
        the state. ``winding`` covers every part, with 0.0 for those rejected
        by the bounding box.
    """
    winding: dict[str, float] = {}
    solid: list[str] = []
    surface: list[str] = []
    for name, mesh in meshes.items():
        lo, hi = mesh.bbox_min, mesh.bbox_max
        if any(point[k] < lo[k] or point[k] > hi[k] for k in range(3)):
            winding[name] = 0.0
            continue
        w = winding_number(mesh, point)
        winding[name] = w
        if abs(w - 0.5) < SURFACE_BAND:
            surface.append(name)
        elif abs(w) >= 0.5:
            solid.append(name)
    if solid:
        state = "solid"
        parts = solid
    elif surface:
        state = "on_surface"
        parts = surface
    else:
        state = "air"
        parts = []
    return {"state": state, "parts": parts, "winding": winding}


# ---------------------------------------------------------------------------
# Rays
# ---------------------------------------------------------------------------


def _ray_tri(o: Vec3, d: Vec3, tri: Tri) -> tuple[float, float, float, float] | None:
    """Moller-Trumbore intersection of a ray with a triangle.

    Returns ``(t, u, v, det)`` for a hit anywhere on the *line*, including behind
    the origin, or None when the ray misses or is parallel to the triangle.
    Deciding what a given ``t`` means is the caller's job, and the order of
    the tests matters: ``t < -eps`` is behind the origin and must be discarded
    *before* ``|t| <= eps`` is read as "starts on this surface". Folding the
    two together turns every triangle behind the origin into a spurious
    surface hit.

    ``det`` is the signed volume term; it equals ``-dot(direction, normal)``
    up to the triangle's scale, so its sign is the *opposite* of the
    ray-versus-normal sense.
    """
    v0, v1, v2 = tri
    e1 = _sub(v1, v0)
    e2 = _sub(v2, v0)
    pv = _cross(d, e2)
    det = _dot(e1, pv)
    if -1e-12 < det < 1e-12:
        return None
    inv = 1.0 / det
    tv = _sub(o, v0)
    u = _dot(tv, pv) * inv
    if u < -1e-9 or u > 1.0 + 1e-9:
        return None
    qv = _cross(tv, e1)
    v = _dot(d, qv) * inv
    if v < -1e-9 or u + v > 1.0 + 1e-9:
        return None
    return _dot(e2, qv) * inv, u, v, det


@dataclass
class RayHit:
    """One crossing of a surface by a ray."""

    t: float
    point: Vec3
    normal: Vec3
    entering: bool
    tri: int
    part: str


def _ray_candidates(bvh: BVH, o: Vec3, d: Vec3, max_distance: float | None) -> list[int]:
    """Triangle indices whose node boxes the ray enters, via slab traversal."""
    if bvh.root < 0:
        return []
    limit = math.inf if max_distance is None else max_distance
    inv = [math.inf if abs(d[k]) < 1e-30 else 1.0 / d[k] for k in range(3)]
    out: list[int] = []
    stack = [bvh.root]
    while stack:
        i = stack.pop()
        lo, hi = bvh.lo[i], bvh.hi[i]
        t0, t1 = 0.0, limit
        hit = True
        for k in range(3):
            lo_k = (lo[k] - o[k]) * inv[k]
            hi_k = (hi[k] - o[k]) * inv[k]
            if lo_k > hi_k:
                lo_k, hi_k = hi_k, lo_k
            if lo_k > t0:
                t0 = lo_k
            if hi_k < t1:
                t1 = hi_k
            if t0 > t1:
                hit = False
                break
        if not hit:
            continue
        if bvh.left[i] < 0:
            s = bvh.start[i]
            out.extend(bvh.order[s : s + bvh.count[i]])
        else:
            stack.append(bvh.left[i])
            stack.append(bvh.right[i])
    return out


def ray_cast(
    mesh: Mesh,
    origin: Vec3,
    direction: Vec3,
    max_distance: float | None = None,
) -> list[RayHit]:
    """Every forward crossing of ``mesh`` by a ray, ordered by distance.

    ``direction`` is normalised, so ``t`` is a distance in millimetres.
    Crossings behind the origin are discarded, and so is a crossing at the
    origin itself (``t <= eps``): a ray that starts on a face reports the
    faces it goes on to meet, not the one it starts on.

    Args:
        mesh: The mesh to intersect.
        origin: Ray origin.
        direction: Ray direction; need not be a unit vector.
        max_distance: Ignore crossings beyond this distance.

    Returns:
        Hits sorted by ``t``. ``entering`` is True when the ray passes from
        outside to inside the surface at that crossing. A ray that runs
        exactly along an edge shared by two triangles reports that crossing
        once, not twice.

    Raises:
        ValueError: If ``direction`` has (near) zero length.
    """
    unit = _unit(direction)
    if unit is None:
        raise ValueError(f"ray direction must be non-zero, got {direction!r}")
    hits: list[RayHit] = []
    part = mesh.name
    for idx in _ray_candidates(mesh.bvh(), origin, unit, max_distance):
        tri = mesh.triangles[idx]
        res = _ray_tri(origin, unit, tri)
        if res is None:
            continue
        t = res[0]
        if t < -EPS:
            continue  # behind the ray origin
        if t <= EPS:
            continue  # starts on this surface
        if max_distance is not None and t > max_distance:
            continue
        normal, area = _tri_normal(tri)
        if normal is None or area <= 0.0:
            continue
        hits.append(
            RayHit(
                t=t,
                point=(
                    origin[0] + unit[0] * t,
                    origin[1] + unit[1] * t,
                    origin[2] + unit[2] * t,
                ),
                normal=normal,
                # Read the direction of travel off the face normal rather than
                # the intersection determinant: the determinant carries the
                # same sign information, inverted, and getting that inversion
                # backwards flips every entering/exiting flag in the list.
                entering=_dot(normal, unit) < 0.0,
                tri=idx,
                part=part,
            )
        )
    hits.sort(key=lambda h: h.t)
    return _dedupe_hits(hits)


def _dedupe_hits(hits: list[RayHit]) -> list[RayHit]:
    """Collapse crossings that are the same crossing seen twice.

    A ray through the edge two triangles share is reported by both of them.
    Two hits at the same point, on the same part, going the same way through
    the surface, are one crossing of that surface.
    """
    out: list[RayHit] = []
    for hit in hits:
        if out:
            prev = out[-1]
            if (
                abs(hit.t - prev.t) <= 1e-7
                and hit.part == prev.part
                and hit.entering == prev.entering
            ):
                continue
        out.append(hit)
    return out


def ray_cast_parts(
    meshes: dict[str, Mesh],
    origin: Vec3,
    direction: Vec3,
    max_distance: float | None = None,
) -> list[RayHit]:
    """:func:`ray_cast` across a named set of parts, merged and sorted by ``t``.

    Each hit's ``part`` is the dict key, not the mesh's own name, so the
    caller's naming wins.
    """
    hits: list[RayHit] = []
    for name, mesh in meshes.items():
        for hit in ray_cast(mesh, origin, direction, max_distance):
            hit.part = name
            hits.append(hit)
    hits.sort(key=lambda h: h.t)
    return _dedupe_hits(hits)


def polyline_clear(meshes: dict[str, Mesh], points: list[Vec3]) -> dict[str, Any]:
    """Walk a polyline and report the first part that blocks it.

    This is the "can the part get from here to there" query: a straight-line
    corridor test along an arbitrary path, with no swept volume to construct.

    Args:
        meshes: Parts by name.
        points: Two or more waypoints in millimetres.

    Returns:
        ``{"clear": bool, "blocked_by": name|None, "blocked_at_mm": float|None,
        "point": Vec3|None, "length_mm": float, "segments": [...]}``.
        ``blocked_at_mm`` is the arc length along the whole polyline. Each
        segment reports its own length, whether it was clear, and what stopped
        it; segments after the first blockage carry ``clear: None``, because
        the walk stops there rather than pretending to know.

    Raises:
        ValueError: If fewer than two points are given.
    """
    if len(points) < 2:
        raise ValueError(f"polyline needs at least 2 points, got {len(points)}")
    segments: list[dict[str, Any]] = []
    travelled = 0.0
    blocked_by: str | None = None
    blocked_at: float | None = None
    blocked_point: Vec3 | None = None
    for index in range(len(points) - 1):
        a = points[index]
        b = points[index + 1]
        seg: Vec3 = _sub(b, a)
        length = _length(seg)
        entry: dict[str, Any] = {
            "index": index,
            "from": list(a),
            "to": list(b),
            "length_mm": length,
            "clear": True,
            "blocked_by": None,
            "blocked_at_mm": None,
        }
        if length <= EPS:
            segments.append(entry)
            continue
        if blocked_by is None:
            hits = ray_cast_parts(meshes, a, seg, max_distance=length)
            if hits:
                first = hits[0]
                entry["clear"] = False
                entry["blocked_by"] = first.part
                entry["blocked_at_mm"] = first.t
                blocked_by = first.part
                blocked_at = travelled + first.t
                blocked_point = first.point
        else:
            entry["clear"] = None
        travelled += length
        segments.append(entry)
    return {
        "clear": blocked_by is None,
        "blocked_by": blocked_by,
        "blocked_at_mm": blocked_at,
        "point": blocked_point,
        "length_mm": travelled,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Surface crossing, penetration and contact
# ---------------------------------------------------------------------------


def _tri_tri_segment(t1: Tri, t2: Tri, tol: float = 1e-6) -> tuple[Vec3, Vec3, float] | None:
    """The intersection segment of two triangles that genuinely pierce.

    Returns ``(midpoint, inward_bisector, overlap_length)``, or None when the
    triangles are parallel, coplanar, disjoint, or merely touching. That last
    exclusion is the point of the function: two parts sharing a flush face
    have wall triangles that *meet* the mating face without crossing it, and
    calling that interference is the single most common false positive in
    assembly checking. A triangle must have vertices strictly on both sides of
    the other's plane, by more than ``tol``, to count.

    The midpoint of the real intersection segment is used as the witness. An
    earlier prototype averaged the six vertices of the two triangles instead,
    which lands metres away from the overlap when one triangle is a large
    deck face and the other a 3 mm bracket wall.
    """
    n1 = _tri_normal(t1)[0]
    n2 = _tri_normal(t2)[0]
    if n1 is None or n2 is None:
        return None
    d1 = -_dot(n1, t1[0])
    d2 = -_dot(n2, t2[0])
    du = [_dot(n1, u) + d1 for u in t2]
    dv = [_dot(n2, v) + d2 for v in t1]
    if max(du) < tol or min(du) > -tol:
        return None
    if max(dv) < tol or min(dv) > -tol:
        return None
    direction = _unit(_cross(n1, n2))
    if direction is None:
        return None  # parallel or coplanar
    # Point on the line where the two planes meet.
    ca = _cross(n2, direction)
    cb = _cross(direction, n1)
    den = _dot(n1, ca)
    if abs(den) < 1e-14:
        return None
    base: Vec3 = (
        ((-d1) * ca[0] + (-d2) * cb[0]) / den,
        ((-d1) * ca[1] + (-d2) * cb[1]) / den,
        ((-d1) * ca[2] + (-d2) * cb[2]) / den,
    )

    def span(tri: Tri, dist: list[float]) -> tuple[float, float] | None:
        params: list[float] = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if dist[i] * dist[j] <= 0.0 and dist[i] != dist[j]:
                w = dist[i] / (dist[i] - dist[j])
                q: Vec3 = (
                    tri[i][0] + (tri[j][0] - tri[i][0]) * w,
                    tri[i][1] + (tri[j][1] - tri[i][1]) * w,
                    tri[i][2] + (tri[j][2] - tri[i][2]) * w,
                )
                params.append(_dot(direction, _sub(q, base)))
        if len(params) < 2:
            return None
        return min(params[0], params[1]), max(params[0], params[1])

    s1 = span(t1, dv)
    s2 = span(t2, du)
    if s1 is None or s2 is None:
        return None
    low = max(s1[0], s2[0])
    high = min(s1[1], s2[1])
    if high - low <= tol:
        return None
    mid = (low + high) * 0.5
    point: Vec3 = (
        base[0] + direction[0] * mid,
        base[1] + direction[1] * mid,
        base[2] + direction[2] * mid,
    )
    # Push along the inward bisector of the two OUTWARD normals: the direction
    # that goes into both solids at once.
    bisector = _unit((-(n1[0] + n2[0]), -(n1[1] + n2[1]), -(n1[2] + n2[2])))
    if bisector is None:
        bisector = (-n1[0], -n1[1], -n1[2])
    return point, bisector, high - low


def _overlap_pairs(a: BVH, b: BVH, eps: float = 1e-9, limit: int = 200000) -> list[tuple[int, int]]:
    """Triangle index pairs whose leaf boxes overlap within ``eps``."""
    out: list[tuple[int, int]] = []
    if a.root < 0 or b.root < 0:
        return out
    stack = [(a.root, b.root)]
    while stack:
        i, j = stack.pop()
        a_lo, a_hi, b_lo, b_hi = a.lo[i], a.hi[i], b.lo[j], b.hi[j]
        if any(b_lo[k] - a_hi[k] > eps or a_lo[k] - b_hi[k] > eps for k in range(3)):
            continue
        la, lb = a.left[i], b.left[j]
        if la < 0 and lb < 0:
            sa, sb = a.start[i], b.start[j]
            for x in range(sa, sa + a.count[i]):
                for y in range(sb, sb + b.count[j]):
                    out.append((a.order[x], b.order[y]))
            if len(out) >= limit:
                return out
        elif lb < 0 or (la >= 0 and a.count[i] >= b.count[j]):
            stack.append((a.left[i], j))
            stack.append((a.right[i], j))
        else:
            stack.append((i, b.left[j]))
            stack.append((i, b.right[j]))
    return out


#: Push distances tried along the inward bisector, largest first.
_WITNESS_DELTAS = (1.0, 0.5, 0.25, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001, 5e-4, 2e-4, 1e-4)

#: A witness this deep inside both solids settles the question; the search for
#: a deeper one stops there.
_DECISIVE_DEPTH = 0.05


def _crossing_witness(
    a: Mesh, b: Mesh, min_depth: float = 1e-4, max_tests: int = 48
) -> tuple[Vec3, float] | None:
    """Find a point that is a real distance inside both meshes.

    Two long bars crossing at right angles have no vertex of either inside the
    other, so a vertex test alone reports them as clear. Their surfaces do
    cross, though, and the midpoint of the intersection segment pushed along
    the inward bisector is inside both.

    The depth of a witness is its distance to the nearer of the two surfaces,
    *not* how far it was pushed. That distinction decides real cases. Two
    parts with a conformal fit -- a pinion bore on a motor shaft, drawn at the
    same ``$fn`` so the facets coincide -- have thousands of triangle pairs
    that cross by a few hundredths of a micron, pure floating-point noise on a
    flush fit. Pushing a millimetre along the bisector from one of those
    crossings still lands inside both solids, because the bisector runs almost
    parallel to both surfaces, and reporting that push as a millimetre of
    interference contradicts OpenSCAD, which computes the intersection of that
    same pair as empty. Measuring the distance to the surfaces instead gives
    5e-5 mm, below ``min_depth``, and the pair is correctly left to the
    contact branch.

    Candidate crossings are sampled across the whole overlap region rather
    than taken from one corner of it, and the search stops early once a
    witness is decisively deep.

    Args:
        a: First mesh.
        b: Second mesh.
        min_depth: Witnesses shallower than this are noise, not interference.
        max_tests: Cap on crossing pairs examined, for a bounded cost.

    Returns:
        ``(witness, depth_mm)`` with ``depth_mm > min_depth``, or None.
    """
    pairs = _overlap_pairs(a.bvh(), b.bvh())
    if not pairs:
        return None
    stride = max(1, len(pairs) // max_tests)
    tris_a, tris_b = a.triangles, b.triangles
    bvh_a, bvh_b = a.bvh(), b.bvh()
    best: tuple[Vec3, float] | None = None
    tested = 0
    for index in range(0, len(pairs), stride):
        if tested >= max_tests:
            break
        ia, ib = pairs[index]
        seg = _tri_tri_segment(tris_a[ia], tris_b[ib])
        if seg is None:
            continue
        tested += 1
        point, bisector, _overlap = seg
        floor = best[1] if best is not None else min_depth
        for delta in _WITNESS_DELTAS:
            # A push of `delta` can never put the point more than `delta` from
            # either surface, so once the ladder drops below the incumbent
            # depth there is nothing left to win from this crossing.
            if delta <= floor:
                break
            q: Vec3 = (
                point[0] + bisector[0] * delta,
                point[1] + bisector[1] * delta,
                point[2] + bisector[2] * delta,
            )
            da = _nearest_surface(bvh_a, q, upper=delta * 1.001)[0]
            if da <= floor:
                continue
            db = _nearest_surface(bvh_b, q, upper=delta * 1.001)[0]
            depth = min(da, db)
            if depth <= floor:
                continue
            if _inside_fast(a, q) and _inside_fast(b, q):
                best = (q, depth)
                floor = depth
                break
        if best is not None and best[1] >= _DECISIVE_DEPTH:
            break
    return best


def penetration_depth(a: Mesh, b: Mesh, max_candidates: int = 20000) -> tuple[float, Vec3 | None]:
    """How deep the two solids overlap, as a lower bound, plus a witness.

    The measure is the largest distance from a vertex contained in the other
    solid to that solid's surface, taken in both directions. It is a lower
    bound rather than the true maximum overlap depth because the deepest point
    of an intersection need not be a vertex of either mesh; for the shapes
    OpenSCAD produces it is usually exact and never optimistic.

    Candidates are ordered by how far inside the shared bounding box they sit
    and each nearest-surface query is pruned by the incumbent depth, so most
    vertices are rejected without a full search.

    Args:
        a: First mesh.
        b: Second mesh.
        max_candidates: Cap on vertices examined per direction. Reaching it
            can only make the answer more conservative, never less.

    Returns:
        ``(depth_mm, witness)``, or ``(0.0, None)`` when neither solid
        contains a vertex of the other.
    """
    lo = tuple(max(a.bbox_min[k], b.bbox_min[k]) for k in range(3))
    hi = tuple(min(a.bbox_max[k], b.bbox_max[k]) for k in range(3))
    if any(lo[k] > hi[k] for k in range(3)):
        return 0.0, None

    best_depth = 0.0
    witness: Vec3 | None = None
    for src, dst in ((a, b), (b, a)):
        candidates: list[tuple[float, Vec3]] = []
        for v in src.vertices():
            if any(v[k] < lo[k] or v[k] > hi[k] for k in range(3)):
                continue
            # Distance to the wall of the shared box. Only an ordering
            # heuristic -- a vertex sitting on the wall of the shared box can
            # still be the deepest one inside the other solid -- but it grows
            # the incumbent depth early, and the incumbent is what prunes the
            # nearest-surface queries for every candidate after it.
            inset = min(min(v[k] - lo[k], hi[k] - v[k]) for k in range(3))
            candidates.append((inset, v))
        candidates.sort(key=lambda item: -item[0])
        dst_bvh = dst.bvh()
        for _inset, v in candidates[:max_candidates]:
            if best_depth > 0.0 and _nearest_surface(dst_bvh, v, upper=best_depth)[2] >= 0:
                # Something is nearer to this vertex than the incumbent depth,
                # so it cannot be deeper. Note the direction: the query looks
                # for the *closest* surface, so a hit inside the incumbent
                # radius disqualifies the vertex and a miss promotes it.
                continue
            d, _point, tri = _nearest_surface(dst_bvh, v)
            if tri < 0 or d <= best_depth:
                continue
            if _inside_fast(dst, v):
                best_depth = d
                witness = v
    return best_depth, witness


def _interior_probe(a: Mesh, b: Mesh, min_depth: float) -> tuple[Vec3, float] | None:
    """Look for a point well inside both solids, by sampling the shared box.

    The last resort in the interference ladder, and it catches a case the
    other two cannot. Slide a 10 mm cube 15 mm into an identical one: the two
    overlap in a 5 mm slab, but all four side walls are exactly coincident, so
    every triangle crossing is flush rather than piercing, and every vertex in
    the overlap sits on the other solid's boundary rather than inside it. Both
    witnesses come up empty on an unmistakable 500 mm3 overlap. Sampling the
    interior of the shared bounding box finds it at once.

    The sample must be a real distance inside both solids to count, so a flush
    face contact -- whose shared box is a zero-thickness slab of surface
    points -- is never mistaken for an overlap.
    """
    lo: Vec3 = (
        max(a.bbox_min[0], b.bbox_min[0]),
        max(a.bbox_min[1], b.bbox_min[1]),
        max(a.bbox_min[2], b.bbox_min[2]),
    )
    hi: Vec3 = (
        min(a.bbox_max[0], b.bbox_max[0]),
        min(a.bbox_max[1], b.bbox_max[1]),
        min(a.bbox_max[2], b.bbox_max[2]),
    )
    if any(hi[k] - lo[k] <= min_depth for k in range(3)):
        return None
    best: tuple[Vec3, float] | None = None
    fractions = (0.5, 0.25, 0.75)
    for fx in fractions:
        for fy in fractions:
            for fz in fractions:
                q: Vec3 = (
                    lo[0] + (hi[0] - lo[0]) * fx,
                    lo[1] + (hi[1] - lo[1]) * fy,
                    lo[2] + (hi[2] - lo[2]) * fz,
                )
                if not (_inside_fast(a, q) and _inside_fast(b, q)):
                    continue
                depth = min(
                    _nearest_surface(a.bvh(), q)[0],
                    _nearest_surface(b.bvh(), q)[0],
                )
                if depth > min_depth and (best is None or depth > best[1]):
                    best = (q, depth)
    return best


def _clip_halfplane(
    poly: list[tuple[float, float]], a: tuple[float, float], b: tuple[float, float]
) -> list[tuple[float, float]]:
    """Sutherland-Hodgman clip of a 2D polygon to the left of the edge a->b."""
    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        c = poly[i]
        d = poly[(i + 1) % n]
        sc = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        sd = (b[0] - a[0]) * (d[1] - a[1]) - (b[1] - a[1]) * (d[0] - a[0])
        if sc >= -1e-12:
            out.append(c)
        if (sc > 1e-12 and sd < -1e-12) or (sc < -1e-12 and sd > 1e-12):
            t = sc / (sc - sd)
            out.append((c[0] + t * (d[0] - c[0]), c[1] + t * (d[1] - c[1])))
    return out


def _poly_area_centroid(poly: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Absolute area and centroid of a simple 2D polygon."""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    twice = 0.0
    cx = cy = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        twice += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(twice) < 1e-18:
        return 0.0, 0.0, 0.0
    return abs(twice) * 0.5, cx / (3.0 * twice), cy / (3.0 * twice)


def contact_area(
    a: Mesh, b: Mesh, eps: float = 1e-4, angle_deg: float = 1.0
) -> tuple[float, Vec3 | None, float | None, Vec3 | None]:
    """Area of the flush contact between two meshes.

    Only pairs of triangles that are near-parallel and facing each other
    (their outward normals within ``angle_deg`` of anti-parallel) and coplanar
    within ``eps`` can contribute. Each such pair is clipped against the other
    in their shared plane, which gives the exact overlap polygon: no epsilon
    shift, no OpenSCAD run, and no double counting when one part's face is
    split into many triangles.

    Args:
        a: First mesh; the reported normal points from it toward ``b``.
        b: Second mesh.
        eps: Maximum out-of-plane separation for two faces to count as flush.
        angle_deg: Maximum deviation from exactly anti-parallel.

    Returns:
        ``(area_mm2, normal, plane_offset, centroid)``. The normal is the
        outward normal of ``a`` on the dominant patch, ``plane_offset`` is that
        plane's signed offset along it, and the centroid is area weighted. All
        three are None when there is no coplanar contact, which is the normal
        result for an edge or point touch.
    """
    cos_limit = -math.cos(math.radians(angle_deg))
    pairs = _overlap_pairs(a.bvh(), b.bvh(), eps=max(eps, 1e-9))
    total = 0.0
    acc: Vec3 = (0.0, 0.0, 0.0)
    best_patch = 0.0
    best_normal: Vec3 | None = None
    best_offset: float | None = None
    tris_a, tris_b = a.triangles, b.triangles
    for ia, ib in pairs:
        tri_a = tris_a[ia]
        tri_b = tris_b[ib]
        na, area_a = _tri_normal(tri_a)
        nb, area_b = _tri_normal(tri_b)
        if na is None or nb is None or area_a <= 0.0 or area_b <= 0.0:
            continue
        if _dot(na, nb) > cos_limit:
            continue
        offset = _dot(na, tri_a[0])
        if any(abs(_dot(na, p) - offset) > eps for p in tri_b):
            continue
        ref: Vec3 = (1.0, 0.0, 0.0) if abs(na[0]) < 0.9 else (0.0, 1.0, 0.0)
        u = _unit(_cross(na, ref))
        if u is None:
            continue
        v = _cross(na, u)
        poly = [(_dot(p, u), _dot(p, v)) for p in tri_a]
        clip = [(_dot(p, u), _dot(p, v)) for p in tri_b]
        turn = 0.0
        for i in range(3):
            x1, y1 = clip[i]
            x2, y2 = clip[(i + 1) % 3]
            turn += x1 * y2 - x2 * y1
        if turn < 0:
            clip = clip[::-1]
        for i in range(3):
            poly = _clip_halfplane(poly, clip[i], clip[(i + 1) % 3])
            if not poly:
                break
        if not poly:
            continue
        area, cx, cy = _poly_area_centroid(poly)
        if area <= 1e-12:
            continue
        total += area
        centre: Vec3 = (
            u[0] * cx + v[0] * cy + na[0] * offset,
            u[1] * cx + v[1] * cy + na[1] * offset,
            u[2] * cx + v[2] * cy + na[2] * offset,
        )
        acc = _add(acc, _mul(centre, area))
        if area > best_patch:
            best_patch = area
            best_normal = na
            best_offset = offset
    if total <= 0.0:
        return 0.0, None, None, None
    return total, best_normal, best_offset, _mul(acc, 1.0 / total)


@dataclass
class PairRelation:
    """How two parts of an assembly relate to each other."""

    state: str  # "clear" | "contact" | "interference"
    distance_mm: float
    penetration_mm: float | None = None
    contact_area_mm2: float | None = None
    normal: Vec3 | None = None
    plane_offset: float | None = None
    at: Vec3 | None = None
    closest: tuple[Vec3, Vec3] | None = None
    intersection_volume_mm3: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe summary, omitting the fields that do not apply."""
        out: dict[str, Any] = {"state": self.state, "distance_mm": self.distance_mm}
        if self.penetration_mm is not None:
            out["penetration_mm"] = self.penetration_mm
        if self.contact_area_mm2 is not None:
            out["contact_area_mm2"] = self.contact_area_mm2
        if self.normal is not None:
            out["normal"] = list(self.normal)
        if self.plane_offset is not None:
            out["plane_offset"] = self.plane_offset
        if self.at is not None:
            out["at"] = list(self.at)
        if self.closest is not None:
            out["closest"] = [list(self.closest[0]), list(self.closest[1])]
        if self.intersection_volume_mm3 is not None:
            out["intersection_volume_mm3"] = self.intersection_volume_mm3
        return out


def _classify(
    a: Mesh, b: Mesh, tolerance: float, contact_eps: float, want_area: bool, quick: bool = False
) -> PairRelation:
    """Shared ladder behind :func:`classify_pair` (see it for the reasoning)."""
    result = min_distance(a, b)
    distance = result.distance
    closest = (result.point_a, result.point_b) if result.tri_a >= 0 else None
    if distance > max(tolerance, contact_eps):
        return PairRelation(state="clear", distance_mm=distance, closest=closest)

    # Surfaces are touching or crossing. Only a witness point that is inside
    # BOTH solids proves interference; a flush face contact never produces one.
    witness = _crossing_witness(a, b, min_depth=contact_eps)
    if quick and witness is not None and witness[1] >= _DECISIVE_DEPTH:
        # A sweep asks "does this collide, and roughly how badly"; once a
        # decisively deep witness exists the vertex scan can only refine a
        # number that is documented as a lower bound anyway.
        return PairRelation(
            state="interference",
            distance_mm=0.0,
            penetration_mm=witness[1],
            at=witness[0],
            closest=closest,
        )
    depth, vertex_witness = penetration_depth(a, b)
    at = witness[0] if witness is not None else vertex_witness
    bound = max(depth, witness[1] if witness is not None else 0.0)
    if witness is None and depth <= contact_eps:
        probe = _interior_probe(a, b, contact_eps)
        if probe is not None:
            at, bound = probe[0], max(bound, probe[1])
    # A crossing witness is only ever returned when it is deeper than
    # contact_eps, so this one test covers all three sources of evidence.
    if bound > contact_eps:
        return PairRelation(
            state="interference",
            distance_mm=0.0,
            penetration_mm=bound,
            at=at,
            closest=closest,
        )

    area: float | None = None
    normal: Vec3 | None = None
    offset: float | None = None
    centroid: Vec3 | None = None
    if want_area:
        area, normal, offset, centroid = contact_area(a, b, eps=max(contact_eps, tolerance, 1e-6))
    return PairRelation(
        state="contact",
        distance_mm=max(0.0, distance),
        contact_area_mm2=area,
        normal=normal,
        plane_offset=offset,
        at=centroid if centroid is not None else (closest[0] if closest else None),
        closest=closest,
    )


def classify_pair(
    a: Mesh, b: Mesh, tolerance: float = 0.0, contact_eps: float = 1e-4
) -> PairRelation:
    """Decide whether two parts are clear, touching, or interfering.

    The ladder, in order, and why:

    1. **Distance.** If the exact surface distance is greater than
       ``tolerance`` the parts are clear, and that is the whole answer. The
       bounding boxes are checked first so a distant pair costs almost nothing.
    2. **Crossing.** Otherwise look for a witness point inside both solids,
       from the midpoint of a triangle-triangle intersection segment pushed
       along the inward bisector, and from vertices of one solid contained in
       the other. Either one proves interference. The two are complementary:
       vertices catch a part sunk into another, the intersection segment
       catches two bars crossing with no vertex inside anything.
    3. **Contact.** With no such witness, surfaces at distance ~0 are in
       contact. Contact is decided by the distance kernel, never by the
       crossing test, because two parts sharing a flush face have wall
       triangles that meet the mating face and would otherwise be read as
       interference.

    Args:
        a: First mesh.
        b: Second mesh.
        tolerance: Gaps up to this count as contact rather than clear. Use it
            for "is there at least 0.2 mm of clearance" questions.
        contact_eps: Distance below which surfaces are treated as touching.

    Returns:
        A :class:`PairRelation`. ``distance_mm`` is always the measured
        surface distance (0.0 for interference), and ``penetration_mm`` is a
        lower bound on the overlap depth.
    """
    return _classify(a, b, tolerance, contact_eps, want_area=True)


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------


def _sweep_step(
    moving: Mesh, against: dict[str, Mesh], label: dict[str, Any], tolerance: float
) -> dict[str, Any]:
    """Classify one pose of a moving part against every static part."""
    contacts: list[dict[str, Any]] = []
    min_gap = math.inf
    nearest: str | None = None
    max_pen = 0.0
    deepest: str | None = None
    for name, other in against.items():
        rel = _classify(moving, other, tolerance, 1e-4, want_area=False, quick=True)
        if rel.distance_mm < min_gap:
            min_gap = rel.distance_mm
            nearest = name
        if rel.state != "clear":
            contacts.append(
                {
                    "part": name,
                    "state": rel.state,
                    "distance_mm": rel.distance_mm,
                    "penetration_mm": rel.penetration_mm,
                    "at": list(rel.at) if rel.at is not None else None,
                }
            )
            pen = rel.penetration_mm or 0.0
            if pen > max_pen:
                max_pen = pen
                deepest = name
    # The worst offender is whatever is deepest into the moving part, or when
    # nothing overlaps, whatever it comes closest to.
    worst = deepest if deepest is not None else nearest
    step = dict(label)
    step["contacts"] = contacts
    step["min_gap_mm"] = None if min_gap == math.inf else min_gap
    step["max_penetration_mm"] = max_pen
    step["worst_part"] = worst
    return step


def _sweep_values(span: tuple[float, float], steps: int) -> list[float]:
    """Inclusive samples across ``span``; a single step samples the start."""
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    if steps == 1:
        return [float(span[0])]
    width = float(span[1]) - float(span[0])
    return [float(span[0]) + width * i / (steps - 1) for i in range(steps)]


def sweep_rotation(
    moving: Mesh,
    axis: Vec3,
    center: Vec3,
    range_deg: tuple[float, float],
    steps: int,
    against: dict[str, Mesh],
    rate: float = 1.0,
) -> list[dict[str, Any]]:
    """Rotate a part through a range and classify it at each step.

    A motion sweep never changes any part's geometry, so it needs no CAD
    re-run at all: the cached mesh is transformed in Python and the BVH is
    refit rather than rebuilt.

    Sampling is discrete, so this finds contact at the sampled poses only. A
    thin feature can pass through a gap between two samples; increase
    ``steps`` when the answer matters, or use :func:`rz_footprint` and
    :func:`can_ever_touch` for a full-turn proof.

    Args:
        moving: The part being moved, in its assembled pose.
        axis: Rotation axis direction.
        center: A point on the axis.
        range_deg: ``(start, end)`` angles in degrees, inclusive.
        steps: Number of poses to sample.
        against: Static parts by name.
        rate: Degrees per second, used only to report ``t_s`` for each step.

    Returns:
        One dict per step with ``angle_deg``, ``t_s``, ``contacts``,
        ``min_gap_mm``, ``max_penetration_mm`` and ``worst_part``.
    """
    if rate <= 0.0:
        raise ValueError(f"rate must be positive, got {rate}")
    moving.bvh()  # build once so every pose can refit instead of rebuild
    out: list[dict[str, Any]] = []
    for angle in _sweep_values(range_deg, steps):
        posed = moving.transformed(rotation(axis, angle, center))
        label = {"angle_deg": angle, "t_s": abs(angle - range_deg[0]) / rate}
        out.append(_sweep_step(posed, against, label, tolerance=0.0))
    return out


def sweep_translation(
    moving: Mesh,
    vector: Vec3,
    range_mm: tuple[float, float],
    steps: int,
    against: dict[str, Mesh],
) -> list[dict[str, Any]]:
    """Slide a part along a direction and classify it at each step.

    Args:
        moving: The part being moved, in its assembled pose.
        vector: Direction of travel; normalised, so the range is in millimetres.
        range_mm: ``(start, end)`` offsets along ``vector``, inclusive.
        steps: Number of poses to sample.
        against: Static parts by name.

    Returns:
        One dict per step with ``offset_mm``, ``contacts``, ``min_gap_mm``,
        ``max_penetration_mm`` and ``worst_part``.

    Raises:
        ValueError: If ``vector`` has (near) zero length.
    """
    unit = _unit(vector)
    if unit is None:
        raise ValueError(f"translation vector must be non-zero, got {vector!r}")
    moving.bvh()
    out: list[dict[str, Any]] = []
    for offset in _sweep_values(range_mm, steps):
        posed = moving.transformed(translation(_mul(unit, offset)))
        out.append(_sweep_step(posed, against, {"offset_mm": offset}, tolerance=0.0))
    return out


# ---------------------------------------------------------------------------
# Rotational footprint: the full-turn certificate
# ---------------------------------------------------------------------------


@dataclass
class Footprint:
    """Rasterised (radius, height) occupancy of a solid of revolution.

    Every point of the mesh, swept a full turn about the axis, lands in one of
    these cells. Two footprints are comparable only when they share an axis,
    a centre and both pitches.
    """

    cells: set[tuple[int, int]]
    r_pitch: float
    z_pitch: float
    axis: Vec3
    center: Vec3
    name: str = ""

    @property
    def cell_count(self) -> int:
        """Number of occupied cells."""
        return len(self.cells)

    def bounds(self) -> tuple[float, float, float, float]:
        """``(r_min, r_max, z_min, z_max)`` of the occupied cells."""
        if not self.cells:
            return (0.0, 0.0, 0.0, 0.0)
        i_lo = min(i for i, _ in self.cells)
        i_hi = max(i for i, _ in self.cells)
        j_lo = min(j for _, j in self.cells)
        j_hi = max(j for _, j in self.cells)
        return (
            i_lo * self.r_pitch,
            (i_hi + 1) * self.r_pitch,
            j_lo * self.z_pitch,
            (j_hi + 1) * self.z_pitch,
        )


def _axis_basis(w: Vec3) -> tuple[Vec3, Vec3]:
    """Two unit vectors completing a right-handed frame with the axis ``w``."""
    ref: Vec3 = (1.0, 0.0, 0.0) if abs(w[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(w, ref))
    if u is None:  # pragma: no cover - w is a unit vector, so this cannot happen
        raise ValueError(f"cannot build a frame around {w!r}")
    return u, _cross(w, u)


def rz_footprint(
    mesh: Mesh,
    axis: Vec3,
    center: Vec3,
    r_pitch: float = 0.25,
    z_pitch: float = 0.25,
) -> Footprint:
    """Rasterise the (r, z) region a mesh sweeps out under a full rotation.

    Each triangle is marked straight into the block of cells its own (r, z)
    bounding box covers. That box is computed exactly, which takes a little
    more than reading the three vertices:

    * The **largest** radius is always at a vertex, because distance from the
      axis is convex along any straight line.
    * The **smallest** is not. An edge passing by the axis dips closer than
      either of its endpoints, so each edge's closest approach is taken into
      account; and if the axis passes through the triangle itself, the
      smallest radius is zero.
    * The height range is exactly the vertices' range.

    Everything else is deliberately not done. There is no per-cell
    point-in-triangle test and no subdivision of large triangles: filling the
    box is the conservative answer, and conservative is the safe direction for
    a clearance proof. Refining each triangle until its box hugged its true
    (r, z) region cost a hundredfold for a slightly tighter raster -- 27
    seconds on a 6.4k-facet platform against a fifth of a second here -- and
    made a twelve-step motion check unusable.

    The interior enclosed by the surface raster is then filled, so the
    footprint describes the solid of revolution rather than just its skin.

    Args:
        mesh: The part.
        axis: Rotation axis direction.
        center: A point on the axis.
        r_pitch: Radial cell size in millimetres.
        z_pitch: Axial cell size in millimetres.

    Returns:
        A :class:`Footprint`.

    Raises:
        ValueError: If the axis is degenerate or a pitch is not positive.
    """
    w = _unit(axis)
    if w is None:
        raise ValueError(f"footprint axis must be non-zero, got {axis!r}")
    if r_pitch <= 0.0 or z_pitch <= 0.0:
        raise ValueError(f"pitches must be positive, got r={r_pitch}, z={z_pitch}")

    u, v = _axis_basis(w)
    ux, uy, uz = u
    vx, vy, vz = v
    wx, wy, wz = w
    cx, cy, cz = center
    inv_r = 1.0 / r_pitch
    inv_z = 1.0 / z_pitch
    sqrt = math.sqrt
    floor = math.floor

    cells: set[tuple[int, int]] = set()
    add = cells.add
    for tri in mesh.solid_triangles():
        (p0, p1, p2) = tri
        # Into the axis frame: (u, v) spans the plane the rotation turns in,
        # so the radius is a plain 2D length there and the height is one dot.
        x0 = p0[0] - cx
        y0 = p0[1] - cy
        z0 = p0[2] - cz
        a_u = x0 * ux + y0 * uy + z0 * uz
        a_v = x0 * vx + y0 * vy + z0 * vz
        a_z = x0 * wx + y0 * wy + z0 * wz
        x1 = p1[0] - cx
        y1 = p1[1] - cy
        z1 = p1[2] - cz
        b_u = x1 * ux + y1 * uy + z1 * uz
        b_v = x1 * vx + y1 * vy + z1 * vz
        b_z = x1 * wx + y1 * wy + z1 * wz
        x2 = p2[0] - cx
        y2 = p2[1] - cy
        z2 = p2[2] - cz
        c_u = x2 * ux + y2 * uy + z2 * uz
        c_v = x2 * vx + y2 * vy + z2 * vz
        c_z = x2 * wx + y2 * wy + z2 * wz

        ra = sqrt(a_u * a_u + a_v * a_v)
        rb = sqrt(b_u * b_u + b_v * b_v)
        rc = sqrt(c_u * c_u + c_v * c_v)
        r_hi = ra if ra > rb else rb
        if rc > r_hi:
            r_hi = rc
        r_lo = ra if ra < rb else rb
        if rc < r_lo:
            r_lo = rc

        # An edge that passes by the axis gets closer to it than either end.
        for e_u, e_v, f_u, f_v in (
            (a_u, a_v, b_u, b_v),
            (b_u, b_v, c_u, c_v),
            (c_u, c_v, a_u, a_v),
        ):
            d_u = f_u - e_u
            d_v = f_v - e_v
            dd = d_u * d_u + d_v * d_v
            if dd <= 0.0:
                continue
            t = -(e_u * d_u + e_v * d_v) / dd
            if t <= 0.0 or t >= 1.0:
                continue  # the nearest point is an endpoint, already counted
            q_u = e_u + d_u * t
            q_v = e_v + d_v * t
            d = sqrt(q_u * q_u + q_v * q_v)
            if d < r_lo:
                r_lo = d

        if r_lo > 0.0:
            # The axis may pass through the triangle's middle, touching no
            # edge, in which case the swept region reaches all the way in.
            s0 = a_u * b_v - b_u * a_v
            s1 = b_u * c_v - c_u * b_v
            s2 = c_u * a_v - a_u * c_v
            if (s0 >= 0.0 and s1 >= 0.0 and s2 >= 0.0) or (s0 <= 0.0 and s1 <= 0.0 and s2 <= 0.0):
                r_lo = 0.0

        z_lo = a_z if a_z < b_z else b_z
        if c_z < z_lo:
            z_lo = c_z
        z_hi = a_z if a_z > b_z else b_z
        if c_z > z_hi:
            z_hi = c_z

        i_lo = floor(r_lo * inv_r)
        i_hi = floor(r_hi * inv_r)
        j_lo = floor(z_lo * inv_z)
        j_hi = floor(z_hi * inv_z)
        if i_lo == i_hi:
            if j_lo == j_hi:
                add((i_lo, j_lo))
            else:
                for j in range(j_lo, j_hi + 1):
                    add((i_lo, j))
        else:
            for i in range(i_lo, i_hi + 1):
                for j in range(j_lo, j_hi + 1):
                    add((i, j))

    _fill_interior(cells)
    return Footprint(
        cells=cells,
        r_pitch=r_pitch,
        z_pitch=z_pitch,
        axis=w,
        center=center,
        name=mesh.name,
    )


def _fill_interior(cells: set[tuple[int, int]]) -> None:
    """Add every cell the outside cannot reach, in place.

    A flood fill from a one-cell margin around the raster. Anything the fill
    does not touch is enclosed by the surface, so it belongs to the solid. If
    the raster leaks the fill simply reaches everywhere and nothing is added,
    which leaves the surface-only footprint: never an over-fill.
    """
    if not cells:
        return
    # No padding column to the left of the axis: radius cannot go negative, so
    # there is no outside there. Padding it would hand the flood fill a lane
    # straight past the axis and into the middle of any part that reaches it,
    # and a solid rod would come back hollow. Columns inside the raster but
    # short of the part's inner radius are reached from the rows above and
    # below instead, which is right, because a bore open at both ends is
    # outside.
    i_lo = 0
    i_hi = max(i for i, _ in cells) + 1
    j_lo = min(j for _, j in cells) - 1
    j_hi = max(j for _, j in cells) + 1
    outside: set[tuple[int, int]] = set()
    stack = [(i_hi, j_lo)]
    while stack:
        cell = stack.pop()
        if cell in outside or cell in cells:
            continue
        i, j = cell
        if i < i_lo or i > i_hi or j < j_lo or j > j_hi:
            continue
        outside.add(cell)
        stack.append((i + 1, j))
        stack.append((i - 1, j))
        stack.append((i, j + 1))
        stack.append((i, j - 1))
    for i in range(i_lo, i_hi + 1):
        for j in range(j_lo, j_hi + 1):
            if (i, j) not in outside:
                cells.add((i, j))


def _boundary_cells(cells: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Cells with at least one free 4-neighbour: the only ones that can be closest."""
    out = []
    for i, j in cells:
        if (
            (i + 1, j) not in cells
            or (i - 1, j) not in cells
            or (i, j + 1) not in cells
            or (i, j - 1) not in cells
        ):
            out.append((i, j))
    return out


def can_ever_touch(a: Footprint, b: Footprint) -> tuple[bool, float]:
    """Can any rotation about the shared axis bring these two parts together?

    This is a proof, not a sample. Both parts are reduced to the (r, z) region
    they occupy over a full turn; if those regions are disjoint then no angle
    exists at which any point of one meets any point of the other, however
    finely a sweep might have been sampled. A sweep can only ever say "not at
    the poses I tried".

    The answer is conservative in one direction: because the raster
    over-reports occupancy, an overlap means "may touch", while disjoint
    footprints are conclusive.

    Args:
        a: First footprint.
        b: Second footprint, with the same axis, centre and pitches.

    Returns:
        ``(True, 0.0)`` when the footprints overlap, else ``(False, gap_mm)``
        with the smallest gap between the two regions in the (r, z) plane.

    Raises:
        ValueError: If the two footprints are not comparable.
    """
    if abs(a.r_pitch - b.r_pitch) > 1e-12 or abs(a.z_pitch - b.z_pitch) > 1e-12:
        raise ValueError("footprints must share both pitches to be compared")
    if _length(_sub(a.axis, b.axis)) > 1e-9 or _length(_sub(a.center, b.center)) > 1e-9:
        raise ValueError("footprints must share the same axis and centre")
    if not a.cells or not b.cells:
        return False, math.inf
    if not a.cells.isdisjoint(b.cells):
        return True, 0.0

    edge_a = _boundary_cells(a.cells)
    columns: dict[int, list[int]] = {}
    for i, j in _boundary_cells(b.cells):
        columns.setdefault(i, []).append(j)
    for js in columns.values():
        js.sort()
    keys = sorted(columns)
    best = math.inf
    for ai, aj in edge_a:
        for bi in keys:
            di = abs(bi - ai) - 1
            dx = di * a.r_pitch if di > 0 else 0.0
            if dx >= best:
                if bi > ai:
                    break
                continue
            for bj in columns[bi]:
                dj = abs(bj - aj) - 1
                dy = dj * a.z_pitch if dj > 0 else 0.0
                d = math.sqrt(dx * dx + dy * dy)
                if d < best:
                    best = d
    return False, best


# ---------------------------------------------------------------------------
# Render quality
# ---------------------------------------------------------------------------


def inscribed_polygon_error(radius_mm: float, fn: int) -> float:
    """How far a faceted circle falls short of the true one, in millimetres.

    OpenSCAD inscribes its polygons, so a cylinder of radius ``r`` drawn with
    ``fn`` facets is *smaller* than ``r`` everywhere except at the vertices,
    by ``r * (1 - cos(180/fn))`` at the middle of each facet. That is the
    number to compare against a fit tolerance: a 16 mm bore at ``$fn=24`` is
    0.137 mm undersize at the flats, which is the difference between a press
    fit and a slip fit.

    Args:
        radius_mm: Nominal radius.
        fn: Number of facets; below 3 there is no polygon.

    Returns:
        The sagitta in millimetres, 0.0 for a non-positive radius.
    """
    if fn < 3 or radius_mm <= 0.0:
        return 0.0
    return radius_mm * (1.0 - math.cos(math.pi / fn))


def segments_for(radius_mm: float, fn: int = 0, fa: float = 12.0, fs: float = 2.0) -> int:
    """Facets OpenSCAD will use for a circle of this radius.

    Mirrors ``get_fragments_from_r()``: ``$fn`` wins when set (clamped to at
    least 3), otherwise the count is the smaller of the angle limit
    ``360/$fa`` and the size limit ``2*pi*r/$fs``, never below 5, and rounded
    up. A radius that rounds to nothing gets 3.

    Args:
        radius_mm: Circle radius.
        fn: ``$fn``; 0 means unset.
        fa: ``$fa``, minimum angle per fragment in degrees.
        fs: ``$fs``, minimum fragment size in millimetres.

    Returns:
        The facet count.
    """
    if fn > 0:
        return max(3, int(fn))
    if radius_mm < 1e-8:
        return 3
    return int(math.ceil(max(min(360.0 / fa, radius_mm * 2.0 * math.pi / fs), 5.0)))
