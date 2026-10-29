"""A part that was only moved reads as "moved", with the motion, and only when that's provably true."""

import math

import pytest
import trimesh
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from theseus.analyze import analyze_models
from theseus.compare import diff_one_file
from theseus.load import load_model


def plate(hole_r=4.0):
    box = BRepPrimAPI_MakeBox(200, 50, 10).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(50, 25, -1), gp_Dir(0, 0, 1)), hole_r, 12).Shape()
    return BRepAlgoAPI_Cut(box, hole).Shape()


def moved(shape, offset=(0, 0, 0), degrees=0.0, axis=(0, 0, 1)):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*offset))
    shape = BRepBuilderAPI_Transform(shape, t, True).Shape()
    if degrees:
        r = gp_Trsf()
        r.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), math.radians(degrees))
        shape = BRepBuilderAPI_Transform(shape, r, True).Shape()
    return shape


def step(shape, path) -> str:
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return step(plate(), tmp_path_factory.mktemp("base") / "plate.step")


def analyze(a, b):
    return analyze_models(load_model(str(a)), load_model(str(b)))


def test_translation(tmp_path, base):
    r = analyze(base, step(moved(plate(), (5, 0, 0)), tmp_path / "n.step"))
    motion = r["precise"]["motion"]
    assert motion["translation"] == pytest.approx([5, 0, 0], abs=1e-9)
    assert motion["rotation_deg"] == 0
    section = diff_one_file("plate.step", r["old"], r["new"], precise=r["precise"])
    assert "— moved (+5.000 mm X; shape unchanged)" in section


def test_one_micron_move_is_still_a_move(tmp_path, base):
    r = analyze(base, step(moved(plate(), (0.001, 0, 0)), tmp_path / "n.step"))
    assert r["precise"]["motion"]["translation"] == pytest.approx([0.001, 0, 0], abs=1e-9)


def test_rotation_about_an_oblique_axis(tmp_path, base):
    r = analyze(base, step(moved(plate(), (10, 0, 0), 30, (1, 1, 1)), tmp_path / "n.step"))
    motion = r["precise"]["motion"]
    assert motion["rotation_deg"] == pytest.approx(30, abs=1e-6)
    assert motion["axis"] == pytest.approx([1 / math.sqrt(3)] * 3, abs=1e-6)


def test_symmetric_part_reports_the_smallest_rotation(tmp_path, base):
    """The plate turned 90° about Z also equals it flipped 180° about a diagonal; say 90°."""
    r = analyze(base, step(moved(plate(), degrees=90), tmp_path / "n.step"))
    motion = r["precise"]["motion"]
    assert abs(motion["rotation_deg"]) == pytest.approx(90, abs=1e-6)
    assert motion["axis"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert "rotated 90° about Z" in diff_one_file("p", r["old"], r["new"], precise=r["precise"])


def test_moved_and_modified_is_not_a_move(tmp_path, base):
    r = analyze(base, step(moved(plate(hole_r=4.2), (5, 0, 0)), tmp_path / "n.step"))
    assert r["precise"]["motion"] is None
    assert "(changed)" in diff_one_file("p", r["old"], r["new"], precise=r["precise"])


def test_mesh_move(tmp_path):
    box = trimesh.creation.box((40, 20, 10))
    box.export(tmp_path / "a.stl")
    box.apply_translation((3, 0, 0))
    box.apply_transform(trimesh.transformations.rotation_matrix(math.radians(45), (0, 0, 1)))
    box.export(tmp_path / "b.stl")
    motion = analyze(tmp_path / "a.stl", tmp_path / "b.stl")["precise"]["motion"]
    assert motion["rotation_deg"] == pytest.approx(45, abs=1e-4)
