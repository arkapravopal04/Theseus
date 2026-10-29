"""Difference images: what they show (booleans vs deviation maps) and that it lands in the right place."""

import math

import matplotlib.image as mpimg
import numpy as np
import pytest
import trimesh
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from theseus.analyze import analyze_models
from theseus.diff.visual import ADDED, REMOVED, diff_visual, union_bounds
from theseus.load import load_model
from theseus.render import Camera, render_scene, reserved_right


def block(length=20.0, hole_x=10.0, hole_r=4.0, width=20.0):
    box = BRepPrimAPI_MakeBox(length, width, 10).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(hole_x, width / 2, -1), gp_Dir(0, 0, 1)), hole_r, 12).Shape()
    return BRepAlgoAPI_Cut(box, hole).Shape()


def step(shape, path) -> str:
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    w.Write(str(path))
    return str(path)


def visual_for(old_path, new_path):
    old, new = load_model(str(old_path)), load_model(str(new_path))
    precise = analyze_models(old, new)["precise"]
    return old, new, precise, diff_visual(old, new, precise)


def test_boolean_volumes_match_geometry(tmp_path):
    """Enlarging a Ø8 hole to Ø10 through a 10 mm block removes π(5² − 4²)·10 mm³."""
    _, _, _, v = visual_for(step(block(), tmp_path / "a.step"), step(block(hole_r=5), tmp_path / "b.step"))
    assert v.mode == "boolean"
    assert v.removed_mm3 == pytest.approx(math.pi * (25 - 16) * 10, rel=1e-6)
    assert v.added_mm3 == pytest.approx(0, abs=1e-6)


def test_boolean_volumes_balance_the_volume_change(tmp_path):
    fixtures = "tests/fixtures"
    old, new, _, v = visual_for(f"{fixtures}/cube20.step", f"{fixtures}/bracket_fillets.step")
    assert v.mode == "boolean"
    from theseus.metrics import volume

    assert v.added_mm3 - v.removed_mm3 == pytest.approx(volume(new.shape) - volume(old.shape), rel=1e-6)
    assert [label.split(" ")[0] for _, label in v.legend] == ["added", "removed"]


def test_mesh_booleans(tmp_path):
    a = trimesh.creation.box((20, 20, 20))
    b = trimesh.creation.box((25, 20, 20))
    b.apply_translation((2.5, 0, 0))
    a.export(tmp_path / "a.stl")
    b.export(tmp_path / "b.stl")
    _, _, _, v = visual_for(tmp_path / "a.stl", tmp_path / "b.stl")
    assert v.mode == "boolean"
    assert v.added_mm3 == pytest.approx(5 * 20 * 20, rel=1e-4)


def test_micro_change_uses_a_deviation_map_with_local_colour(tmp_path):
    """A 1 µm face offset is invisible as a boolean sliver; the map must show it, and only it."""
    old, new, _, v = visual_for(step(block(length=200, hole_x=50), tmp_path / "a.step"),
                                step(block(length=200.001, hole_x=50), tmp_path / "b.step"))
    assert v.mode == "deviation"
    layer = v.layers[0]
    assert layer.scalars.max() == pytest.approx(0.001, rel=0.01)
    coloured = layer.vertices[np.abs(layer.scalars) > 0]
    assert len(coloured) and np.all(coloured[:, 0] > 199.99)  # only the moved end face
    assert v.detail is not None and v.detail[0][0] == pytest.approx(200, abs=1)


def test_identical_files_show_the_part_plainly(tmp_path):
    a = step(block(), tmp_path / "a.step")
    _, _, _, v = visual_for(a, step(block(), tmp_path / "b.step"))
    assert v.mode == "identical" and len(v.layers) == 1


def _pixels_near(png, xy, radius=25):
    img = mpimg.imread(png)[..., :3]
    x, y = (int(round(c)) for c in xy)
    patch = img[max(0, y - radius): y + radius, max(0, x - radius): x + radius].reshape(-1, 3)
    return patch, img.reshape(-1, 3)


def _is(colour, pixels, tol=0.12):
    return (np.abs(pixels - np.array(colour)) < tol).all(axis=1)


def test_difference_image_puts_colour_where_the_change_is(tmp_path):
    """Removed material (red) appears around the enlarged hole and nowhere far from it."""
    old, new, precise, v = visual_for(step(block(length=60, hole_x=20), tmp_path / "a.step"),
                                      step(block(length=60, hole_x=20, hole_r=6), tmp_path / "b.step"))
    lo, hi = union_bounds(old, new)
    camera = Camera.fit(lo, hi, reserve_right=reserved_right(v.scale_bar is not None, v.detail is not None))
    out = tmp_path / "diff.png"
    render_scene(v.layers, str(out), camera, legend=v.legend, scale_bar=v.scale_bar, detail=v.detail)
    hole_top = camera.project((20, 10, 10))[0]
    near, everything = _pixels_near(out, hole_top, radius=40)
    reds_near = _is(REMOVED, near, tol=0.25).sum()
    assert reds_near > 50
    far, _ = _pixels_near(out, camera.project((55, 2, 10))[0], radius=15)
    assert not _is(REMOVED, far, tol=0.25).any() and not _is(ADDED, far, tol=0.25).any()
