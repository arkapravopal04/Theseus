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
from theseus.render import display, render_preview
from theseus.render.scene import Camera

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


def _projected_aspect(size) -> float:
    corners = np.array([[x, y, z] for x in (0, size[0]) for y in (0, size[1]) for z in (0, size[2])])
    p = Camera.fit((0, 0, 0), size).project(corners)
    return np.ptp(p[:, 0]) / np.ptp(p[:, 1])


@pytest.mark.parametrize("renderer", ["vtk", "mpl"])
def test_preview_keeps_proportions(tmp_path, monkeypatch, renderer):
    """Regression: render.py called ndarray.ptp(), which NumPy 2 removed, inside a blanket
    `except AttributeError` — so the equal-aspect setting silently never ran and every part
    was drawn squashed into a cube. The bar's silhouette must match what the camera predicts
    for a 100x10x10 bar, not for a cube."""
    if renderer == "vtk" and not display._vtk_works():
        pytest.skip("VTK has no usable OpenGL here (e.g. a Windows machine without a GPU driver)")
    monkeypatch.setenv("THESEUS_RENDERER", renderer)
    monkeypatch.setattr(display, "_backend", None)
    out = tmp_path / "bar.png"
    render_preview(BRepPrimAPI_MakeBox(100, 10, 10).Shape(), str(out))
    assert _silhouette_aspect(out) == pytest.approx(_projected_aspect((100, 10, 10)), rel=0.05)
    assert abs(_silhouette_aspect(out) - _projected_aspect((10, 10, 10))) > 0.5


def test_a_crashing_renderer_falls_back_instead_of_crashing(monkeypatch):
    """VTK can segfault on a display it can't use (e.g. a stale $DISPLAY from SSH); in-process that
    would kill the CLI, so the backend is checked in a throwaway process first."""
    monkeypatch.delenv("THESEUS_RENDERER", raising=False)
    monkeypatch.setattr(display, "_PROBE", "import ctypes; ctypes.string_at(0)")  # VTK crashing
    monkeypatch.setattr(display, "_backend", None)
    assert display.backend() == "mpl"


def test_a_stale_display_does_not_crash(monkeypatch):
    """With $DISPLAY pointing at nothing, VTK either fails (→ matplotlib) or falls back to headless
    EGL on its own (→ VTK, and then it really renders). Either way this process survives."""
    monkeypatch.delenv("THESEUS_RENDERER", raising=False)
    monkeypatch.setenv("DISPLAY", ":987")  # nothing is listening there
    monkeypatch.setattr(display, "_backend", None)
    chosen = display.backend()
    assert chosen in ("vtk", "mpl")
    assert (chosen == "vtk") == display._vtk_works()


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


def test_images_flag_writes_and_links_images(tmp_path):
    out = tmp_path / "report.md"
    args = [fixture("cube20.step"), fixture("bracket_fillets.step"), "--out", str(out), "--images", str(tmp_path / "img")]
    assert main(args) == EXIT_OK
    assert sorted(os.listdir(tmp_path / "img")) == ["after.png", "before.png", "diff.png", "viewer.html", "views.png"]
    report = out.read_text(encoding="utf-8")
    assert "[![Difference](img/diff.png)](img/diff.png)" in report
    assert "[standard views](img/views.png)" in report
    assert "[interactive 3D viewer](img/viewer.html)" in report
