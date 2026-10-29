"""Format detection, every reader, units, and open/inside-out geometry."""

import os
import shutil

import pytest
import trimesh
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pln

from theseus.formats import detect, is_candidate, paths_filter
from theseus.load import LoadError, load_model, load_shape
from theseus.metrics import compute_metrics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def cube_mesh(size: float = 20.0) -> trimesh.Trimesh:
    m = trimesh.creation.box((size, size, size))
    m.apply_translation((size / 2, size / 2, size / 2))
    return m


def metrics_of(path, **kw) -> dict:
    return compute_metrics(load_model(str(path), **kw))


# --- units -----------------------------------------------------------------------------


def test_inch_step_is_converted_to_mm_and_says_so():
    m = metrics_of(fixture("cube20inch.step"))
    assert m["bbox"][3] - m["bbox"][0] == pytest.approx(508, abs=1e-3)  # 20 in
    assert "inch" in m["units_note"] and "converted" in m["units_note"]


def test_mm_step_reports_mm():
    assert metrics_of(fixture("cube20.step"))["units_note"] == "file units: mm"


def test_inch_iges_is_converted_to_mm():
    m = metrics_of(fixture("cube1inch.igs"))
    assert m["bbox"][3] - m["bbox"][0] == pytest.approx(25.4, abs=1e-3)
    assert "inch" in m["units_note"]


def test_iges_cube_volume():
    m = metrics_of(fixture("cube20.igs"))
    assert m["format"] == "iges"
    assert m["volume_mm3"] == pytest.approx(8000, rel=1e-6)
    assert m["solid_count"] == 1


def test_declared_units_scale_unitless_mesh():
    m = metrics_of(fixture("cube20.stl"), assume_units="in")
    assert m["bbox"][3] - m["bbox"][0] == pytest.approx(508, rel=1e-6)
    assert "declared in" in m["units_note"]


def test_declared_units_ignored_for_formats_with_their_own_units():
    m = metrics_of(fixture("cube20.step"), assume_units="in")
    assert m["bbox"][3] - m["bbox"][0] == pytest.approx(20, abs=1e-3)


def test_unknown_unit_rejected():
    with pytest.raises(LoadError):
        load_model(fixture("cube20.stl"), assume_units="furlong")


def test_unitless_mesh_says_mm_is_an_assumption():
    assert metrics_of(fixture("cube20.stl"))["units_note"] == "unitless format, assumed mm"


# --- mesh formats ----------------------------------------------------------------------


@pytest.mark.parametrize("ext", ["obj", "ply", "off", "3mf", "stl"])
def test_mesh_formats_load_with_exact_volume(tmp_path, ext):
    path = tmp_path / f"cube.{ext}"
    cube_mesh().export(path)
    m = metrics_of(path)
    assert m["kind"] == "mesh"
    assert m["volume_mm3"] == pytest.approx(8000, rel=1e-9)
    assert m["com"] == pytest.approx((10, 10, 10), abs=1e-9)
    assert m["area_mm2"] == pytest.approx(2400, rel=1e-9)


def test_glb_metres_are_converted_to_mm(tmp_path):
    path = tmp_path / "cube.glb"
    cube_mesh(0.02).export(path)  # glTF is defined in metres: a 20 mm cube is 0.02 m
    m = metrics_of(path)
    assert m["bbox"][3] - m["bbox"][0] == pytest.approx(20, rel=1e-6)
    assert m["volume_mm3"] == pytest.approx(8000, rel=1e-5)


def test_inside_out_mesh_is_flipped_not_negative(tmp_path):
    inverted = cube_mesh()
    inverted.invert()
    path = tmp_path / "inverted.stl"
    inverted.export(path)
    m = metrics_of(path)
    assert m["volume_mm3"] == pytest.approx(8000, rel=1e-9)
    assert any("inside-out" in w for w in m["warnings"])


def test_open_mesh_has_no_volume(tmp_path):
    cube = cube_mesh()
    path = tmp_path / "open.stl"
    trimesh.Trimesh(cube.vertices, cube.faces[:-2]).export(path)
    m = metrics_of(path)
    # trimesh's split() fills holes by default; this guards against that sneaking back in.
    assert m["volume_mm3"] is None
    assert m["com"] is None
    assert m["solid_count"] == 0 and m["open_bodies"] == 1
    assert any("open mesh" in w for w in m["warnings"])
    assert m["bbox"] == pytest.approx((0, 0, 0, 20, 20, 20))


def test_mesh_with_closed_and_open_bodies_counts_only_closed_volume(tmp_path):
    closed = cube_mesh(10)
    opened = cube_mesh(10)
    opened.apply_translation((30, 0, 0))
    opened = trimesh.Trimesh(opened.vertices, opened.faces[:-2])
    path = tmp_path / "mixed.stl"
    trimesh.util.concatenate([closed, opened]).export(path)
    m = metrics_of(path)
    assert m["volume_mm3"] == pytest.approx(1000, rel=1e-9)
    assert m["solid_count"] == 1 and m["open_bodies"] == 1


def test_surface_only_brep_has_no_volume(tmp_path):
    face = BRepBuilderAPI_MakeFace(gp_Pln(), 0, 10, 0, 10).Face()
    path = str(tmp_path / "sheet.brep")
    BRepTools.Write_s(face, path)
    m = metrics_of(path)
    assert m["volume_mm3"] is None
    assert m["solid_count"] == 0 and m["open_bodies"] == 1
    assert any("no closed solids" in w for w in m["warnings"])


def test_valid_brep_is_reported_valid():
    assert metrics_of(fixture("bracket_fillets.step"))["valid"] is True


def test_load_shape_rejects_mesh_formats():
    with pytest.raises(LoadError):
        load_shape(fixture("cube20.stl"))


# --- detection -------------------------------------------------------------------------


def test_uppercase_extension(tmp_path):
    path = tmp_path / "PART.STEP"
    shutil.copy(fixture("cube20.step"), path)
    assert detect(str(path)).name == "step"
    assert is_candidate("cad/PART.STEP")


def test_content_wins_over_wrong_extension(tmp_path):
    path = tmp_path / "actually_step.stl"
    shutil.copy(fixture("cube20.step"), path)
    assert metrics_of(path)["format"] == "step"


def test_unknown_extension_detected_by_content(tmp_path):
    path = tmp_path / "part.step.bak"
    shutil.copy(fixture("cube20.step"), path)
    assert metrics_of(path)["volume_mm3"] == pytest.approx(8000, abs=1e-3)


def test_binary_stl_with_solid_header_is_binary(tmp_path):
    path = tmp_path / "cube.stl"
    cube_mesh().export(path, file_type="stl")  # binary
    data = bytearray(path.read_bytes())
    data[:5] = b"solid"  # many exporters do this; the size check must still win
    path.write_bytes(bytes(data))
    assert metrics_of(path)["volume_mm3"] == pytest.approx(8000, rel=1e-9)


def test_compiled_object_file_is_not_cad(tmp_path):
    path = tmp_path / "main.obj"
    path.write_bytes(b"\x4c\x01\x03\x00" + bytes(200))  # COFF header, not Wavefront OBJ
    assert detect(str(path)) is None
    with pytest.raises(LoadError, match="not a recognised CAD file"):
        load_model(str(path))


def test_corrupt_step_is_a_load_error(tmp_path):
    path = tmp_path / "broken.step"
    path.write_text("ISO-10303-21;\nthis is not a STEP file\n")
    with pytest.raises(LoadError):
        load_model(str(path))


def test_empty_mesh_is_a_load_error(tmp_path):
    path = tmp_path / "empty.obj"
    path.write_text("v 0 0 0\n")
    with pytest.raises(LoadError):
        load_model(str(path))


def test_paths_filter_matches_any_case():
    import fnmatch

    globs = paths_filter()
    for name in ("a/part.step", "a/PART.STEP", "a/Part.Step", "x.3MF", "y.stp"):
        # fnmatch understands the same [xX] classes GitHub's filter does
        assert any(fnmatch.fnmatchcase(name, g.replace("**", "*")) for g in globs), name
    assert not any(fnmatch.fnmatchcase("notes.txt", g.replace("**", "*")) for g in globs)
