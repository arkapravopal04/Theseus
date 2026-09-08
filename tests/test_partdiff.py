import os

import pytest
import trimesh

from partdiff.compare import build_report, diff_one_file
from partdiff.export import export_mesh
from partdiff.load import LoadError, load_shape
from partdiff.mesh import extract_mesh
from partdiff.metrics import compute_metrics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_cube_volume_is_exact():
    shape = load_shape(fixture("cube20.step"))
    m = compute_metrics(shape)
    assert m["volume_mm3"] == pytest.approx(8000, abs=1e-3)
    assert m["solid_count"] == 1
    assert m["com"] == pytest.approx((10, 10, 10), abs=1e-3)


def test_assembly_has_two_solids():
    shape = load_shape(fixture("assembly_two_boxes.step"))
    m = compute_metrics(shape)
    assert m["solid_count"] == 2
    assert m["volume_mm3"] == pytest.approx(2000, abs=1e-3)


def test_bracket_volume_less_than_solid_box():
    shape = load_shape(fixture("bracket_fillets.step"))
    m = compute_metrics(shape)
    assert 0 < m["volume_mm3"] < 40 * 20 * 10


def test_load_missing_file_raises():
    with pytest.raises(LoadError):
        load_shape(fixture("does_not_exist.step"))


def test_load_unsupported_extension_raises():
    with pytest.raises(LoadError):
        load_shape(fixture("cube20.notacad"))


def test_brep_cube_volume_is_exact():
    shape = load_shape(fixture("cube20.brep"))
    m = compute_metrics(shape)
    assert m["volume_mm3"] == pytest.approx(8000, abs=1e-3)
    assert m["solid_count"] == 1
    assert m["com"] == pytest.approx((10, 10, 10), abs=1e-3)


def test_stl_cube_volume_is_approximately_correct():
    """STL is a tessellated mesh, not exact BRep geometry, so the sewn-solid
    volume only agrees with the STEP file to tessellation faceting error."""
    shape = load_shape(fixture("cube20.stl"))
    m = compute_metrics(shape)
    assert m["volume_mm3"] == pytest.approx(8000, rel=0.01)
    assert m["solid_count"] == 1
    assert m["com"] == pytest.approx((10, 10, 10), abs=0.5)


def test_stl_assembly_has_two_solids():
    shape = load_shape(fixture("assembly_two_boxes.stl"))
    m = compute_metrics(shape)
    assert m["solid_count"] == 2
    assert m["volume_mm3"] == pytest.approx(2000, rel=0.01)


def test_diff_step_old_to_stl_new():
    """Cross-format diff: an old STEP version compared to a new STL version of
    the same part should read as unchanged, since both resolve to the same
    volume/bbox/COM metrics regardless of source format."""
    old = compute_metrics(load_shape(fixture("cube20.step")))
    new = compute_metrics(load_shape(fixture("cube20.stl")))
    section = diff_one_file("part.stl", old, new)
    assert "no significant change" in section


def test_diff_new_file():
    m = compute_metrics(load_shape(fixture("cube20.step")))
    section = diff_one_file("part.step", None, m)
    assert "new file" in section
    assert "8000.000" in section


def test_diff_deleted_file():
    m = compute_metrics(load_shape(fixture("cube20.step")))
    section = diff_one_file("part.step", m, None)
    assert "deleted" in section


def test_diff_no_change():
    m = compute_metrics(load_shape(fixture("cube20.step")))
    section = diff_one_file("part.step", m, m)
    assert "no significant change" in section


def test_diff_changed_volume():
    old = compute_metrics(load_shape(fixture("cube20.step")))
    new = compute_metrics(load_shape(fixture("bracket_fillets.step")))
    section = diff_one_file("part.step", old, new)
    assert "Volume" in section


def test_build_report_empty():
    report = build_report({})
    assert "No CAD file changes" in report


@pytest.mark.parametrize(
    "fixture_name",
    ["cube20.step", "bracket_fillets.step", "assembly_two_boxes.step"],
)
def test_mesh_volume_agrees_with_exact_brep_volume(fixture_name):
    """Independent cross-check: a tessellated mesh's own volume (computed via the
    divergence theorem over its triangles) should match the exact analytic BRep
    volume to within tessellation faceting error. A much larger gap means the mesh
    winding/normals are wrong (this caught a real reversed-face bug during dev)."""
    shape = load_shape(fixture(fixture_name))
    exact_volume = compute_metrics(shape)["volume_mm3"]
    vertices, faces = extract_mesh(shape, deflection=0.5)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    assert mesh.volume == pytest.approx(exact_volume, rel=0.01)


def test_export_glb_roundtrip(tmp_path):
    shape = load_shape(fixture("cube20.step"))
    out = str(tmp_path / "cube.glb")
    export_mesh(shape, out)
    assert os.path.getsize(out) > 0
    loaded = trimesh.load(out)
    assert loaded.bounds is not None


def test_export_glb_includes_normals(tmp_path):
    """A .glb with no NORMAL accessor renders solid black under any lit material in
    downstream viewers (GitHub's built-in viewer, model-viewer, three.js) — this
    caught a real bug during dev where the exported mesh had no normal data at all."""
    shape = load_shape(fixture("bracket_fillets.step"))
    out = str(tmp_path / "bracket.glb")
    export_mesh(shape, out)
    loaded = trimesh.load(out)
    geom = next(iter(loaded.geometry.values())) if hasattr(loaded, "geometry") else loaded
    assert geom.vertex_normals is not None
    assert len(geom.vertex_normals) == len(geom.vertices)
    norms = geom.vertex_normals
    lengths = (norms**2).sum(axis=1) ** 0.5
    assert lengths.min() > 0.99 and lengths.max() < 1.01


def test_build_report_with_changes():
    old = compute_metrics(load_shape(fixture("cube20.step")))
    new = compute_metrics(load_shape(fixture("bracket_fillets.step")))
    report = build_report({"part.step": (old, new)})
    assert "partdiff report" in report
    assert "part.step" in report
