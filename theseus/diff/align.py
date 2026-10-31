"""Line two versions of a part up before comparing them, when one was also moved or mirrored.

Exact face comparison happens in world coordinates, so a part that was moved *and* edited reads
as "everything changed". This searches for the transform under which most of the part agrees
exactly, and only then compares: what's left is the edit.

A wrong alignment would produce a confident, wrong report, so it has to earn its place:
  - candidates come from triples of vertices with the same mutual distances in both versions;
  - the winner must put a good share of vertices on vertices and of analytic surface area on
    identical surfaces (a chance fit of a few points does neither);
  - the part as it sits must fit clearly worse, so an edit in place is never "explained" as a move;
  - the comparison must come out with fewer differences than the unaligned one.
Otherwise there's no alignment and the report stays as it was. A symmetric part can fit several
ways equally well: the plainest wins (no mirror over mirror, then the smallest rotation).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist

from theseus.diff import PreciseDiff
from theseus.diff.brep import (
    KERNEL_PRECISION_MM,
    Face,
    _describe_face,
    _in_bodies,
    _vertices,
    compare_breps,
    detection_limit,
)
from theseus.diff.motion import _kabsch, _rotation_axis_angle, _transformed, mirror_description
from theseus.model import Model

MAX_VERTICES = 3000  # the search compares all pairs of vertices; bigger parts aren't aligned
_MIN_TOL_MM = 1e-4  # exporters round: an alignment can't demand better than 0.1 µm agreement
_ANCHOR_TRIPLES = 40
_MAX_FITS = 3000
_MAX_SCORE_POINTS = 400
_KEEP_FITS = 8
_MIN_VERTEX_SHARE = 0.25
_MIN_SURFACE_SHARE = 0.5
_MIN_STAYED_SHARE = 0.05
_CLEAR_FIT_SHARE = 0.9
_CLEAR_FIT_MARGIN = 0.05
_MIN_MOVE_PER_TOL = 10


@dataclass
class Alignment:
    rot: np.ndarray
    t: np.ndarray
    mirrored: bool
    vertex_share: float  # of the old version's vertices that land on a new vertex
    surface_share: float  # of the old version's analytic area that lands on an identical surface
    residual_mm: float  # how far off the worst of those vertices still is

    def to_dict(self) -> dict:
        d = {"vertex_share": self.vertex_share, "surface_share": self.surface_share, "residual_mm": self.residual_mm}
        if self.mirrored:
            d["mirror"] = mirror_description(self.rot, self.t)
        else:
            axis, angle = _rotation_axis_angle(self.rot)
            d.update(translation=[float(v) for v in self.t], rotation_deg=float(angle) if abs(angle) > 1e-9 else 0.0,
                     axis=[float(v) for v in axis])
        return d


def _parallel(axes: np.ndarray, axis: np.ndarray, tol: float) -> np.ndarray:
    return np.linalg.norm(np.cross(axes, axis), axis=1) < tol


def _surface_groups(faces: list[Face]) -> dict[str, tuple[list[Face], dict[str, np.ndarray]]]:
    groups: dict[str, list[Face]] = {}
    for f in faces:
        if f.type in ("plane", "cylinder", "cone", "sphere", "torus"):
            groups.setdefault(f.type, []).append(f)
    out = {}
    for kind, members in groups.items():
        arrays = {"area": np.array([f.area for f in members]),
                  "radius": np.array([f.radius for f in members]),
                  "radius2": np.array([f.radius2 for f in members])}
        if kind == "plane":
            arrays["axis"] = np.array([f.axis for f in members])
            arrays["origin"] = arrays["axis"] * np.array([[f.offset] for f in members])
        else:
            arrays["origin"] = np.array([f.origin for f in members])
            if kind != "sphere":
                arrays["axis"] = np.array([f.axis for f in members])
        out[kind] = (members, arrays)
    return out


def _surface_share(old_groups, new_groups, rot: np.ndarray, t: np.ndarray, tol: float, size: float) -> float:
    """The share of the old version's analytic area on an identical surface once moved by (rot, t)."""
    total = covered = 0.0
    for kind, (members, arrays) in old_groups.items():
        total += float(arrays["area"].sum())
        if kind not in new_groups:
            continue
        other = new_groups[kind][1]
        origins = arrays["origin"] @ rot.T + t
        axes = arrays["axis"] @ rot.T if "axis" in arrays else None
        for i, f in enumerate(members):
            d = other["origin"] - origins[i]
            if kind == "plane":
                same = _parallel(other["axis"], axes[i], tol / size) & (np.abs(np.sum(d * other["axis"], axis=1)) < tol)
            elif kind == "cylinder":
                along = np.sum(d * axes[i], axis=1, keepdims=True) * axes[i]
                same = (_parallel(other["axis"], axes[i], tol / size) & (np.linalg.norm(d - along, axis=1) < tol)
                        & (np.abs(other["radius"] - f.radius) < tol))
            else:
                same = (np.linalg.norm(d, axis=1) < tol) & (np.abs(other["radius"] - f.radius) < tol)
                if kind != "sphere":
                    same &= _parallel(other["axis"], axes[i], tol / size) & (np.abs(other["radius2"] - f.radius2) < tol / size)
            covered += min(f.area, float(other["area"][same].sum()))
    return covered / total if total else 0.0


def _share_untouched(old: list[Face], new: list[Face], tol: float, rot: np.ndarray = np.eye(3),
                     t: np.ndarray = np.zeros(3)) -> float:
    """The share of the old version's area in faces that exist unchanged in the new one, with the
    old version moved by (rot, t): same type, same area, same centre."""
    if not old or not new:
        return 0.0
    tree = cKDTree(np.array([f.centroid for f in new]))
    total = untouched = 0.0
    for f in old:
        total += f.area
        dist, j = tree.query(rot @ f.centroid + t)
        if dist < tol and new[j].type == f.type and abs(new[j].area - f.area) < tol * max(f.size, 1.0):
            untouched += f.area
    return untouched / total if total else 0.0


def _anchor_triples(points: np.ndarray, rng: np.random.Generator) -> list[np.ndarray]:
    """Well-spread triples of points: the larger the triangle, the better it pins a transform."""
    triples = []
    for _ in range(_ANCHOR_TRIPLES):
        tries = rng.integers(0, len(points), size=(24, 3))
        a, b, c = points[tries[:, 0]], points[tries[:, 1]], points[tries[:, 2]]
        areas = np.linalg.norm(np.cross(b - a, c - a), axis=1)
        best = int(np.argmax(areas))
        if areas[best] > 1e-9:
            triples.append(tries[best])
    return triples


def _fits(src: np.ndarray, dst: np.ndarray, tol: float, rng: np.random.Generator):
    """(rot, t, mirrored) for each way an anchor triple of src fits a triple of dst. Three points
    fit twice, once each way round, so both hands are tried."""
    distances = cdist(dst, dst)
    budget = _MAX_FITS
    for triple in _anchor_triples(src, rng):
        a, b, c = src[triple]
        ab, ac, bc = np.linalg.norm(a - b), np.linalg.norm(a - c), np.linalg.norm(b - c)
        ii, jj = np.nonzero(np.abs(distances - ab) < 2 * tol)
        order = rng.permutation(len(ii))[:400]
        for i, j in zip(ii[order], jj[order]):
            ks = np.nonzero((np.abs(distances[i] - ac) < 2 * tol) & (np.abs(distances[j] - bc) < 2 * tol))[0]
            for k in ks[:8]:
                for mirrored in (False, True):
                    budget -= 1
                    if budget < 0:
                        return
                    yield (*_kabsch(src[triple], dst[[i, j, k]], mirrored), mirrored)


def find_alignment(old: Model, new: Model, tol: float | None = None) -> Alignment | None:
    """The transform taking `old` onto `new`, if a clearly good one exists (see module docstring)."""
    if old.kind != "brep" or new.kind != "brep":
        return None
    src, _ = _vertices(old.shape)
    dst, _ = _vertices(new.shape)
    if not 3 <= len(src) <= MAX_VERTICES or not 3 <= len(dst) <= MAX_VERTICES:
        return None
    tol = tol if tol is not None else max(_MIN_TOL_MM, 3 * detection_limit(old, new)[0], 10 * KERNEL_PRECISION_MM)
    tree = cKDTree(dst)

    def share(points: np.ndarray) -> float:
        return float(np.mean(tree.query(points)[0] < tol))

    # An edit in place keeps much of the part where it was, so the part as it sits is the bar to beat.
    in_place = share(src)
    rng = np.random.default_rng(0)  # the same files always give the same report
    sample = src if len(src) <= _MAX_SCORE_POINTS else src[rng.choice(len(src), _MAX_SCORE_POINTS, replace=False)]
    scored = []
    for rot, t, mirrored in _fits(src, dst, tol, rng):
        s = share(sample @ rot.T + t)
        if s >= _MIN_VERTEX_SHARE:
            scored.append((s, mirrored, rot, t))
    if not scored:
        return None
    scored.sort(key=lambda c: (-round(c[0], 3), c[1]))

    old_faces = [_describe_face(TopoDS.Face(f)) for f in _in_bodies(old.shape, TopAbs_FACE)]
    new_faces = [_describe_face(TopoDS.Face(f)) for f in _in_bodies(new.shape, TopAbs_FACE)]
    old_groups, new_groups = _surface_groups(old_faces), _surface_groups(new_faces)
    size = float(np.ptp(np.vstack([src, dst]), axis=0).max()) or 1.0

    best, best_key, seen = None, None, []
    for _, mirrored, rot, t in scored:
        if any(m == mirrored and np.allclose(r, rot, atol=1e-6) and np.allclose(s, t, atol=tol) for m, r, s in seen):
            continue
        seen.append((mirrored, rot, t))
        if len(seen) > _KEEP_FITS:
            break
        dist, idx = tree.query(src @ rot.T + t)
        inliers = dist < tol
        if inliers.sum() < 3:
            continue
        # Refit to every vertex that agrees: three points fix a transform, all of them fix it best.
        rot, t = _kabsch(src[inliers], dst[idx[inliers]], mirrored)
        moved = src @ rot.T + t
        dist = tree.query(moved)[0]
        inliers = dist < tol
        vertex_share = float(inliers.mean())
        surface_share = _surface_share(old_groups, new_groups, rot, t, tol, size)
        angle = abs(_rotation_axis_angle(-rot if mirrored else rot)[1])
        turn = 180 - angle if mirrored else angle
        key = (-round(vertex_share + surface_share, 3), mirrored, round(turn, 6))
        if best_key is None or key < best_key:
            best_key = key
            best = Alignment(rot, t, mirrored, vertex_share, surface_share,
                             float(dist[inliers].max()) if inliers.any() else 0.0)
    if best is None or best.vertex_share < _MIN_VERTEX_SHARE or best.surface_share < _MIN_SURFACE_SHARE:
        return None
    # A "move" no bigger than the fitting tolerance can't be told from a tiny edit it would swallow.
    if not best.mirrored and float(np.linalg.norm(src @ best.rot.T + best.t - src, axis=1).max()) <= _MIN_MOVE_PER_TOL * tol:
        return None
    # A nearly symmetric part flipped or mirrored coincides with itself in most places, so a good
    # share in place is no argument against a transform that fits almost everything, and clearly better.
    clear_fit = best.vertex_share >= _CLEAR_FIT_SHARE and best.vertex_share - in_place >= _CLEAR_FIT_MARGIN
    if in_place > best.vertex_share / 2 and not clear_fit:
        return None
    # Faces still exactly where they were suggest the part didn't move and the rest moved relative
    # to them: all the holes shifting isn't the plate moving the other way. Unless the transform
    # leaves clearly more intact: a part mirrored about its own base keeps that base in place.
    stayed = _share_untouched(old_faces, new_faces, tol)
    needed = stayed if clear_fit else 1.25 * stayed
    if stayed > _MIN_STAYED_SHARE and _share_untouched(old_faces, new_faces, tol, best.rot, best.t) < needed:
        return None
    return best


def compare_aligned(old: Model, new: Model, direct_findings: int,
                    direct_largest_mm: float = 0.0) -> tuple[Model, PreciseDiff, Alignment] | None:
    """(old moved into place, the comparison after that, the alignment), or None if lining the
    versions up doesn't help: neither fewer differences than as they sit, nor far smaller ones."""
    alignment = find_alignment(old, new)
    if alignment is None:
        return None
    moved = _transformed(old, alignment.rot, alignment.t)
    moved.warnings = old.warnings
    result = compare_breps(moved, new, min_floor=2 * alignment.residual_mm)
    if len(result.findings) >= direct_findings and result.max_deviation_mm > direct_largest_mm / 2:
        return None
    return moved, result, alignment
