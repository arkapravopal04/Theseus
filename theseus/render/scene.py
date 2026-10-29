"""What gets drawn: coloured triangle layers with feature edges, seen through one camera.

The camera is orthographic (the CAD convention: parallel lines stay parallel, sizes compare
across the image) and is computed here rather than by a backend, so every image of a file —
before, after, difference — uses exactly the same view, and markers and labels can be placed
on top with the same projection whichever backend drew the pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh

from theseus.model import Model

GREY = (0.78, 0.80, 0.84)
EDGE_COLOUR = (0.12, 0.13, 0.16)
_FEATURE_BEND = math.radians(30)  # mesh edges sharper than this are drawn as model edges

# Direction from the part toward the viewer, and which way is up on screen (Z-up, like most CAD).
VIEWS = {
    "iso": ((1.0, -1.0, 0.8), (0.0, 0.0, 1.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}


@dataclass
class Layer:
    vertices: np.ndarray  # (N, 3)
    faces: np.ndarray  # (M, 3) indices into vertices
    colour: tuple[float, float, float] = GREY
    opacity: float = 1.0
    edges: np.ndarray | None = None  # (K, 2, 3) line segments drawn over the surface
    scalars: np.ndarray | None = None  # per-vertex values; when set, coloured by `colormap`
    colormap: object = None  # a matplotlib Colormap
    value_range: tuple[float, float] = (0.0, 1.0)


@dataclass
class Marker:
    point: tuple[float, float, float]
    label: str


@dataclass
class ScaleBar:
    colormap: object
    value_range: tuple[float, float]
    title: str
    tick_format: object = None  # callable(value) -> str


@dataclass
class Camera:
    center: np.ndarray
    direction: np.ndarray  # unit vector from the centre toward the viewer
    up: np.ndarray  # unit, perpendicular to direction
    half_height: float  # world units from the image centre to its top edge
    size: tuple[int, int] = (800, 600)  # pixels (width, height)
    _right: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._right = np.cross(self.up, self.direction)

    @classmethod
    def fit(cls, lo, hi, view: str = "iso", size: tuple[int, int] = (800, 600), margin: float = 1.08,
            reserve_right: float = 0.0) -> Camera:
        """A camera that frames the box lo..hi from the named view, leaving the rightmost
        `reserve_right` fraction of the image free (for a scale bar)."""
        direction, up = (np.array(v, dtype=float) for v in VIEWS[view])
        direction /= np.linalg.norm(direction)
        up = up - np.dot(up, direction) * direction
        up /= np.linalg.norm(up)
        lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
        center = (lo + hi) / 2
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        right = np.cross(up, direction)
        rel = corners - center
        half_w = np.abs(rel @ right).max()
        half_h = np.abs(rel @ up).max()
        aspect = size[0] * (1 - reserve_right) / size[1]
        half_height = max(half_h, half_w / aspect, 1e-9) * margin
        # Slide the view so the part sits centred in the unreserved part of the image.
        shift = reserve_right * size[0] / 2 * (2 * half_height / size[1])
        return cls(center + right * shift, direction, up, half_height, size)

    @property
    def right(self) -> np.ndarray:
        return self._right

    @property
    def pixels_per_unit(self) -> float:
        return self.size[1] / (2 * self.half_height)

    def project(self, points) -> np.ndarray:
        """World points -> (x, y) pixels, origin top-left, y down."""
        rel = np.atleast_2d(np.asarray(points, dtype=float)) - self.center
        s = self.pixels_per_unit
        x = self.size[0] / 2 + (rel @ self.right) * s
        y = self.size[1] / 2 - (rel @ self.up) * s
        return np.column_stack([x, y])

    def depth(self, points) -> np.ndarray:
        """Distance toward the viewer: bigger is nearer."""
        return (np.atleast_2d(np.asarray(points, dtype=float)) - self.center) @ self.direction


def mesh_feature_edges(mesh: trimesh.Trimesh) -> np.ndarray:
    """Segments a CAD viewer would draw for a mesh: open boundaries and sharp bends."""
    if not len(mesh.faces):
        return np.zeros((0, 2, 3))
    sharp = mesh.face_adjacency_edges[mesh.face_adjacency_angles > _FEATURE_BEND]
    boundary = mesh.edges_sorted[trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)]
    edges = np.vstack([sharp.reshape(-1, 2), boundary.reshape(-1, 2)])
    return mesh.vertices[edges]


def brep_edges(shape, deflection: float) -> np.ndarray:
    """The model's own edges (not triangle edges), sampled finely enough to look smooth.
    Seam edges — where a cylinder's surface wraps onto itself — aren't real edges and are skipped."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection
    from OCP.OCP.collections import IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeFaces
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    edge_faces = EdgeFaces()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    segments = []
    for i in range(1, edge_faces.Extent() + 1):
        edge = TopoDS.Edge(edge_faces.FindKey(i))
        if BRep_Tool.Degenerated_s(edge):
            continue
        faces = list(edge_faces.FindFromIndex(i))
        if faces and all(BRep_Tool.IsClosed_s(edge, TopoDS.Face(f)) for f in faces):
            continue  # seam
        curve = BRepAdaptor_Curve(edge)
        sampler = GCPnts_TangentialDeflection(curve, 0.15, deflection)
        pts = np.array([[p.X(), p.Y(), p.Z()] for p in (sampler.Value(j) for j in range(1, sampler.NbPoints() + 1))])
        if len(pts) >= 2:
            segments.append(np.stack([pts[:-1], pts[1:]], axis=1))
    return np.concatenate(segments) if segments else np.zeros((0, 2, 3))


def model_bounds(model: Model) -> tuple[np.ndarray, np.ndarray]:
    if model.kind == "mesh":
        return model.mesh.bounds[0], model.mesh.bounds[1]
    from theseus.metrics import bounding_box

    b = bounding_box(model.shape)
    return np.array(b[:3]), np.array(b[3:])


def default_deflection(lo, hi) -> float:
    """Tessellation fine enough to look smooth at report image sizes."""
    return max(float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) * 1e-3, 1e-4)


def model_layer(model: Model, deflection: float | None = None, colour=GREY, opacity: float = 1.0) -> Layer:
    """A model as one layer: its triangles plus its feature edges."""
    from theseus.mesh import extract_mesh

    if deflection is None:
        deflection = default_deflection(*model_bounds(model))
    vertices, faces = extract_mesh(model, deflection)
    if model.kind == "mesh":
        edges = mesh_feature_edges(model.mesh)
    else:
        edges = brep_edges(model.shape, deflection)
    return Layer(vertices, faces, colour, opacity, edges)
