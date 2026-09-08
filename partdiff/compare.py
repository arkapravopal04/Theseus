"""Turn two metrics dicts (see partdiff.metrics) into a human-readable markdown diff.

This is a volume/bounding-box/centre-of-mass diff, not a true geometric diff:
two different parts that happen to share the same volume will read as "no change".
"""

from __future__ import annotations

# Below these thresholds a numeric change is treated as tessellation/float noise,
# not a real design change.
ABS_THRESHOLD_MM = 0.01
REL_THRESHOLD = 0.001  # 0.1%


def _changed(old_val: float, new_val: float) -> bool:
    abs_diff = abs(new_val - old_val)
    if abs_diff <= ABS_THRESHOLD_MM:
        return False
    denom = max(abs(old_val), abs(new_val), 1e-9)
    return (abs_diff / denom) > REL_THRESHOLD


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def diff_one_file(path: str, old: dict | None, new: dict | None) -> str:
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
    vol_changed = _changed(old["volume_mm3"], new["volume_mm3"])
    if vol_changed:
        delta = new["volume_mm3"] - old["volume_mm3"]
        pct = (delta / old["volume_mm3"] * 100) if old["volume_mm3"] else float("inf")
        rows.append(
            f"- Volume: {_fmt(old['volume_mm3'])} → {_fmt(new['volume_mm3'])} mm³ "
            f"({'+' if delta >= 0 else ''}{_fmt(delta)} mm³, {pct:+.1f}%)"
        )

    if old["solid_count"] != new["solid_count"]:
        rows.append(f"- Solid count: {old['solid_count']} → {new['solid_count']}")

    old_bbox = old["bbox"]
    new_bbox = new["bbox"]
    old_dims = tuple(old_bbox[i + 3] - old_bbox[i] for i in range(3))
    new_dims = tuple(new_bbox[i + 3] - new_bbox[i] for i in range(3))
    if any(_changed(o, n) for o, n in zip(old_dims, new_dims)):
        rows.append(
            f"- Bounding box: {tuple(round(d, 2) for d in old_dims)} → "
            f"{tuple(round(d, 2) for d in new_dims)} mm (dx, dy, dz)"
        )

    old_com = old["com"]
    new_com = new["com"]
    if any(_changed(o, n) for o, n in zip(old_com, new_com)):
        rows.append(
            f"- Centre of mass: {tuple(round(c, 2) for c in old_com)} → "
            f"{tuple(round(c, 2) for c in new_com)} mm"
        )

    if not rows:
        return f"### `{path}` — no significant change"

    return "\n".join([f"### `{path}` (changed)", *rows])


def build_report(results: dict[str, tuple[dict | None, dict | None]]) -> str:
    """results: path -> (old_metrics_or_None, new_metrics_or_None)."""
    sections = [diff_one_file(path, old, new) for path, (old, new) in results.items()]
    sections = [s for s in sections if s]

    header = "## partdiff report\n"
    if not sections:
        return header + "\nNo CAD file changes to report.\n"

    note = (
        "\n*Volume/bounding-box based diff — not a topological diff. "
        "Two different parts with equal volume will report as unchanged.*\n"
    )
    return header + "\n" + "\n\n".join(sections) + "\n" + note
