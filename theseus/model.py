"""The format-independent in-memory representation every reader produces.

Two kinds of geometry come out of CAD files, and they need different maths downstream:
  - "brep": exact boundary representation (STEP/IGES/BREP) — an OCP TopoDS_Shape with
    analytic surfaces, so volume/area/centre of mass are exact integrals.
  - "mesh": triangles only (STL/OBJ/PLY/3MF/...) — a trimesh.Trimesh. Pushing these through
    OCP's B-rep machinery (one TopoDS_Face per triangle, then sewing) is orders of magnitude
    slower and can't tell an open surface from a closed body, so meshes stay meshes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import trimesh
    from OCP.TopoDS import TopoDS_Shape


class LoadError(Exception):
    pass


def part_of(shape: TopoDS_Shape) -> TopoDS_Shape:
    """The part itself. STEP files also carry annotation and construction geometry (sheets, wires,
    points for PMI): when there are solids, those are the part; failing that, its faces. The
    annotation stays out of pictures and booleans, which it clutters and can even crash."""
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    for kind in (TopAbs_SOLID, TopAbs_FACE):
        exp = TopExp_Explorer(shape, kind)
        if exp.More():
            compound = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(compound)
            while exp.More():
                builder.Add(compound, exp.Current())
                exp.Next()
            return compound
    return shape


def skin_of(shape: TopoDS_Shape) -> TopoDS_Shape:
    """The part's faces, without the solid they enclose: a distance to this is a distance to its
    surface, where a distance to a solid is zero for any point inside it."""
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    exp = TopExp_Explorer(part_of(shape), TopAbs_FACE)
    while exp.More():
        builder.Add(compound, exp.Current())
        exp.Next()
    return compound


@dataclass
class Model:
    path: str
    format: str  # registry name, e.g. "step", "stl", "3mf"
    kind: str  # "brep" | "mesh"
    shape: TopoDS_Shape | None = None
    mesh: trimesh.Trimesh | None = None
    # Every reader normalises geometry to millimetres; these say where that came from so the
    # report never silently presents an assumption as a fact.
    units: str = "mm"
    units_note: str = ""
    # Size of one of the file's own length units in mm (25.4 for an inch file). Lets
    # precision figures written in the file's units be converted.
    mm_per_file_unit: float = 1.0
    warnings: list[str] = field(default_factory=list)
    # Meshes only: connected components, each closed body already oriented outward.
    bodies: list[trimesh.Trimesh] = field(default_factory=list)
