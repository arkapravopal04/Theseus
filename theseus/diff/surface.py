"""Comparison when at least one side is a triangle mesh (STL, 3MF, ...).

Same triangulation on both sides (the common "re-exported from the same CAD, one dimension
changed" case): exact per-vertex deltas, limited only by the file's float precision.

Otherwise: the distance from each side's vertices to the other side's surface, both ways
(a symmetric, vertex-sampled Hausdorff distance). Vertices lie *on* the true surface, but the
flat triangles between them cut corners on curved geometry (chord error) — so a coarse STL of
an unchanged part differs from its STEP by up to that chord error. Each triangle's chord
error is estimated from its own shape (height × bend angle to its neighbours ÷ 8, exact for a
tessellated circle), and a distance measured *to* a triangle only counts as a difference when
it exceeds that triangle's error. Flat regions keep float-precision sensitivity; coarsely
meshed curves get the looser limit they need.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from scipy.spatial import cKDTree

from theseus.diff import Finding, PreciseDiff
from theseus.diff.brep import KERNEL_PRECISION_MM
from theseus.fmt import fmt_len, fmt_point
from theseus.mesh import tessellate
from theseus.model import Model

MAX_SAMPLES = 50_000
MAX_REGIONS = 5
_FLOAT32_REL = 2.0**-22  # two float32 ulps, relative — most mesh formats store float32
# Bends sharper than this between neighbouring triangles are taken as real edges, not a
# tessellated curve. Coarse exports of small fillets bend ~40° per triangle; CAD edges are
# usually 90°.
_SMOOTH_BEND = np.radians(60)


@dataclass
class _Side:
    """One version as triangles, plus — for an exact B-rep — which face each triangle belongs
    to, so distances to it can be measured on the true surface instead of the tessellation."""

    mesh: trimesh.Trimesh
    owners: np.ndarray | None = None
    surfaces: list | None = None  # Geom_Surface per B-rep face; None where the face is planar


def _side(model: Model, deflection: float) -> _Side:
    if model.kind == "mesh":
        return _Side(model.mesh)
    # Mesh a copy: triangulations attach to the shape, and this fine one would otherwise be
    # inherited by previews and exports of the same model (slow to draw, huge to export).
    shape = BRepBuilderAPI_Copy(model.shape, False, False).Shape()
    vertices, faces, owners, brep_faces = tessellate(shape, deflection)
    # process=True welds the per-face vertex copies; it keeps the face order, so owners still line up.
    surfaces = [None if BRepAdaptor_Surface(f, True).GetType() == GeomAbs_Plane else BRep_Tool.Surface_s(f)
                for f in brep_faces]
    return _Side(trimesh.Trimesh(vertices, faces), owners, surfaces)


def _exact_distance(points: np.ndarray, dist: np.ndarray, tri: np.ndarray, side: _Side,
                    tessellation_error: np.ndarray, floor: float) -> tuple[np.ndarray, np.ndarray]:
    """Refine tessellation distances to a B-rep by projecting onto the true surface of the face
    each point landed on. Returns (distances, per-point limits)."""
    dist = dist.copy()
    limit = np.full(len(points), floor + KERNEL_PRECISION_MM)
    for k in np.nonzero(dist > floor)[0]:
        surface = side.surfaces[side.owners[tri[k]]]
        if surface is None:
            continue  # planar faces tessellate exactly
        proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*points[k]), surface)
        exact = proj.LowerDistance() if proj.NbPoints() else None
        # The untrimmed surface extends past the face; only trust a projection that agrees with
        # the tessellation to within the tessellation's own error.
        if exact is not None and abs(exact - dist[k]) <= tessellation_error[tri[k]] + floor:
            dist[k] = exact
        else:
            limit[k] = tessellation_error[tri[k]] + floor
    return dist, limit


def _sample(points: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if len(points) <= MAX_SAMPLES:
        return points
    return points[rng.choice(len(points), MAX_SAMPLES, replace=False)]


def _regions(points: np.ndarray, dev: np.ndarray, radius: float) -> list[Finding]:
    """Group out-of-tolerance sample points into a few regions, worst first."""
    order = np.argsort(-dev)
    points, dev = points[order], dev[order]
    tree = cKDTree(points)
    claimed = np.zeros(len(points), dtype=bool)
    findings = []
    for i in range(len(points)):
        if claimed[i]:
            continue
        members = [m for m in tree.query_ball_point(points[i], radius) if not claimed[m]]
        claimed[members] = True
        findings.append(Finding(f"surfaces differ by up to {fmt_len(float(dev[i]))} near {fmt_point(points[i])}",
                                float(dev[i]), tuple(map(float, points[i])), "region"))
        if len(findings) == MAX_REGIONS:
            remaining = int((~claimed).sum())
            if remaining:
                findings.append(Finding(f"…and {remaining} more differing sample points elsewhere",
                                        float(dev[~claimed].max()), None, "region"))
            break
    return findings


def _distance(points: np.ndarray, mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """(distance to the surface, index of the nearest triangle) for each point."""
    if not len(points):
        return np.zeros(0), np.zeros(0, dtype=int)
    _, dist, tri = trimesh.proximity.closest_point(mesh, points)
    return dist, tri


def chord_errors(mesh: trimesh.Trimesh) -> np.ndarray:
    """Per-triangle estimate of how far the mesh strays from the smooth surface it approximates.

    Across an edge where neighbouring triangles bend by θ (a tessellated curve rather than a
    real sharp edge), the true surface bulges out of the triangle of height h by about h·θ/8.
    Each triangle also takes its neighbours' values: the nearest triangle to a point isn't
    always the one carrying the bulge.
    """
    err = np.zeros(len(mesh.faces))
    if not len(mesh.face_adjacency):
        return err
    f1, f2 = mesh.face_adjacency.T
    theta = mesh.face_adjacency_angles
    edges = mesh.face_adjacency_edges
    length = np.maximum(np.linalg.norm(mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], axis=1), 1e-12)
    longest = np.linalg.norm(mesh.vertices[mesh.faces] - np.roll(mesh.vertices[mesh.faces], 1, axis=1), axis=2).max(axis=1)
    # Height of each triangle across the shared edge (never more than its longest edge, which
    # tames slivers). Take the smaller of the two: a long fillet strip meeting a small corner
    # patch across its short end doesn't curve along its length.
    height = np.minimum(np.minimum(2 * mesh.area_faces[f1] / length, longest[f1]),
                        np.minimum(2 * mesh.area_faces[f2] / length, longest[f2]))
    sag = np.where(theta < _SMOOTH_BEND, height * theta / 8, 0.0)
    # Summed over a triangle's edges: on a cylinder only one edge bends, but a doubly curved
    # patch (a sphere-like fillet corner) bends across two and bulges out roughly twice as far.
    np.add.at(err, f1, sag)
    np.add.at(err, f2, sag)
    spread = err.copy()
    np.maximum.at(spread, f1, err[f2])
    np.maximum.at(spread, f2, err[f1])
    return spread * 1.5  # margin: h·θ/8 is an estimate, not a bound

def compare_surfaces(old: Model, new: Model) -> PreciseDiff:
    bounds = [m.mesh.bounds if m.kind == "mesh" else None for m in (old, new)]
    deflection = 1e-3
    if old.kind == "brep" or new.kind == "brep":
        from theseus.metrics import bounding_box

        boxes = [np.array(bounding_box(m.shape)).reshape(2, 3) if b is None else b for m, b in zip((old, new), bounds)]
        diag = max(float(np.linalg.norm(b[1] - b[0])) for b in boxes)
        deflection = max(diag * 1e-4, 1e-4)  # fine tessellation of the exact side
    old_side, new_side = _side(old, deflection), _side(new, deflection)
    a, b = old_side.mesh, new_side.mesh
    scale = max(float(np.abs(a.vertices).max(initial=0)), float(np.abs(b.vertices).max(initial=0)), 1.0)
    float_floor = max(scale * _FLOAT32_REL, 1e-9)
    radius = 0.05 * max(float(np.linalg.norm(np.ptp(m.vertices, axis=0))) for m in (a, b))

    if old.kind == new.kind == "mesh" and len(a.vertices) == len(b.vertices) and np.array_equal(a.faces, b.faces):
        dev = np.linalg.norm(b.vertices - a.vertices, axis=1)
        off = dev > float_floor
        findings = _regions(b.vertices[off], dev[off], radius) if off.any() else []
        return PreciseDiff("mesh vertices (same triangulation)", float_floor,
                           f"float precision of the mesh coordinates ({fmt_len(float_floor)})",
                           identical=not off.any(), max_deviation_mm=float(dev.max(initial=0)), findings=findings,
                           notes=[f"{int(off.sum())} of {len(dev)} vertices moved"] if off.any() else [])

    rng = np.random.default_rng(0)
    points, devs, limits = [], [], []
    for src, dst in ((new_side, old_side), (old_side, new_side)):  # new→old surface, then old→new
        pts = _sample(src.mesh.vertices, rng)
        dist, tri = _distance(pts, dst.mesh)
        # A mesh surface is only as accurate as its chord error; a B-rep is measured exactly.
        error = chord_errors(dst.mesh)
        if dst.surfaces is not None:
            dist, limit = _exact_distance(pts, dist, tri, dst, error, float_floor)
        else:
            limit = error[tri] + float_floor
        points.append(pts)
        devs.append(dist)
        limits.append(limit)
    points, devs, limits = np.vstack(points), np.concatenate(devs), np.concatenate(limits)
    off = devs > limits
    findings = _regions(points[off], devs[off], radius) if off.any() else []

    floor_min, floor_max = float(limits.min(initial=float_floor)), float(limits.max(initial=float_floor))
    if floor_max > 2 * floor_min:
        why = (f"tessellation: {fmt_len(floor_min)} on flat areas, up to {fmt_len(floor_max)} where "
               "curves are coarsely meshed")
    elif floor_max > 10 * float_floor:
        why = f"tessellation accuracy ({fmt_len(floor_max)})"
    else:
        why = f"float precision ({fmt_len(floor_max)})"
    kinds = "exact B-rep vs mesh" if "brep" in (old.kind, new.kind) else "mesh vs mesh"
    return PreciseDiff(f"surface deviation ({kinds})", floor_max, why, identical=not off.any(),
                       max_deviation_mm=float(devs.max(initial=0)), findings=findings)


MAX_COLOURED_VERTICES = 200_000
_MAX_REFINE_PASSES = 10


def _snap(vertices: np.ndarray, owners: np.ndarray, side: _Side) -> np.ndarray:
    """Project points onto the true surface of the B-rep face each came from (a midpoint of a
    chord across a curved face would otherwise read as a deviation)."""
    out = vertices.copy()
    for k, owner in enumerate(owners):
        surface = side.surfaces[owner]
        if surface is None:
            continue  # planar: the midpoint is already on the face
        proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*vertices[k]), surface)
        if proj.NbPoints():
            p = proj.NearestPoint()
            out[k] = (p.X(), p.Y(), p.Z())
    return out


def signed_deviation(old: Model, new: Model, deflection: float,
                     max_edge: float | None = None) -> tuple[trimesh.Trimesh, np.ndarray, np.ndarray]:
    """The new surface as triangles, with each vertex's signed distance from the old surface
    (positive outside it — material added; negative inside — material removed) and the
    per-vertex limit below which that distance is noise. Used to colour deviation maps.

    With max_edge, triangles whose colour would change across them are split (down to edges of
    max_edge) so colours stay where the deviation is instead of smearing across long flat
    triangles. Unchanged areas — most of a part — are left alone.
    """
    old_side, new_side = _side(old, deflection), _side(new, deflection)
    scale = max(float(np.abs(new_side.mesh.vertices).max(initial=0)),
                float(np.abs(old_side.mesh.vertices).max(initial=0)), 1.0)
    float_floor = max(scale * _FLOAT32_REL, 1e-9)
    old_error = chord_errors(old_side.mesh)

    def measure(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        sample = np.arange(len(points))
        if len(points) > MAX_COLOURED_VERTICES:
            # Very large meshes: measure a sample; every other point takes its nearest sample's value.
            sample = np.sort(np.random.default_rng(0).choice(len(points), MAX_COLOURED_VERTICES, replace=False))
        pts = points[sample]
        closest, dist, tri = trimesh.proximity.closest_point(old_side.mesh, pts)
        if old_side.surfaces is not None:
            dist, limit = _exact_distance(pts, dist, tri, old_side, old_error, float_floor)
        else:
            limit = old_error[tri] + float_floor
        sign = np.sign(np.einsum("ij,ij->i", pts - closest, old_side.mesh.face_normals[tri]))
        signed = np.where(sign < 0, -dist, dist)
        if len(sample) < len(points):
            _, nearest = cKDTree(pts).query(points)
            signed, limit = signed[nearest], limit[nearest]
        return signed, limit

    vertices, faces = np.asarray(new_side.mesh.vertices), np.asarray(new_side.mesh.faces)
    owners = new_side.owners if new_side.surfaces is not None else None
    signed, limit = measure(vertices)
    for _ in range(_MAX_REFINE_PASSES if max_edge else 0):
        values = signed[faces]
        varies = values.max(axis=1) - values.min(axis=1) > limit[faces].max(axis=1)
        tri = vertices[faces]
        longest = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max(axis=1)
        pick = np.nonzero(varies & (longest > max_edge))[0]
        if not len(pick):
            break
        new_vertices, new_faces = trimesh.remesh.subdivide(vertices, faces, face_index=pick)
        added = np.arange(len(vertices), len(new_vertices))
        if owners is not None:
            # Unpicked faces keep their order; each picked face's 4 children are appended in order.
            kept = np.ones(len(faces), dtype=bool)
            kept[pick] = False
            owners = np.concatenate([owners[kept], np.repeat(owners[pick], 4)])
            vertex_owner = np.empty(len(new_vertices), dtype=int)
            vertex_owner[new_faces.ravel()] = np.repeat(owners, 3)
            new_vertices[added] = _snap(new_vertices[added], vertex_owner[added], new_side)
        more_signed, more_limit = measure(new_vertices[added])
        vertices, faces = new_vertices, new_faces
        signed, limit = np.concatenate([signed, more_signed]), np.concatenate([limit, more_limit])
    return trimesh.Trimesh(vertices, faces, process=False), signed, limit
