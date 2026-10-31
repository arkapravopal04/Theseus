"""Problems that only showed up on real CAD files, kept fixed with small stand-ins for them."""

import numpy as np
import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.Geom import Geom_Plane
from OCP.Geom2d import Geom2d_Line
from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d
from OCP.Poly import Poly_Triangle, Poly_Triangulation
from OCP.OCP.collections import Array2_gp_Pnt
from OCP.TopoDS import TopoDS_Compound, TopoDS_Face

from theseus.analyze import analyze_models
from theseus.cli import main as cli_main
from theseus.compare import file_section
from theseus.diff import visual
from theseus.diff.brep import compare_breps
from theseus.formats.brep import triangles_only
from theseus.load import load_model
from theseus.metrics import bounding_box, compute_metrics
from theseus.model import Model, part_of
from theseus.render.scene import model_layer
from tests.test_motion import mirrored, moved, step


def compound(*shapes):
    out = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(out)
    for shape in shapes:
        builder.Add(out, shape)
    return out


def box_with_annotation():
    """A solid, plus the construction sheet and wire a STEP file with PMI often carries, far away."""
    sheet = BRepBuilderAPI_MakeFace(Geom_Plane(gp_Pnt(500, 0, 0), gp_Dir(0, 0, 1)), 0, 50, 0, 50, 1e-6).Face()
    wire = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 900), gp_Pnt(10, 0, 900)).Edge()
    return compound(BRepPrimAPI_MakeBox(20, 20, 20).Shape(), sheet, wire)


def test_annotation_is_not_part_of_the_size_or_the_picture():
    shape = box_with_annotation()
    assert bounding_box(shape) == pytest.approx((0, 0, 0, 20, 20, 20), abs=1e-6)
    assert np.asarray(model_layer(Model("", "step", "brep", shape=shape)).vertices).max() == pytest.approx(20)


def test_annotation_changes_are_not_part_changes(tmp_path):
    old = Model("a", "brep", "brep", shape=box_with_annotation())
    new = Model("b", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 20).Shape())
    assert compare_breps(old, new).identical


def test_a_file_with_no_solids_keeps_its_faces():
    sheet = BRepBuilderAPI_MakeFace(Geom_Plane(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0, 50, 0, 50, 1e-6).Face()
    assert bounding_box(part_of(compound(sheet)))[3] == pytest.approx(50, abs=1e-6)


def trimmed_spline_face():
    """A flat spline surface with a tall bump in one corner, trimmed to a triangle clear of it. The
    face stays near z = 0, though the surface's box reaches the bump."""
    points = Array2_gp_Pnt(1, 6, 1, 6)
    for i in range(1, 7):
        for j in range(1, 7):
            points.SetValue(i, j, gp_Pnt(20.0 * (i - 1), 20.0 * (j - 1), 50.0 if i == 6 and j == 6 else 0.0))
    surface = GeomAPI_PointsToBSplineSurface(points).Surface()
    u0, u1, v0, v1 = surface.Bounds()
    du, dv = u1 - u0, v1 - v0
    corners = [gp_Pnt2d(u0 + .05 * du, v0 + .05 * dv), gp_Pnt2d(u0 + .45 * du, v0 + .05 * dv), gp_Pnt2d(u0 + .05 * du, v0 + .45 * dv)]
    wire = BRepBuilderAPI_MakeWire()
    for a, b in zip(corners, corners[1:] + corners[:1]):
        line = Geom2d_Line(a, gp_Dir2d(b.X() - a.X(), b.Y() - a.Y()))
        wire.Add(BRepBuilderAPI_MakeEdge(line, surface, 0.0, a.Distance(b)).Edge())
    BRepLib.BuildCurves3d_s(wire.Wire())
    return BRepBuilderAPI_MakeFace(surface, wire.Wire()).Face()


def test_bounding_box_covers_a_trimmed_spline_face_not_its_whole_surface():
    assert bounding_box(trimmed_spline_face())[5] < 5.0


def spindle(bore=3.0):
    """A solid with real spline side faces, and a hole off-centre so that it has a handedness."""
    sections = BRepOffsetAPI_ThruSections(True, False)
    for z, r in ((0, 20), (15, 26), (30, 18), (45, 24)):
        sections.AddWire(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), r)).Edge()).Wire())
    shape = sections.Shape()
    for x, y, r in ((8, 2, bore), (-5, 9, 2.0)):
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, 10), gp_Dir(0, 0, 1)), r, 50).Shape()).Shape()
    return shape


def analyze(tmp_path, old, new):
    return analyze_models(load_model(step(old, tmp_path / "a.step")), load_model(step(new, tmp_path / "b.step")))


def test_mirrored_spline_part_has_no_invented_differences(tmp_path):
    """Spline faces once reported millimetres of deviation, and their "re-trimmed" boundaries half a
    millimetre, between a part and its exact mirror."""
    r = analyze(tmp_path, spindle(), mirrored(spindle(bore=3.002)))
    assert r["precise"]["alignment"]["mirror"]
    spline = [f for f in r["precise"]["findings"] if "bspline" in f["what"]]
    assert not spline or max(f["magnitude_mm"] for f in spline) < 0.01
    assert r["precise"]["max_deviation_mm"] < 0.01


def test_a_tiny_edit_on_a_mirrored_part_stays_a_mirror_verdict(tmp_path):
    r = analyze(tmp_path, spindle(), mirrored(spindle(bore=3.002)))
    section = file_section("p", r["old"], r["new"], precise=r["precise"])
    assert section.status == "mirrored" and "opposite hand" in section.suffix


def test_round_faces_at_right_angles_are_not_one_face_changed(tmp_path):
    def holed(direction, start):
        return BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(40, 40, 40).Shape(), BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(*start), gp_Dir(*direction)), 4, 50).Shape()).Shape()

    # Compared directly: as whole files, one is simply the other turned 90°, and says so.
    old = Model("a", "brep", "brep", shape=holed((0, 0, 1), (20, 20, -1)))
    new = Model("b", "brep", "brep", shape=holed((1, 0, 0), (-1, 20, 20)))
    text = " ".join(f.what for f in compare_breps(old, new).findings)
    assert "tilted" not in text
    assert "removed through hole along Z" in text and "added through hole along X" in text


def filleted_post(split=False):
    """A plate with a filleted edge, written with that fillet as one face or split in two."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideArea
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from theseus.diff.brep import _unique

    box = BRepPrimAPI_MakeBox(60, 40, 20).Shape()
    maker = BRepFilletAPI_MakeFillet(box)
    for edge in _unique(box, TopAbs_EDGE):
        curve = BRepAdaptor_Curve(TopoDS.Edge(edge))
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        if abs(a.X() - b.X()) < 1e-9 and abs(a.Y() - b.Y()) < 1e-9:
            maker.Add(4.0, TopoDS.Edge(edge))
    shape = maker.Shape()
    if split:
        divide = ShapeUpgrade_ShapeDivideArea(shape)
        divide.MaxArea = 60.0
        divide.Perform()
        shape = divide.Result()
    return shape


def test_same_fillet_written_as_different_faces_is_not_removed(tmp_path):
    """Real exports differ like this: one rounded corner as two faces, or as one. Nothing changed."""
    r = analyze(tmp_path, filleted_post(), filleted_post(split=True))
    text = " ".join(f["what"] for f in r["precise"]["findings"])
    assert "fillet" not in text
    assert r["precise"]["max_deviation_mm"] < 0.01


def test_a_really_removed_fillet_is_still_reported(tmp_path):
    r = analyze(tmp_path, filleted_post(), BRepPrimAPI_MakeBox(60, 40, 20).Shape())
    assert any("removed fillet R4.000" in f["what"] for f in r["precise"]["findings"])


def test_a_ball_end_is_not_a_fillet():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    from theseus.diff.brep import _describe_face, _unique
    from theseus.diff.features import find_fillets

    ball = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 30), 10).Shape()
    post = BRepPrimAPI_MakeCylinder(4, 25).Shape()
    shape = BRepAlgoAPI_Fuse(post, ball).Shape()
    faces = [_describe_face(TopoDS.Face(f)) for f in _unique(shape, TopAbs_FACE)]
    assert find_fillets(faces, shape, []) == []


def test_summaries_survive_the_cap_on_findings():
    from theseus.diff import MAX_FINDINGS, Finding, PreciseDiff

    many = [Finding(f"face {i}", 1e-3) for i in range(MAX_FINDINGS + 50)] + [Finding("faces re-trimmed", 0.5, kind="retrim")]
    d = PreciseDiff("exact B-rep", 1e-7, "", False, findings=many).to_dict()
    assert sum(f["kind"] == "surface" for f in d["findings"]) == MAX_FINDINGS
    assert any(f["kind"] == "retrim" for f in d["findings"])


def test_a_retrim_shift_is_not_the_largest_difference(tmp_path):
    """A whole face against half of one shows a big boundary shift; the geometry didn't move that far."""
    r = analyze(tmp_path, filleted_post(), filleted_post(split=True))
    section = file_section("p", r["old"], r["new"], precise=r["precise"])
    assert not section.largest_mm or section.largest_mm < 0.01
    assert section.status != "changed"


def test_edges_split_differently_are_not_moved_vertices():
    """The same faces, with a hole's seam written elsewhere: nothing about the part moved."""
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    part = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(40, 40, 10).Shape(), BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(20, 20, -1), gp_Dir(0, 0, 1)), 5, 12).Shape()).Shape()
    divide = ShapeUpgrade_ShapeDivideClosed(part)
    divide.SetNbSplitPoints(1)
    divide.Perform()
    result = compare_breps(Model("a", "brep", "brep", shape=part), Model("b", "brep", "brep", shape=divide.Result()))
    assert not [f for f in result.findings if f.kind == "vertices"]


def test_a_vertex_really_off_the_other_part_is_still_reported():
    a = Model("a", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 20).Shape())
    b = Model("b", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 23).Shape())
    assert [f for f in compare_breps(a, b).findings if f.kind == "vertices"][0].magnitude_mm == pytest.approx(3)


def nearly_symmetric(extra_hole=False, handed=False):
    """Symmetric about y = 30 except for three holes on one side, and possibly a fourth. A flat plate
    has no handedness (its mirror image is itself turned over), unless a notch in one face gives it one."""
    shape = BRepPrimAPI_MakeBox(160, 60, 12).Shape()
    if handed:
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeBox(gp_Pnt(150, -1, 8), gp_Pnt(161, 8, 13)).Shape()).Shape()
    holes = [(20 + i * 20, y) for i in range(7) for y in (15, 45)] + [(30, 20), (70, 20), (110, 20)]
    if extra_hole:
        holes.append((130, 22))
    for x, y in holes:
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), 2, 14).Shape()).Shape()
    return shape


def test_nearly_symmetric_part_mirrored_and_edited_says_mirrored(tmp_path):
    """Most of it coincides in place with its own mirror image, which isn't the part staying put."""
    r = analyze(tmp_path, nearly_symmetric(handed=True), mirrored(nearly_symmetric(extra_hole=True, handed=True), (0, 30, 0), (0, 1, 0)))
    assert r["precise"]["alignment"]["mirror"]
    assert "added through hole along Z Ø4.000" in " ".join(f["what"] for f in r["precise"]["findings"])


def test_nearly_symmetric_part_turned_over_and_edited_says_rotated(tmp_path):
    """With no handedness, the same flip is a 180° turn, and the plainer thing to call it."""
    r = analyze(tmp_path, nearly_symmetric(), mirrored(nearly_symmetric(extra_hole=True), (0, 30, 0), (0, 1, 0)))
    assert abs(r["precise"]["alignment"]["rotation_deg"]) == pytest.approx(180, abs=1e-6)
    assert "added through hole along Z Ø4.000" in " ".join(f["what"] for f in r["precise"]["findings"])


def test_annotation_does_not_stop_a_part_reading_as_moved_or_mirrored(tmp_path):
    """Comparing the exact mirror of a part with an annotation sheet, to that part without one."""
    def sheeted(shape):
        return compound(shape, BRepBuilderAPI_MakeFace(Geom_Plane(gp_Pnt(500, 0, 0), gp_Dir(0, 0, 1)), 0, 50, 0, 50, 1e-6).Face())

    notch = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, 5), gp_Pnt(10, 10, 21)).Shape()
    part = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(60, 40, 20).Shape(), notch).Shape()
    r = analyze(tmp_path, sheeted(part), mirrored(part))
    assert r["precise"]["motion"]["mirror"]


def test_an_added_face_is_sized_by_how_far_the_geometry_moved(tmp_path):
    """Filling a 2 mm recess removes a large floor, but the geometry only moved 2 mm."""
    recess = BRepPrimAPI_MakeBox(gp_Pnt(10, 10, 18), gp_Pnt(50, 30, 21)).Shape()
    r = analyze(tmp_path, BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(60, 40, 20).Shape(), recess).Shape(),
                BRepPrimAPI_MakeBox(60, 40, 20).Shape())
    assert r["precise"]["max_deviation_mm"] == pytest.approx(2, abs=0.01)


def test_a_face_written_as_a_spline_is_not_removed_and_added(tmp_path):
    """The same flat side, exported as a spline by another system."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    box = BRepPrimAPI_MakeBox(60, 40, 20).Shape()
    r = analyze(tmp_path, box, BRepBuilderAPI_NurbsConvert(box).Shape())
    assert not [f for f in r["precise"]["findings"] if f["kind"] in ("added", "removed")]
    assert file_section("p", r["old"], r["new"], precise=r["precise"]).status != "changed"


def test_the_reported_comparison_is_the_fine_one_not_the_first_look(tmp_path, monkeypatch):
    """The quick first look is only for deciding what to compare."""
    import theseus.analyze as analyze_module

    calls = []
    real = analyze_module.precise_diff

    def spy(old, new, coarse=False):
        calls.append(coarse)
        return real(old, new, coarse=coarse)

    monkeypatch.setattr(analyze_module, "precise_diff", spy)
    analyze(tmp_path, spindle(), spindle(bore=3.5))  # an edit in place, with spline faces
    assert calls == [True, False]


def test_a_timeout_also_ends_grandchildren_and_does_not_hang(tmp_path):
    """The worker's boolean child holds its output pipes: killing only the worker hung for an hour."""
    import subprocess
    import sys
    import time

    from theseus.isolate import run_with_timeout

    marker = tmp_path / "survived.txt"
    grandchild = f"import time; time.sleep(8); open({str(marker)!r}, 'w').write('x')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
    started = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_timeout([sys.executable, "-c", parent], 2.0)
    assert time.time() - started < 7
    time.sleep(9)
    assert not marker.exists()


def test_run_with_timeout_passes_results_through():
    import sys

    from theseus.isolate import run_with_timeout

    done = run_with_timeout([sys.executable, "-c", "import sys; print('out'); sys.exit(3)"], 30)
    assert done.returncode == 3 and b"out" in done.stdout


def test_first_look_is_cheap_and_names_nothing():
    """It only has to say whether, and roughly how much, the versions differ."""
    a = Model("a", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 20).Shape())
    b = Model("b", "brep", "brep", shape=BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(20, 20, 20).Shape(), BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(10, 10, -1), gp_Dir(0, 0, 1)), 3, 30).Shape()).Shape())
    first = compare_breps(a, b, coarse=True)
    assert not first.identical
    assert not any("hole" in f.what for f in first.findings)
    assert any("through hole" in f.what for f in compare_breps(a, b).findings)


def tetrahedron_faces():
    corners = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)]
    triangulation = Poly_Triangulation(4, 4, False)
    for i, p in enumerate(corners, 1):
        triangulation.SetNode(i, gp_Pnt(*p))
    for i, (a, b, c) in enumerate(((1, 3, 2), (1, 2, 4), (1, 4, 3), (2, 3, 4)), 1):
        triangulation.SetTriangle(i, Poly_Triangle(a, b, c))
    face = TopoDS_Face()
    BRep_Builder().MakeFace(face, triangulation)
    return compound(face)


def test_triangles_with_no_surfaces_are_recognised():
    vertices, faces = triangles_only(tetrahedron_faces())
    assert len(vertices) == 4 and len(faces) == 4


def test_exact_geometry_is_not_mistaken_for_triangles():
    assert triangles_only(BRepPrimAPI_MakeBox(10, 10, 10).Shape()) is None


def test_a_boolean_the_kernel_cannot_do_falls_back_to_a_deviation_map(monkeypatch):
    """A kernel crash in the child process reaches us as this exception: the picture must survive."""
    def crashed(*_):
        raise RuntimeError("the geometry kernel could not cut these shapes")

    monkeypatch.setattr(visual, "_brep_cuts_isolated", crashed)
    old = Model("a", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 20).Shape())
    new = Model("b", "brep", "brep", shape=BRepPrimAPI_MakeBox(20, 20, 30).Shape())
    picture = visual.diff_visual(old, new, compare_breps(old, new).to_dict())
    assert picture.mode == "deviation"
    assert any("could not cut" in note for note in picture.notes)


def test_booleans_really_run_in_their_child_process():
    a, b = BRepPrimAPI_MakeBox(20, 20, 20).Shape(), BRepPrimAPI_MakeBox(20, 20, 30).Shape()
    a_minus_b, b_minus_a = visual._brep_cuts_isolated(a, b, 1e-6)
    assert compute_metrics(b_minus_a)["volume_mm3"] == pytest.approx(4000, rel=1e-6)
    assert not compute_metrics(a_minus_b)["volume_mm3"]  # nothing left of the smaller box


def test_markdown_command_draws_the_lined_up_version_and_a_short_title(tmp_path, monkeypatch):
    """`theseus OLD NEW --images` once drew the old file where it sat, under the full temp path."""
    import theseus.images

    seen = {}

    def fake(old, new, precise, out_dir, title=""):
        seen.update(old=old, new=new, title=title)
        return {}

    monkeypatch.setattr(theseus.images, "render_file_images", fake)
    notch = BRepPrimAPI_MakeBox(gp_Pnt(-1, -1, 15), gp_Pnt(10, 10, 21)).Shape()
    part = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(100, 60, 20).Shape(), notch).Shape()
    def holed(r):
        return BRepAlgoAPI_Cut(part, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(30, 30, -1), gp_Dir(0, 0, 1)), r, 30).Shape()).Shape()

    a, b = step(holed(4), tmp_path / "a.step"), step(moved(holed(5), (7, 0, 0)), tmp_path / "b.step")
    assert cli_main([a, b, "--out", str(tmp_path / "r.md"), "--images", str(tmp_path / "img")]) == 0
    assert bounding_box(seen["old"].shape)[0] == pytest.approx(7, abs=1e-3)
    assert seen["title"] == "b.step"
