"""Exact B-rep readers: STEP, IGES, and OpenCascade's native BREP, via OCP."""

from __future__ import annotations

import numpy as np
import trimesh
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Trsf
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader
from OCP.OCP.collections import Sequence_TCollection_AsciiString
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

from theseus.model import LoadError, Model

_MM_SPELLINGS = {"mm", "millimetre", "millimeter", "millimetres", "millimeters"}
# Checked in order: "millimetre" contains "metre".
_UNIT_NAME_MM = (("inch", 25.4), ("foot", 304.8), ("feet", 304.8), ("milli", 1.0), ("micro", 1e-3),
                 ("centi", 10.0), ("kilo", 1e6), ("metre", 1000.0), ("meter", 1000.0), ("mm", 1.0))


def _mm_per_unit(file_unit: str | None) -> float:
    name = (file_unit or "").lower()
    return next((mm for key, mm in _UNIT_NAME_MM if key in name), 1.0)


def _units_note(file_unit: str | None) -> str:
    """STEP and IGES carry their own length unit, and OCCT's readers convert everything to
    millimetres on import — so the numbers are always mm, but an inch part should say so."""
    if not file_unit:
        return "converted to mm by the reader"
    if file_unit.strip().lower() in _MM_SPELLINGS:
        return "file units: mm"
    return f"file units: {file_unit.lower()}, converted to mm"


def _transfer(reader, path: str) -> TopoDS_Shape:
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise LoadError(f"failed to parse {path} (reader status {getattr(status, 'name', status)})")
    reader.TransferRoots()
    return reader.OneShape()


def triangles_only(shape: TopoDS_Shape) -> tuple[np.ndarray, np.ndarray] | None:
    """(vertices, faces) if the shape's faces are all triangles with no exact surface behind them
    (AP242 tessellated geometry), else None. Such a "B-rep" has nothing for exact maths to work on."""
    vertices, faces = [], []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if BRep_Tool.Surface_s(face) is not None or tri is None:
            return None
        trsf = loc.Transformation()
        base = len(vertices)
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            vertices.append((p.X(), p.Y(), p.Z()))
        for i in range(1, tri.NbTriangles() + 1):
            n1, n2, n3 = tri.Triangle(i).Get()
            if face.Orientation() == TopAbs_REVERSED:
                n2, n3 = n3, n2
            faces.append((base + n1 - 1, base + n2 - 1, base + n3 - 1))
    return (np.array(vertices), np.array(faces, dtype=int)) if faces else None


def load_step(path: str, fmt: str, assume_units: str | None) -> Model:
    reader = STEPControl_Reader()
    shape = _transfer(reader, path)
    lengths = Sequence_TCollection_AsciiString()
    reader.FileUnits(lengths, Sequence_TCollection_AsciiString(), Sequence_TCollection_AsciiString())
    file_unit = next((s.ToCString() for s in lengths), None)
    triangles = triangles_only(shape)
    if triangles is not None:
        from theseus.formats.mesh import _orient_bodies

        mesh, bodies, _ = _orient_bodies(trimesh.Trimesh(*triangles))
        model = Model(path, fmt, "mesh", mesh=mesh, units_note=_units_note(file_unit), bodies=bodies,
                      mm_per_file_unit=_mm_per_unit(file_unit))
        model.warnings.append("this STEP file holds triangles only (tessellated), so it's compared as a mesh")
        return model
    return Model(path, fmt, "brep", shape=shape, units_note=_units_note(file_unit),
                 mm_per_file_unit=_mm_per_unit(file_unit))


def load_iges(path: str, fmt: str, assume_units: str | None) -> Model:
    reader = IGESControl_Reader()
    shape = _transfer(reader, path)
    try:
        file_unit = reader.IGESModel().GlobalSection().UnitName().ToCString()
    except Exception:  # noqa: BLE001 — a missing/odd global section only costs the note
        file_unit = None
    return Model(path, fmt, "brep", shape=shape, units_note=_units_note(file_unit),
                 mm_per_file_unit=_mm_per_unit(file_unit))


def scale_shape(shape: TopoDS_Shape, factor: float) -> TopoDS_Shape:
    trsf = gp_Trsf()
    trsf.SetScale(gp_Pnt(0, 0, 0), factor)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def load_brep(path: str, fmt: str, assume_units: str | None) -> Model:
    from theseus.formats.units import to_mm_factor

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, path, BRep_Builder()):
        raise LoadError(f"failed to parse {path}")
    # .brep has no unit field at all — the numbers are whatever the writer used.
    units = assume_units or "mm"
    if units != "mm" and not shape.IsNull():
        shape = scale_shape(shape, to_mm_factor(units))
    note = "unitless format, assumed mm" if assume_units is None else f"unitless format, declared {units}"
    return Model(path, fmt, "brep", shape=shape, units_note=note)
