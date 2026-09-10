"""Exact mass properties from a triangle soup.

Volume, centre of mass and the full inertia tensor of a closed triangle mesh,
computed by signed-tetrahedron decomposition. Everything here is standard
library only and single pass, so an 86k-triangle export is integrated in about
0.05 s -- an order of magnitude less than parsing the STL that produced it.

Three things make the numbers trustworthy rather than merely plausible:

* **Integration about the bounding-box centre.** A part modelled at
  ``[1000, 2000, 3000]`` shows products of inertia around 1e-6 relative where
  symmetry says exactly zero, because the tetrahedra are then huge compared to
  the part. Shifting to the bbox centre first and translating the result back
  drives those to ~1e-17.
* **A watertightness flag, not a silent answer.** Deleting 40 facets from a
  1208-triangle mesh moves its volume by 58% with no error anywhere. The flag
  is reported; refusing or warning is the caller's decision.
* **Exact composition.** Per-part results combine through the parallel-axis
  theorem, so an assembly never needs a whole-assembly export and per-part
  material or mass overrides stay exact.

Typical use::

    mp = mass_properties(load_stl("platform.stl"), material="PLA")
    j = mp.inertia_about_axis((0, 0, 0), (0, 0, 1))   # g*mm^2 about the Z axis
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .mesh import MATERIAL_DENSITIES, Triangle, analyze_triangles, mass_from_volume

__all__ = [
    "MassProperties",
    "compose",
    "mass_properties",
    "point_mass",
    "tip_margin",
]

Vec3 = tuple[float, float, float]
Matrix3 = list[list[float]]

#: Volumes below this (mm^3) are treated as no material at all.
_VOLUME_EPSILON = 1e-12


def _normalize_triangles(tris: Iterable[Any]) -> list[tuple[Vec3, Vec3, Vec3]]:
    """Accept :class:`~openscad_mcp.mesh.Triangle` objects or plain vertex triples."""
    out: list[tuple[Vec3, Vec3, Vec3]] = []
    for tri in tris:
        if isinstance(tri, Triangle):
            out.append((tri.v0, tri.v1, tri.v2))
        else:
            a, b, c = tri
            out.append((a, b, c))
    return out


def _jacobi_eigen(matrix: Matrix3) -> tuple[list[float], list[Vec3]]:
    """Eigenvalues and orthonormal eigenvectors of a symmetric 3x3 matrix.

    Cyclic Jacobi rotations; converges in a handful of sweeps for 3x3 and costs
    about 0.1 ms. Results are sorted by ascending eigenvalue.

    Args:
        matrix: A symmetric 3x3 matrix as a list of three rows.

    Returns:
        ``(eigenvalues, eigenvectors)`` with eigenvalues ascending and each
        eigenvector a unit 3-tuple, matched by position.
    """
    a = [list(row) for row in matrix]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(64):
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        if off < 1e-18:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            apq = a[p][q]
            if abs(apq) < 1e-300:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * apq)
            t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                akp = a[k][p]
                akq = a[k][q]
                a[k][p] = c * akp - s * akq
                a[k][q] = s * akp + c * akq
            for k in range(3):
                apk = a[p][k]
                aqk = a[q][k]
                a[p][k] = c * apk - s * aqk
                a[q][k] = s * apk + c * aqk
            for k in range(3):
                vkp = v[k][p]
                vkq = v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq

    values = [a[0][0], a[1][1], a[2][2]]
    vectors = [(v[0][i], v[1][i], v[2][i]) for i in range(3)]
    order = sorted(range(3), key=lambda i: values[i])
    axes: list[Vec3] = []
    for i in order:
        x, y, z = vectors[i]
        length = math.sqrt(x * x + y * y + z * z)
        axes.append((x / length, y / length, z / length) if length else (0.0, 0.0, 0.0))
    return [values[i] for i in order], axes


def _round(value: float, ndigits: int | None) -> float:
    """Round a float when ``ndigits`` is set, otherwise pass it through."""
    return value if ndigits is None else round(value, ndigits)


def _round_vec(vec: Sequence[float], ndigits: int | None) -> list[float]:
    """Round a coordinate tuple to a JSON-safe list."""
    if ndigits is None:
        return [float(v) for v in vec]
    return [round(float(v), ndigits) for v in vec]


@dataclass
class MassProperties:
    """Volume, mass, centre of mass and inertia of one body.

    The inertia tensor is about the centre of mass, in the model's own axes,
    in g*mm^2. Multiply by 1e-9 for kg*m^2, the unit a motor-sizing calculation
    wants.
    """

    volume_mm3: float
    mass_g: float
    density_g_cm3: float
    center_of_mass: Vec3
    inertia_about_com_g_mm2: Matrix3
    principal_moments: list[float]
    principal_axes: list[Vec3]
    is_watertight: bool
    triangle_count: int

    def inertia_about_axis(self, point: Vec3, direction: Vec3) -> float:
        """Moment of inertia about an arbitrary line, in g*mm^2.

        The tensor is projected onto the axis direction to get the moment about
        the parallel line through the centre of mass, then the parallel-axis
        theorem moves it to the requested line.

        Args:
            point: Any point on the line.
            direction: Line direction; need not be a unit vector.

        Returns:
            The moment of inertia in g*mm^2.

        Raises:
            ValueError: If ``direction`` has (near) zero length.
        """
        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-15:
            raise ValueError(f"axis direction must be non-zero, got {direction!r}")
        ux, uy, uz = dx / length, dy / length, dz / length

        tensor = self.inertia_about_com_g_mm2
        about_com = (
            tensor[0][0] * ux * ux
            + tensor[1][1] * uy * uy
            + tensor[2][2] * uz * uz
            + 2.0 * tensor[0][1] * ux * uy
            + 2.0 * tensor[0][2] * ux * uz
            + 2.0 * tensor[1][2] * uy * uz
        )

        wx = self.center_of_mass[0] - point[0]
        wy = self.center_of_mass[1] - point[1]
        wz = self.center_of_mass[2] - point[2]
        along = wx * ux + wy * uy + wz * uz
        rx, ry, rz = wx - along * ux, wy - along * uy, wz - along * uz
        return about_com + self.mass_g * (rx * rx + ry * ry + rz * rz)

    def to_dict(self, detailed: bool = True) -> dict[str, Any]:
        """Return a JSON-safe dict.

        Args:
            detailed: When True, include the full 3x3 tensor and the principal
                axes at full precision. When False, round to 6 decimals and omit
                the tensor and the axes, keeping mass, centre of mass and the
                principal moments -- the answer a sizing question actually uses.
        """
        ndigits = None if detailed else 6
        result: dict[str, Any] = {
            "volume_mm3": _round(self.volume_mm3, ndigits),
            "mass_g": _round(self.mass_g, ndigits),
            "density_g_cm3": _round(self.density_g_cm3, ndigits),
            "center_of_mass": _round_vec(self.center_of_mass, ndigits),
            "principal_moments_g_mm2": _round_vec(self.principal_moments, ndigits),
            "is_watertight": self.is_watertight,
            "triangle_count": self.triangle_count,
        }
        if detailed:
            result["inertia_about_com_g_mm2"] = [
                _round_vec(row, ndigits) for row in self.inertia_about_com_g_mm2
            ]
            result["principal_axes"] = [_round_vec(axis, ndigits) for axis in self.principal_axes]
        return result


def _resolve_density(
    volume_mm3: float,
    density_g_cm3: float | None,
    material: str | None,
    mass_g: float | None,
) -> tuple[float, float]:
    """Work out ``(density_g_cm3, mass_g)`` from whichever input was supplied."""
    if mass_g is not None:
        if mass_g < 0:
            raise ValueError(f"mass_g must not be negative, got {mass_g}")
        if volume_mm3 <= _VOLUME_EPSILON:
            raise ValueError(
                "mass_g cannot be applied to a mesh with no enclosed volume; "
                "use point_mass() for a body given only as a mass"
            )
        return mass_g / (volume_mm3 / 1000.0), mass_g

    if density_g_cm3 is None and material is not None:
        key = material.strip()
        density_g_cm3 = MATERIAL_DENSITIES.get(key)
        if density_g_cm3 is None:
            lowered = {name.lower(): value for name, value in MATERIAL_DENSITIES.items()}
            density_g_cm3 = lowered.get(key.lower())
        if density_g_cm3 is None:
            known = ", ".join(sorted(MATERIAL_DENSITIES))
            raise ValueError(f"unknown material {material!r}; known materials: {known}")

    if density_g_cm3 is None:
        known = ", ".join(sorted(MATERIAL_DENSITIES))
        raise ValueError(
            "one of density_g_cm3, material or mass_g is required " f"(known materials: {known})"
        )
    if density_g_cm3 <= 0:
        raise ValueError(f"density_g_cm3 must be positive, got {density_g_cm3}")
    return density_g_cm3, mass_from_volume(volume_mm3, density_g_cm3)


def mass_properties(
    tris: Iterable[Any],
    density_g_cm3: float | None = None,
    material: str | None = None,
    mass_g: float | None = None,
    *,
    watertight: bool | None = None,
) -> MassProperties:
    """Volume, centre of mass and inertia tensor of a closed triangle mesh.

    Each triangle forms a tetrahedron with a reference point; the signed volumes
    and second moments of those tetrahedra sum to the body's, exactly, for any
    closed and consistently oriented mesh. The reference point is the bounding
    box centre rather than the world origin, which keeps the cancellation
    well conditioned for parts modelled far from the origin.

    A mesh with inverted normals (negative signed volume) is measured as the
    solid it outlines: the volume and the tensor are both negated, which is the
    exact result for a consistently flipped mesh.

    Exactly one of ``density_g_cm3``, ``material`` and ``mass_g`` decides the
    scale. ``mass_g`` wins over the other two and rescales the density to match
    the measured volume, which is how a weighed part or a purchased component
    with a datasheet mass is entered.

    Args:
        tris: Triangles as vertex triples or :class:`~openscad_mcp.mesh.Triangle`.
        density_g_cm3: Material density in g/cm^3.
        material: A key of :data:`~openscad_mcp.mesh.MATERIAL_DENSITIES`,
            matched case-insensitively.
        mass_g: A known total mass, overriding the density path.
        watertight: Pass the answer in when the caller has already run
            :func:`~openscad_mcp.mesh.analyze_triangles`, to skip a second edge
            census (about 0.38 s on 86k triangles). ``None`` computes it.

    Returns:
        A :class:`MassProperties`. ``is_watertight`` is reported, never enforced:
        an open mesh still gets numbers, and they are meaningless.

    Raises:
        ValueError: If the mesh has no triangles or no enclosed volume, or if
            none of the density inputs was supplied.
    """
    triangles = _normalize_triangles(tris)
    if not triangles:
        raise ValueError("cannot compute mass properties of an empty mesh")

    # Reference point: the bbox centre, so the tetrahedra are the size of the
    # part rather than the size of its distance from the world origin.
    min_x = min_y = min_z = math.inf
    max_x = max_y = max_z = -math.inf
    for tri in triangles:
        for x, y, z in tri:
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if z < min_z:
                min_z = z
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            if z > max_z:
                max_z = z
    ox = (min_x + max_x) * 0.5
    oy = (min_y + max_y) * 0.5
    oz = (min_z + max_z) * 0.5

    volume = 0.0
    fx = fy = fz = 0.0
    xx = yy = zz = xy = xz = yz = 0.0
    for a, b, c in triangles:
        ax = a[0] - ox
        ay = a[1] - oy
        az = a[2] - oz
        bx = b[0] - ox
        by = b[1] - oy
        bz = b[2] - oz
        cx = c[0] - ox
        cy = c[1] - oy
        cz = c[2] - oz
        # Six times the signed volume of the tetrahedron (reference, a, b, c).
        det = ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        v = det / 6.0
        volume += v
        sx = ax + bx + cx
        sy = ay + by + cy
        sz = az + bz + cz
        fx += v * sx
        fy += v * sy
        fz += v * sz
        # Simplex second-moment identity for a tetra with one vertex at the
        # reference point: integral(x_i x_j) = V/20 * (sum_k p_k[i] p_k[j] + S_i S_j).
        k = v / 20.0
        xx += k * (ax * ax + bx * bx + cx * cx + sx * sx)
        yy += k * (ay * ay + by * by + cy * cy + sy * sy)
        zz += k * (az * az + bz * bz + cz * cz + sz * sz)
        xy += k * (ax * ay + bx * by + cx * cy + sx * sy)
        xz += k * (ax * az + bx * bz + cx * cz + sx * sz)
        yz += k * (ay * az + by * bz + cy * cz + sy * sz)

    if abs(volume) <= _VOLUME_EPSILON:
        raise ValueError(
            "mesh encloses no volume (a flat sheet, an empty result, or "
            "inconsistent facet winding); mass properties are undefined"
        )

    if volume < 0.0:
        # Consistently inverted mesh: negate every quantity that is linear in
        # the per-tetrahedron signed volume. The centre of mass is a ratio and
        # is already correct.
        volume = -volume
        fx, fy, fz = -fx, -fy, -fz
        xx, yy, zz = -xx, -yy, -zz
        xy, xz, yz = -xy, -xz, -yz

    # Centroid of a tetra is S/4, so the first moment contribution is v * S / 4.
    gx = fx / (4.0 * volume)
    gy = fy / (4.0 * volume)
    gz = fz / (4.0 * volume)

    density, mass = _resolve_density(volume, density_g_cm3, material, mass_g)
    # Unit-density tensor (mm^5) -> g*mm^2: density in g/cm^3 is g/1000 mm^3.
    scale = density / 1000.0

    ixx = (yy + zz - volume * (gy * gy + gz * gz)) * scale
    iyy = (xx + zz - volume * (gx * gx + gz * gz)) * scale
    izz = (xx + yy - volume * (gx * gx + gy * gy)) * scale
    ixy = (-xy + volume * gx * gy) * scale
    ixz = (-xz + volume * gx * gz) * scale
    iyz = (-yz + volume * gy * gz) * scale

    tensor: Matrix3 = [
        [ixx, ixy, ixz],
        [ixy, iyy, iyz],
        [ixz, iyz, izz],
    ]
    moments, axes = _jacobi_eigen(tensor)

    if watertight is None:
        watertight = analyze_triangles(triangles).is_watertight

    return MassProperties(
        volume_mm3=volume,
        mass_g=mass,
        density_g_cm3=density,
        center_of_mass=(gx + ox, gy + oy, gz + oz),
        inertia_about_com_g_mm2=tensor,
        principal_moments=moments,
        principal_axes=axes,
        is_watertight=bool(watertight),
        triangle_count=len(triangles),
    )


def point_mass(mass_g: float, at: Vec3) -> MassProperties:
    """A body known only as a mass at a point, for :func:`compose`.

    A purchased part -- a motor, a bearing, a battery -- usually arrives as a
    datasheet mass with no mesh. Its own inertia about its own centre is
    unknown and is reported as zero, so the composed assembly tensor carries
    only its parallel-axis term. That is the dominant term whenever the part is
    small compared with its offset from the assembly centre, and it is an
    under-estimate otherwise.

    Args:
        mass_g: Mass in grams.
        at: Where the mass acts, in model coordinates.

    Returns:
        A :class:`MassProperties` with zero volume and a zero tensor.
        ``is_watertight`` is True so the value does not trip a caller's gate.

    Raises:
        ValueError: If ``mass_g`` is negative.
    """
    if mass_g < 0:
        raise ValueError(f"mass_g must not be negative, got {mass_g}")
    return MassProperties(
        volume_mm3=0.0,
        mass_g=float(mass_g),
        density_g_cm3=0.0,
        center_of_mass=(float(at[0]), float(at[1]), float(at[2])),
        inertia_about_com_g_mm2=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        principal_moments=[0.0, 0.0, 0.0],
        principal_axes=[(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
        is_watertight=True,
        triangle_count=0,
    )


def compose(parts: list[tuple[str, MassProperties]]) -> dict[str, Any]:
    """Combine per-part mass properties into one assembly, exactly.

    Masses add, the centre of mass is the mass-weighted mean, and each part's
    tensor moves to the assembly centre by the parallel-axis theorem. No
    approximation enters, so composing two halves of a part reproduces the whole
    part's tensor to floating-point round-off.

    Args:
        parts: ``(name, properties)`` pairs. Names are echoed in the per-part
            table; duplicates are allowed.

    Returns:
        A dict with ``total_mass_g``, ``total_volume_mm3``, ``center_of_mass``,
        ``inertia_about_com_g_mm2``, ``principal_moments_g_mm2``,
        ``principal_axes``, ``all_watertight`` and a ``parts`` table carrying
        each part's mass, volume, centre and mass fraction.

    Raises:
        ValueError: If ``parts`` is empty or the total mass is not positive.
    """
    if not parts:
        raise ValueError("compose() needs at least one part")

    total_mass = 0.0
    total_volume = 0.0
    sx = sy = sz = 0.0
    for _name, part in parts:
        total_mass += part.mass_g
        total_volume += part.volume_mm3
        sx += part.mass_g * part.center_of_mass[0]
        sy += part.mass_g * part.center_of_mass[1]
        sz += part.mass_g * part.center_of_mass[2]

    if total_mass <= 0.0:
        raise ValueError(f"assembly total mass must be positive, got {total_mass}")

    com = (sx / total_mass, sy / total_mass, sz / total_mass)

    tensor: Matrix3 = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for _name, part in parts:
        m = part.mass_g
        rx = part.center_of_mass[0] - com[0]
        ry = part.center_of_mass[1] - com[1]
        rz = part.center_of_mass[2] - com[2]
        r2 = rx * rx + ry * ry + rz * rz
        own = part.inertia_about_com_g_mm2
        tensor[0][0] += own[0][0] + m * (r2 - rx * rx)
        tensor[1][1] += own[1][1] + m * (r2 - ry * ry)
        tensor[2][2] += own[2][2] + m * (r2 - rz * rz)
        tensor[0][1] += own[0][1] - m * rx * ry
        tensor[0][2] += own[0][2] - m * rx * rz
        tensor[1][2] += own[1][2] - m * ry * rz
    tensor[1][0] = tensor[0][1]
    tensor[2][0] = tensor[0][2]
    tensor[2][1] = tensor[1][2]

    moments, axes = _jacobi_eigen(tensor)

    return {
        "total_mass_g": total_mass,
        "total_volume_mm3": total_volume,
        "center_of_mass": [com[0], com[1], com[2]],
        "inertia_about_com_g_mm2": [list(row) for row in tensor],
        "principal_moments_g_mm2": list(moments),
        "principal_axes": [list(axis) for axis in axes],
        "all_watertight": all(part.is_watertight for _name, part in parts),
        "parts": [
            {
                "name": name,
                "mass_g": part.mass_g,
                "volume_mm3": part.volume_mm3,
                "density_g_cm3": part.density_g_cm3,
                "center_of_mass": list(part.center_of_mass),
                "mass_fraction": part.mass_g / total_mass,
            }
            for name, part in parts
        ],
    }


def _convex_hull_2d(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Counter-clockwise convex hull of 2D points (Andrew's monotone chain)."""
    unique = sorted(set(points))
    if len(unique) < 3:
        return unique

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0.0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0.0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else unique


def _distance_to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Shortest distance from a point to a line segment."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def tip_margin(bed_contact_points: list[Vec3], com: Vec3) -> dict[str, Any]:
    """How far the centre of mass sits inside the part's footprint on the bed.

    The support polygon is the convex hull of the bed-contact points projected
    to XY. A centre of mass inside that hull means the part stands; the margin
    is the shortest distance to the hull boundary, which is what a knock or a
    gantry acceleration has to overcome. A negative margin means the part tips
    on its own.

    This is a static test only. It says nothing about adhesion, and a hull with
    a tiny area is a warning in itself.

    Args:
        bed_contact_points: Points where the part meets the bed, in model
            coordinates. Only X and Y are used.
        com: The centre of mass.

    Returns:
        ``{"bed_hull_area_mm2", "com_margin_mm", "inside"}``. With fewer than
        three distinct contact points the hull has no area, ``inside`` is False
        and the margin is the negative distance to the contact line or point.
    """
    projected = [(float(p[0]), float(p[1])) for p in bed_contact_points]
    target = (float(com[0]), float(com[1]))

    if not projected:
        return {"bed_hull_area_mm2": 0.0, "com_margin_mm": 0.0, "inside": False}

    hull = _convex_hull_2d(projected)
    if len(hull) < 3:
        if len(hull) == 1:
            distance = math.hypot(target[0] - hull[0][0], target[1] - hull[0][1])
        else:
            distance = _distance_to_segment(target, hull[0], hull[-1])
        return {"bed_hull_area_mm2": 0.0, "com_margin_mm": -distance, "inside": False}

    area2 = 0.0
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    area = abs(area2) * 0.5

    # The hull is counter-clockwise, so a point inside is left of every edge.
    inside = True
    margin = math.inf
    boundary = math.inf
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        edge_length = math.hypot(bx - ax, by - ay)
        if edge_length <= 0.0:
            continue
        signed = ((bx - ax) * (target[1] - ay) - (by - ay) * (target[0] - ax)) / edge_length
        if signed < 0.0:
            inside = False
        if signed < margin:
            margin = signed
        d = _distance_to_segment(target, hull[i], hull[(i + 1) % n])
        if d < boundary:
            boundary = d

    return {
        "bed_hull_area_mm2": area,
        "com_margin_mm": margin if inside else -boundary,
        "inside": inside,
    }
