"""The command-line entry point, the PNG preview, and the .glb export."""

import os

import matplotlib.image as mpimg
import numpy as np
import pytest
import trimesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from theseus.cli import EXIT_ERROR, EXIT_OK, main
from theseus.export import export_mesh
from theseus.load import load_model
from theseus.render import render_preview

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_single_file_report(tmp_path):
    out = tmp_path / "report.md"
    assert main([fixture("cube20.step"), "--out", str(out)]) == EXIT_OK
    report = out.read_text(encoding="utf-8")
    assert "(new file)" in report and "8000.000" in report


def test_two_file_report(tmp_path):
    out = tmp_path / "report.md"
    assert main([fixture("cube20.step"), fixture("bracket_fillets.step"), "--out", str(out)]) == EXIT_OK
    assert "(changed)" in out.read_text(encoding="utf-8")


def test_missing_file_exits_with_error(tmp_path, capsys):
    assert main([fixture("does_not_exist.step"), "--out", str(tmp_path / "r.md")]) == EXIT_ERROR
    assert "no such file" in capsys.readouterr().err


def test_unwritable_output_exits_with_error(tmp_path, capsys):
    out = tmp_path / "no" / "such" / "dir" / "r.md"
    assert main([fixture("cube20.step"), "--out", str(out)]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_units_flag(tmp_path):
    out = tmp_path / "r.md"
    assert main([fixture("cube20.stl"), "--units", "in", "--out", str(out)]) == EXIT_OK
    assert "(508.0, 508.0, 508.0)" in out.read_text(encoding="utf-8")


def test_png_and_glb_outputs(tmp_path):
    png, glb = tmp_path / "p.png", tmp_path / "p.glb"
    args = [fixture("cube20.stl"), "--out", str(tmp_path / "r.md"), "--png", str(png), "--glb", str(glb)]
    assert main(args) == EXIT_OK
    assert png.stat().st_size > 0 and glb.stat().st_size > 0


def _silhouette_aspect(png_path) -> float:
    img = mpimg.imread(png_path)[..., :3]
    ys, xs = np.nonzero((img < 0.9).any(axis=2))
    return (np.ptp(xs) + 1) / (np.ptp(ys) + 1)


def test_preview_keeps_proportions(tmp_path):
    """Regression: render.py called ndarray.ptp(), which NumPy 2 removed, inside a blanket
    `except AttributeError` — so the equal-aspect setting silently never ran and every part
    was drawn squashed into a cube. A 100x10x10 bar drawn as a cube has a silhouette of ~1.1:1;
    drawn correctly it's ~2.7:1 from the default viewpoint."""
    out = tmp_path / "bar.png"
    render_preview(BRepPrimAPI_MakeBox(100, 10, 10).Shape(), str(out))
    assert _silhouette_aspect(out) > 2


def test_preview_of_a_mesh(tmp_path):
    out = tmp_path / "mesh.png"
    render_preview(load_model(fixture("assembly_two_boxes.stl")), str(out))
    assert out.stat().st_size > 0


def test_glb_export_is_in_metres(tmp_path):
    """glTF is defined in metres; writing millimetre numbers makes a 20mm part 20m long in
    any viewer that measures."""
    out = tmp_path / "cube.glb"
    export_mesh(load_model(fixture("cube20.step")), str(out))
    extents = trimesh.load(out, force="mesh").extents
    assert extents == pytest.approx((0.02, 0.02, 0.02), rel=1e-6)
