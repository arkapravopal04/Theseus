"""Detection limits: how small a change theseus finds, where it says it is, and how it grades it.

Every case builds a real part with OpenCascade, perturbs it, writes both versions to disk, and
runs the same analysis the CLI and GitHub Action run.
"""

import math

import pytest
import trimesh
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from theseus.analyze import analyze_models
from theseus.compare import SENSITIVITY, diff_one_file
from theseus.diff.brep import step_precision_mm
from theseus.fmt import fmt_len
from theseus.load import load_model


def plate(length=200.0, hole_x=50.0, hole_r=4.0):
    """A 200x50x10 plate with one Ø8 through-hole."""
    box = BRepPrimAPI_MakeBox(length, 50, 10).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(hole_x, 25, -1), gp_Dir(0, 0, 1)), hole_r, 12).Shape()
    return BRepAlgoAPI_Cut(box, hole).Shape()


def rotated(shape, degrees):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(degrees))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def write_step(shape, path) -> str:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))
    return str(path)


def write_stl(shape, path, deflection) -> str:
    BRepMesh_IncrementalMesh(shape, deflection)
    StlAPI_Writer().Write(shape, str(path))
    return str(path)


def analyze(old_path, new_path) -> dict:
    return analyze_models(load_model(str(old_path)), load_model(str(new_path)))


def report(old_path, new_path, sensitivity="engineering") -> str:
    r = analyze(old_path, new_path)
    return diff_one_file("part", r["old"], r["new"], SENSITIVITY[sensitivity], precise=r["precise"])


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return write_step(plate(), tmp_path_factory.mktemp("base") / "plate.step")


# --- exact B-rep vs exact B-rep ------------------------------------------------------------


@pytest.mark.parametrize("offset_mm, grade", [
    (1.0, "changed"),
    (0.19, "changed"),
    (0.001, "micro"),  # 1 µm
    (0.0001, "micro"),  # 0.1 µm
])
def test_face_offset_ladder(tmp_path, base, offset_mm, grade):
    new = write_step(plate(length=200 + offset_mm), tmp_path / "new.step")
    precise = analyze(base, new)["precise"]
    assert not precise["identical"]
    assert precise["max_deviation_mm"] == pytest.approx(offset_mm, rel=1e-6)
    top = max(precise["findings"], key=lambda f: f["magnitude_mm"])
    assert top["what"].startswith("plane x = 200.")  # located: the end face moved
    assert fmt_len(offset_mm, signed=True) in top["what"]
    section = report(base, new)
    assert ("(changed)" in section) == (grade == "changed")
    assert ("micro-change only" in section) == (grade == "micro")


def test_200mm_part_with_019mm_face_offset_is_changed(tmp_path, base):
    """Regression: the old rule needed a change to beat BOTH 0.01 mm AND 0.1%; on a 200 mm part
    0.1% is 0.2 mm, so a 0.19 mm face offset was reported as "no significant change"."""
    new = write_step(plate(length=200.19), tmp_path / "new.step")
    section = report(base, new)
    assert "(changed)" in section
    assert "plane x = 200.000 → 200.190" in section


def test_any_change_is_a_change_at_exact_sensitivity(tmp_path, base):
    new = write_step(plate(length=200.0001), tmp_path / "new.step")
    assert "micro-change only" in report(base, new, "engineering")
    assert "(changed)" in report(base, new, "exact")


def test_sensitivity_presets(tmp_path, base):
    new = write_step(plate(length=200.005), tmp_path / "new.step")  # 5 µm
    assert "micro-change only" in report(base, new, "engineering")  # threshold 10 µm
    assert "(changed)" in report(base, new, "fine")  # threshold 1 µm


def test_hole_diameter_change_of_2_microns(tmp_path, base):
    new = write_step(plate(hole_r=4.001), tmp_path / "new.step")
    precise = analyze(base, new)["precise"]
    hole = next(f for f in precise["findings"] if f["what"].startswith("through hole"))
    assert "Ø8.0000 → 8.0020" in hole["what"] and "Ø +2.0 µm" in hole["what"]
    assert hole["at"][0] == pytest.approx(50, abs=5)  # located at the hole


def test_hole_moved_with_volume_unchanged(tmp_path, base):
    """Aggregate metrics are blind to this: same volume, same bounding box."""
    new = write_step(plate(hole_x=50.5), tmp_path / "new.step")
    r = analyze(base, new)
    assert r["new"]["volume_mm3"] == pytest.approx(r["old"]["volume_mm3"], rel=1e-12)
    assert r["new"]["bbox"] == pytest.approx(r["old"]["bbox"])
    hole = next(f for f in r["precise"]["findings"] if f["what"].startswith("through hole"))
    assert "(50.000, 25.000) → (50.500, 25.000)" in hole["what"] and "axis moved 0.500 mm" in hole["what"]
    assert "(changed)" in diff_one_file("part", r["old"], r["new"], precise=r["precise"])


def test_the_changed_hole_leads_the_report(tmp_path, base):
    """The hole's corner vertices move further (0.75 mm) than its axis (0.5 mm), but "the Ø8 hole
    became Ø8.5 and moved" is what a reviewer needs first: face changes lead, summaries follow."""
    from theseus.compare import file_section

    new = write_step(plate(hole_x=50.5, hole_r=4.25), tmp_path / "new.step")
    r = analyze(base, new)
    assert [f["kind"] for f in r["precise"]["findings"]][-2:] == ["vertices", "retrim"]
    section = file_section("part", r["old"], r["new"], precise=r["precise"])
    assert section.headline.startswith("through hole along Z Ø8.000 → 8.500")
    assert section.largest_mm == pytest.approx(0.75, abs=1e-6)  # still the true largest difference


def test_tiny_rotation(tmp_path, base):
    new = write_step(rotated(plate(), 0.01), tmp_path / "new.step")
    precise = analyze(base, new)["precise"]
    assert any("tilted 0.01°" in f["what"] for f in precise["findings"])
    assert precise["max_deviation_mm"] == pytest.approx(math.radians(0.01) * math.hypot(200, 50), rel=0.05)


def test_reexport_is_identical(tmp_path, base):
    new = write_step(plate(), tmp_path / "new.step")  # same geometry, new timestamp in the header
    precise = analyze(base, new)["precise"]
    assert precise["identical"] and precise["method"] == "exact B-rep"
    assert "identical (to within 0.1 nm" in report(base, new)


def test_byte_identical(tmp_path, base):
    precise = analyze(base, base)["precise"]
    assert precise["identical"] and precise["method"] == "byte-identical"
    assert "identical (byte-for-byte)" in report(base, base)


def test_declared_precision_sets_the_limit(tmp_path, base):
    """A file that declares 1 µm precision can't show a 0.1 µm change as real."""
    def coarse_copy(src, name):
        text = open(src, encoding="utf-8").read().replace("LENGTH_MEASURE(1.E-07)", "LENGTH_MEASURE(1.E-03)")
        assert "LENGTH_MEASURE(1.E-03)" in text
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    old = coarse_copy(base, "old.step")
    new = coarse_copy(write_step(plate(length=200.0001), tmp_path / "n.step"), "new.step")
    precise = analyze(old, new)["precise"]
    assert precise["floor_mm"] == pytest.approx(1e-3)
    assert "declared precision" in precise["floor_note"]
    assert precise["identical"]


def test_written_digits_set_the_limit(tmp_path):
    path = tmp_path / "six_digits.step"
    path.write_text("ISO-10303-21;\nDATA;\n#1 = CARTESIAN_POINT('',(123.456,0.,0.));\nENDSEC;\n", encoding="utf-8")
    limit, why = step_precision_mm(str(path), 1.0)
    assert limit == pytest.approx(123.456e-5)
    assert "6 significant digits" in why


def test_inch_declared_precision_is_converted(tmp_path):
    path = tmp_path / "inch.step"
    path.write_text("ISO-10303-21;\nDATA;\n#1 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-04),#2,'','');\n",
                    encoding="utf-8")
    limit, _ = step_precision_mm(str(path), 25.4)
    assert limit == pytest.approx(2.54e-3)


# --- meshes ----------------------------------------------------------------------------


def _cube(size=20.0):
    m = trimesh.creation.box((size, size, size))
    m.apply_translation((size / 2,) * 3)
    return m


def test_mesh_vertex_moved_one_micron(tmp_path):
    cube = _cube()
    cube.export(tmp_path / "old.stl")
    cube.vertices[0] += (0.001, 0, 0)
    cube.export(tmp_path / "new.stl")
    precise = analyze(tmp_path / "old.stl", tmp_path / "new.stl")["precise"]
    assert precise["method"] == "mesh vertices (same triangulation)"
    assert precise["max_deviation_mm"] == pytest.approx(0.001, rel=1e-3)  # float32 storage
    assert precise["findings"][0]["what"].startswith("surfaces differ by up to 1.0 µm")
    assert "micro-change only" in report(tmp_path / "old.stl", tmp_path / "new.stl")


def test_retriangulated_flat_mesh_is_identical(tmp_path):
    _cube().export(tmp_path / "old.stl")
    _cube().subdivide().export(tmp_path / "new.stl")
    assert analyze(tmp_path / "old.stl", tmp_path / "new.stl")["precise"]["identical"]


def test_retessellated_sphere_is_identical_but_a_bigger_sphere_is_not(tmp_path):
    trimesh.creation.icosphere(subdivisions=4, radius=10).export(tmp_path / "coarse.stl")
    trimesh.creation.icosphere(subdivisions=5, radius=10).export(tmp_path / "fine.stl")
    trimesh.creation.icosphere(subdivisions=5, radius=10.2).export(tmp_path / "bigger.stl")
    assert analyze(tmp_path / "coarse.stl", tmp_path / "fine.stl")["precise"]["identical"]
    precise = analyze(tmp_path / "coarse.stl", tmp_path / "bigger.stl")["precise"]
    assert not precise["identical"] and precise["max_deviation_mm"] == pytest.approx(0.2, abs=0.02)


def test_step_vs_its_own_coarse_stl_is_identical(tmp_path, base):
    """A coarse STL cuts corners on the hole (chord error); that isn't a design change."""
    stl = write_stl(plate(), tmp_path / "plate.stl", 0.1)
    precise = analyze(base, stl)["precise"]
    assert precise["identical"]
    assert "coarsely meshed" in precise["floor_note"]


def test_step_vs_stl_with_a_bigger_hole(tmp_path, base):
    stl = write_stl(plate(hole_r=4.1), tmp_path / "plate.stl", 0.1)
    precise = analyze(base, stl)["precise"]
    assert not precise["identical"]
    assert precise["max_deviation_mm"] == pytest.approx(0.1, abs=0.01)
    assert precise["findings"][0]["at"][0] == pytest.approx(50, abs=5)


def test_step_vs_fine_stl_finds_10_micron_change(tmp_path, base):
    stl = write_stl(plate(hole_r=4.01), tmp_path / "plate.stl", 0.002)
    precise = analyze(base, stl)["precise"]
    assert not precise["identical"]
    assert precise["max_deviation_mm"] == pytest.approx(0.01, abs=0.002)


# --- formatting ------------------------------------------------------------------------


@pytest.mark.parametrize("mm, text", [
    (0.19, "0.190 mm"), (0.002, "2.0 µm"), (1e-4, "0.1 µm"), (3e-7, "0.3 nm"), (12.5, "12.500 mm"),
])
def test_lengths_print_in_a_readable_unit(mm, text):
    assert fmt_len(mm) == text
