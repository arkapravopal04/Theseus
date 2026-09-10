"""Export a shape to a real 3D file format (.glb, .stl, .obj, ...) for viewing.

Unlike render.py's flat PNG, a .glb committed to a GitHub repo gets GitHub's
built-in interactive 3D viewer (rotate/zoom/pan) for free when you click the
file — no custom viewer, and no GPU needed on the machine that generates it,
since the rendering happens client-side in whoever's browser opens it.
"""

from __future__ import annotations

import numpy as np
import trimesh
from trimesh.visual.material import PBRMaterial

from theseus.mesh import extract_mesh
from OCP.TopoDS import TopoDS_Shape


def _flat_vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """One normal per vertex, computed as its own triangle's flat face normal.

    extract_mesh() gives every triangle its own 3 unique vertices (no welding across
    faces), so there's nothing to angle/area-weight-average — the vertex normal for a
    flat-shaded, non-shared corner is just that triangle's face normal. Plain numpy,
    no scipy (trimesh's own vertex_normals/fix_normals need scipy for the general
    shared-vertex case, which doesn't apply here and isn't worth the dependency).
    """
    tri = vertices[faces]  # (M, 3, 3)
    face_normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(face_normals, axis=1, keepdims=True)
    degenerate = (lengths == 0).ravel()
    lengths[degenerate] = 1
    face_normals = face_normals / lengths
    # OCCT's tessellation occasionally produces zero-area sliver triangles (e.g. at
    # fillet seams); glTF requires unit-length normals, so give these an arbitrary
    # valid direction rather than an invalid zero vector — their area is ~0, so it's
    # visually inconsequential either way.
    face_normals[degenerate] = (0.0, 0.0, 1.0)

    normals = np.zeros_like(vertices)
    normals[faces[:, 0]] = face_normals
    normals[faces[:, 1]] = face_normals
    normals[faces[:, 2]] = face_normals
    return normals


def export_mesh(shape: TopoDS_Shape, out_path: str, deflection: float = 0.5) -> None:
    """Export `shape` to `out_path`. Format is inferred from the extension (.glb, .stl, .obj, ...)."""
    vertices, faces = extract_mesh(shape, deflection)
    if len(faces) == 0:
        raise ValueError("shape produced no triangulated faces to export")

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    # Formats like .glb need a NORMAL accessor for correct lighting in downstream
    # viewers (GitHub's built-in viewer, model-viewer, etc.) — without it, a lit
    # material's N.L term is zero everywhere and the model renders solid black
    # regardless of light intensity.
    mesh.vertex_normals = _flat_vertex_normals(vertices, faces)
    # Without an explicit material, glTF's spec-mandated default is fully metallic
    # (metallicFactor=1). Metallic surfaces have no diffuse term — they only show
    # environment reflections — so in any viewer that doesn't set up environment/IBL
    # lighting (GitHub's built-in viewer included), the model renders solid black or
    # invisible even though punctual lights are present. A plain, non-metallic
    # material makes it visible everywhere.
    mesh.visual = trimesh.visual.TextureVisuals(
        material=PBRMaterial(
            baseColorFactor=[200, 200, 200, 255],
            metallicFactor=0.0,
            roughnessFactor=0.6,
            doubleSided=True,
        )
    )
    mesh.export(out_path)
