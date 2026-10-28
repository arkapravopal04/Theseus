"""Exact comparison of two B-rep shapes, face by face and vertex by vertex.

Each face is reduced to its surface type and analytic parameters (a plane's normal and offset,
a cylinder's axis and radius, ...) plus its area and centroid. Faces are matched old-to-new
by type and position; a matched pair whose surface parameters differ is a change *to that
surface* ("plane x = 200.000 → 200.190"); a pair with the same surface but a different
boundary was re-trimmed by a neighbouring change; unmatched faces were added or removed.
Vertices are matched by position as a safety net, within each vertex's own tolerance.

The detection limit is the coarsest of: the kernel's confusion tolerance, the precision the
file declares (STEP UNCERTAINTY_MEASURE_WITH_UNIT), and the precision its numbers were
written with (an exporter printing 6 significant digits can't record changes smaller than
that).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
from scipy.spatial import cKDTree

from theseus.diff import Finding, PreciseDiff
from theseus.fmt import decimals_for, fmt_len, fmt_point
from theseus.model import Model

KERNEL_PRECISION_MM = 1e-7  # OCCT's Precision::Confusion()
_TYPE_NAMES = {
    GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere", GeomAbs_Torus: "torus",
}
_AXES = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}
_MAX_SAMPLES_PER_FREEFORM_FACE = 200


# --- detection limit -------------------------------------------------------------------

_POINT = re.compile(rb"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)")
_UNCERTAINTY = re.compile(rb"UNCERTAINTY_MEASURE_WITH_UNIT\s*\(\s*LENGTH_MEASURE\s*\(\s*([-+.\dEe]+)\s*\)")


def _sig_digits(number: bytes) -> int:
    mantissa = re.split(rb"[eE]", number.strip().lstrip(b"+-"))[0].replace(b".", b"")
    return len(mantissa.lstrip(b"0")) or 1


def step_precision_mm(path: str, mm_per_unit: float) -> tuple[float, str]:
    """(limit in mm, explanation) from what a STEP file says and shows about its precision."""
    with open(path, "rb") as f:
        text = f.read()
    limit, why = KERNEL_PRECISION_MM, f"geometry kernel precision ({fmt_len(KERNEL_PRECISION_MM)})"

    m = _UNCERTAINTY.search(text)
    if m:
        declared = abs(float(m.group(1))) * mm_per_unit
        if declared > limit:
            limit, why = declared, f"the file's declared precision ({fmt_len(declared)})"

    coords = [c for pt in _POINT.findall(text) for c in pt.split(b",") if c.strip()]
    if coords:
        digits = max(_sig_digits(c) for c in coords)
        # Only meaningful when the exporter evidently prints a fixed number of digits: a file
        # of short round numbers ("20.") hasn't been truncated at all.
        if digits >= 5:
            biggest = max(abs(float(c)) for c in coords) * mm_per_unit
            written = biggest * 10.0 ** (1 - digits)
            if written > limit:
                limit, why = written, f"coordinates written with {digits} significant digits ({fmt_len(written)})"
    return limit, why


def detection_limit(old: Model, new: Model) -> tuple[float, str]:
    best = (KERNEL_PRECISION_MM, f"geometry kernel precision ({fmt_len(KERNEL_PRECISION_MM)})")
    for model in (old, new):
        if model.format == "step":
            best = max(best, step_precision_mm(model.path, model.mm_per_file_unit))
    return best


# --- face descriptors ------------------------------------------------------------------


def _canonical_dir(v: np.ndarray) -> np.ndarray:
    """A direction with its sign fixed (largest component positive), so an axis and its
    reverse compare equal — face orientation isn't a geometric change."""
    v = v / np.linalg.norm(v)
    return -v if v[np.argmax(np.abs(v))] < 0 else v


def _vec(d) -> np.ndarray:
    return np.array([d.X(), d.Y(), d.Z()])


def _axis_name(axis: np.ndarray) -> str | None:
    for vec, name in _AXES.items():
        if np.allclose(axis, vec, atol=1e-9):
            return name
    return None


@dataclass
class Face:
    type: str
    area: float
    centroid: np.ndarray
    face: object
    axis: np.ndarray | None = None  # plane normal, or the axis of a cylinder/cone/torus
    origin: np.ndarray | None = None  # point on the axis closest to the world origin, or a centre
    offset: float = 0.0  # plane: signed distance from the origin along the normal
    radius: float = 0.0
    radius2: float = 0.0  # torus minor radius / cone half-angle (radians)

    extent: float = 0.0  # bounding-box diagonal: how far a tilt moves the face's far edge

    @property
    def size(self) -> float:
        return math.sqrt(max(self.area, 0.0))

    @property
    def label(self) -> str:
        axis = _axis_name(self.axis) if self.axis is not None else None
        if self.type == "plane":
            return "plane" if axis else f"plane ⟂ {fmt_point(self.axis, 4)}"
        if self.type in ("cylinder", "cone", "torus"):
            return f"{self.type} along {axis or fmt_point(self.axis, 4)}"
        return self.type if self.type == "sphere" else f"{self.type} surface"

    def params(self, decimals: int = 3) -> list[tuple[str, str]]:
        """(name, value) pairs that identify this surface, formatted for a report."""
        axis = _axis_name(self.axis) if self.axis is not None else None
        if self.type == "plane":
            return [(f"{axis.lower()} =" if axis else "offset", f"{self.offset:.{decimals}f}")]
        if self.origin is not None and axis and self.type in ("cylinder", "torus"):
            where = fmt_point(self.origin[[i for i in range(3) if i != "XYZ".index(axis)]], decimals)
        else:
            where = fmt_point(self.origin if self.origin is not None else self.centroid, decimals)
        if self.type == "cylinder":
            return [("Ø", f"{2 * self.radius:.{decimals}f}"), ("at", where)]
        if self.type == "sphere":
            return [("Ø", f"{2 * self.radius:.{decimals}f}"), ("at", where)]
        if self.type == "torus":
            return [("R", f"{self.radius:.{decimals}f}"), ("r", f"{self.radius2:.{decimals}f}"), ("at", where)]
        if self.type == "cone":
            return [("angle", f"{math.degrees(2 * self.radius2):.4f}°"), ("apex", where)]
        return [("near", where)]

    def describe(self, decimals: int = 3) -> str:
        return " ".join([self.label, *(_param(k, v) for k, v in self.params(decimals))])


def _param(name: str, value: str) -> str:
    return f"{name}{value}" if name in ("Ø", "R", "r") else f"{name} {value}"


def _unique(shape: TopoDS_Shape, kind) -> list:
    shapes = ShapeMap()
    TopExp.MapShapes_s(shape, kind, shapes)
    return [shapes.FindKey(i) for i in range(1, shapes.Extent() + 1)]


def _describe_face(face) -> Face:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    adaptor = BRepAdaptor_Surface(face, True)
    kind = adaptor.GetType()
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box)
    out = Face(type=_TYPE_NAMES.get(kind, str(kind).rsplit("_", 1)[-1].lower().replace("surface", "") or "other"),
               area=props.Mass(), centroid=np.array([c.X(), c.Y(), c.Z()]), face=face,
               extent=box.CornerMin().Distance(box.CornerMax()) if not box.IsVoid() else 0.0)

    def axis_origin(ax) -> None:
        out.axis = _canonical_dir(_vec(ax.Direction()))
        loc = _vec(ax.Location())
        out.origin = loc - np.dot(loc, out.axis) * out.axis

    if kind == GeomAbs_Plane:
        pln = adaptor.Plane()
        out.axis = _canonical_dir(_vec(pln.Axis().Direction()))
        out.offset = float(np.dot(_vec(pln.Location()), out.axis))
    elif kind == GeomAbs_Cylinder:
        cyl = adaptor.Cylinder()
        axis_origin(cyl.Axis())
        out.radius = cyl.Radius()
    elif kind == GeomAbs_Cone:
        cone = adaptor.Cone()
        axis_origin(cone.Axis())
        out.origin = _vec(cone.Apex())
        out.radius2 = abs(cone.SemiAngle())
    elif kind == GeomAbs_Sphere:
        sph = adaptor.Sphere()
        out.origin = _vec(sph.Location())
        out.radius = sph.Radius()
    elif kind == GeomAbs_Torus:
        tor = adaptor.Torus()
        out.axis = _canonical_dir(_vec(tor.Axis().Direction()))
        out.origin = _vec(tor.Location())
        out.radius, out.radius2 = tor.MajorRadius(), tor.MinorRadius()
    return out


# --- comparing a matched pair ----------------------------------------------------------


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    return math.acos(min(1.0, abs(float(np.dot(a, b)))))


def _line_offset(o1: np.ndarray, o2: np.ndarray, axis: np.ndarray) -> np.ndarray:
    d = o2 - o1
    return d - np.dot(d, axis) * axis


def _surface_changes(a: Face, b: Face) -> list[tuple[str, float]]:
    """(description, displacement in mm) for each way b's surface differs from a's."""
    out = []
    if a.axis is not None and b.axis is not None:
        tilt = _angle(a.axis, b.axis)
        if tilt > 0:
            out.append((f"tilted {math.degrees(tilt):.4g}°", tilt * max(a.extent, b.extent)))
    if a.type == "plane":
        out.append(("offset", b.offset - a.offset))
    elif a.type in ("cylinder", "torus"):
        out.append(("radius", b.radius - a.radius))
        out.append(("axis", float(np.linalg.norm(_line_offset(a.origin, b.origin, a.axis)))))
        if a.type == "torus":
            out.append(("minor radius", b.radius2 - a.radius2))
    elif a.type == "cone":
        out.append(("apex", float(np.linalg.norm(b.origin - a.origin))))
        out.append(("half-angle", (b.radius2 - a.radius2) * max(a.extent, b.extent)))
    elif a.type == "sphere":
        out.append(("radius", b.radius - a.radius))
        out.append(("centre", float(np.linalg.norm(b.origin - a.origin))))
    return out


def _surface_distance(a: Face, b: Face) -> float:
    return max((abs(m) for _, m in _surface_changes(a, b)), default=0.0)


def _face_points(face, deflection: float) -> np.ndarray:
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        BRepMesh_IncrementalMesh(face, deflection)
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            return np.zeros((0, 3))
    trsf = loc.Transformation()
    n = tri.NbNodes()
    step = max(1, n // _MAX_SAMPLES_PER_FREEFORM_FACE)
    pts = [tri.Node(i).Transformed(trsf) for i in range(1, n + 1, step)]
    return np.array([[p.X(), p.Y(), p.Z()] for p in pts])


def _freeform_deviation(a: Face, b: Face, deflection: float) -> tuple[float, np.ndarray | None]:
    """Largest distance from points on face b to face a's surface (and where)."""
    surface = BRep_Tool.Surface_s(a.face)
    worst, where = 0.0, None
    for p in _face_points(b.face, deflection):
        proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), surface)
        if proj.NbPoints() and proj.LowerDistance() > worst:
            worst, where = proj.LowerDistance(), p
    return worst, where


_ANALYTIC = ("plane", "cylinder", "cone", "sphere", "torus")


def _change_text(a: Face, b: Face, changes: list[tuple[str, float]], floor: float) -> str:
    """'cylinder along Z Ø8.0000 → 8.0020 at (50.0000, 25.0000) — Ø +2.0 µm'."""
    decimals = decimals_for(min(abs(m) for _, m in changes))
    parts = []
    for (name, va), (_, vb) in zip(a.params(decimals), b.params(decimals)):
        parts.append(_param(name, va) if va == vb else f"{_param(name, va)} → {vb}")
    details = []
    for what, m in changes:
        if what.startswith("tilted"):
            details.append(f"{what} (far edge moves {fmt_len(m)})")
        elif what == "offset":
            details.append(fmt_len(m, signed=True))
        elif what in ("axis", "centre", "apex"):
            details.append(f"{what} moved {fmt_len(m)}")
        elif what == "radius" and a.type in ("cylinder", "sphere"):
            details.append(f"Ø {fmt_len(2 * m, signed=True)}")  # engineers think in diameters
        elif what in ("radius", "minor radius"):
            details.append(f"{'R' if what == 'radius' else 'r'} {fmt_len(m, signed=True)}")
        elif what == "half-angle":
            details.append("cone angle changed")
    return " ".join([a.label, *parts]) + (" — " + ", ".join(details) if details else "")

# --- matching --------------------------------------------------------------------------


def _match(old: list[Face], new: list[Face]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy one-to-one matching of faces of the same type, cheapest first."""
    candidates = []
    for kind in {f.type for f in new}:
        old_idx = [i for i, f in enumerate(old) if f.type == kind]
        new_idx = [j for j, f in enumerate(new) if f.type == kind]
        if not old_idx:
            continue
        tree = cKDTree(np.array([old[i].centroid for i in old_idx]))
        k = min(8, len(old_idx))
        dists, hits = tree.query(np.array([new[j].centroid for j in new_idx]), k=k)
        dists, hits = np.reshape(dists, (len(new_idx), k)), np.reshape(hits, (len(new_idx), k))
        for row, j in enumerate(new_idx):
            for dist, hit in zip(dists[row], hits[row]):
                a, b = old[old_idx[hit]], new[j]
                # Too far apart to be the same face moved — it's a removed face and an added one.
                if dist > 2 * max(a.size, b.size) + 1e-6:
                    continue
                cost = dist + _surface_distance(a, b) + abs(a.area - b.area) / (4 * max(a.size, b.size, 1e-9))
                candidates.append((cost, old_idx[hit], j))

    pairs, used_old, used_new = [], set(), set()
    for _, i, j in sorted(candidates):
        if i not in used_old and j not in used_new:
            pairs.append((i, j))
            used_old.add(i)
            used_new.add(j)
    return (pairs, [i for i in range(len(old)) if i not in used_old],
            [j for j in range(len(new)) if j not in used_new])


# --- the comparison --------------------------------------------------------------------


def _vertices(shape) -> tuple[np.ndarray, np.ndarray]:
    pts, tols = [], []
    for v in _unique(shape, TopAbs_VERTEX):
        vertex = TopoDS.Vertex(v)
        p = BRep_Tool.Pnt_s(vertex)
        pts.append((p.X(), p.Y(), p.Z()))
        tols.append(BRep_Tool.Tolerance_s(vertex))
    return np.array(pts).reshape(-1, 3), np.array(tols)


def _vertex_findings(old_shape, new_shape, floor: float) -> list[Finding]:
    """Vertices with no counterpart within tolerance — a safety net under the face matching."""
    (op, ot), (np_, nt) = _vertices(old_shape), _vertices(new_shape)
    if not len(op) or not len(np_):
        return []
    count, worst, worst_at = 0, 0.0, None
    for src, src_tol, dst, dst_tol in ((np_, nt, op, ot), (op, ot, np_, nt)):
        dist, idx = cKDTree(dst).query(src)
        off = dist > np.maximum(np.maximum(src_tol, dst_tol[idx]), floor)
        count = max(count, int(off.sum()))
        if off.any() and dist[off].max() > worst:
            i = int(np.argmax(np.where(off, dist, -1)))
            worst, worst_at = float(dist[i]), src[i]
    if not count:
        return []
    return [Finding(f"{count} vert{'ex' if count == 1 else 'ices'} moved, added or removed — "
                    f"largest {fmt_len(worst)} at {fmt_point(worst_at)}", worst, tuple(map(float, worst_at)))]

def _counts(shape) -> dict[str, int]:
    return {name: len(_unique(shape, kind)) for name, kind in
            (("solids", TopAbs_SOLID), ("faces", TopAbs_FACE), ("edges", TopAbs_EDGE), ("vertices", TopAbs_VERTEX))}


def compare_breps(old: Model, new: Model) -> PreciseDiff:
    floor, floor_note = detection_limit(old, new)
    old_faces = [_describe_face(TopoDS.Face(f)) for f in _unique(old.shape, TopAbs_FACE)]
    new_faces = [_describe_face(TopoDS.Face(f)) for f in _unique(new.shape, TopAbs_FACE)]
    pairs, removed, added = _match(old_faces, new_faces)

    findings: list[Finding] = []
    retrimmed = []  # (boundary shift, face): same surface, different extent
    deflection = max(max([f.size for f in new_faces + old_faces] or [1.0]) * 1e-3, 1e-3)

    for i, j in pairs:
        a, b = old_faces[i], new_faces[j]
        perimeter = 4 * max(a.size, b.size, 1e-9)
        shift = max(float(np.linalg.norm(a.centroid - b.centroid)), abs(a.area - b.area) / perimeter)
        if a.type in _ANALYTIC:
            changes = [(what, m) for what, m in _surface_changes(a, b) if abs(m) > floor]
            if changes:
                findings.append(Finding(_change_text(a, b, changes, floor), max(abs(m) for _, m in changes),
                                        tuple(map(float, b.centroid))))
                continue
        elif shift > floor * 1e-2:
            # Free-form surface: no parameters to compare, so measure it. (Skipped when area and
            # centroid agree to far below the floor — an edit can't preserve both that exactly.)
            dev, where = _freeform_deviation(a, b, deflection)
            if dev > floor:
                findings.append(Finding(
                    f"{b.describe()} deviates by up to {fmt_len(dev)}" + (f" at {fmt_point(where)}" if where is not None else ""),
                    dev, tuple(map(float, where)) if where is not None else None))
                continue
        if shift > floor:
            retrimmed.append((shift, b))

    for i in removed:
        f = old_faces[i]
        findings.append(Finding(f"removed face: {f.describe()} ({f.area:.3f} mm²)", f.size, tuple(map(float, f.centroid))))
    for j in added:
        f = new_faces[j]
        findings.append(Finding(f"added face: {f.describe()} ({f.area:.3f} mm²)", f.size, tuple(map(float, f.centroid))))
    if retrimmed:
        shift, face = max(retrimmed, key=lambda t: t[0])
        findings.append(Finding(
            f"{len(retrimmed)} face{'s' if len(retrimmed) > 1 else ''} with unchanged surfaces re-trimmed by "
            f"neighbouring changes (largest boundary shift {fmt_len(shift)}, {face.describe()})",
            shift, tuple(map(float, face.centroid)),
        ))
    findings += _vertex_findings(old.shape, new.shape, floor)

    notes = []
    old_counts, new_counts = _counts(old.shape), _counts(new.shape)
    if old_counts != new_counts:
        notes.append("topology: " + ", ".join(f"{k} {old_counts[k]} → {new_counts[k]}"
                                              for k in old_counts if old_counts[k] != new_counts[k]))

    max_dev = max((f.magnitude_mm for f in findings), default=0.0)
    return PreciseDiff("exact B-rep", floor, floor_note, identical=not findings and old_counts == new_counts,
                       max_deviation_mm=max_dev, findings=findings, notes=notes)
