"""Write and inspect 3MF files containing several named objects.

3MF is the format slicers actually want for a multi-part model: one file, one
object per part, each with a name the slicer shows in its object list, and a
per-part placement transform. STL can carry only one nameless triangle soup, so
a bundle of STLs loses both the part names and the assembly layout.

This module is standard library only (``zipfile`` + ``xml``) and produces
deterministic bytes, so the same input always yields the same file and the
result can be cached or committed.

Typical use::

    write_3mf_from_stls(
        "plate.3mf",
        [
            {"name": "bracket", "stl": "build/bracket.stl"},
            {"name": "cover", "stl": "build/cover.stl",
             "transform": translation(40, 0, 0), "color": "#3366cc"},
        ],
    )

Transform convention
--------------------
Everywhere else in this project a 4x4 matrix is a *column-vector* matrix, the
same convention OpenSCAD's ``multmatrix`` uses: a point is transformed as
``p' = M @ p``, the linear part is ``M[0:3][0:3]`` and the translation is the
last *column* ``M[0][3], M[1][3], M[2][3]``.

3MF stores 12 numbers that form a *row-vector* 3x4 affine::

    transform="m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32"

applied as ``p' = p_row * T``, which puts the translation in the last *row*
(``m30 m31 m32``). Converting between the two is a transpose of the 3x3 block
plus a move of the translation from the last column to the last row; see
:func:`mat4_to_3mf` and :func:`mat4_from_3mf`. Both directions are exercised in
the tests, so a transform written here reads back as the same 4x4 matrix.
"""

from __future__ import annotations

import math
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .mesh import load_stl

__all__ = [
    "CORE_NS",
    "MATERIAL_NS",
    "MODEL_PATH",
    "UNITS",
    "ThreeMFObject",
    "identity",
    "mat4_from_3mf",
    "mat4_to_3mf",
    "multiply",
    "read_3mf_summary",
    "translation",
    "write_3mf",
    "write_3mf_from_stls",
]

Vec3 = tuple[float, float, float]
TriangleTuple = tuple[Vec3, Vec3, Vec3]
Mat4 = Sequence[Sequence[float]]

#: 3MF core specification namespace (the only one a conforming reader must know).
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
#: Materials-and-properties extension namespace, used only for optional colours.
MATERIAL_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
#: OPC relationship type that points at the root model part.
MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
#: Path of the root model part inside the zip container.
MODEL_PATH = "3D/3dmodel.model"

_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_RELS_PATH = "_rels/.rels"
_CONTENT_TYPES_PATH = "[Content_Types].xml"

#: Units the 3MF core specification allows on ``<model unit=...>``.
UNITS = ("micron", "millimeter", "centimeter", "inch", "foot", "meter")

#: Vertices closer together than this (per axis) are merged into one.
WELD_TOLERANCE = 1e-6
_WELD_SCALE = 1.0 / WELD_TOLERANCE
_COORD_DIGITS = 6

# Fixed zip timestamp (the earliest a zip can represent) so output is byte
# identical between runs. Real timestamps would defeat caching and diffing.
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# ---------------------------------------------------------------------------
# Transform helpers
# ---------------------------------------------------------------------------


def identity() -> list[list[float]]:
    """Return the 4x4 identity matrix."""
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def translation(x: float, y: float, z: float) -> list[list[float]]:
    """Return a 4x4 translation matrix (column-vector convention)."""
    return [
        [1.0, 0.0, 0.0, float(x)],
        [0.0, 1.0, 0.0, float(y)],
        [0.0, 0.0, 1.0, float(z)],
        [0.0, 0.0, 0.0, 1.0],
    ]


def multiply(a: Mat4, b: Mat4) -> list[list[float]]:
    """Return ``a @ b``: the transform that applies ``b`` first, then ``a``."""
    m = _normalize_mat4(a, "a")
    n = _normalize_mat4(b, "b")
    return [[sum(m[i][k] * n[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _normalize_mat4(transform: Mat4 | Sequence[float] | None, label: str) -> list[list[float]]:
    """Coerce the accepted transform spellings into a 4x4 nested list.

    Accepts a 4x4 or 3x4 nested sequence, or a flat sequence of 16 or 12 numbers
    in row-major order. A 3x4 input is completed with the ``0 0 0 1`` bottom row.
    """
    if transform is None:
        return identity()

    rows: list[list[float]]
    flat: list[float] | None = None
    if all(isinstance(item, (int, float)) for item in transform):
        flat = [float(item) for item in transform]  # type: ignore[arg-type]
    if flat is not None:
        if len(flat) not in (12, 16):
            raise ValueError(
                f"{label} transform: a flat matrix must have 12 or 16 numbers, got {len(flat)}"
            )
        rows = [flat[i : i + 4] for i in range(0, 12, 4)]
    else:
        rows = []
        for row in transform:
            if isinstance(row, (int, float)) or len(row) != 4:
                raise ValueError(f"{label} transform: every row must have 4 numbers, got {row!r}")
            rows.append([float(value) for value in row])
        if len(rows) not in (3, 4):
            raise ValueError(f"{label} transform: expected 3 or 4 rows, got {len(rows)}")
        rows = rows[:3]

    rows = rows[:3] + [[0.0, 0.0, 0.0, 1.0]]
    for row in rows[:3]:
        for value in row:
            if not math.isfinite(value):
                raise ValueError(f"{label} transform: values must be finite, got {value!r}")
    return rows


def mat4_to_3mf(transform: Mat4 | Sequence[float] | None) -> list[float]:
    """Convert a column-vector 4x4 matrix to 3MF's 12 row-vector numbers.

    3MF applies the matrix as ``p_row * T``, so the 3x3 linear block is the
    transpose of ours and the translation moves from the last column to the last
    row. The returned order is ``m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31
    m32``.
    """
    m = _normalize_mat4(transform, "")
    return [
        m[0][0],
        m[1][0],
        m[2][0],
        m[0][1],
        m[1][1],
        m[2][1],
        m[0][2],
        m[1][2],
        m[2][2],
        m[0][3],
        m[1][3],
        m[2][3],
    ]


def mat4_from_3mf(values: Sequence[float]) -> list[list[float]]:
    """Convert 3MF's 12 row-vector numbers back to a column-vector 4x4 matrix.

    The inverse of :func:`mat4_to_3mf`.
    """
    v = [float(value) for value in values]
    if len(v) != 12:
        raise ValueError(f"A 3MF transform must have 12 numbers, got {len(v)}")
    return [
        [v[0], v[3], v[6], v[9]],
        [v[1], v[4], v[7], v[10]],
        [v[2], v[5], v[8], v[11]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _is_identity(transform: Mat4 | None) -> bool:
    """True when the matrix is the identity within the weld tolerance."""
    if transform is None:
        return True
    m = _normalize_mat4(transform, "")
    ident = identity()
    return all(abs(m[i][j] - ident[i][j]) <= WELD_TOLERANCE for i in range(3) for j in range(4))


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


@dataclass
class ThreeMFObject:
    """One named part in a 3MF file.

    Attributes:
        name: Name the slicer shows in its object list. Preserved verbatim.
        triangles: Mesh as ``((x, y, z), (x, y, z), (x, y, z))`` tuples, wound
            counter-clockwise seen from outside, exactly as an STL gives them.
        transform: Optional placement, as a column-vector 4x4 matrix (or 3x4 /
            flat 12 / flat 16). ``None`` means the mesh is already in its
            assembly position.
        color: Optional ``"#rgb"``, ``"#rrggbb"`` or ``"#rrggbbaa"`` display
            colour, written via the materials extension.
    """

    name: str
    triangles: list[TriangleTuple] = field(default_factory=list)
    transform: Mat4 | None = None
    color: str | None = None


def _normalize_color(color: str | None) -> str | None:
    """Normalise a hex colour to the ``#RRGGBBAA`` form the 3MF schema wants."""
    if color is None:
        return None
    if not isinstance(color, str) or not _COLOR_RE.match(color.strip()):
        raise ValueError(f"Invalid colour {color!r}: expected '#rgb', '#rrggbb' or '#rrggbbaa' hex")
    digits = color.strip()[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) == 6:
        digits += "FF"
    return "#" + digits.upper()


def _fmt(value: float) -> str:
    """Format a coordinate compactly and deterministically."""
    rounded = round(float(value), _COORD_DIGITS)
    if rounded == 0.0:
        return "0"
    if rounded == int(rounded) and abs(rounded) < 1e15:
        return str(int(rounded))
    text = f"{rounded:.{_COORD_DIGITS}f}".rstrip("0").rstrip(".")
    return text or "0"


def _weld(triangles: Iterable[TriangleTuple]) -> tuple[list[Vec3], list[tuple[int, int, int]], int]:
    """Merge coincident vertices and index the triangles against them.

    Vertices are snapped to a ``WELD_TOLERANCE`` grid, which is what turns an
    STL cube's 36 loose vertices into the 8 a 3MF cube should have. Triangles
    that collapse to a line or a point after welding are dropped: 3MF readers
    reject a triangle whose three indices are not distinct.

    Returns:
        ``(vertices, indexed_triangles, dropped_count)``.
    """
    index: dict[tuple[int, int, int], int] = {}
    vertices: list[Vec3] = []
    indexed: list[tuple[int, int, int]] = []
    dropped = 0

    for tri in triangles:
        if len(tri) != 3:
            raise ValueError(f"Triangle must have 3 vertices, got {len(tri)}")
        corners: list[int] = []
        for point in tri:
            if len(point) != 3:
                raise ValueError(f"Vertex must have 3 coordinates, got {len(point)}")
            x, y, z = (float(c) for c in point)
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                raise ValueError(f"Vertex coordinates must be finite, got {point!r}")
            key = (round(x * _WELD_SCALE), round(y * _WELD_SCALE), round(z * _WELD_SCALE))
            slot = index.get(key)
            if slot is None:
                slot = len(vertices)
                index[key] = slot
                vertices.append((x, y, z))
            corners.append(slot)
        a, b, c = corners
        if a in (b, c) or b == c:
            dropped += 1
            continue
        indexed.append((a, b, c))

    return vertices, indexed, dropped


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _attr(value: str) -> str:
    """Escape a string for use inside a double-quoted XML attribute."""
    return escape(value, {'"': "&quot;", "\t": "&#9;", "\n": "&#10;", "\r": "&#13;"})


def _content_types_xml() -> str:
    """The OPC content-type map: one Default per file extension in the zip."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Types xmlns="{_CONTENT_TYPES_NS}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" '
        'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>\n"
    )


def _rels_xml() -> str:
    """The package relationships part, pointing at the root model."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{_RELS_NS}">'
        f'<Relationship Id="rel0" Target="/{MODEL_PATH}" Type="{MODEL_REL_TYPE}"/>'
        "</Relationships>\n"
    )


def _model_xml(
    prepared: list[dict[str, Any]],
    unit: str,
    metadata: dict[str, str] | None,
    color_group_id: int | None,
) -> str:
    """Render the 3D model part.

    Resources are emitted colour group first, because a 3MF resource may only
    reference resources declared before it.
    """
    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    root = f'<model unit="{unit}" xml:lang="en-US" xmlns="{CORE_NS}"'
    if color_group_id is not None:
        root += f' xmlns:m="{MATERIAL_NS}"'
    lines.append(root + ">")

    for key in sorted(metadata or {}):
        lines.append(
            f' <metadata name="{_attr(str(key))}">{escape(str((metadata or {})[key]))}</metadata>'
        )

    lines.append(" <resources>")
    if color_group_id is not None:
        lines.append(f'  <m:colorgroup id="{color_group_id}">')
        for item in prepared:
            if item["color"] is not None:
                lines.append(f'   <m:color color="{item["color"]}"/>')
        lines.append("  </m:colorgroup>")

    for item in prepared:
        head = f'  <object id="{item["id"]}" type="model" name="{_attr(item["name"])}"'
        if item["color_index"] is not None:
            head += f' pid="{color_group_id}" pindex="{item["color_index"]}"'
        lines.append(head + ">")
        lines.append("   <mesh>")
        lines.append("    <vertices>")
        lines.extend(
            f'     <vertex x="{_fmt(v[0])}" y="{_fmt(v[1])}" z="{_fmt(v[2])}"/>'
            for v in item["vertices"]
        )
        lines.append("    </vertices>")
        lines.append("    <triangles>")
        lines.extend(
            f'     <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in item["triangles"]
        )
        lines.append("    </triangles>")
        lines.append("   </mesh>")
        lines.append("  </object>")
    lines.append(" </resources>")

    lines.append(" <build>")
    for item in prepared:
        entry = f'  <item objectid="{item["id"]}"'
        if not _is_identity(item["transform"]):
            numbers = " ".join(_fmt(value) for value in mat4_to_3mf(item["transform"]))
            entry += f' transform="{numbers}"'
        lines.append(entry + "/>")
    lines.append(" </build>")

    lines.append("</model>")
    return "\n".join(lines) + "\n"


def write_3mf(
    path: Path | str,
    objects: Sequence[ThreeMFObject],
    unit: str = "millimeter",
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write several named meshes into one multi-object 3MF file.

    Each object becomes a separate ``<object>`` resource with its name intact
    and a ``<build>`` item carrying its placement, which is what makes slicers
    (PrusaSlicer, Bambu Studio, Cura) open the file as a multi-part model rather
    than one merged blob. Vertices are welded per object, and the zip is written
    with a fixed timestamp so the bytes are reproducible.

    Args:
        path: Destination ``.3mf`` file. Parent directories must exist.
        objects: Parts to bundle. Must be non-empty, and each must have at least
            one non-degenerate triangle.
        unit: Model unit, one of :data:`UNITS`.
        metadata: Optional ``<metadata>`` entries. Names outside the 3MF core set
            (Title, Designer, Description, Copyright, LicenseTerms, Rating,
            CreationDate, ModificationDate, Application) should carry a namespace
            prefix if strict validators will read the file.

    Returns:
        A dict with ``path``, ``object_count``, ``vertex_count``,
        ``triangle_count`` and ``bytes``, plus a per-object ``objects`` list and
        ``degenerate_triangles_dropped``.

    Raises:
        ValueError: If ``objects`` is empty, a unit or colour is invalid, or a
            part has no usable triangles.
    """
    if unit not in UNITS:
        raise ValueError(f"Invalid unit {unit!r}: expected one of {', '.join(UNITS)}")
    if not objects:
        raise ValueError("write_3mf needs at least one object")

    has_color = any(obj.color is not None for obj in objects)
    color_group_id = 1 if has_color else None
    next_id = 2 if has_color else 1

    prepared: list[dict[str, Any]] = []
    color_index = 0
    dropped_total = 0
    for position, obj in enumerate(objects):
        if not isinstance(obj, ThreeMFObject):
            raise TypeError(
                f"objects[{position}] must be a ThreeMFObject, got {type(obj).__name__}"
            )
        vertices, triangles, dropped = _weld(obj.triangles)
        dropped_total += dropped
        name = obj.name if isinstance(obj.name, str) and obj.name else f"object_{position + 1}"
        if not triangles:
            raise ValueError(
                f"Object {name!r} has no usable triangles "
                f"({dropped} degenerate of {len(list(obj.triangles))})"
            )
        color = _normalize_color(obj.color)
        prepared.append(
            {
                "id": next_id + position,
                "name": name,
                "vertices": vertices,
                "triangles": triangles,
                "transform": (
                    None if obj.transform is None else _normalize_mat4(obj.transform, name)
                ),
                "color": color,
                "color_index": None if color is None else color_index,
            }
        )
        if color is not None:
            color_index += 1

    model = _model_xml(prepared, unit, metadata, color_group_id)

    out = Path(path)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for member, payload in (
            (_CONTENT_TYPES_PATH, _content_types_xml()),
            (_RELS_PATH, _rels_xml()),
            (MODEL_PATH, model),
        ):
            info = zipfile.ZipInfo(member, date_time=_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, payload.encode("utf-8"))

    return {
        "path": str(out),
        "object_count": len(prepared),
        "vertex_count": sum(len(item["vertices"]) for item in prepared),
        "triangle_count": sum(len(item["triangles"]) for item in prepared),
        "bytes": out.stat().st_size,
        "degenerate_triangles_dropped": dropped_total,
        "objects": [
            {
                "id": item["id"],
                "name": item["name"],
                "vertex_count": len(item["vertices"]),
                "triangle_count": len(item["triangles"]),
            }
            for item in prepared
        ],
    }


def write_3mf_from_stls(
    path: Path | str,
    parts: Sequence[dict[str, Any]],
    unit: str = "millimeter",
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bundle per-part STL files into one multi-object 3MF file.

    Args:
        path: Destination ``.3mf`` file.
        parts: One dict per part with keys ``name`` (defaults to the STL's stem),
            ``stl`` (path, required), optional ``transform`` and optional
            ``color``.
        unit: Model unit, one of :data:`UNITS`.
        metadata: Optional ``<metadata>`` entries.

    Returns:
        The same summary dict :func:`write_3mf` returns.

    Raises:
        FileNotFoundError: If an STL is missing.
        ValueError: If a part has no ``stl`` key or an STL holds no geometry.
    """
    objects: list[ThreeMFObject] = []
    for position, part in enumerate(parts):
        stl = part.get("stl") or part.get("path")
        if not stl:
            raise ValueError(f"parts[{position}] has no 'stl' path")
        stl_path = Path(stl)
        if not stl_path.exists():
            raise FileNotFoundError(f"Missing STL for part {position}: {stl_path}")
        objects.append(
            ThreeMFObject(
                name=str(part.get("name") or stl_path.stem),
                triangles=load_stl(stl_path),
                transform=part.get("transform"),
                color=part.get("color"),
            )
        )
    return write_3mf(path, objects, unit=unit, metadata=metadata)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _tag(name: str, namespace: str = CORE_NS) -> str:
    """Return a namespace-qualified ElementTree tag."""
    return f"{{{namespace}}}{name}"


def read_3mf_summary(path: Path | str) -> dict[str, Any]:
    """Parse a 3MF file back into a summary of its objects.

    Reads only what a caller needs to verify or measure a bundle: the unit, the
    metadata, and per object the id, name, counts, bounding box, colour and the
    build transform converted back to our column-vector 4x4 convention. Meshes
    themselves are not returned.

    Args:
        path: Path to the ``.3mf`` file.

    Returns:
        A dict with ``path``, ``unit``, ``metadata``, ``object_count``,
        ``vertex_count``, ``triangle_count``, ``bytes``, ``parts`` (the zip
        member names) and ``objects``.

    Raises:
        ValueError: If the zip is not a 3MF container or the model XML is
            malformed.
    """
    file_path = Path(path)
    with zipfile.ZipFile(file_path) as zf:
        members = sorted(zf.namelist())
        missing = [
            required
            for required in (_CONTENT_TYPES_PATH, _RELS_PATH, MODEL_PATH)
            if required not in members
        ]
        if missing:
            raise ValueError(f"Not a 3MF container, missing {', '.join(missing)}: {file_path}")
        model_bytes = zf.read(MODEL_PATH)

    try:
        root = ET.fromstring(model_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed 3MF model XML in {file_path}: {exc}") from exc
    if root.tag != _tag("model"):
        raise ValueError(f"Root element is {root.tag!r}, expected a 3MF <model>")

    metadata = {
        element.get("name", ""): (element.text or "") for element in root.findall(_tag("metadata"))
    }

    colors: dict[int, list[str]] = {}
    for color_group in root.iter(_tag("colorgroup", MATERIAL_NS)):
        group_id = color_group.get("id")
        if group_id is None:
            continue
        colors[int(group_id)] = [
            entry.get("color", "") for entry in color_group.findall(_tag("color", MATERIAL_NS))
        ]

    objects: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    resources = root.find(_tag("resources"))
    for element in resources.findall(_tag("object")) if resources is not None else []:
        raw_id = element.get("id")
        if raw_id is None:
            raise ValueError("Found an <object> without an id")
        object_id = int(raw_id)
        mesh = element.find(_tag("mesh"))
        vertices: list[Vec3] = []
        triangle_count = 0
        if mesh is not None:
            container = mesh.find(_tag("vertices"))
            for vertex in container.findall(_tag("vertex")) if container is not None else []:
                vertices.append(
                    (
                        float(vertex.get("x", "0")),
                        float(vertex.get("y", "0")),
                        float(vertex.get("z", "0")),
                    )
                )
            tris = mesh.find(_tag("triangles"))
            triangle_count = len(tris.findall(_tag("triangle"))) if tris is not None else 0

        bbox_min, bbox_max = _bbox(vertices)
        color: str | None = None
        pid, pindex = element.get("pid"), element.get("pindex")
        if pid is not None and pindex is not None:
            palette = colors.get(int(pid), [])
            slot = int(pindex)
            if 0 <= slot < len(palette):
                color = palette[slot]

        objects[object_id] = {
            "id": object_id,
            "name": element.get("name", ""),
            "type": element.get("type", "model"),
            "vertex_count": len(vertices),
            "triangle_count": triangle_count,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "color": color,
            "transform": None,
        }
        order.append(object_id)

    build = root.find(_tag("build"))
    items: list[dict[str, Any]] = []
    for item in build.findall(_tag("item")) if build is not None else []:
        raw_id = item.get("objectid")
        if raw_id is None:
            raise ValueError("Found a build <item> without an objectid")
        object_id = int(raw_id)
        raw_transform = item.get("transform")
        transform = (
            mat4_from_3mf([float(value) for value in raw_transform.split()])
            if raw_transform
            else None
        )
        items.append({"objectid": object_id, "transform": transform})
        if object_id in objects:
            objects[object_id]["transform"] = transform

    ordered = [objects[object_id] for object_id in order]
    return {
        "path": str(file_path),
        "unit": root.get("unit", "millimeter"),
        "metadata": metadata,
        "object_count": len(ordered),
        "vertex_count": sum(obj["vertex_count"] for obj in ordered),
        "triangle_count": sum(obj["triangle_count"] for obj in ordered),
        "bytes": file_path.stat().st_size,
        "parts": members,
        "objects": ordered,
        "items": items,
    }


def _bbox(vertices: Sequence[Vec3]) -> tuple[list[float], list[float]]:
    """Return ``([min_x, min_y, min_z], [max_x, max_y, max_z])`` for vertices."""
    if not vertices:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
