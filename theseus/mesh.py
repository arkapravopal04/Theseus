"""Shared triangulation: turn an OCP shape into a plain (vertices, faces) mesh.

Used by both render.py (matplotlib PNG) and export.py (glTF/STL for real 3D viewers).
"""

from __future__ import annotations

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape


def extract_mesh(shape: TopoDS_Shape, deflection: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Returns (vertices, faces): vertices is (N,3) float, faces is (M,3) int indices into it.

    No vertex deduplication across triangles — fine for the small parts this tool targets,
    and much simpler than welding shared edges between OCCT's per-face triangulations.
    """
    BRepMesh_IncrementalMesh(shape, deflection)

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            reversed_face = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                n1, n2, n3 = tri.Triangle(i).Get()
                if reversed_face:
                    # OCCT stores triangulation node order in the face's natural (FORWARD)
                    # sense regardless of topological orientation — a REVERSED face needs
                    # its winding flipped or its outward normal points the wrong way, which
                    # breaks anything relying on consistent winding (mesh volume, backface
                    # culling in some viewers).
                    n1, n2, n3 = n1, n3, n2
                base = len(vertices)
                for idx in (n1, n2, n3):
                    p = tri.Node(idx).Transformed(trsf)
                    vertices.append((p.X(), p.Y(), p.Z()))
                faces.append((base, base + 1, base + 2))
        exp.Next()

    if not vertices:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)

    return np.array(vertices), np.array(faces, dtype=int)
