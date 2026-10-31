"""Faces read as the features an engineer would name — and an unrecognised face is still reported."""

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopoDS import TopoDS

from theseus.analyze import analyze_models
from theseus.compare import diff_one_file
from theseus.diff.brep import _describe_face, _unique
from theseus.diff.features import find_fillets, find_holes
from theseus.load import load_model
from tests.test_motion import mirrored, step


def box():
    return BRepPrimAPI_MakeBox(100, 60, 20).Shape()


def cut_cyl(shape, r, z0=-1, height=30, x=30, y=30):
    return BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r, height).Shape()).Shape()


def cut_cone(shape, r1, r2, z0, height, x=30, y=30):
    return BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r1, r2, height).Shape()).Shape()


def faces_of(shape):
    return [_describe_face(TopoDS.Face(f)) for f in _unique(shape, TopAbs_FACE)]


def only_hole(shape):
    holes = find_holes(faces_of(shape), shape)
    assert len(holes) == 1
    return holes[0]


def filleted(radius=3.0):
    """The box with its four vertical edges rounded."""
    shape = box()
    maker = BRepFilletAPI_MakeFillet(shape)
    for edge in _unique(shape, TopAbs_EDGE):
        curve = BRepAdaptor_Curve(TopoDS.Edge(edge))
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        if abs(a.X() - b.X()) < 1e-9 and abs(a.Y() - b.Y()) < 1e-9:
            maker.Add(radius, TopoDS.Edge(edge))
    return maker.Shape()


def compare(tmp_path, old, new):
    r = analyze_models(load_model(step(old, tmp_path / "a.step")), load_model(step(new, tmp_path / "b.step")))
    return r, diff_one_file("p", r["old"], r["new"], precise=r["precise"])


def test_through_hole():
    hole = only_hole(cut_cyl(box(), 4))
    assert hole.kind == "through hole" and hole.diameter == pytest.approx(8) and hole.depth is None


def test_blind_hole_with_flat_bottom():
    hole = only_hole(cut_cyl(box(), 4, z0=8, height=20))
    assert hole.kind == "blind hole" and hole.depth == pytest.approx(12)


def test_blind_hole_with_drill_point_measures_the_bore():
    shape = cut_cone(cut_cyl(box(), 4, z0=8, height=20), 0, 4, 4, 4)
    hole = only_hole(shape)
    assert hole.kind == "blind hole" and hole.depth == pytest.approx(12)


def test_counterbored_hole():
    hole = only_hole(cut_cyl(cut_cyl(box(), 3), 6, z0=15, height=10))
    assert hole.kind == "counterbored through hole"
    assert hole.diameter == pytest.approx(6) and hole.cbore == pytest.approx((12, 5))


def test_countersunk_hole():
    hole = only_hole(cut_cone(cut_cyl(box(), 3), 3, 6, 17, 3.0001))
    assert hole.kind == "countersunk through hole" and hole.csink_deg == pytest.approx(90, abs=0.1)


def test_a_boss_is_not_a_hole():
    assert find_holes(faces_of(BRepPrimAPI_MakeCylinder(5, 20).Shape()), BRepPrimAPI_MakeCylinder(5, 20).Shape()) == []


def test_a_hole_split_into_halves_is_one_hole():
    """Many exporters write a hole as two half cylinders."""
    divide = ShapeUpgrade_ShapeDivideClosed(cut_cyl(box(), 4))
    divide.SetNbSplitPoints(1)
    divide.Perform()
    shape = divide.Result()
    faces = faces_of(shape)
    assert sum(f.type == "cylinder" for f in faces) == 2
    hole = only_hole(shape)
    assert hole.kind == "through hole" and hole.diameter == pytest.approx(8)


def test_hole_recognised_on_a_mirrored_part():
    assert only_hole(mirrored(cut_cyl(box(), 4, z0=8, height=20))).kind == "blind hole"


def test_fillets_found_and_radius_read():
    shape = filleted(3.0)
    fillets = find_fillets(faces_of(shape), shape, [])
    assert len(fillets) == 4 and all(f.radius == pytest.approx(3) for f in fillets)


def test_a_shaft_split_into_quarters_has_no_fillets():
    divide = ShapeUpgrade_ShapeDivideClosed(BRepPrimAPI_MakeCylinder(20, 30).Shape())
    divide.SetNbSplitPoints(3)
    divide.Perform()
    shape = divide.Result()
    assert sum(f.type == "cylinder" for f in faces_of(shape)) == 4
    assert find_fillets(faces_of(shape), shape, []) == []


def test_a_hole_that_became_a_notch_still_reports_the_notch(tmp_path):
    """The old cylinder is accounted for by "removed hole"; the new half cylinder, which no
    feature recognises, must still appear."""
    new = cut_cyl(box(), 4, x=0, y=30)
    _, text = compare(tmp_path, cut_cyl(box(), 4, x=5, y=30), new)
    assert "removed through hole" in text
    assert "cylinder along Z Ø8.000" in text


def test_resized_hole_is_one_line(tmp_path):
    r, text = compare(tmp_path, cut_cyl(box(), 4), cut_cyl(box(), 5))
    assert "through hole along Z Ø8.000 → 10.000 at (30.000, 30.000) — Ø +2.000 mm" in text
    assert "cylinder" not in text


def test_split_hole_resized_is_still_one_finding(tmp_path):
    def split(shape):
        divide = ShapeUpgrade_ShapeDivideClosed(shape)
        divide.SetNbSplitPoints(1)
        divide.Perform()
        return divide.Result()

    r, text = compare(tmp_path, split(cut_cyl(box(), 4)), split(cut_cyl(box(), 5)))
    assert sum("hole" in f["what"] for f in r["precise"]["findings"]) == 1
    assert "cylinder" not in text


def test_added_and_removed_hole(tmp_path):
    with_hole, without = cut_cyl(box(), 4), box()
    assert "added through hole along Z Ø8.000" in compare(tmp_path, without, with_hole)[1]
    assert "removed through hole along Z Ø8.000" in compare(tmp_path, with_hole, without)[1]


def test_hole_that_moved_far_is_the_same_hole(tmp_path):
    _, text = compare(tmp_path, cut_cyl(box(), 2), cut_cyl(box(), 2, x=80))
    assert "axis moved 50.000 mm" in text and "added through hole" not in text


def test_blind_hole_deepened(tmp_path):
    _, text = compare(tmp_path, cut_cyl(box(), 4, z0=8, height=20), cut_cyl(box(), 4, z0=5, height=20))
    assert "depth 12.000 → 15.000" in text


def test_blind_hole_drilled_through(tmp_path):
    _, text = compare(tmp_path, cut_cyl(box(), 4, z0=8, height=20), cut_cyl(box(), 4))
    assert "blind hole → through hole" in text


def test_fillet_resized_and_removed(tmp_path):
    _, text = compare(tmp_path, filleted(3.0), filleted(4.0))
    assert "fillet R3.000 → 4.000" in text and "R +1.000 mm" in text
    _, text = compare(tmp_path, filleted(3.0), box())
    assert "removed fillet R3.000" in text


def test_hole_edit_on_a_moved_part_uses_feature_words(tmp_path):
    from tests.test_motion import moved
    notch = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, 15), gp_Pnt(10, 10, 21)).Shape()
    old = BRepAlgoAPI_Cut(cut_cyl(box(), 4), notch).Shape()
    new = moved(BRepAlgoAPI_Cut(cut_cyl(box(), 5), notch).Shape(), (7, 0, 0))
    r, text = compare(tmp_path, old, new)
    assert r["precise"]["alignment"] and "through hole along Z Ø8.000 → 10.000" in text


def test_identical_parts_have_no_feature_findings(tmp_path):
    r, _ = compare(tmp_path, cut_cyl(box(), 4), cut_cyl(box(), 4))
    assert r["precise"]["identical"]
