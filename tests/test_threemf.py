"""Tests for the multi-object 3MF writer in ``openscad_mcp.threemf``.

Meshes are generated in code, so almost nothing here needs OpenSCAD. The one
test that shells out to OpenSCAD is skipped when the binary is absent. There is
no dependency on ``lib3mf``; the file is validated structurally against the 3MF
core specification (zip layout, content types, relationship, model namespace).
"""

import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from openscad_mcp.threemf import (
    CORE_NS,
    MATERIAL_NS,
    MODEL_PATH,
    ThreeMFObject,
    identity,
    mat4_from_3mf,
    mat4_to_3mf,
    multiply,
    read_3mf_summary,
    translation,
    write_3mf,
    write_3mf_from_stls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_box(origin=(0.0, 0.0, 0.0), size=10.0):
    """Return the 12 triangles of an axis-aligned box with outward normals."""
    sx, sy, sz = (size, size, size) if isinstance(size, (int, float)) else size
    x0, y0, z0 = origin
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz
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
    faces = [
        (0, 3, 2),
        (0, 2, 1),  # bottom
        (4, 5, 6),
        (4, 6, 7),  # top
        (0, 1, 5),
        (0, 5, 4),  # front
        (2, 3, 7),
        (2, 7, 6),  # back
        (3, 0, 4),
        (3, 4, 7),  # left
        (1, 2, 6),
        (1, 6, 5),  # right
    ]
    return [(corners[a], corners[b], corners[c]) for a, b, c in faces]


def write_binary_stl(path, triangles):
    """Write triangles to a binary STL file."""
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            fh.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for vertex in tri:
                fh.write(struct.pack("<3f", *vertex))
            fh.write(struct.pack("<H", 0))
    return Path(path)


def model_root(path):
    """Return the parsed ``3D/3dmodel.model`` element of a 3MF file."""
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read(MODEL_PATH))


def tag(name, namespace=CORE_NS):
    return f"{{{namespace}}}{name}"


@pytest.fixture
def two_cubes(tmp_path):
    """A 3MF with two cubes, the second placed 40 mm along +X."""
    out = tmp_path / "two_cubes.3mf"
    result = write_3mf(
        out,
        [
            ThreeMFObject(name="left_cube", triangles=make_box(size=10.0)),
            ThreeMFObject(
                name="right_cube",
                triangles=make_box(size=10.0),
                transform=translation(40.0, 0.0, 0.0),
            ),
        ],
        metadata={"Title": "two cubes"},
    )
    return out, result


# ---------------------------------------------------------------------------
# Container structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContainerStructure:
    """The zip must be a valid OPC package with the three required parts."""

    def test_zip_has_required_parts(self, two_cubes):
        path, _ = two_cubes
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            assert zf.testzip() is None
        assert {"[Content_Types].xml", "_rels/.rels", MODEL_PATH} <= names

    def test_all_parts_are_valid_xml(self, two_cubes):
        path, _ = two_cubes
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                ET.fromstring(zf.read(member))

    def test_content_types_declare_model_and_rels(self, two_cubes):
        path, _ = two_cubes
        with zipfile.ZipFile(path) as zf:
            types = ET.fromstring(zf.read("[Content_Types].xml"))
        declared = {element.get("Extension"): element.get("ContentType") for element in types}
        assert declared["model"] == "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        assert declared["rels"] == ("application/vnd.openxmlformats-package.relationships+xml")

    def test_relationship_points_at_model(self, two_cubes):
        path, _ = two_cubes
        with zipfile.ZipFile(path) as zf:
            rels = ET.fromstring(zf.read("_rels/.rels"))
        relationship = rels[0]
        assert relationship.get("Target") == "/" + MODEL_PATH
        assert relationship.get("Type").endswith("/3dmodel")

    def test_model_uses_core_namespace_and_unit(self, two_cubes):
        path, _ = two_cubes
        root = model_root(path)
        assert root.tag == tag("model")
        assert root.get("unit") == "millimeter"

    def test_every_object_has_mesh_and_build_item(self, two_cubes):
        path, _ = two_cubes
        root = model_root(path)
        objects = root.find(tag("resources")).findall(tag("object"))
        items = root.find(tag("build")).findall(tag("item"))
        assert len(objects) == 2
        assert len(items) == 2
        ids = {obj.get("id") for obj in objects}
        assert {item.get("objectid") for item in items} == ids
        assert len(ids) == 2, "object ids must be unique"
        for obj in objects:
            assert obj.get("type") == "model"
            assert obj.find(tag("mesh")) is not None

    def test_triangle_indices_are_in_range_and_distinct(self, two_cubes):
        path, _ = two_cubes
        root = model_root(path)
        for obj in root.find(tag("resources")).findall(tag("object")):
            mesh = obj.find(tag("mesh"))
            count = len(mesh.find(tag("vertices")).findall(tag("vertex")))
            for triangle in mesh.find(tag("triangles")).findall(tag("triangle")):
                corners = [int(triangle.get(key)) for key in ("v1", "v2", "v3")]
                assert len(set(corners)) == 3
                assert all(0 <= corner < count for corner in corners)


# ---------------------------------------------------------------------------
# Geometry, welding, names, transforms
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGeometry:
    def test_cube_welds_to_eight_vertices(self, two_cubes):
        _, result = two_cubes
        assert result["object_count"] == 2
        assert result["vertex_count"] == 16
        assert result["triangle_count"] == 24
        for entry in result["objects"]:
            assert entry["vertex_count"] == 8
            assert entry["triangle_count"] == 12

    def test_summary_round_trips_counts_and_names(self, two_cubes):
        path, result = two_cubes
        summary = read_3mf_summary(path)
        assert summary["object_count"] == 2
        assert summary["vertex_count"] == result["vertex_count"]
        assert summary["triangle_count"] == result["triangle_count"]
        assert [obj["name"] for obj in summary["objects"]] == ["left_cube", "right_cube"]
        assert summary["unit"] == "millimeter"
        assert summary["metadata"]["Title"] == "two cubes"
        assert summary["bytes"] == result["bytes"] == path.stat().st_size

    def test_transform_round_trips(self, two_cubes):
        path, _ = two_cubes
        summary = read_3mf_summary(path)
        by_name = {obj["name"]: obj for obj in summary["objects"]}
        assert by_name["left_cube"]["transform"] is None
        assert by_name["right_cube"]["transform"] == translation(40.0, 0.0, 0.0)

    def test_transform_translation_lands_in_last_row(self, two_cubes):
        """3MF is row-vector: the translation is m30 m31 m32, the last 3 numbers."""
        path, _ = two_cubes
        root = model_root(path)
        items = root.find(tag("build")).findall(tag("item"))
        raw = items[1].get("transform").split()
        assert len(raw) == 12
        assert [float(value) for value in raw[:9]] == [1, 0, 0, 0, 1, 0, 0, 0, 1]
        assert [float(value) for value in raw[9:]] == [40.0, 0.0, 0.0]

    def test_bbox_reflects_local_mesh(self, two_cubes):
        path, _ = two_cubes
        summary = read_3mf_summary(path)
        for obj in summary["objects"]:
            assert obj["bbox_min"] == [0.0, 0.0, 0.0]
            assert obj["bbox_max"] == [10.0, 10.0, 10.0]

    def test_names_with_xml_special_characters_survive(self, tmp_path):
        out = tmp_path / "escaped.3mf"
        name = 'brack<et> & "quote"'
        write_3mf(out, [ThreeMFObject(name=name, triangles=make_box())])
        assert read_3mf_summary(out)["objects"][0]["name"] == name

    def test_unnamed_object_gets_a_placeholder(self, tmp_path):
        out = tmp_path / "unnamed.3mf"
        write_3mf(out, [ThreeMFObject(name="", triangles=make_box())])
        assert read_3mf_summary(out)["objects"][0]["name"] == "object_1"

    def test_degenerate_triangles_are_dropped(self, tmp_path):
        out = tmp_path / "degenerate.3mf"
        point = (0.0, 0.0, 0.0)
        triangles = make_box() + [(point, point, point)]
        result = write_3mf(out, [ThreeMFObject(name="cube", triangles=triangles)])
        assert result["degenerate_triangles_dropped"] == 1
        assert result["triangle_count"] == 12

    def test_near_coincident_vertices_weld(self, tmp_path):
        """Vertices within the 1e-6 weld tolerance collapse to one."""
        out = tmp_path / "welded.3mf"
        triangles = make_box(size=10.0)
        nudged = [
            tuple((x + 1e-9, y, z) for x, y, z in tri) if index % 2 else tri
            for index, tri in enumerate(triangles)
        ]
        assert write_3mf(out, [ThreeMFObject(name="cube", triangles=nudged)])["vertex_count"] == 8

    def test_output_is_deterministic(self, tmp_path):
        objects = [
            ThreeMFObject(name="a", triangles=make_box()),
            ThreeMFObject(name="b", triangles=make_box(), transform=translation(5, 0, 0)),
        ]
        first = tmp_path / "a.3mf"
        second = tmp_path / "b.3mf"
        write_3mf(first, objects, metadata={"Title": "t", "Designer": "d"})
        write_3mf(second, objects, metadata={"Designer": "d", "Title": "t"})
        assert first.read_bytes() == second.read_bytes()


# ---------------------------------------------------------------------------
# Transform conversion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransformConversion:
    def test_identity_maps_to_identity(self):
        assert mat4_to_3mf(identity()) == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        assert mat4_from_3mf(mat4_to_3mf(identity())) == identity()

    def test_linear_block_is_transposed(self):
        """A 90 degree rotation about Z, as OpenSCAD's multmatrix would write it."""
        rotate_z = [
            [0.0, -1.0, 0.0, 1.0],
            [1.0, 0.0, 0.0, 2.0],
            [0.0, 0.0, 1.0, 3.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        values = mat4_to_3mf(rotate_z)
        assert values[:9] == [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        assert values[9:] == [1.0, 2.0, 3.0]
        assert mat4_from_3mf(values) == rotate_z

    def test_row_vector_multiplication_matches_column_vector(self):
        """p_row * T must give the same point as M @ p."""
        matrix = [
            [0.0, -1.0, 0.0, 1.0],
            [1.0, 0.0, 0.0, 2.0],
            [0.0, 0.0, 2.0, 3.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        point = (2.0, 5.0, 7.0)
        expected = tuple(
            sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3)
        )
        t = mat4_to_3mf(matrix)
        rows = [t[0:3], t[3:6], t[6:9], t[9:12]]
        actual = tuple(sum(point[i] * rows[i][j] for i in range(3)) + rows[3][j] for j in range(3))
        assert actual == pytest.approx(expected)

    def test_accepts_flat_and_three_by_four_forms(self, tmp_path):
        flat16 = [1, 0, 0, 4, 0, 1, 0, 5, 0, 0, 1, 6, 0, 0, 0, 1]
        flat12 = [1, 0, 0, 4, 0, 1, 0, 5, 0, 0, 1, 6]
        rows3 = [[1, 0, 0, 4], [0, 1, 0, 5], [0, 0, 1, 6]]
        for index, form in enumerate((flat16, flat12, rows3)):
            out = tmp_path / f"form{index}.3mf"
            write_3mf(out, [ThreeMFObject(name="c", triangles=make_box(), transform=form)])
            assert read_3mf_summary(out)["objects"][0]["transform"] == translation(4, 5, 6)

    def test_multiply_composes_right_to_left(self):
        composed = multiply(translation(1, 0, 0), translation(0, 2, 0))
        assert composed == translation(1, 2, 0)

    def test_bad_transform_shapes_are_rejected(self, tmp_path):
        for bad in ([1, 2, 3], [[1, 2, 3], [4, 5, 6]], [[1, 2, 3, 4]] * 5):
            with pytest.raises(ValueError):
                write_3mf(
                    tmp_path / "bad.3mf",
                    [ThreeMFObject(name="c", triangles=make_box(), transform=bad)],
                )


# ---------------------------------------------------------------------------
# Colours (materials extension)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestColors:
    def test_colorgroup_written_and_read_back(self, tmp_path):
        out = tmp_path / "colored.3mf"
        write_3mf(
            out,
            [
                ThreeMFObject(name="red", triangles=make_box(), color="#ff0000"),
                ThreeMFObject(name="plain", triangles=make_box()),
                ThreeMFObject(name="blue", triangles=make_box(), color="#00f"),
            ],
        )
        root = model_root(out)
        groups = root.find(tag("resources")).findall(tag("colorgroup", MATERIAL_NS))
        assert len(groups) == 1
        assert [c.get("color") for c in groups[0]] == ["#FF0000FF", "#0000FFFF"]

        by_name = {obj["name"]: obj for obj in read_3mf_summary(out)["objects"]}
        assert by_name["red"]["color"] == "#FF0000FF"
        assert by_name["blue"]["color"] == "#0000FFFF"
        assert by_name["plain"]["color"] is None

    def test_colorgroup_precedes_the_objects_that_use_it(self, tmp_path):
        """A 3MF resource may only reference a resource declared before it."""
        out = tmp_path / "order.3mf"
        write_3mf(out, [ThreeMFObject(name="red", triangles=make_box(), color="#ff0000")])
        resources = list(model_root(out).find(tag("resources")))
        assert resources[0].tag == tag("colorgroup", MATERIAL_NS)
        assert resources[1].tag == tag("object")
        assert resources[1].get("pid") == "1"
        assert resources[1].get("pindex") == "0"

    def test_no_material_namespace_when_no_colors(self, two_cubes):
        path, _ = two_cubes
        with zipfile.ZipFile(path) as zf:
            model = zf.read(MODEL_PATH).decode("utf-8")
        assert MATERIAL_NS not in model
        assert "requiredextensions" not in model

    def test_invalid_color_is_rejected(self, tmp_path):
        for bad in ("red", "#12345", "ff0000", "#gggggg"):
            with pytest.raises(ValueError, match="colour"):
                write_3mf(
                    tmp_path / "bad.3mf",
                    [ThreeMFObject(name="c", triangles=make_box(), color=bad)],
                )


# ---------------------------------------------------------------------------
# STL bundling and errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromStls:
    def test_bundles_stl_files(self, tmp_path):
        first = write_binary_stl(tmp_path / "bracket.stl", make_box(size=10.0))
        second = write_binary_stl(tmp_path / "cover.stl", make_box(size=(20.0, 5.0, 2.0)))
        out = tmp_path / "plate.3mf"
        result = write_3mf_from_stls(
            out,
            [
                {"name": "bracket", "stl": first},
                {"stl": second, "transform": translation(30, 0, 0), "color": "#3366cc"},
            ],
        )
        assert result["object_count"] == 2
        assert result["vertex_count"] == 16
        assert result["triangle_count"] == 24

        summary = read_3mf_summary(out)
        assert [obj["name"] for obj in summary["objects"]] == ["bracket", "cover"]
        assert summary["objects"][1]["transform"] == translation(30, 0, 0)
        assert summary["objects"][1]["color"] == "#3366CCFF"

    def test_missing_stl_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            write_3mf_from_stls(tmp_path / "x.3mf", [{"name": "a", "stl": tmp_path / "gone.stl"}])

    def test_part_without_stl_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no 'stl' path"):
            write_3mf_from_stls(tmp_path / "x.3mf", [{"name": "a"}])


@pytest.mark.unit
class TestValidation:
    def test_empty_object_list_raises(self, tmp_path):
        with pytest.raises(ValueError, match="at least one object"):
            write_3mf(tmp_path / "empty.3mf", [])

    def test_object_without_triangles_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no usable triangles"):
            write_3mf(tmp_path / "empty.3mf", [ThreeMFObject(name="void", triangles=[])])

    def test_invalid_unit_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid unit"):
            write_3mf(
                tmp_path / "unit.3mf",
                [ThreeMFObject(name="c", triangles=make_box())],
                unit="cubits",
            )

    def test_units_other_than_millimeter_are_written(self, tmp_path):
        out = tmp_path / "inch.3mf"
        write_3mf(out, [ThreeMFObject(name="c", triangles=make_box())], unit="inch")
        assert read_3mf_summary(out)["unit"] == "inch"

    def test_reading_a_non_3mf_zip_raises(self, tmp_path):
        out = tmp_path / "plain.zip"
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("hello.txt", "not a 3mf")
        with pytest.raises(ValueError, match="Not a 3MF container"):
            read_3mf_summary(out)

    def test_malformed_vertex_raises(self, tmp_path):
        bad = [((0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))]
        with pytest.raises(ValueError, match="3 coordinates"):
            write_3mf(tmp_path / "bad.3mf", [ThreeMFObject(name="c", triangles=bad)])


# ---------------------------------------------------------------------------
# OpenSCAD integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("openscad") is None, reason="OpenSCAD is not installed")
def test_bundles_two_openscad_exports(tmp_path):
    """Export two cubes from OpenSCAD, bundle them, and check the result."""
    stls = []
    for name, source in (
        ("cube_a", "cube([10, 10, 10]);"),
        ("cube_b", "cube([20, 5, 2]);"),
    ):
        scad = tmp_path / f"{name}.scad"
        scad.write_text(source)
        stl = tmp_path / f"{name}.stl"
        proc = subprocess.run(
            [shutil.which("openscad"), "-o", str(stl), str(scad)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        stls.append(stl)

    out = tmp_path / "bundle.3mf"
    result = write_3mf_from_stls(
        out,
        [
            {"name": "cube_a", "stl": stls[0]},
            {"name": "cube_b", "stl": stls[1], "transform": translation(20, 0, 0)},
        ],
    )
    assert result["object_count"] == 2
    assert result["vertex_count"] == 16, "each exported cube should weld to 8 vertices"
    assert result["triangle_count"] == 24
    assert result["bytes"] > 0

    summary = read_3mf_summary(out)
    assert [obj["name"] for obj in summary["objects"]] == ["cube_a", "cube_b"]
    assert summary["objects"][0]["bbox_max"] == [10.0, 10.0, 10.0]
    assert summary["objects"][1]["bbox_max"] == [20.0, 5.0, 2.0]
    assert summary["objects"][1]["transform"] == translation(20, 0, 0)
