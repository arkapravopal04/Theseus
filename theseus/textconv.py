"""`theseus textconv FILE`: a CAD file as stable, line-by-line text, for `git diff`.

Git can show any binary file as text through a "textconv" driver (`theseus setup git` installs
this one). The summary is built so that a real design change shows up as a changed line: the
headline numbers, then every face as its surface type and dimensions, sorted and counted, printed
to 6 decimals (1 nm). A 2 µm larger hole is one line changing from Ø8.000000 to Ø8.004000; a
re-export of the same geometry changes nothing.
"""

from __future__ import annotations

import collections
import hashlib
import os
import tempfile

import numpy as np

from theseus.gitops import LFS_POINTER_PREFIX, _resolve_lfs
from theseus.load import LoadError, load_model
from theseus.metrics import compute_metrics

_DECIMALS = 6


def _point(p) -> str:
    return "(" + ", ".join(f"{v:.{_DECIMALS}f}" for v in p) + ")"


def _face_lines(shape) -> list[str]:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    from theseus.diff.brep import _describe_face, _unique

    faces = collections.Counter(_describe_face(TopoDS.Face(f)).describe(_DECIMALS) for f in _unique(shape, TopAbs_FACE))
    return [f"  {count}× {text}" if count > 1 else f"  {text}" for text, count in sorted(faces.items())]


def _mesh_lines(mesh) -> list[str]:
    # Vertex positions rounded to 1 nm, order-independent: any moved vertex changes the fingerprint.
    rounded = np.round(np.asarray(mesh.vertices, dtype=float), _DECIMALS)
    rows = np.unique(rounded, axis=0)
    fingerprint = hashlib.sha256(rows.tobytes()).hexdigest()[:16]
    return [f"  triangles: {len(mesh.faces)}", f"  vertices: {len(mesh.vertices)}",
            f"  geometry fingerprint: {fingerprint}"]


def _mm(value, unit: str = "mm") -> str:
    return "n/a" if value is None else f"{value:.{_DECIMALS}f} {unit}"


def _load(path: str):
    """Load the file; a Git LFS pointer (what git hands textconv for an LFS-stored blob) is
    swapped for the real content first."""
    with open(path, "rb") as f:
        head = f.read(len(LFS_POINTER_PREFIX))
    if head != LFS_POINTER_PREFIX:
        return load_model(path)
    with open(path, "rb") as f:
        content = _resolve_lfs(f.read(), os.path.basename(path))
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, os.path.basename(path))
        with open(real, "wb") as f:
            f.write(content)
        return load_model(real)


def summary(path: str) -> str:
    try:
        model = _load(path)
    except (LoadError, OSError) as e:
        return f"theseus: couldn't read this file as CAD ({e})\n"
    m = compute_metrics(model)
    lo, hi = m["bbox"][:3], m["bbox"][3:]
    size = " × ".join(f"{b - a:.{_DECIMALS}f}" for a, b in zip(lo, hi))
    kind = "exact B-rep" if m["kind"] == "brep" else "triangle mesh"
    lines = [
        "theseus summary",
        f"format: {m['format']} ({kind}); {m['units_note']}",
        f"volume: {_mm(m['volume_mm3'], 'mm³')}",
        f"surface area: {_mm(m['area_mm2'], 'mm²')}",
        f"size: {size} mm",
        f"bounding box: {_point(lo)} to {_point(hi)} mm",
        f"centre of mass: {'n/a' if m['com'] is None else _point(m['com']) + ' mm'}",
        f"solids: {m['solid_count']}; open bodies: {m['open_bodies']}"
        + ("" if m["valid"] is None else f"; valid: {'yes' if m['valid'] else 'NO'}"),
        *[f"warning: {w}" for w in m["warnings"]],
        "faces:" if model.kind == "brep" else "mesh:",
        *(_face_lines(model.shape) if model.kind == "brep" else _mesh_lines(model.mesh)),
    ]
    return "\n".join(lines) + "\n"
