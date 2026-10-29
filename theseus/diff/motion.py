"""Was the part just moved? A part that was translated or rotated but not reshaped shouldn't
read as "changed everywhere" — it should read "moved +5 mm in X, shape unchanged".

A rigid move preserves volume, surface area and the principal moments of inertia, so those
must match first. Candidate transforms are then tried — the plain translation between centres
of mass, and the rotations that line up the principal axes — each refined by point matching
(Kabsch/ICP on the part's vertices). A candidate is accepted only if the exact comparison of
the moved old version against the new one says identical: "moved" is a proof, not a guess.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.spatial import cKDTree

from theseus.model import Model

_INVARIANT_REL = 1e-6  # volume, area and principal moments must agree this closely
_ICP_ITERATIONS = 30


def _key_points(model: Model) -> np.ndarray:
    """Points that move with the part and match one-to-one when it's only moved: B-rep vertices,
    or mesh vertices. (Sampled tessellation points wouldn't correspond across versions.)"""
    if model.kind == "mesh":
        return np.asarray(model.mesh.vertices)
    from theseus.diff.brep import _vertices

    points, _ = _vertices(model.shape)
    return points


def _kabsch(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Best rotation R and translation t with R @ src + t ≈ dst (rows correspond)."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    u, _, vt = np.linalg.svd((src - cs).T @ (dst - cd))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1, 1, d]) @ u.T
    return rot, cd - rot @ cs


def _icp(src: np.ndarray, dst: np.ndarray, rot: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    tree = cKDTree(dst)
    err = math.inf
    for _ in range(_ICP_ITERATIONS):
        dist, idx = tree.query(src @ rot.T + t)
        new_err = float(dist.max())
        rot, t = _kabsch(src, dst[idx])
        if new_err >= err - 1e-15:
            break
        err = new_err
    dist, _ = tree.query(src @ rot.T + t)
    return rot, t, float(dist.max())


def _principal_axes(inertia) -> tuple[np.ndarray, np.ndarray]:
    moments, axes = np.linalg.eigh(np.asarray(inertia, dtype=float))
    return moments, axes


def _candidates(old: dict, new: dict):
    """Starting transforms (R, t) taking old to new."""
    c_old, c_new = np.asarray(old["com"]), np.asarray(new["com"])
    yield np.eye(3), c_new - c_old
    if old.get("inertia") is None or new.get("inertia") is None:
        return
    _, e_old = _principal_axes(old["inertia"])
    _, e_new = _principal_axes(new["inertia"])
    for signs in itertools.product((1, -1), repeat=3):
        rot = e_new @ np.diag(signs) @ e_old.T
        if np.linalg.det(rot) > 0:
            yield rot, c_new - rot @ c_old


def _invariants_match(old: dict, new: dict) -> bool:
    def close(a, b) -> bool:
        return abs(a - b) <= _INVARIANT_REL * max(abs(a), abs(b), 1e-12)

    if old.get("volume_mm3") is None or new.get("volume_mm3") is None:
        return False
    if old.get("com") is None or new.get("com") is None:
        return False
    if not close(old["volume_mm3"], new["volume_mm3"]) or not close(old["area_mm2"], new["area_mm2"]):
        return False
    if old["solid_count"] != new["solid_count"]:
        return False
    if old.get("inertia") is not None and new.get("inertia") is not None:
        mo, _ = _principal_axes(old["inertia"])
        mn, _ = _principal_axes(new["inertia"])
        return all(close(a, b) for a, b in zip(mo, mn))
    return True


def _transformed(model: Model, rot: np.ndarray, t: np.ndarray) -> Model:
    if model.kind == "mesh":
        moved = model.mesh.copy()
        matrix = np.eye(4)
        matrix[:3, :3], matrix[:3, 3] = rot, t
        moved.apply_transform(matrix)
        return Model(model.path, model.format, "mesh", mesh=moved, units_note=model.units_note,
                     mm_per_file_unit=model.mm_per_file_unit)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetValues(*rot[0], t[0], *rot[1], t[1], *rot[2], t[2])
    shape = BRepBuilderAPI_Transform(model.shape, trsf, True).Shape()
    return Model(model.path, model.format, "brep", shape=shape, units_note=model.units_note,
                 mm_per_file_unit=model.mm_per_file_unit)


def _rotation_axis_angle(rot: np.ndarray) -> tuple[np.ndarray, float]:
    """(unit axis, angle in degrees). atan2 rather than acos: acos is ill-conditioned near 0°,
    where a pure translation would otherwise show a spurious ~1e-6° rotation."""
    axis = np.array([rot[2, 1] - rot[1, 2], rot[0, 2] - rot[2, 0], rot[1, 0] - rot[0, 1]])
    sin2 = np.linalg.norm(axis)  # 2·sin θ
    angle = math.degrees(math.atan2(sin2 / 2, (np.trace(rot) - 1) / 2))
    if sin2 < 1e-9:  # 0° or 180°: take the axis from the symmetric part
        w, v = np.linalg.eigh((rot + np.eye(3)) / 2)
        axis = v[:, np.argmax(w)]
    else:
        axis = axis / sin2
    if axis[np.argmax(np.abs(axis))] < 0:
        axis = -axis
        angle = -angle if 0 < angle < 180 else angle
    return axis, angle


def detect_motion(old: Model, new: Model, old_metrics: dict, new_metrics: dict) -> dict | None:
    """{"translation": [dx, dy, dz], "rotation_deg": θ, "axis": [x, y, z]} if `new` is `old` moved
    rigidly (verified exactly), else None."""
    if old.kind != new.kind or not _invariants_match(old_metrics, new_metrics):
        return None
    src, dst = _key_points(old), _key_points(new)
    if len(src) != len(dst) or not len(src):
        return None

    from theseus.diff.brep import compare_breps
    from theseus.diff.surface import compare_surfaces

    compare = compare_breps if old.kind == "brep" else compare_surfaces
    size = float(np.ptp(np.vstack([src, dst]), axis=0).max()) or 1.0
    best = None  # (|angle|, axis, angle)
    for rot, t in _candidates(old_metrics, new_metrics):
        rot, t, err = _icp(src, dst, rot, t)
        if err > size * 1e-6 + 1e-6:
            continue
        axis, angle = _rotation_axis_angle(rot)
        if best is not None and abs(angle) >= best[0]:
            continue
        # A symmetric part matches several rotations (a plate turned 90° is also the plate
        # flipped 180° about a diagonal): keep the smallest one that verifies.
        if compare(_transformed(old, rot, t), new).identical:
            best = (abs(angle), axis, angle if abs(angle) > 1e-9 else 0.0)
            if best[0] == 0:
                break
    if best is None:
        return None
    # Reported as the centre of mass's shift: the same as t for a pure translation, and the
    # intuitive "where did it go" when the part also turned.
    shift = np.asarray(new_metrics["com"]) - np.asarray(old_metrics["com"])
    return {"translation": [float(v) for v in shift], "rotation_deg": float(best[2]),
            "axis": [float(v) for v in best[1]]}
