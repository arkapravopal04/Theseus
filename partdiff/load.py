"""Load a STEP, IGES, BREP, or STL file into an OCP TopoDS_Shape."""

from __future__ import annotations

import os

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader
from OCP.StlAPI import StlAPI_Reader
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape

CAD_EXTENSIONS = (".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl")


class LoadError(Exception):
    pass


def _load_step_or_iges(reader, path: str) -> TopoDS_Shape:
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise LoadError(f"failed to parse {path} (reader status {status})")
    reader.TransferRoots()
    return reader.OneShape()


def _load_brep(path: str) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, path, BRep_Builder()):
        raise LoadError(f"failed to parse {path}")
    return shape


def _load_stl(path: str) -> TopoDS_Shape:
    """StlAPI_Reader gives back a bare compound of unconnected planar triangle
    faces, with no shells or solids — BRepGProp can't compute a meaningful
    volume/solid-count from that. Sew the triangles into shells and close each
    into a solid (one per connected body) so an STL-derived shape behaves
    exactly like a STEP/IGES/BREP one for every downstream metric.
    """
    raw = TopoDS_Shape()
    if not StlAPI_Reader().Read(raw, path):
        raise LoadError(f"failed to parse {path}")

    sewer = BRepBuilderAPI_Sewing(1e-6)
    sewer.Add(raw)
    sewer.Perform()
    sewn = sewer.SewedShape()

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)

    exp = TopExp_Explorer(sewn, TopAbs_SHELL)
    found_shell = False
    while exp.More():
        shell = TopoDS.Shell(exp.Current())
        maker = BRepBuilderAPI_MakeSolid(shell)
        # An open/non-manifold mesh (a surface, not a watertight body) can't be
        # closed into a solid — keep it as a shell so bounding box/rendering
        # still work, even though volume won't mean much for an open surface.
        builder.Add(compound, maker.Solid() if maker.IsDone() else shell)
        found_shell = True
        exp.Next()

    return compound if found_shell else sewn


def load_shape(path: str) -> TopoDS_Shape:
    """Read a .step/.stp/.iges/.igs/.brep/.brp/.stl file.

    Raises LoadError on failure or empty geometry.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in (".step", ".stp"):
        shape = _load_step_or_iges(STEPControl_Reader(), path)
    elif ext in (".iges", ".igs"):
        shape = _load_step_or_iges(IGESControl_Reader(), path)
    elif ext in (".brep", ".brp"):
        shape = _load_brep(path)
    elif ext == ".stl":
        shape = _load_stl(path)
    else:
        raise LoadError(f"unsupported file extension: {ext}")

    if shape.IsNull():
        # A successful read status only means the file parsed, not that it
        # produced usable geometry.
        raise LoadError(f"{path} parsed but contains no geometry (null shape)")

    return shape
