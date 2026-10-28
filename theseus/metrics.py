"""Geometric metrics for a loaded model: volume, surface area, bounding box, centre of mass,
body counts, and validity.

Note: "volume", not "mass" — we have no density, so don't call this mass anywhere.
All lengths are millimetres: every reader converts to mm on load (see Model.units_note for
whether that was a real conversion or an assumption about a unitless format).

Volume and centre of mass only mean something for closed bodies. Open geometry (a surface,
or a mesh with holes) is counted in open_bodies and excluded, and if nothing is closed both
are None rather than a meaningless number.
"""

from __future__ import annotations

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

from theseus.model import Model

METRICS_SCHEMA = 2


def _volume_props(shape: TopoDS_Shape) -> GProp_GProps:
    props = GProp_GProps()
    # OnlyClosed: an open shell's signed "volume" is an artefact of where its boundary
    # happens to sit, not material — only closed shells contribute.
    BRepGProp.VolumeProperties_s(shape, props, True)
    return props


def volume(shape: TopoDS_Shape) -> float:
    return _volume_props(shape).Mass()  # for VolumeProperties, "mass" is the volume magnitude


def surface_area(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return props.Mass()


def bounding_box(shape: TopoDS_Shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return (box.GetXMin(), box.GetYMin(), box.GetZMin(), box.GetXMax(), box.GetYMax(), box.GetZMax())


def center_of_mass(shape: TopoDS_Shape) -> tuple[float, float, float]:
    com = _volume_props(shape).CentreOfMass()
    return (com.X(), com.Y(), com.Z())


def _count(shape: TopoDS_Shape, kind, avoid=None) -> int:
    exp = TopExp_Explorer(shape, kind, avoid) if avoid is not None else TopExp_Explorer(shape, kind)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    return count


def solid_count(shape: TopoDS_Shape) -> int:
    return _count(shape, TopAbs_SOLID)


def _brep_metrics(model: Model) -> dict:
    shape = model.shape
    solids = solid_count(shape)
    # Shells that aren't part of any solid, plus loose faces that aren't in any shell:
    # surface geometry with no enclosed volume.
    open_bodies = _count(shape, TopAbs_SHELL, TopAbs_SOLID) + _count(shape, TopAbs_FACE, TopAbs_SHELL)

    props = _volume_props(shape)
    vol = props.Mass() if solids else None
    com = None
    if vol:
        c = props.CentreOfMass()
        com = (c.X(), c.Y(), c.Z())

    valid = BRepCheck_Analyzer(shape).IsValid()
    warnings = []
    if not valid:
        warnings.append("geometry fails the OpenCascade validity check (may break CAM/meshing downstream)")
    if not solids:
        warnings.append("no closed solids (surface or wireframe geometry) — volume not applicable")
    elif open_bodies:
        warnings.append(f"{open_bodies} open surface bod{'y' if open_bodies == 1 else 'ies'} excluded from volume")

    return {
        "volume_mm3": vol,
        "area_mm2": surface_area(shape),
        "bbox": bounding_box(shape),
        "com": com,
        "solid_count": solids,
        "open_bodies": open_bodies,
        "valid": valid,
        "warnings": warnings,
    }


def _mesh_metrics(model: Model) -> dict:
    bodies = model.bodies or [model.mesh]
    closed = [b for b in bodies if b.is_watertight]
    open_count = len(bodies) - len(closed)

    vol = sum(b.volume for b in closed) if closed else None
    com = None
    if vol:
        weighted = sum(b.volume * b.center_mass for b in closed)
        com = tuple(float(c) for c in weighted / vol)

    warnings = []
    if not closed:
        warnings.append("open mesh (not watertight) — volume not applicable")
    elif open_count:
        warnings.append(f"{open_count} open (non-watertight) bod{'y' if open_count == 1 else 'ies'} excluded from volume")

    return {
        "volume_mm3": float(vol) if vol is not None else None,
        "area_mm2": float(model.mesh.area),
        "bbox": tuple(float(v) for v in model.mesh.bounds.ravel()),
        "com": com,
        "solid_count": len(closed),
        "open_bodies": open_count,
        "valid": None,  # no B-rep validity notion for a triangle soup
        "warnings": warnings,
    }


def compute_metrics(model: Model | TopoDS_Shape) -> dict:
    """Metrics for a Model (or a bare TopoDS_Shape, treated as an exact B-rep)."""
    if isinstance(model, TopoDS_Shape):
        model = Model(path="", format="brep", kind="brep", shape=model)

    metrics = _brep_metrics(model) if model.kind == "brep" else _mesh_metrics(model)
    metrics["warnings"] = [*model.warnings, *metrics["warnings"]]
    return {
        "schema": METRICS_SCHEMA,
        "format": model.format,
        "kind": model.kind,
        "units": model.units,
        "units_note": model.units_note,
        **metrics,
    }
