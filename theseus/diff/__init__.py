"""Precise geometric comparison: find the smallest real difference between two versions of a
part, say where it is, and state the detection limit below which a difference can't be told
apart from numerical noise.

Aggregate metrics are blunt — a hole moving 0.5 mm sideways changes neither volume nor
bounding box — so this compares the geometry itself:
  - exact B-rep vs exact B-rep (theseus.diff.brep): every face matched by surface type and
    analytic parameters (plane offset, cylinder radius and axis, ...), every vertex matched by
    position, free-form faces measured by projecting onto the other surface.
  - anything involving a mesh (theseus.diff.surface): per-vertex deltas when both meshes share
    connectivity, otherwise the surface deviation between them in both directions.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from theseus.model import Model

MAX_FINDINGS = 200  # kept in the result; the report shows fewer


@dataclass
class Finding:
    what: str  # human-readable, e.g. "cylinder Ø8.0000 → Ø8.0040 along Z at (20.000, 10.000)"
    magnitude_mm: float  # how far the geometry moved, for deciding how loudly to report it
    at: tuple[float, float, float] | None = None
    # "surface" (a face's own geometry changed), "added"/"removed" (a face appeared/went),
    # "region" (a mesh area that deviates), or a summary: "retrim", "vertices".
    kind: str = "surface"


@dataclass
class PreciseDiff:
    method: str  # what was compared, e.g. "exact B-rep", "mesh vertices", "surface deviation"
    floor_mm: float  # detection limit: differences at or below this are indistinguishable from noise
    floor_note: str  # where the limit comes from
    identical: bool
    max_deviation_mm: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = sorted(d["findings"], key=lambda f: -f["magnitude_mm"])[:MAX_FINDINGS]
        return d


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def precise_diff(old: Model, new: Model) -> PreciseDiff:
    if _sha256(old.path) == _sha256(new.path):
        return PreciseDiff("byte-identical", 0.0, "the files are byte-for-byte identical", True)

    if old.kind == "brep" and new.kind == "brep":
        from theseus.diff.brep import compare_breps

        result = compare_breps(old, new)
    else:
        from theseus.diff.surface import compare_surfaces

        result = compare_surfaces(old, new)
    if result.identical:
        result.notes.insert(0, "file contents differ but the geometry is identical (re-export or metadata change)")
    return result
