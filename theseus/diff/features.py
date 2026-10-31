"""Read groups of faces as the features an engineer would name: holes and fillets.

An exchange file has no feature tree, so these are recognised from the geometry. A face that
isn't recognised simply stays a face in the report, so this can only ever improve a line's
wording: nothing is hidden by a feature being missed.

  - Hole: a concave full circle of cylinder, possibly split across several faces (many exporters
    split a hole into two halves), with whatever else shares its axis and touches it: a counterbore,
    a countersink, a drill point. Material just past an end means that end is closed.
  - Fillet: a cylinder, torus or sphere patch of less than a half turn that meets its neighbours
    tangentially on at least two edges; connected patches of one radius are one fillet.

Exact B-reps only. Threads aren't recognisable: a STEP file holds a tapped hole as a plain bore.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt, gp_Vec
from OCP.OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS

from theseus.diff import Finding
from theseus.diff.brep import (
    _MAX_SAME_FACE_TILT,
    Face,
    _angle,
    _axis_name,
    _line_offset,
    _param,
    _surface_distance,
    _unique,
    face_gap,
    gap_tolerance,
)
from theseus.fmt import decimals_for, fmt_len, fmt_point
from theseus.model import skin_of

_SAME = 1e-6  # mm: the least two faces can differ in axis and radius and be one surface
_SAME_DIRECTION = 1e-4  # radians: coarse enough for directions an exporter rounded to 6 digits
_SAME_PER_FLOOR = 10  # how far above the detection limit rounded coordinates can scatter
_TANGENT = math.cos(math.radians(1.0))
_FULL_TURN = math.radians(359.0)
_HALF_TURN = math.radians(179.0)
_PROBE_MM = 1e-3


def _vec(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()])


def _radial(f: Face, p: np.ndarray) -> np.ndarray:
    v = p - f.origin
    return v - np.dot(v, f.axis) * f.axis


def _is_concave(f: Face) -> bool:
    """Whether a surface of revolution's outward normal (out of the material) faces its axis."""
    adaptor = BRepAdaptor_Surface(f.face, True)
    u = (adaptor.FirstUParameter() + adaptor.LastUParameter()) / 2
    v = (adaptor.FirstVParameter() + adaptor.LastVParameter()) / 2
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    adaptor.D1(u, v, p, du, dv)
    normal = _vec(du.Crossed(dv))
    if f.face.Orientation() == TopAbs_REVERSED:
        normal = -normal
    return float(np.dot(normal, _radial(f, _vec(p)))) < 0


def _sweep(f: Face) -> float:
    adaptor = BRepAdaptor_Surface(f.face, True)
    return adaptor.LastUParameter() - adaptor.FirstUParameter()


def _axial_range(f: Face) -> tuple[float, float]:
    adaptor = BRepAdaptor_Surface(f.face, True)
    us = (adaptor.FirstUParameter(), adaptor.LastUParameter())
    vs = (adaptor.FirstVParameter(), adaptor.LastVParameter())
    values = [float(np.dot(_vec(adaptor.Value(u, v)), f.axis)) for u in us for v in vs]
    return min(values), max(values)


def _normal_at(f: Face, p: np.ndarray) -> np.ndarray | None:
    """The surface's unit normal at p, up to sign."""
    if f.type == "plane":
        return f.axis
    if f.type == "sphere":
        v = p - f.origin
    elif f.type == "cylinder":
        v = _radial(f, p)
    elif f.type == "torus":
        radial = _radial(f, p)
        v = radial - f.radius * radial / max(np.linalg.norm(radial), 1e-12) + np.dot(p - f.origin, f.axis) * f.axis
    elif f.type == "cone":
        radial = _radial(f, p)
        along = np.sign(np.dot(p - f.origin, f.axis)) * f.axis
        v = radial / max(np.linalg.norm(radial), 1e-12) * math.cos(f.radius2) - along * math.sin(f.radius2)
    else:
        return None
    length = np.linalg.norm(v)
    return v / length if length > 1e-12 else None


# --- holes -----------------------------------------------------------------------------


@dataclass
class Hole:
    kind: str  # "through hole" | "blind hole" | "hole" (no solid to tell by), or with a modifier below
    diameter: float  # of the narrowest bore
    axis: np.ndarray
    origin: np.ndarray  # the point on the axis nearest the world origin
    centre: np.ndarray
    faces: set[int]
    depth: float | None = None  # blind holes: from the opening to the bottom of the bore
    cbore: tuple[float, float] | None = None  # counterbore (diameter, depth)
    csink_deg: float | None = None  # countersink full angle
    stepped: tuple[float, ...] = ()  # the other diameters, if more than a counterbore

    def signature(self) -> tuple:
        return (self.kind, round(self.diameter, 5), round(self.depth or 0, 5), self.cbore and round(self.cbore[0], 5),
                self.csink_deg and round(self.csink_deg, 3))

    def params(self, decimals: int = 3) -> list[tuple[str, str]]:
        axis = _axis_name(self.axis)
        where = self.origin[[i for i in range(3) if i != "XYZ".index(axis)]] if axis else self.origin
        out = [("along", axis or fmt_point(self.axis, 4)), ("Ø", f"{self.diameter:.{decimals}f}"),
               ("at", fmt_point(where, decimals))]
        if self.depth is not None:
            out.append(("depth", f"{self.depth:.{decimals}f}"))
        if self.cbore:
            out += [("counterbore Ø", f"{self.cbore[0]:.{decimals}f}"), ("counterbore depth", f"{self.cbore[1]:.{decimals}f}")]
        if self.csink_deg is not None:
            out.append(("countersink", f"{self.csink_deg:.2f}°"))
        if self.stepped:
            out.append(("steps Ø", ", ".join(f"{d:.{decimals}f}" for d in self.stepped)))
        return out

    def describe(self, decimals: int = 3) -> str:
        return " ".join([self.kind, *(_show(k, v) for k, v in self.params(decimals))])


def _show(name: str, value: str) -> str:
    return f"{name}{value}" if name.endswith("Ø") else _param(name, value)


def _classifiers(solids: list) -> list:
    """One per solid: loading walks the whole solid, so it's done once, not once per probe."""
    out = []
    for solid in solids:
        classifier = BRepClass3d_SolidClassifier()
        classifier.Load(solid)
        out.append(classifier)
    return out


def _in_material(classifiers: list, point: np.ndarray) -> bool:
    for classifier in classifiers:
        classifier.Perform(gp_Pnt(*point), 1e-7)
        if classifier.State() == TopAbs_IN:
            return True
    return False


def _hole_from_stack(stack: list[tuple[float, float, Face, list[int]]], solids: list, same: float = _SAME) -> Hole | None:
    """stack: (axial start, axial end, a face, the faces it stands for), touching end to end."""
    cylinders = [s for s in stack if s[2].type == "cylinder"]
    if not cylinders:
        return None
    axis, origin = stack[0][2].axis, stack[0][2].origin
    origin = origin - np.dot(origin, axis) * axis
    lo, hi = stack[0][0], stack[-1][1]
    closed_lo = closed_hi = None
    if solids:
        closed_lo = _in_material(solids, origin + (lo - _PROBE_MM) * axis)
        closed_hi = _in_material(solids, origin + (hi + _PROBE_MM) * axis)

    narrow = min(cylinders, key=lambda s: s[2].radius)
    others = [s for s in cylinders if s[2].radius > narrow[2].radius + same]
    # A cone at an open end is a countersink; at a closed end it's a drill point, not part of the depth.
    cones = [s for s in stack if s[2].type == "cone"]
    open_cones = [s for s in cones if (s is stack[0] and closed_lo is False) or (s is stack[-1] and closed_hi is False)]
    csink = math.degrees(2 * open_cones[0][2].radius2) if open_cones else None

    if closed_lo is None:
        kind, depth = "hole", None
    elif closed_lo or closed_hi:
        kind = "blind hole"
        bores = cylinders + open_cones
        depth = max(s[1] for s in bores) - min(s[0] for s in bores)
    else:
        kind, depth = "through hole", None

    cbore, stepped = None, ()
    if len(others) == 1 and (others[0] is cylinders[0] or others[0] is cylinders[-1]):
        cbore = (2 * others[0][2].radius, others[0][1] - others[0][0])
        kind = "counterbored " + kind
    elif others:
        stepped = tuple(2 * s[2].radius for s in others)
        kind = "stepped " + kind
    if csink is not None:
        kind = "countersunk " + kind

    return Hole(kind, 2 * narrow[2].radius, axis, origin, origin + (lo + hi) / 2 * axis,
                {i for s in stack for i in s[3]}, depth, cbore, csink, stepped)


def find_holes(faces: list[Face], shape, floor: float = 0.0) -> list[Hole]:
    same = max(_SAME, _SAME_PER_FLOOR * floor)
    round_faces = [(i, f) for i, f in enumerate(faces) if f.type in ("cylinder", "cone") and _is_concave(f)]
    lines: list[list[tuple[int, Face]]] = []  # faces sharing one axis line
    for i, f in round_faces:
        for line in lines:
            g = line[0][1]
            if _angle(f.axis, g.axis) < _SAME_DIRECTION and np.linalg.norm(_line_offset(g.origin, f.origin, g.axis)) < same:
                line.append((i, f))
                break
        else:
            lines.append([(i, f)])

    solids = _classifiers(_unique(shape, TopAbs_SOLID))
    holes = []
    for line in lines:
        # Merge a split bore (same radius, overlapping along the axis) into full turns.
        rings: list[dict] = []
        for i, f in line:
            lo, hi = _axial_range(f)
            for ring in rings:
                g = ring["face"]
                if f.type == g.type and abs(f.radius - g.radius) < same and abs(f.radius2 - g.radius2) < same \
                        and lo < ring["hi"] - same and hi > ring["lo"] + same:
                    ring["sweep"] += _sweep(f)
                    ring["members"].append(i)
                    ring["lo"], ring["hi"] = min(lo, ring["lo"]), max(hi, ring["hi"])
                    break
            else:
                rings.append({"face": f, "lo": lo, "hi": hi, "sweep": _sweep(f), "members": [i]})
        rings = sorted((r for r in rings if r["sweep"] >= _FULL_TURN), key=lambda r: r["lo"])
        stack: list = []
        for ring in rings + [None]:
            if ring is None or (stack and ring["lo"] > stack[-1][1] + same):
                hole = _hole_from_stack(stack, solids, same) if stack else None
                if hole:
                    holes.append(hole)
                stack = []
            if ring is not None:
                stack.append((ring["lo"], ring["hi"], ring["face"], ring["members"]))
    return holes


# --- fillets ---------------------------------------------------------------------------


@dataclass
class Fillet:
    radius: float
    centre: np.ndarray
    area: float
    faces: set[int]

    def describe(self, decimals: int = 3) -> str:
        return f"fillet R{self.radius:.{decimals}f} near {fmt_point(self.centre)}"


def _blend_radius(f: Face) -> float | None:
    if f.type == "cylinder":
        return f.radius if _sweep(f) < _HALF_TURN else None
    if f.type == "torus":
        adaptor = BRepAdaptor_Surface(f.face, True)
        return f.radius2 if adaptor.LastVParameter() - adaptor.FirstVParameter() < _HALF_TURN else None
    return f.radius if f.type == "sphere" else None


def find_fillets(faces: list[Face], shape, holes: list[Hole]) -> list[Fillet]:
    in_hole = {i for h in holes for i in h.faces}
    radii = {i: r for i, f in enumerate(faces) if i not in in_hole and (r := _blend_radius(f)) is not None}
    if not radii:
        return []

    edges = ShapeMap()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    edge_faces: dict[int, list[int]] = {}
    for i, f in enumerate(faces):
        for edge in _unique(f.face, TopAbs_EDGE):
            edge_faces.setdefault(edges.FindIndex(edge), []).append(i)

    tangent: dict[int, int] = {}
    links: list[tuple[int, int]] = []
    for index, owners in edge_faces.items():
        owners = sorted(set(owners))
        if len(owners) != 2 or not any(o in radii for o in owners):
            continue
        a, b = owners
        if faces[a].type == faces[b].type and _surface_distance(faces[a], faces[b]) < _SAME:
            continue  # one surface split in two (a shaft in quarters): not a blend meeting a neighbour
        if a in radii and b in radii and abs(radii[a] - radii[b]) < _SAME:
            links.append((a, b))
        curve = BRepAdaptor_Curve(TopoDS.Edge(edges.FindKey(index)))
        p = _vec(curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2))
        na, nb = _normal_at(faces[a], p), _normal_at(faces[b], p)
        if na is not None and nb is not None and abs(float(np.dot(na, nb))) > _TANGENT:
            for o in owners:
                tangent[o] = tangent.get(o, 0) + 1

    blends = {i for i in radii if tangent.get(i, 0) >= 2}
    parent = {i: i for i in blends}

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in links:
        if a in blends and b in blends:
            parent[root(a)] = root(b)
    groups: dict[int, set[int]] = {}
    for i in blends:
        groups.setdefault(root(i), set()).add(i)

    out = []
    for members in groups.values():
        if all(faces[i].type == "sphere" for i in members):
            continue  # a ball end on a post: spheres only blend where edge fillets meet
        area = sum(faces[i].area for i in members)
        centre = sum(faces[i].centroid * faces[i].area for i in members) / area
        out.append(Fillet(radii[next(iter(members))], centre, area, members))
    return out


# --- comparing -------------------------------------------------------------------------


def _greedy(pairs: list[tuple[float, int, int]]) -> list[tuple[int, int]]:
    used_a, used_b, out = set(), set(), []
    for _, i, j in sorted(pairs, key=lambda p: p[0]):
        if i not in used_a and j not in used_b:
            out.append((i, j))
            used_a.add(i)
            used_b.add(j)
    return out


def _match_holes(old: list[Hole], new: list[Hole]) -> list[tuple[int, int]]:
    def same_direction(a: Hole, b: Hole) -> bool:  # one hole turned far off its axis is a different hole
        return _angle(a.axis, b.axis) <= _MAX_SAME_FACE_TILT

    near = []
    for i, a in enumerate(old):
        for j, b in enumerate(new):
            dist = float(np.linalg.norm(a.centre - b.centre))
            if dist <= 2 * max(a.diameter, b.diameter) and same_direction(a, b):
                near.append((dist + abs(a.diameter - b.diameter), i, j))
    pairs = _greedy(near)
    # A hole that moved further than its own size is still that hole, if it's the same hole.
    rest_old = [i for i in range(len(old)) if i not in {p[0] for p in pairs}]
    rest_new = [j for j in range(len(new)) if j not in {p[1] for p in pairs}]
    far = [(float(np.linalg.norm(old[i].centre - new[j].centre)), i, j) for i in rest_old for j in rest_new
           if old[i].signature() == new[j].signature() and same_direction(old[i], new[j])]
    return pairs + _greedy(far)


def _hole_changes(a: Hole, b: Hole) -> list[tuple[str, float]]:
    out = [("Ø", b.diameter - a.diameter)]
    tilt = _angle(a.axis, b.axis)
    if tilt > 0:
        out.append((f"tilted {math.degrees(tilt):.4g}°", tilt * max(a.diameter, b.diameter)))
    out.append(("axis", float(np.linalg.norm(_line_offset(a.origin, b.origin, a.axis)))))
    if a.depth is not None and b.depth is not None:
        out.append(("depth", b.depth - a.depth))
    if a.cbore and b.cbore:
        out += [("counterbore Ø", b.cbore[0] - a.cbore[0]), ("counterbore depth", b.cbore[1] - a.cbore[1])]
    return out


def _hole_text(a: Hole, b: Hole, changes: list[tuple[str, float]]) -> str:
    """'through hole along Z Ø8.0000 → 8.0020 at (50.0000, 25.0000) — Ø +2.0 µm'."""
    sized = [abs(m) for _, m in changes if m]
    decimals = decimals_for(min(sized)) if sized else 3
    old_params, new_params = dict(a.params(decimals)), dict(b.params(decimals))
    parts = []
    for name in dict.fromkeys([*old_params, *new_params]):
        va, vb = old_params.get(name), new_params.get(name)
        if va == vb:
            parts.append(_show(name, va))
        elif va is None:
            parts.append(f"{_show(name, vb)} (new)")
        elif vb is None:
            parts.append(f"{_show(name, va)} (gone)")
        else:
            parts.append(f"{_show(name, va)} → {vb}")
    details = []
    for what, m in changes:
        if what.startswith("tilted"):
            details.append(what)
        elif what == "axis":
            details.append(f"axis moved {fmt_len(m)}")
        else:
            details.append(f"{what} {fmt_len(m, signed=True)}")
    kind = a.kind if a.kind == b.kind else f"{a.kind} → {b.kind}"
    return " ".join([kind, *parts]) + (" — " + ", ".join(details) if details else "")


def _gap(face_ids: set[int], faces: list[Face], other) -> float:
    """How far these faces get from the other version's surface. Exporters split and retype the
    same surfaces (a rounded corner as two half-faces, or one), so features can fail to pair up
    although nothing changed: only a measured gap makes a feature's change real."""
    return max((face_gap(faces[i], other) for i in face_ids), default=0.0)


def compare_features(old_faces: list[Face], new_faces: list[Face], old_shape, new_shape,
                     floor: float) -> tuple[list[Finding], set[int], set[int]]:
    """(findings, old face indices they account for, new face indices they account for). Features
    that match unchanged account for nothing: anything found on their faces is reported as usual."""
    findings: list[Finding] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    old_part, new_part = skin_of(old_shape), skin_of(new_shape)

    tolerance = gap_tolerance(floor)

    def gone(face_ids: set[int]) -> float:
        """How far these old faces are from the new version; 0 if not measurably off it."""
        gap = _gap(face_ids, old_faces, new_part)
        return gap if gap > tolerance else 0.0

    def appeared(face_ids: set[int]) -> float:
        gap = _gap(face_ids, new_faces, old_part)
        return gap if gap > tolerance else 0.0

    old_holes, new_holes = find_holes(old_faces, old_shape, floor), find_holes(new_faces, new_shape, floor)
    pairs = _match_holes(old_holes, new_holes)
    for i, j in pairs:
        a, b = old_holes[i], new_holes[j]
        changes = [(w, m) for w, m in _hole_changes(a, b) if abs(m) > floor]
        if not changes and a.kind == b.kind:
            continue
        # Same bore on the same axis can't be a false pairing; its depth or end simply changed.
        same_bore = not any(what == "Ø" or what == "axis" or what.startswith("tilted") for what, _ in changes)
        if not same_bore and not gone(a.faces) and not appeared(b.faces):
            continue
        magnitude = max([abs(m) for _, m in changes], default=b.diameter)
        findings.append(Finding(_hole_text(a, b, changes), magnitude, tuple(map(float, b.centre))))
        used_old |= a.faces
        used_new |= b.faces
    for i in set(range(len(old_holes))) - {p[0] for p in pairs}:
        h = old_holes[i]
        gap = gone(h.faces)
        if not gap:
            continue
        findings.append(Finding(f"removed {h.describe()}", gap, tuple(map(float, h.centre)), "removed"))
        used_old |= h.faces
    for j in set(range(len(new_holes))) - {p[1] for p in pairs}:
        h = new_holes[j]
        gap = appeared(h.faces)
        if not gap:
            continue
        findings.append(Finding(f"added {h.describe()}", gap, tuple(map(float, h.centre)), "added"))
        used_new |= h.faces

    old_fillets, new_fillets = find_fillets(old_faces, old_shape, old_holes), find_fillets(new_faces, new_shape, new_holes)
    near = []
    for i, a in enumerate(old_fillets):
        for j, b in enumerate(new_fillets):
            dist = float(np.linalg.norm(a.centre - b.centre))
            if dist <= max(math.sqrt(a.area), math.sqrt(b.area)) + 2 * max(a.radius, b.radius):
                near.append((dist + abs(a.radius - b.radius), i, j))
    fillet_pairs = _greedy(near)
    for i, j in fillet_pairs:
        a, b = old_fillets[i], new_fillets[j]
        delta = b.radius - a.radius
        if abs(delta) > floor and (gone(a.faces) or appeared(b.faces)):
            d = decimals_for(abs(delta))
            findings.append(Finding(f"fillet R{a.radius:.{d}f} → {b.radius:.{d}f} near {fmt_point(b.centre)} — "
                                    f"R {fmt_len(delta, signed=True)}", abs(delta), tuple(map(float, b.centre))))
            used_old |= a.faces
            used_new |= b.faces
    for i in set(range(len(old_fillets))) - {p[0] for p in fillet_pairs}:
        f = old_fillets[i]
        gap = gone(f.faces)
        if not gap:
            continue
        findings.append(Finding(f"removed {f.describe()}", gap, tuple(map(float, f.centre)), "removed"))
        used_old |= f.faces
    for j in set(range(len(new_fillets))) - {p[1] for p in fillet_pairs}:
        f = new_fillets[j]
        gap = appeared(f.faces)
        if not gap:
            continue
        findings.append(Finding(f"added {f.describe()}", gap, tuple(map(float, f.centre)), "added"))
        used_new |= f.faces
    return findings, used_old, used_new
