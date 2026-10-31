"""What the "difference" picture shows.

Two ways to show a change, chosen per file:
  - material booleans: the new part as a translucent ghost, material that was added in green,
    material that was removed in red. Exact for B-reps (OpenCascade booleans), and for closed
    meshes via manifold3d. Best for real design changes.
  - a signed deviation map: the new surface coloured by how far it sits from the old one (green
    outward — material added, red inward — removed, grey within the detection limit), with a
    scale bar. Used for micro-changes (a 2 µm change is an invisible sliver as a boolean), for
    open meshes, and whenever a boolean fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from theseus.fmt import fmt_len
from theseus.model import Model, part_of
from theseus.render.scene import (
    GREY,
    Layer,
    ScaleBar,
    brep_edges,
    default_deflection,
    mesh_feature_edges,
    model_bounds,
    model_layer,
)

ADDED = (0.18, 0.66, 0.30)
REMOVED = (0.86, 0.22, 0.20)
UNCHANGED = (0.86, 0.87, 0.90)
DEVIATION_MAP = LinearSegmentedColormap.from_list("theseus-deviation", [REMOVED, UNCHANGED, ADDED])
GHOST_OPACITY = 0.22
MICRO_FRACTION = 0.01  # changes smaller than this share of the part's size are shown as a deviation map


DETAIL_BELOW = 0.04  # a change smaller than this share of the part gets a zoomed detail inset
BOOLEAN_TIMEOUT_S = 60  # past this, show a deviation map instead of waiting for material booleans


@dataclass
class DiffVisual:
    layers: list[Layer]
    mode: str  # "boolean" | "deviation" | "identical"
    legend: list[tuple[tuple, str]] = field(default_factory=list)
    scale_bar: ScaleBar | None = None
    added_mm3: float | None = None
    removed_mm3: float | None = None
    notes: list[str] = field(default_factory=list)
    detail: tuple[tuple[float, float, float], float] | None = None  # (point, radius) to zoom in on


def _detail(precise: dict | None, size: float) -> tuple[tuple[float, float, float], float] | None:
    """Where to zoom: the largest located finding, when the change is small next to the part."""
    if not precise:
        return None
    located = [f for f in precise["findings"] if f.get("at") and f.get("kind") != "retrim"]
    if not located or precise["max_deviation_mm"] >= DETAIL_BELOW * size:
        return None
    top = max(located, key=lambda f: f["magnitude_mm"])
    return tuple(top["at"]), max(size * 0.04, top["magnitude_mm"] * 20)


def union_bounds(*models: Model | None) -> tuple[np.ndarray, np.ndarray]:
    boxes = [model_bounds(m) for m in models if m is not None]
    return np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)


# --- booleans --------------------------------------------------------------------------


def _brep_cut(a, b, fuzzy: float):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.OCP.collections import List_TopoDS_Shape

    args, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    args.Append(a)
    tools.Append(b)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(fuzzy)
    op.SetRunParallel(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean operation failed")
    return op.Shape()


def _brep_cuts_isolated(a, b, fuzzy: float) -> tuple[object, object]:
    """(a - b, b - a), cut in a child process: a crash or hang there raises here, and costs only
    the material picture (see theseus._boolean_worker)."""
    import os
    import subprocess
    import sys
    import tempfile

    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    import theseus
    from theseus.isolate import run_with_timeout

    env = dict(os.environ)
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(theseus.__file__)))
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_parent, env.get("PYTHONPATH")) if p)
    with tempfile.TemporaryDirectory() as tmp:
        a_path, b_path, a_b, b_a = (os.path.join(tmp, f"{name}.brep") for name in ("a", "b", "a_b", "b_a"))
        BRepTools.Write_s(a, a_path)
        BRepTools.Write_s(b, b_path)
        try:
            proc = run_with_timeout([sys.executable, "-m", "theseus._boolean_worker", a_path, b_path, a_b, b_a, repr(fuzzy)],
                                    BOOLEAN_TIMEOUT_S, env)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"took over {BOOLEAN_TIMEOUT_S} s") from None
        if proc.returncode != 0:
            raise RuntimeError("the geometry kernel could not cut these shapes")
        results = []
        for path in (a_b, b_a):
            shape = TopoDS_Shape()
            if not BRepTools.Read_s(shape, path, BRep_Builder()):
                raise RuntimeError("boolean result could not be read")
            results.append(shape)
    return results[0], results[1]


def _solid_pieces(shape, min_thickness: float) -> tuple[object, float]:
    """The solids of a boolean result that are real material, not fuzzy-tolerance slivers:
    (compound of them, their total volume)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    total = 0.0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid = exp.Current()
        vol, area = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, vol, True)
        BRepGProp.SurfaceProperties_s(solid, area)
        volume = abs(vol.Mass())
        # A slab of thickness t and footprint F has volume tF and area ≈ 2F: thickness = 2V/A.
        if area.Mass() > 0 and 2 * volume / area.Mass() > min_thickness:
            builder.Add(compound, solid)
            total += volume
        exp.Next()
    return compound, total


def _brep_booleans(old: Model, new: Model, scale: float) -> tuple[list[Layer], float, float]:
    fuzzy = max(scale * 1e-6, 1e-6)
    removed_shape, added_shape = _brep_cuts_isolated(part_of(old.shape), part_of(new.shape), fuzzy)
    added, added_vol = _solid_pieces(added_shape, 4 * fuzzy)
    removed, removed_vol = _solid_pieces(removed_shape, 4 * fuzzy)
    layers = []
    for shape, colour in ((removed, REMOVED), (added, ADDED)):
        piece = Model(path="", format="brep", kind="brep", shape=shape)
        layer = model_layer(piece, default_deflection(*union_bounds(old, new)), colour=colour)
        if len(layer.faces):
            layers.append(layer)
    return layers, added_vol, removed_vol


def _mesh_booleans(old: Model, new: Model) -> tuple[list[Layer], float, float]:
    import manifold3d

    def to_manifold(mesh):
        m = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                                                tri_verts=np.asarray(mesh.faces, np.uint32)))
        if m.is_empty() and len(mesh.faces):
            raise RuntimeError("mesh is not a closed manifold")
        return m

    a, b = to_manifold(old.mesh), to_manifold(new.mesh)
    layers, volumes = [], []
    for result, colour in ((a - b, REMOVED), (b - a, ADDED)):
        out = result.to_mesh()
        volumes.append(result.volume())
        if len(out.tri_verts):
            vertices = np.asarray(out.vert_properties)[:, :3].astype(float)
            faces = np.asarray(out.tri_verts, dtype=int)
            import trimesh

            piece = trimesh.Trimesh(vertices, faces)
            layers.append(Layer(vertices, faces, colour, edges=mesh_feature_edges(piece)))
    return layers, volumes[1], volumes[0]


def _ghost(model: Model, deflection: float) -> Layer:
    return model_layer(model, deflection, colour=GREY, opacity=GHOST_OPACITY)


# --- deviation map ---------------------------------------------------------------------


def _deviation_layers(old: Model, new: Model, size: float, floor: float) -> tuple[list[Layer], ScaleBar, float]:
    from theseus.diff.surface import signed_deviation

    # Fine tessellation (vertices on the true surface) and short edges (local colours).
    deflection = max(size * 1e-4, 1e-4)
    mesh, signed, limit = signed_deviation(old, new, deflection, max_edge=size / 150)
    signed = np.where(np.abs(signed) <= np.maximum(limit, floor), 0.0, signed)
    span = max(float(np.abs(signed).max(initial=0)), floor * 3, 1e-9)
    edges = brep_edges(part_of(new.shape), deflection) if new.kind == "brep" else mesh_feature_edges(mesh)
    layer = Layer(np.asarray(mesh.vertices), np.asarray(mesh.faces), edges=edges, scalars=signed,
                  colormap=DEVIATION_MAP, value_range=(-span, span))
    bar = ScaleBar(DEVIATION_MAP, (-span, span), "deviation",
                   tick_format=lambda v: "0" if v == 0 else fmt_len(v, signed=True))
    return [layer], bar, span


# --- choosing ----------------------------------------------------------------------------


def diff_visual(old: Model, new: Model, precise: dict | None) -> DiffVisual:
    lo, hi = union_bounds(old, new)
    size = float(np.linalg.norm(hi - lo))
    deflection = default_deflection(lo, hi)
    floor = precise["floor_mm"] if precise else 0.0

    if precise and precise["identical"]:
        return DiffVisual([model_layer(new, deflection)], "identical",
                          notes=["identical within the detection limit — nothing to highlight"])

    max_dev = precise["max_deviation_mm"] if precise else None
    notes = []
    if max_dev is None or max_dev >= MICRO_FRACTION * size:
        try:
            if old.kind == new.kind == "brep":
                layers, added, removed = _brep_booleans(old, new, size)
            elif old.kind == new.kind == "mesh" and old.mesh.is_watertight and new.mesh.is_watertight:
                layers, added, removed = _mesh_booleans(old, new)
            else:
                raise RuntimeError("mixed B-rep/mesh or open mesh")
            if layers:
                return DiffVisual(
                    [_ghost(new, deflection), *layers], "boolean",
                    legend=[(ADDED, f"added ({added:.3f} mm³)"), (REMOVED, f"removed ({removed:.3f} mm³)")],
                    added_mm3=added, removed_mm3=removed,
                )
            notes.append("no material added or removed beyond the boolean tolerance — showing deviation instead")
        except Exception as e:  # noqa: BLE001 — fall back to the deviation map, never lose the image
            notes.append(f"material comparison unavailable ({e}) — showing deviation instead")

    layers, bar, span = _deviation_layers(old, new, size, floor)
    return DiffVisual(layers, "deviation", scale_bar=bar,
                      legend=[(ADDED, "outward (added)"), (REMOVED, "inward (removed)")], notes=notes,
                      detail=_detail(precise, size))
