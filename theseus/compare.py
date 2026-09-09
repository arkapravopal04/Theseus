"""Turn two metrics dicts (see theseus.metrics) into a human-readable markdown diff.

This is a volume/bounding-box/centre-of-mass diff, not a true geometric diff:
two different parts that happen to share the same volume will read as "no change".
"""

from __future__ import annotations

from dataclasses import dataclass

# Below these thresholds a numeric change is treated as tessellation/float noise, not a
# real design change. Length-based metrics (bbox dimensions, centre of mass) are plain mm
# values, so the absolute floor applies to them directly. Volume is a cubic quantity — mm^3,
# not mm — so reusing the same raw number as its absolute floor is a unit mismatch (see
# Thresholds.abs_mm3 below for the fix).
DEFAULT_ABS_THRESHOLD_MM = 0.01
DEFAULT_REL_THRESHOLD = 0.001  # 0.1%


@dataclass(frozen=True)
class Thresholds:
    """Noise thresholds for deciding whether a metric change is real.

    abs_mm / rel apply to length-valued metrics (bbox dimensions, centre of mass) directly.
    Volume is cubic, so its own absolute floor (abs_mm3) is derived by cubing abs_mm rather
    than reusing it as-is — a change smaller than a cube of side abs_mm in every direction is
    the noise floor for volume, not abs_mm of volume itself.
    """

    abs_mm: float = DEFAULT_ABS_THRESHOLD_MM
    rel: float = DEFAULT_REL_THRESHOLD

    @property
    def abs_mm3(self) -> float:
        return self.abs_mm**3


DEFAULT_THRESHOLDS = Thresholds()


def _changed(old_val: float, new_val: float, *, abs_threshold: float, rel_threshold: float) -> bool:
    abs_diff = abs(new_val - old_val)
    if abs_diff <= abs_threshold:
        return False
    denom = max(abs(old_val), abs(new_val), 1e-9)
    return (abs_diff / denom) > rel_threshold


def _changed_length(old_val: float, new_val: float, thresholds: Thresholds) -> bool:
    return _changed(old_val, new_val, abs_threshold=thresholds.abs_mm, rel_threshold=thresholds.rel)


def _changed_volume(old_val: float, new_val: float, thresholds: Thresholds) -> bool:
    return _changed(old_val, new_val, abs_threshold=thresholds.abs_mm3, rel_threshold=thresholds.rel)


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def _fmt_small(v: float) -> str:
    """Format a sub-threshold delta, which can be far smaller than _fmt's 3 decimals show."""
    return f"{v:+.6g}"


def diff_one_file(
    path: str,
    old: dict | None,
    new: dict | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> str:
    """Return a markdown section describing the change to a single file."""
    if old is None and new is None:
        return ""

    if old is None:
        lines = [f"### `{path}` (new file)"]
        lines.append(f"- Volume: {_fmt(new['volume_mm3'])} mm³")
        lines.append(f"- Solids: {new['solid_count']}")
        return "\n".join(lines)

    if new is None:
        lines = [f"### `{path}` (deleted)"]
        lines.append(f"- Was: {_fmt(old['volume_mm3'])} mm³, {old['solid_count']} solid(s)")
        return "\n".join(lines)

    rows = []
    noted = []  # sub-threshold deltas: real but too small to call a change

    vol_delta = new["volume_mm3"] - old["volume_mm3"]
    if _changed_volume(old["volume_mm3"], new["volume_mm3"], thresholds):
        pct = (vol_delta / old["volume_mm3"] * 100) if old["volume_mm3"] else float("inf")
        rows.append(
            f"- Volume: {_fmt(old['volume_mm3'])} → {_fmt(new['volume_mm3'])} mm³ "
            f"({'+' if vol_delta >= 0 else ''}{_fmt(vol_delta)} mm³, {pct:+.1f}%)"
        )
    elif abs(vol_delta) > 1e-9:
        noted.append(f"Δvolume {_fmt_small(vol_delta)} mm³ (below noise threshold)")

    if old["solid_count"] != new["solid_count"]:
        rows.append(f"- Solid count: {old['solid_count']} → {new['solid_count']}")

    old_bbox = old["bbox"]
    new_bbox = new["bbox"]
    old_dims = tuple(old_bbox[i + 3] - old_bbox[i] for i in range(3))
    new_dims = tuple(new_bbox[i + 3] - new_bbox[i] for i in range(3))
    if any(_changed_length(o, n, thresholds) for o, n in zip(old_dims, new_dims)):
        rows.append(
            f"- Bounding box: {tuple(round(d, 2) for d in old_dims)} → "
            f"{tuple(round(d, 2) for d in new_dims)} mm (dx, dy, dz)"
        )
    else:
        max_bbox_delta = max(abs(n - o) for o, n in zip(old_dims, new_dims))
        if max_bbox_delta > 1e-9:
            noted.append(f"Δbbox {_fmt_small(max_bbox_delta)} mm max axis (below noise threshold)")

    old_com = old["com"]
    new_com = new["com"]
    if any(_changed_length(o, n, thresholds) for o, n in zip(old_com, new_com)):
        rows.append(
            f"- Centre of mass: {tuple(round(c, 2) for c in old_com)} → "
            f"{tuple(round(c, 2) for c in new_com)} mm"
        )
    else:
        max_com_delta = max(abs(n - o) for o, n in zip(old_com, new_com))
        if max_com_delta > 1e-9:
            noted.append(f"Δcentre of mass {_fmt_small(max_com_delta)} mm max axis (below noise threshold)")

    if not rows:
        header = f"### `{path}` — no significant change"
        if noted:
            return "\n".join([header, *[f"- {n}" for n in noted]])
        return header

    return "\n".join([f"### `{path}` (changed)", *rows])


def build_report(
    results: dict[str, tuple[dict | None, dict | None]],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> str:
    """results: path -> (old_metrics_or_None, new_metrics_or_None)."""
    sections = [diff_one_file(path, old, new, thresholds) for path, (old, new) in results.items()]
    sections = [s for s in sections if s]

    header = "## theseus report\n"
    if not sections:
        return header + "\nNo CAD file changes to report.\n"

    note = (
        "\n*Volume/bounding-box based diff — not a topological diff. "
        "Two different parts with equal volume will report as unchanged.*\n"
    )
    return header + "\n" + "\n\n".join(sections) + "\n" + note
