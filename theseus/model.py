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
    warnings: list[str] = field(default_factory=list)
    # Meshes only: connected components, each closed body already oriented outward.
    bodies: list[trimesh.Trimesh] = field(default_factory=list)
