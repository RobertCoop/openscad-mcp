"""Circular-feature extraction from OpenSCAD CSG dumps.

``openscad --export-format=csg -o out.csg part.scad`` writes the fully evaluated,
post-expansion, pre-boolean CSG tree as text: every module call is gone, every
transform has become a ``multmatrix``, and every primitive carries its resolved
arguments including ``$fn``/``$fa``/``$fs``.  That makes the dump the cheapest
place to recover *design intent* for round features: a hole is still a
``cylinder`` with a diameter, not a ring of triangles.

This module parses that text (:func:`parse_csg`), walks the tree accumulating the
world transform and the additive/subtractive polarity of each primitive
(:func:`extract_features`), collapses repeated features into patterns and bolt
circles (:func:`group_features`, :func:`describe_group`), names likely fits from
the reference tables (:func:`fit_candidates`) and lines features up across parts
of an assembly (:func:`align_features`).

Conventions and limits
----------------------
* Coordinates are whatever frame the dump is in; the caller is responsible for
  moving a part's features into an assembly frame (:func:`transform_features`).
* ``entry``/``exit`` are canonicalised so that ``axis_dir`` points down the
  first non-zero of the world Z, Y, X axes: a hole drilled from the top of a
  part reads as "from z=+6.0 along -Z" rather than the other way round.
* BOSL2 rounded cuboids are ``hull()``s of tiny corner cylinders, so anything
  under a ``hull`` or ``minkowski`` is masked out by default, as are stubs
  shorter than ``stub_ratio`` times their diameter.
* Cones, non-uniformly scaled (elliptical) cylinders, ``rotate_extrude`` and
  non-circular ``linear_extrude`` profiles are reported in ``unresolved``
  rather than guessed at.
* ``linear_extrude`` of a single ``circle`` *is* resolved, as a cylinder.
* Nodes carrying the ``%`` (background) or ``*`` (disable) modifier are skipped:
  they are not part of the rendered solid.

Transforms, facet counts and the inscribed-polygon undersize come from
``openscad_mcp.geom``; the handful of helpers this module keeps private
(direction-only transforms, scale, mirror) are the ones geom does not expose.
"""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import geom

__all__ = [
    "CylFeature",
    "FeatureSet",
    "Node",
    "PatternGroup",
    "align_features",
    "describe_group",
    "extract_features",
    "extract_from_scad",
    "fit_candidates",
    "group_features",
    "parse_csg",
    "to_dict",
    "transform_features",
]

Vec3 = geom.Vec3
#: Row-major 4x4, applied to column vectors: ``p' = M . [p, 1]``.
Mat4 = geom.Mat4

IDENTITY: Mat4 = geom.identity()

# OpenSCAD defaults for the special variables that drive tessellation.
DEFAULT_FA = 12.0
DEFAULT_FS = 2.0

_EPS = 1e-9

# ---------------------------------------------------------------------------
# 4x4 helpers (private; see module docstring)
# ---------------------------------------------------------------------------


def _compose(a: Mat4, b: Mat4) -> Mat4:
    """Return ``a . b`` (apply ``b`` first, then ``a``)."""
    return geom.compose(a, b)


def _apply_point(m: Mat4, p: Vec3) -> Vec3:
    """Transform a point."""
    return geom.apply(m, p)


def _apply_dir(m: Mat4, v: Vec3) -> Vec3:
    x, y, z = v
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z,
        m[1][0] * x + m[1][1] * y + m[1][2] * z,
        m[2][0] * x + m[2][1] * y + m[2][2] * z,
    )


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale_vec(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(v: Vec3) -> float:
    return math.sqrt(_dot(v, v))


def _unit(v: Vec3) -> Vec3:
    n = _length(v)
    return (v[0] / n, v[1] / n, v[2] / n) if n > _EPS else (0.0, 0.0, 0.0)


def _translation(v: Vec3) -> Mat4:
    return geom.translation(v)


def _scaling(v: Vec3) -> Mat4:
    return (
        (v[0], 0.0, 0.0, 0.0),
        (0.0, v[1], 0.0, 0.0),
        (0.0, 0.0, v[2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _rotation_axis(axis: Vec3, degrees: float) -> Mat4:
    """Right-handed rotation about ``axis`` through the origin, in degrees."""
    if _length(axis) < _EPS:
        return IDENTITY
    return geom.rotation(axis, degrees)


def _rotation_xyz(angles: Vec3) -> Mat4:
    """OpenSCAD ``rotate([x, y, z])``: Rz . Ry . Rx."""
    rx = _rotation_axis((1.0, 0.0, 0.0), angles[0])
    ry = _rotation_axis((0.0, 1.0, 0.0), angles[1])
    rz = _rotation_axis((0.0, 0.0, 1.0), angles[2])
    return _compose(rz, _compose(ry, rx))


def _mirror(v: Vec3) -> Mat4:
    u = _unit(v)
    if u == (0.0, 0.0, 0.0):
        return IDENTITY
    rows = []
    for r in range(3):
        row = [(1.0 if r == c else 0.0) - 2.0 * u[r] * u[c] for c in range(3)]
        row.append(0.0)
        rows.append(tuple(row))
    rows.append((0.0, 0.0, 0.0, 1.0))
    return tuple(rows)


def _plane_basis(axis: Vec3) -> tuple[Vec3, Vec3]:
    """Return an orthonormal (u, v) spanning the plane normal to ``axis``.

    For ``axis == +Z`` this returns the world X and Y axes, so angles measured
    with ``atan2(dot(p, v), dot(p, u))`` are ordinary XY-plane angles.
    """
    n = _unit(axis)
    helper: Vec3 = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    v = _unit(_cross(n, helper))
    u = _unit(_cross(v, n))
    return u, v


# ---------------------------------------------------------------------------
# CSG dump parser
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """One node of a parsed CSG dump."""

    kind: str
    args: dict[Any, Any] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    transform: Mat4 | None = None
    modifier: str = ""

    def walk(self) -> "list[Node]":
        """Return this node and all descendants, depth first."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


_NAME_RE = re.compile(r"([$A-Za-z_][$A-Za-z0-9_]*)\s*\(")
_MODIFIERS = "%#!*"


def _strip_comments(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_top(text: str) -> list[str]:
    """Split on commas that are not inside brackets or strings."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            cur.append(text[i:j])
            i = j
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur)
    if tail.strip():
        parts.append(tail)
    return parts


def _value(token: str) -> Any:
    tok = token.strip()
    if not tok:
        return None
    if tok.startswith("["):
        return [_value(part) for part in _split_top(tok[1:-1])]
    if tok.startswith('"'):
        return tok[1:-1]
    if tok == "true":
        return True
    if tok == "false":
        return False
    if tok == "undef":
        return None
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_args(text: str) -> dict[Any, Any]:
    """Parse a CSG argument list into ``{name: value}`` plus positional ints."""
    args: dict[Any, Any] = {}
    for index, part in enumerate(_split_top(text)):
        if re.match(r"\s*[$A-Za-z_][$A-Za-z0-9_]*\s*=", part):
            name, raw = part.split("=", 1)
            args[name.strip()] = _value(raw)
        else:
            args[index] = _value(part)
    return args


def _vec3(value: Any, default: float = 0.0) -> Vec3:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value), float(value), float(value))
    if isinstance(value, (list, tuple)):
        nums = [float(x) if isinstance(x, (int, float)) else default for x in value[:3]]
        while len(nums) < 3:
            nums.append(default)
        return (nums[0], nums[1], nums[2])
    return (default, default, default)


def _arg(args: dict[Any, Any], *names: Any, default: Any = None) -> Any:
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    return default


def _maybe_num(value: Any, default: float | None = None) -> float | None:
    """Return ``value`` as a float, or ``default`` if it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _num(value: Any, default: float = 0.0) -> float:
    """Return ``value`` as a float, or ``default`` if it is not a number."""
    result = _maybe_num(value, default)
    return default if result is None else result


def _local_transform(kind: str, args: dict[Any, Any]) -> Mat4 | None:
    """Return the 4x4 a transform node contributes, or None if it is not one."""
    if kind == "multmatrix":
        raw = _arg(args, 0, "m")
        if not isinstance(raw, list):
            return IDENTITY
        rows: list[tuple[float, ...]] = []
        for row in raw[:4]:
            values = [float(x) if isinstance(x, (int, float)) else 0.0 for x in row[:4]]
            while len(values) < 4:
                values.append(1.0 if len(values) == len(rows) else 0.0)
            rows.append(tuple(values))
        while len(rows) < 4:
            rows.append((0.0, 0.0, 0.0, 1.0))
        return tuple(rows)
    if kind == "translate":
        return _translation(_vec3(_arg(args, 0, "v")))
    if kind == "scale":
        return _scaling(_vec3(_arg(args, 0, "v", default=1.0), default=1.0))
    if kind == "mirror":
        return _mirror(_vec3(_arg(args, 0, "v")))
    if kind == "rotate":
        angle = _arg(args, "a", 0)
        axis = _arg(args, "v", 1)
        if isinstance(angle, list):
            return _rotation_xyz(_vec3(angle))
        value = float(angle) if isinstance(angle, (int, float)) else 0.0
        if axis is not None:
            return _rotation_axis(_vec3(axis), value)
        return _rotation_axis((0.0, 0.0, 1.0), value)
    if kind == "color":
        return IDENTITY
    return None


def parse_csg(text: str) -> Node:
    """Parse a CSG dump into a tree rooted at a synthetic ``root`` node."""
    src = _strip_comments(text)
    root = Node("root")
    stack: list[Node] = [root]
    modifier = ""
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch in _MODIFIERS:
            modifier = ch
            i += 1
            continue
        if ch == "}":
            if len(stack) > 1:
                stack.pop()
            i += 1
            continue
        if ch == ";":
            i += 1
            continue
        match = _NAME_RE.match(src, i)
        if match is None:
            i += 1
            continue
        kind = match.group(1)
        start = match.end()
        j = start
        depth = 1
        while j < n and depth:
            c = src[j]
            if c == '"':
                j += 1
                while j < n and src[j] != '"':
                    j += 2 if src[j] == "\\" else 1
                j += 1
                continue
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            j += 1
        args = parse_args(src[start : j - 1])
        node = Node(kind=kind, args=args, transform=_local_transform(kind, args), modifier=modifier)
        modifier = ""
        stack[-1].children.append(node)
        k = j
        while k < n and src[k].isspace():
            k += 1
        if k < n and src[k] == "{":
            stack.append(node)
            i = k + 1
        elif k < n and src[k] == ";":
            i = k + 1
        else:
            i = k
    return root


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


@dataclass
class CylFeature:
    """One resolved cylindrical feature in the frame of the dump."""

    kind: str
    polarity: str
    axis_point: Vec3
    axis_dir: Vec3
    entry: Vec3
    exit: Vec3
    nominal_d_mm: float
    length_mm: float
    segments: int
    fn: int
    fa: float
    fs: float
    effective_min_d_mm: float
    undersize_mm: float
    path: list[str]
    center: bool
    # Optional, filled in by callers that know the surrounding solid.
    through: bool | None = None

    @property
    def radius_mm(self) -> float:
        return self.nominal_d_mm / 2.0


@dataclass
class FeatureSet:
    """Everything :func:`extract_features` recovered from one dump."""

    features: list[CylFeature] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def subtractive(self) -> list[CylFeature]:
        return [f for f in self.features if f.polarity == "subtractive"]

    @property
    def additive(self) -> list[CylFeature]:
        return [f for f in self.features if f.polarity == "additive"]


_TRANSFORM_OPS = frozenset({"multmatrix", "translate", "rotate", "scale", "mirror", "color"})
_MASK_OPS = frozenset({"hull", "minkowski"})
_PASSTHROUGH_OPS = frozenset({"root", "group", "union", "render", "offset", "resize"})
_OTHER_PRIMITIVES = frozenset(
    {"cube", "sphere", "polyhedron", "polygon", "square", "circle", "text", "import", "surface"}
)
_PATH_OPS = _MASK_OPS | {"render", "offset", "resize", "projection", "linear_extrude"}


class _Ctx:
    def __init__(self, mask_hull: bool, stub_ratio: float) -> None:
        self.mask_hull = mask_hull
        self.stub_ratio = stub_ratio
        self.features: list[CylFeature] = []
        self.unresolved: Counter[tuple[str, str, str, str]] = Counter()
        self.counts: Counter[str] = Counter()

    def unresolve(self, op: str, kind: str, reason: str, detail: str) -> None:
        self.unresolved[(op, kind, reason, detail)] += 1


def _fragments(radius: float, fn: float, fa: float, fs: float) -> int:
    """Facet count OpenSCAD would use, via :func:`openscad_mcp.geom.segments_for`."""
    return geom.segments_for(
        radius,
        fn=int(fn) if fn > 0 else 0,
        fa=fa if fa > 0 else DEFAULT_FA,
        fs=fs if fs > 0 else DEFAULT_FS,
    )


def _orient(p0: Vec3, p1: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    """Canonicalise (entry, exit, direction) so the direction points 'downward'."""
    direction = _unit(_sub(p1, p0))
    for idx in (2, 1, 0):
        if abs(direction[idx]) > 1e-9:
            if direction[idx] > 0:
                return p1, p0, _scale_vec(direction, -1.0)
            return p0, p1, direction
    return p0, p1, direction


def _radial_semi_axes(matrix: Mat4, radius: float) -> tuple[float, float]:
    """Semi-axes of the ellipse the local radius-``radius`` circle maps to."""
    u = _apply_dir(matrix, (1.0, 0.0, 0.0))
    v = _apply_dir(matrix, (0.0, 1.0, 0.0))
    a, b, c = _dot(u, u), _dot(u, v), _dot(v, v)
    disc = math.sqrt(max((a - c) ** 2 + 4.0 * b * b, 0.0))
    hi = max((a + c + disc) / 2.0, 0.0)
    lo = max((a + c - disc) / 2.0, 0.0)
    return radius * math.sqrt(lo), radius * math.sqrt(hi)


def _cylinder_params(args: dict[Any, Any]) -> tuple[float, float, float, bool, float, float, float]:
    """Return (h, r1, r2, center, $fn, $fa, $fs) from a ``cylinder`` argument dict."""
    height = _num(_arg(args, "h", 0), 1.0)
    r1: float | None = _maybe_num(_arg(args, 1))
    r2: float | None = _maybe_num(_arg(args, 2))
    radius = _maybe_num(_arg(args, "r"))
    if radius is not None:
        r1 = r2 = radius
    r1 = _maybe_num(_arg(args, "r1"), r1)
    r2 = _maybe_num(_arg(args, "r2"), r2)
    diameter = _maybe_num(_arg(args, "d"))
    if diameter is not None:
        r1 = r2 = diameter / 2.0
    d1 = _maybe_num(_arg(args, "d1"))
    d2 = _maybe_num(_arg(args, "d2"))
    if d1 is not None:
        r1 = d1 / 2.0
    if d2 is not None:
        r2 = d2 / 2.0
    if r1 is None:
        r1 = 1.0
    if r2 is None:
        r2 = r1
    center = bool(_arg(args, "center", default=False))
    fn = _num(_arg(args, "$fn"), 0.0)
    fa = _num(_arg(args, "$fa"), DEFAULT_FA) or DEFAULT_FA
    fs = _num(_arg(args, "$fs"), DEFAULT_FS) or DEFAULT_FS
    return height, r1, r2, center, fn, fa, fs


def _emit_cylinder(
    ctx: _Ctx,
    op: str,
    matrix: Mat4,
    polarity: int,
    path: tuple[str, ...],
    height: float,
    r1: float,
    r2: float,
    center: bool,
    fn: float,
    fa: float,
    fs: float,
) -> None:
    ctx.counts["cylinders_raw"] += 1
    pol = "subtractive" if polarity < 0 else "additive"
    if ctx.mask_hull and any(p in _MASK_OPS for p in path):
        ctx.counts["masked_hull"] += 1
        return
    if abs(r1 - r2) > 1e-9:
        ctx.counts["unresolved"] += 1
        ctx.unresolve(op, "cone", "d1 != d2", f"{pol} cone d1={2 * r1:.3f} d2={2 * r2:.3f}")
        return
    radius = r1
    lo, hi = _radial_semi_axes(matrix, radius)
    if hi > _EPS and (hi - lo) > 1e-6 * hi:
        ctx.counts["unresolved"] += 1
        ctx.unresolve(
            op,
            "elliptical_feature",
            "non-uniform scale in the plane of the circle",
            f"{pol} ellipse semi-axes {lo:.4f} x {hi:.4f} mm",
        )
        return
    z0 = -height / 2.0 if center else 0.0
    p0 = _apply_point(matrix, (0.0, 0.0, z0))
    p1 = _apply_point(matrix, (0.0, 0.0, z0 + height))
    length = _length(_sub(p1, p0))
    diameter = 2.0 * hi
    if length <= _EPS or diameter <= _EPS:
        ctx.counts["dropped_degenerate"] += 1
        return
    if length < ctx.stub_ratio * diameter:
        ctx.counts["dropped_stub"] += 1
        return
    entry, exit_, direction = _orient(p0, p1)
    segments = _fragments(radius, fn, fa, fs)
    undersize = 2.0 * geom.inscribed_polygon_error(diameter / 2.0, segments)
    effective = diameter - undersize
    ctx.counts[pol] += 1
    ctx.features.append(
        CylFeature(
            kind="cylinder",
            polarity=pol,
            axis_point=entry,
            axis_dir=direction,
            entry=entry,
            exit=exit_,
            nominal_d_mm=diameter,
            length_mm=length,
            segments=segments,
            fn=int(fn),
            fa=fa,
            fs=fs,
            effective_min_d_mm=effective,
            undersize_mm=undersize,
            path=list(path),
            center=center,
        )
    )


def _collect_2d(node: Node, matrix: Mat4, out: list[tuple[str, Node, Mat4]]) -> bool:
    """Collect 2D primitives under ``node``. Returns False if a boolean is in the way."""
    for child in node.children:
        if child.modifier in ("%", "*"):
            continue
        kind = child.kind
        if kind in _TRANSFORM_OPS and child.transform is not None:
            if not _collect_2d(child, _compose(matrix, child.transform), out):
                return False
            continue
        if kind in ("circle", "square", "polygon", "text", "import", "projection"):
            out.append((kind, child, matrix))
            continue
        if kind in _PASSTHROUGH_OPS:
            if not _collect_2d(child, matrix, out):
                return False
            continue
        return False
    return True


def _handle_linear_extrude(
    ctx: _Ctx, node: Node, matrix: Mat4, polarity: int, path: tuple[str, ...]
) -> None:
    args = node.args
    pol = "subtractive" if polarity < 0 else "additive"
    height = float(_arg(args, "height", 0, default=0.0) or 0.0)
    center = bool(_arg(args, "center", default=False))
    twist = float(_arg(args, "twist", default=0.0) or 0.0)
    scale = _vec3(_arg(args, "scale", default=1.0), default=1.0)
    shapes: list[tuple[str, Node, Mat4]] = []
    simple = (
        abs(twist) < _EPS and abs(scale[0] - 1.0) < 1e-9 and abs(scale[1] - 1.0) < 1e-9
    ) and _collect_2d(node, IDENTITY, shapes)
    if simple and len(shapes) == 1 and shapes[0][0] == "circle":
        _, circle, rel = shapes[0]
        radius = float(_arg(circle.args, "r", 0, default=None) or 0.0)
        d = _arg(circle.args, "d")
        if d is not None:
            radius = float(d) / 2.0
        fn = float(_arg(circle.args, "$fn", default=_arg(args, "$fn", default=0.0)) or 0.0)
        fa = float(_arg(circle.args, "$fa", default=DEFAULT_FA) or DEFAULT_FA)
        fs = float(_arg(circle.args, "$fs", default=DEFAULT_FS) or DEFAULT_FS)
        _emit_cylinder(
            ctx,
            "linear_extrude",
            _compose(matrix, rel),
            polarity,
            path,
            height,
            radius,
            radius,
            center,
            fn,
            fa,
            fs,
        )
        return
    if not simple:
        profile = "a twisted/scaled or booleaned 2D profile"
    elif not shapes:
        profile = "an empty profile"
    elif len(shapes) == 1:
        profile = f"a {shapes[0][0]}"
    else:
        profile = f"{len(shapes)} 2D shapes ({', '.join(sorted({s[0] for s in shapes}))})"
    ctx.counts["unresolved"] += 1
    ctx.unresolve(
        "linear_extrude",
        "extrusion",
        "profile is not a single circle",
        f"{pol} linear_extrude height={height:g} of {profile}",
    )


def _walk(ctx: _Ctx, node: Node, matrix: Mat4, polarity: int, path: tuple[str, ...]) -> None:
    if node.modifier in ("%", "*"):
        ctx.counts["skipped_modifier"] += 1
        return
    kind = node.kind
    ctx.counts["nodes"] += 1

    if kind in _TRANSFORM_OPS and node.transform is not None:
        matrix = _compose(matrix, node.transform)
    elif kind == "cylinder":
        height, r1, r2, center, fn, fa, fs = _cylinder_params(node.args)
        _emit_cylinder(ctx, kind, matrix, polarity, path, height, r1, r2, center, fn, fa, fs)
        return
    elif kind == "linear_extrude":
        _handle_linear_extrude(ctx, node, matrix, polarity, path + ("linear_extrude",))
        return
    elif kind == "rotate_extrude":
        pol = "subtractive" if polarity < 0 else "additive"
        angle = float(_arg(node.args, "angle", default=360.0) or 360.0)
        ctx.counts["unresolved"] += 1
        ctx.unresolve(
            kind,
            "revolve",
            "rotate_extrude profiles are not analysed in v1",
            f"{pol} rotate_extrude angle={angle:g}",
        )
        return
    elif kind in _OTHER_PRIMITIVES:
        ctx.counts[f"primitive_{kind}"] += 1
        return

    if kind in _PATH_OPS:
        path = path + (kind,)

    if kind == "difference":
        for index, child in enumerate(node.children):
            child_polarity = polarity if index == 0 else -polarity
            child_path = path if index == 0 else path + (f"difference[{index}]",)
            _walk(ctx, child, matrix, child_polarity, child_path)
        return
    if kind == "intersection":
        for index, child in enumerate(node.children):
            _walk(ctx, child, matrix, polarity, path + (f"intersection[{index}]",))
        return
    for child in node.children:
        _walk(ctx, child, matrix, polarity, path)


def extract_features(text: str, mask_hull: bool = True, stub_ratio: float = 0.05) -> FeatureSet:
    """Extract cylindrical features from the text of a CSG dump.

    Args:
        text: Contents of an ``--export-format=csg`` dump.
        mask_hull: Drop cylinders under a ``hull`` or ``minkowski``. BOSL2's
            rounded cuboids are hulls of tiny corner cylinders, so leaving this
            on removes most of the false holes on real files.
        stub_ratio: Drop cylinders shorter than ``stub_ratio * diameter``.

    Returns:
        A :class:`FeatureSet`.
    """
    root = parse_csg(text)
    ctx = _Ctx(mask_hull=mask_hull, stub_ratio=stub_ratio)
    _walk(ctx, root, IDENTITY, 1, ())
    unresolved = [
        {"op": op, "kind": kind, "count": count, "reason": reason, "detail": detail}
        for (op, kind, reason, detail), count in sorted(
            ctx.unresolved.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    counts = dict(ctx.counts)
    counts["features"] = len(ctx.features)
    counts.setdefault("cylinders_raw", 0)
    counts.setdefault("additive", 0)
    counts.setdefault("subtractive", 0)
    counts.setdefault("masked_hull", 0)
    counts.setdefault("dropped_stub", 0)
    counts.setdefault("unresolved", 0)
    return FeatureSet(features=ctx.features, unresolved=unresolved, counts=counts)


def extract_from_scad(
    scad_path: str | Path,
    openscad: str = "openscad",
    timeout: int = 120,
    mask_hull: bool = True,
    stub_ratio: float = 0.05,
) -> FeatureSet:
    """Export ``scad_path`` to CSG with OpenSCAD and extract its features.

    The server wraps user code (``include`` plus a ``!`` root modifier) before
    calling :func:`extract_features`; this helper is the standalone path used by
    tests and the command line, and expects geometry at the top level of the
    file.

    Raises:
        RuntimeError: If OpenSCAD exits non-zero or writes no dump.
    """
    path = Path(scad_path)
    with tempfile.TemporaryDirectory(prefix="csgfeat-") as tmp:
        out = Path(tmp) / (path.stem + ".csg")
        proc = subprocess.run(
            [openscad, "--export-format=csg", "-o", str(out), str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"openscad csg export failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[:800]}"
            )
        return extract_features(out.read_text(), mask_hull=mask_hull, stub_ratio=stub_ratio)


def transform_features(features: list[CylFeature], m: Mat4) -> list[CylFeature]:
    """Return copies of ``features`` moved by the 4x4 ``m``.

    Intended for placing a part's features into an assembly frame, so ``m``
    should be rigid or uniformly scaled.

    Raises:
        ValueError: If ``m`` scales the plane of a feature non-uniformly, which
            would turn the circle into an ellipse.
    """
    out: list[CylFeature] = []
    for f in features:
        entry = _apply_point(m, f.entry)
        exit_ = _apply_point(m, f.exit)
        new_entry, new_exit, direction = _orient(entry, exit_)
        u, v = _plane_basis(f.axis_dir)
        su, sv = _length(_apply_dir(m, u)), _length(_apply_dir(m, v))
        if abs(su - sv) > 1e-6 * max(su, sv, _EPS):
            raise ValueError(
                "transform scales the feature plane non-uniformly "
                f"({su:.6f} vs {sv:.6f}); the circle would become an ellipse"
            )
        scale = (su + sv) / 2.0
        diameter = f.nominal_d_mm * scale
        undersize = 2.0 * geom.inscribed_polygon_error(diameter / 2.0, f.segments)
        effective = diameter - undersize
        out.append(
            CylFeature(
                kind=f.kind,
                polarity=f.polarity,
                axis_point=new_entry,
                axis_dir=direction,
                entry=new_entry,
                exit=new_exit,
                nominal_d_mm=diameter,
                length_mm=_length(_sub(exit_, entry)),
                segments=f.segments,
                fn=f.fn,
                fa=f.fa,
                fs=f.fs,
                effective_min_d_mm=effective,
                undersize_mm=undersize,
                path=list(f.path),
                center=f.center,
                through=f.through,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Grouping and description
# ---------------------------------------------------------------------------


@dataclass
class PatternGroup:
    """Identical features collapsed into one row."""

    count: int
    polarity: str
    nominal_d_mm: float
    length_mm: float
    axis_dir: Vec3
    effective_min_d_mm: float
    undersize_mm: float
    entries: list[Vec3]
    features: list[CylFeature]
    bolt_circle: dict[str, Any] | None = None


def _bucket(value: float, tol: float) -> float:
    if tol <= 0:
        return round(value, 9)
    return round(value / tol) * tol


def _dir_key(direction: Vec3) -> tuple[float, float, float]:
    return tuple(round(c, 6) + 0.0 for c in direction)  # type: ignore[return-value]


def _canonical_dir(direction: Vec3) -> Vec3:
    """Return ``direction`` or its negation, whichever points 'positively'."""
    for idx in (2, 1, 0):
        if abs(direction[idx]) > 1e-9:
            return direction if direction[idx] > 0 else _scale_vec(direction, -1.0)
    return direction


def _fit_circle_2d(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Kasa algebraic circle fit. Returns (cx, cy, r, rms)."""
    n = len(points)
    if n < 3:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    u = [p[0] - mx for p in points]
    v = [p[1] - my for p in points]
    suu = sum(x * x for x in u)
    svv = sum(y * y for y in v)
    suv = sum(u[i] * v[i] for i in range(n))
    det = suu * svv - suv * suv
    if abs(det) < 1e-12:
        return None
    a = 0.5 * (sum(x**3 for x in u) + sum(u[i] * v[i] * v[i] for i in range(n)))
    b = 0.5 * (sum(y**3 for y in v) + sum(v[i] * u[i] * u[i] for i in range(n)))
    cu = (a * svv - b * suv) / det
    cv = (b * suu - a * suv) / det
    r = math.sqrt(cu * cu + cv * cv + (suu + svv) / n)
    rms = math.sqrt(sum((math.hypot(u[i] - cu, v[i] - cv) - r) ** 2 for i in range(n)) / n)
    return (cu + mx, cv + my, r, rms)


def _detect_bolt_circle(
    features: list[CylFeature], tol_mm: float, angle_tol_deg: float = 1.0
) -> dict[str, Any] | None:
    if len(features) < 3:
        return None
    axis = _canonical_dir(features[0].axis_dir)
    u, v = _plane_basis(axis)
    origin = features[0].axis_point
    flat = [
        (_dot(_sub(f.axis_point, origin), u), _dot(_sub(f.axis_point, origin), v)) for f in features
    ]
    fit = _fit_circle_2d(flat)
    if fit is None:
        return None
    cx, cy, radius, rms = fit
    if radius <= max(tol_mm, 1e-6):
        return None
    if rms > max(tol_mm, 0.02 * radius):
        return None
    angles = sorted((math.degrees(math.atan2(p[1] - cy, p[0] - cx)) + 360.0) % 360.0 for p in flat)
    step = 360.0 / len(angles)
    diffs = [(angles[(i + 1) % len(angles)] - angles[i]) % 360.0 for i in range(len(angles))]
    if any(abs(d - step) > angle_tol_deg for d in diffs):
        return None
    center = _add(origin, _add(_scale_vec(u, cx), _scale_vec(v, cy)))
    return {
        "center": tuple(round(c, 6) for c in center),
        "radius_mm": round(radius, 6),
        "angles_deg": [round(a, 3) for a in angles],
        "evenly_spaced": True,
        "axis_dir": tuple(round(c, 6) for c in axis),
        "rms_mm": round(rms, 6),
    }


def group_features(features: list[CylFeature], tol_mm: float = 0.01) -> list[PatternGroup]:
    """Collapse features with the same diameter, length, direction and polarity.

    Groups of three or more sharing a common centre at equal radius and equal
    angular spacing are additionally reported as a bolt circle.
    """
    buckets: dict[tuple[Any, ...], list[CylFeature]] = {}
    for f in features:
        key = (
            f.polarity,
            _bucket(f.nominal_d_mm, tol_mm),
            _bucket(f.length_mm, tol_mm),
            _dir_key(f.axis_dir),
        )
        buckets.setdefault(key, []).append(f)
    groups: list[PatternGroup] = []
    for members in buckets.values():
        first = members[0]
        groups.append(
            PatternGroup(
                count=len(members),
                polarity=first.polarity,
                nominal_d_mm=first.nominal_d_mm,
                length_mm=first.length_mm,
                axis_dir=first.axis_dir,
                effective_min_d_mm=first.effective_min_d_mm,
                undersize_mm=first.undersize_mm,
                entries=[f.entry for f in members],
                features=members,
                bolt_circle=_detect_bolt_circle(members, max(tol_mm, 0.05)),
            )
        )
    groups.sort(key=lambda g: (-g.count, -g.nominal_d_mm, g.polarity))
    return groups


_AXIS_NAMES = ("X", "Y", "Z")


def _dir_label(direction: Vec3) -> str:
    for idx in range(3):
        others = [abs(direction[j]) for j in range(3) if j != idx]
        if abs(abs(direction[idx]) - 1.0) < 1e-6 and max(others) < 1e-6:
            return f"{'+' if direction[idx] > 0 else '-'}{_AXIS_NAMES[idx]}"
    return "(" + ", ".join(f"{0.0 if abs(c) < 5e-4 else c:.3f}" for c in direction) + ")"


def _fmt_angle(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 5e-2 else f"{value:.1f}"


def _origin_label(group: PatternGroup) -> str:
    direction = group.axis_dir
    for idx in range(3):
        others = [abs(direction[j]) for j in range(3) if j != idx]
        if abs(abs(direction[idx]) - 1.0) < 1e-6 and max(others) < 1e-6:
            values = [e[idx] for e in group.entries]
            lo, hi = min(values), max(values)
            axis = _AXIS_NAMES[idx].lower()
            if hi - lo < 1e-6:
                return f"{axis}={lo:+.1f}"
            return f"{axis}={lo:+.1f}..{hi:+.1f}"
    point = group.entries[0]
    return "(" + ", ".join(f"{0.0 if abs(c) < 5e-2 else c:.1f}" for c in point) + ")"


def describe_group(group: PatternGroup) -> str:
    """One-line human description of a pattern group."""
    through = {f.through for f in group.features}
    if through == {True}:
        depth = f"through {group.length_mm:.1f} mm"
    elif through == {False}:
        depth = f"blind {group.length_mm:.1f} mm"
    else:
        depth = f"{group.length_mm:.1f} mm deep"
    boss = " boss" if group.polarity == "additive" else ""
    text = (
        f"{group.count}x D{group.nominal_d_mm:.2f}{boss} {depth} "
        f"from {_origin_label(group)} along {_dir_label(group.axis_dir)}"
    )
    circle = group.bolt_circle
    if circle:
        angles = "/".join(_fmt_angle(a) for a in circle["angles_deg"])
        text += f", bolt circle r={circle['radius_mm']:.1f} at {angles} deg"
    return text


# ---------------------------------------------------------------------------
# Fit classification
# ---------------------------------------------------------------------------

_FASTENER_ROLES: tuple[tuple[str, str, str], ...] = (
    ("tap drill", "tap_drill_mm", "threaded / self-tapping"),
    ("close clearance", "clearance_hole_close_mm", "clearance"),
    ("medium clearance", "clearance_hole_medium_mm", "clearance"),
    ("free clearance", "clearance_hole_free_mm", "clearance"),
    ("counterbore", "counterbore_diameter_mm", "counterbore / head clearance"),
)

_SHAFTS: tuple[tuple[str, float], ...] = (
    ("3 mm rod", 3.0),
    ("4 mm rod", 4.0),
    ("5 mm shaft", 5.0),
    ("6 mm rod", 6.0),
    ("8 mm rod", 8.0),
    ("10 mm rod", 10.0),
    ("12 mm rod", 12.0),
)


def _reference_lookup(topic: str) -> list[dict[str, Any]]:
    from . import reference  # local import: keeps the reference tables lazy

    return list(reference.lookup(topic)["entries"])


def _fit_for_clearance(diametral: float) -> str | None:
    """Name the fit class a diametral clearance falls in, or None if it is off the table."""
    best: tuple[float, str] | None = None
    for entry in _reference_lookup("fits"):
        span = entry.get("clearance_diametral_range_mm")
        if not span or not float(span[0]) - 1e-9 <= diametral <= float(span[1]) + 1e-9:
            continue
        nominal = float(entry.get("clearance_diametral_mm", 0.0))
        distance = abs(diametral - nominal)
        if best is None or distance < best[0]:
            best = (distance, str(entry["name"]))
    return best[1] if best else None


def fit_candidates(d_mm: float, tolerance_mm: float = 0.15, limit: int = 3) -> list[dict[str, Any]]:
    """Name the standard holes a diameter is closest to.

    Joins against the ``fasteners`` table (tap drills, ISO 273 clearance holes,
    counterbores) and, for plain bores, against the ``fits`` table to say what
    kind of fit the diameter would give on a standard shaft or bearing.

    Returns:
        Up to ``limit`` dicts of ``{"match", "nominal_mm", "delta_mm", "role"}``,
        closest first.
    """
    out: list[dict[str, Any]] = []
    for row in _reference_lookup("fasteners"):
        name = row["name"]
        for label, key, role in _FASTENER_ROLES:
            value = row.get(key)
            if not isinstance(value, (int, float)):
                continue
            delta = d_mm - float(value)
            if abs(delta) <= tolerance_mm:
                out.append(
                    {
                        "match": f"{name} {label}",
                        "nominal_mm": float(value),
                        "delta_mm": round(delta, 4),
                        "role": role,
                    }
                )
    bores: list[tuple[str, float]] = list(_SHAFTS)
    for row in _reference_lookup("bearings"):
        od = row.get("outer_diameter_mm")
        if isinstance(od, (int, float)):
            bores.append((f"{row['name']} bearing pocket", float(od)))
    for label, nominal in bores:
        delta = d_mm - nominal
        if not -0.12 <= delta <= 0.62:
            continue
        fit = _fit_for_clearance(delta)
        if fit is None:
            continue
        out.append(
            {
                "match": f"{label} ({fit})",
                "nominal_mm": nominal,
                "delta_mm": round(delta, 4),
                "role": fit,
            }
        )
    out.sort(key=lambda c: (abs(float(c["delta_mm"])), float(c["nominal_mm"]), c["match"]))
    return out[:limit]


def _short_fit_label(d_mm: float) -> str:
    best = fit_candidates(d_mm, limit=1)
    if not best:
        return f"D{d_mm:.2f}"
    match = str(best[0]["match"])
    name = match.split()[0]
    if "tap drill" in match:
        return f"{name} tap"
    if "clearance" in match:
        return f"{name} clearance"
    if "counterbore" in match:
        return f"{name} counterbore"
    return match


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _round_vec(v: Vec3, digits: int = 4) -> list[float]:
    return [round(c, digits) + 0.0 for c in v]


def _feature_dict(f: CylFeature) -> dict[str, Any]:
    return {
        "kind": f.kind,
        "polarity": f.polarity,
        "axis_point": _round_vec(f.axis_point),
        "axis_dir": _round_vec(f.axis_dir, 6),
        "entry": _round_vec(f.entry),
        "exit": _round_vec(f.exit),
        "nominal_d_mm": round(f.nominal_d_mm, 4),
        "length_mm": round(f.length_mm, 4),
        "segments": f.segments,
        "fn": f.fn,
        "fa": f.fa,
        "fs": f.fs,
        "effective_min_d_mm": round(f.effective_min_d_mm, 4),
        "undersize_mm": round(f.undersize_mm, 4),
        "through": f.through,
        "path": list(f.path),
        "center": f.center,
    }


def _group_dict(group: PatternGroup) -> dict[str, Any]:
    return {
        "count": group.count,
        "polarity": group.polarity,
        "d_mm": round(group.nominal_d_mm, 4),
        "length_mm": round(group.length_mm, 4),
        "axis_dir": _round_vec(group.axis_dir, 6),
        "entries": [_round_vec(e) for e in group.entries],
        "segments": group.features[0].segments,
        "effective_min_d_mm": round(group.effective_min_d_mm, 4),
        "undersize_mm": round(group.undersize_mm, 4),
        "bolt_circle": group.bolt_circle,
        "fit_candidates": fit_candidates(group.nominal_d_mm),
        "description": describe_group(group),
    }


def to_dict(featureset: FeatureSet, detailed: bool = False) -> dict[str, Any]:
    """Serialise a :class:`FeatureSet`.

    Concise output is one row per pattern plus a one-line description of each;
    ``detailed`` adds every individual feature.
    """
    groups = group_features(featureset.features)
    result: dict[str, Any] = {
        "counts": dict(featureset.counts),
        "patterns": [_group_dict(g) for g in groups],
        "descriptions": [describe_group(g) for g in groups],
        "unresolved": list(featureset.unresolved),
    }
    if detailed:
        result["features"] = [_feature_dict(f) for f in featureset.features]
    return result


# ---------------------------------------------------------------------------
# Cross-part alignment
# ---------------------------------------------------------------------------


def _foot(point: Vec3, direction: Vec3) -> Vec3:
    """Point on the line closest to the origin."""
    return _sub(point, _scale_vec(direction, _dot(point, direction)))


def _perp_distance(p_a: Vec3, p_b: Vec3, direction: Vec3) -> float:
    delta = _sub(p_a, p_b)
    return _length(_sub(delta, _scale_vec(direction, _dot(delta, direction))))


def _span(f: CylFeature, direction: Vec3) -> tuple[float, float]:
    a, b = _dot(f.entry, direction), _dot(f.exit, direction)
    return (min(a, b), max(a, b))


def _align_entry(part: str, f: CylFeature) -> dict[str, Any]:
    return {
        "part": part,
        "d": round(f.nominal_d_mm, 4),
        "entry": _round_vec(f.entry),
        "exit": _round_vec(f.exit),
        "polarity": f.polarity,
        "length_mm": round(f.length_mm, 4),
    }


def align_features(
    parts: dict[str, list[CylFeature]],
    tolerance_mm: float = 0.2,
    near_miss_mm: float = 2.0,
    max_reported: int = 50,
) -> dict[str, Any]:
    """Line features up across parts of an assembly.

    Every part's features must already be in the assembly frame (see
    :func:`transform_features`).

    Returns:
        ``{"axes", "misaligned", "orphans", "counts"}``. ``axes`` holds shared
        axes carrying features from two or more parts, ordered along the axis,
        with a plain-language ``reading`` such as
        ``"M3 tap / M3 clearance stack through base, platform"``. ``misaligned``
        holds pairs from different parts that are parallel and overlap along the
        axis but sit between ``tolerance_mm`` and ``near_miss_mm`` apart.
        ``orphans`` holds subtractive features with no partner in another part.
        ``misaligned`` and ``orphans`` are truncated to ``max_reported`` entries
        (the full counts stay in ``counts``).
    """
    items: list[tuple[str, CylFeature]] = [
        (part, f) for part, feats in parts.items() for f in feats
    ]
    parallel_cos = math.cos(math.radians(0.5))

    clusters: list[dict[str, Any]] = []
    for part, f in items:
        direction = _canonical_dir(f.axis_dir)
        placed = False
        for cluster in clusters:
            if abs(_dot(cluster["dir"], direction)) < parallel_cos:
                continue
            if _perp_distance(f.axis_point, cluster["point"], cluster["dir"]) > tolerance_mm:
                continue
            cluster["members"].append((part, f))
            placed = True
            break
        if not placed:
            clusters.append({"dir": direction, "point": f.axis_point, "members": [(part, f)]})

    axes: list[dict[str, Any]] = []
    for cluster in clusters:
        members = sorted(cluster["members"], key=lambda m: _dot(m[1].axis_point, cluster["dir"]))
        part_names = list(dict.fromkeys(part for part, _ in members))
        if len(part_names) < 2:
            continue
        labels = [_short_fit_label(f.nominal_d_mm) for _, f in members]
        reading = " / ".join(labels) + " stack through " + ", ".join(part_names)
        axes.append(
            {
                "axis_dir": _round_vec(cluster["dir"], 6),
                "through_point": _round_vec(_foot(cluster["point"], cluster["dir"])),
                "features": [_align_entry(part, f) for part, f in members],
                "diameters_mm": [round(f.nominal_d_mm, 4) for _, f in members],
                "parts": part_names,
                "reading": reading,
            }
        )
    axes.sort(key=lambda a: (-len(a["features"]), a["through_point"]))

    aligned_ids = {
        id(f)
        for cluster in clusters
        for _, f in cluster["members"]
        if len({part for part, _ in cluster["members"]}) >= 2
    }

    misaligned: list[dict[str, Any]] = []
    near_ids: set[int] = set()
    for i in range(len(items)):
        part_a, f_a = items[i]
        dir_a = _canonical_dir(f_a.axis_dir)
        for j in range(i + 1, len(items)):
            part_b, f_b = items[j]
            if part_a == part_b:
                continue
            dir_b = _canonical_dir(f_b.axis_dir)
            if abs(_dot(dir_a, dir_b)) < parallel_cos:
                continue
            offset = _perp_distance(f_a.axis_point, f_b.axis_point, dir_a)
            if offset <= tolerance_mm or offset > near_miss_mm:
                continue
            lo_a, hi_a = _span(f_a, dir_a)
            lo_b, hi_b = _span(f_b, dir_a)
            if hi_a < lo_b - near_miss_mm or hi_b < lo_a - near_miss_mm:
                continue
            near_ids.add(id(f_a))
            near_ids.add(id(f_b))
            misaligned.append(
                {
                    "a": _align_entry(part_a, f_a),
                    "b": _align_entry(part_b, f_b),
                    "offset_mm": round(offset, 4),
                }
            )
    misaligned.sort(key=lambda m: m["offset_mm"])

    orphans = [
        _align_entry(part, f)
        for part, f in items
        if f.polarity == "subtractive" and id(f) not in aligned_ids and id(f) not in near_ids
    ]

    return {
        "axes": axes,
        "misaligned": misaligned[:max_reported],
        "orphans": orphans[:max_reported],
        "counts": {
            "parts": len(parts),
            "features": len(items),
            "axes": len(axes),
            "misaligned": len(misaligned),
            "orphans": len(orphans),
        },
    }


def _main(argv: list[str]) -> int:  # pragma: no cover - convenience entry point
    import json
    import sys

    if not argv:
        print("usage: python -m openscad_mcp.csgfeatures FILE.csg|FILE.scad [--detailed]")
        return 2
    path = Path(argv[0])
    detailed = "--detailed" in argv[1:]
    fset = (
        extract_from_scad(path)
        if path.suffix.lower() == ".scad"
        else extract_features(path.read_text())
    )
    json.dump(to_dict(fset, detailed=detailed), sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
