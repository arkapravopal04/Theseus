"""Generate small, deterministic CAD fixtures for testing — no external downloads.

Run from repo root:  python tests/fixtures/generate_fixtures.py

Produces:
  cube20.step             - a 20x20x20mm box, volume = 8000 mm^3 exactly (hand-checkable)
  bracket_fillets.step    - a box with a cylindrical hole and edge fillets (curved geometry)
  assembly_two_boxes.step - two separate boxes in one file (multi-solid / "assembly")
  cube20.brep             - same box as cube20.step, in OpenCascade's native format
  cube20.stl              - same box as cube20.step, as a triangle mesh
  assembly_two_boxes.stl  - same two-box assembly, as a triangle mesh (multi-body STL)
"""

from __future__ import annotations

import os

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.BRep import BRep_Builder

FIXTURES_DIR = os.path.dirname(__file__)


def write_step(shape, filename: str) -> None:
    path = os.path.join(FIXTURES_DIR, filename)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    status = writer.Write(path)
    print(f"wrote {path} (status={status})")


def write_brep(shape, filename: str) -> None:
    path = os.path.join(FIXTURES_DIR, filename)
    BRepTools.Write_s(shape, path)
    print(f"wrote {path}")


def write_stl(shape, filename: str) -> None:
    path = os.path.join(FIXTURES_DIR, filename)
    BRepMesh_IncrementalMesh(shape, 0.1)
    writer = StlAPI_Writer()
    writer.Write(shape, path)
    print(f"wrote {path}")


def make_cube20():
    return BRepPrimAPI_MakeBox(20, 20, 20).Shape()


def make_bracket_fillets():
    box = BRepPrimAPI_MakeBox(40, 20, 10).Shape()
    cyl_axis = gp_Ax2(gp_Pnt(20, 10, -1), gp_Dir(0, 0, 1))
    cyl = BRepPrimAPI_MakeCylinder(cyl_axis, 4, 12).Shape()
    drilled = BRepAlgoAPI_Cut(box, cyl).Shape()

    fillet = BRepFilletAPI_MakeFillet(drilled)
    exp = TopExp_Explorer(drilled, TopAbs_EDGE)
    while exp.More():
        fillet.Add(1.5, TopoDS.Edge(exp.Current()))
        exp.Next()
    return fillet.Shape()


def make_assembly_two_boxes():
    box1 = BRepPrimAPI_MakeBox(10, 10, 10).Shape()

    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(30, 0, 0))
    box2 = BRepBuilderAPI_Transform(BRepPrimAPI_MakeBox(10, 10, 10).Shape(), trsf, True).Shape()

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, box1)
    builder.Add(compound, box2)
    return compound


if __name__ == "__main__":
    write_step(make_cube20(), "cube20.step")
    write_step(make_bracket_fillets(), "bracket_fillets.step")
    write_step(make_assembly_two_boxes(), "assembly_two_boxes.step")
    write_brep(make_cube20(), "cube20.brep")
    write_stl(make_cube20(), "cube20.stl")
    write_stl(make_assembly_two_boxes(), "assembly_two_boxes.stl")
