"""A part moved or mirrored as well as edited reads as the edit — and an edit alone is never
explained away as a move."""

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from theseus.analyze import analyze_models, analyze_pair
from theseus.compare import diff_one_file
from theseus.load import load_model
from tests.test_motion import mirrored, moved, step


def part(hole_r=4.0, extra_hole=False, length=200):
    """A plate with a notched corner and a hole: no symmetry, so only one alignment fits."""
    shape = BRepPrimAPI_MakeBox(length, 50, 10).Shape()
    shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, 5), gp_Pnt(20, 10, 11)).Shape()).Shape()
    shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(50, 25, -1), gp_Dir(0, 0, 1)), hole_r, 12).Shape()).Shape()
    if extra_hole:
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(150, 25, -1), gp_Dir(0, 0, 1)), 3, 12).Shape()).Shape()
    return shape


def analyze(tmp_path, old, new):
    return analyze_models(load_model(step(old, tmp_path / "a.step")), load_model(step(new, tmp_path / "b.step")))


def section(r):
    return diff_one_file("p", r["old"], r["new"], precise=r["precise"])


def test_moved_and_edited_reports_only_the_edit(tmp_path):
    r = analyze(tmp_path, part(), moved(part(hole_r=5.0), (5, 3, 0)))
    alignment = r["precise"]["alignment"]
    assert alignment["translation"] == pytest.approx([5, 3, 0], abs=1e-6)
    assert alignment["rotation_deg"] == 0
    text = section(r)
    assert "Lined up first: moved by (+5.000, +3.000, +0.000) mm." in text
    assert "Ø +2.000 mm" in text
    # The plate's faces aren't reported as moved: all that's left is the hole, and its top and
    # bottom faces (whose areas it changes) as a summary.
    assert [f["kind"] for f in r["precise"]["findings"] if f["kind"] not in ("retrim", "vertices")] == ["surface"]
    # Nor do the metrics report the move again: only the hole's own small effect is left.
    assert r["new"]["com"] == pytest.approx(r["old"]["com"], abs=0.5)


def test_rotated_and_edited_reports_only_the_edit(tmp_path):
    r = analyze(tmp_path, part(), moved(part(hole_r=5.0), (10, -4, 2), 30, (1, 1, 1)))
    assert r["precise"]["alignment"]["rotation_deg"] == pytest.approx(30, abs=1e-6)
    assert "Ø +2.000 mm" in section(r)
    assert [f["kind"] for f in r["precise"]["findings"] if f["kind"] not in ("retrim", "vertices")] == ["surface"]


def test_mirrored_and_edited_says_mirrored_and_reports_the_edit(tmp_path):
    r = analyze(tmp_path, part(), mirrored(part(hole_r=5.0)))
    assert r["precise"]["alignment"]["mirror"]["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    text = section(r)
    assert "Lined up first: mirrored about XY." in text
    assert "Ø +2.000 mm" in text


def test_mirrored_about_its_own_base_and_edited(tmp_path):
    """The base face stays exactly where it was, which must not read as "the part didn't move"."""
    def blind(r):
        return BRepAlgoAPI_Cut(part(), BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(120, 25, 6), gp_Dir(0, 0, 1)), r, 10).Shape()).Shape()

    r = analyze(tmp_path, blind(2.5), mirrored(blind(3.0)))
    assert r["precise"]["alignment"]["mirror"]
    assert "blind hole along Z Ø5.000 → 6.000" in section(r)


def test_an_edit_in_place_is_not_aligned(tmp_path):
    r = analyze(tmp_path, part(), part(hole_r=5.0))
    assert "alignment" not in r["precise"]


def test_lengthening_a_bar_is_not_explained_as_a_move(tmp_path):
    r = analyze(tmp_path, part(), part(length=220))
    assert "alignment" not in r["precise"]


def test_unrelated_parts_are_not_aligned(tmp_path):
    other = BRepPrimAPI_MakeBox(33, 71, 19).Shape()
    r = analyze(tmp_path, part(), other)
    assert "alignment" not in r["precise"]


def test_all_holes_shifting_is_not_the_part_moving(tmp_path):
    """Most vertices belong to the holes, so they all "agree" under a +5 X move, but the outline
    never moved: that's a hole pattern shift."""
    def holes(dx):
        shape = BRepPrimAPI_MakeBox(200, 100, 8).Shape()
        for i in range(3):
            for j in range(6):
                shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(
                    gp_Ax2(gp_Pnt(30 + j * 25 + dx, 25 + i * 25, -1), gp_Dir(0, 0, 1)), 3, 12).Shape()).Shape()
        return shape

    r = analyze(tmp_path, holes(0), holes(5))
    assert "alignment" not in r["precise"]
    assert "axis moved 5.000 mm" in section(r)


def test_faces_split_differently_on_a_moved_part_are_not_changes(tmp_path):
    """Another exporter writes each hole as two halves, and the part sits at 45°."""
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    divide = ShapeUpgrade_ShapeDivideClosed(part())
    divide.SetNbSplitPoints(1)
    divide.Perform()
    r = analyze(tmp_path, part(), moved(divide.Result(), (20, 10, 0), 45))
    # Stating the move is enough: nothing was added, removed or offset.
    assert not [f for f in r["precise"]["findings"] if f["kind"] in ("added", "removed", "surface")]


def test_pure_move_is_still_a_move_not_an_alignment(tmp_path):
    r = analyze(tmp_path, part(), moved(part(), (5, 0, 0)))
    assert r["precise"]["motion"] and "alignment" not in r["precise"]


def test_pictures_use_the_lined_up_old_version(tmp_path):
    a, b = step(part(), tmp_path / "a.step"), step(moved(part(hole_r=5.0), (5, 3, 0)), tmp_path / "b.step")
    result, models = analyze_pair(a, b)
    assert result["precise"]["alignment"]
    assert models["old"].shape is not None
    assert result["old"]["bbox"][0] == pytest.approx(5, abs=1e-3)
