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

# Hidden marker so the GitHub Action can find and update its own comment instead of
# posting a new one on every push.
REPORT_MARKER = "<!-- theseus-report -->"


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


def _vol(v: float | None) -> str:
    return "n/a" if v is None else f"{_fmt(v)} mm³"


def _dims(m: dict) -> tuple[float, float, float]:
    b = m["bbox"]
    return tuple(b[i + 3] - b[i] for i in range(3))


def _fmt_dims(dims) -> str:
    return f"{tuple(round(d, 2) for d in dims)} mm (dx, dy, dz)"


def _title(path: str, old_path: str | None) -> str:
    if old_path and old_path != path:
        return f"`{path}` (renamed from `{old_path}`)"
    return f"`{path}`"


def _summary_lines(m: dict) -> list[str]:
    lines = [
        f"- Volume: {_vol(m['volume_mm3'])}",
        f"- Bounding box: {_fmt_dims(_dims(m))}",
        f"- Solids: {m['solid_count']}",
    ]
    if m.get("units_note"):
        lines.append(f"- Units: {m['units_note']}")
    return lines


def _warning_lines(m: dict) -> list[str]:
    return [f"- ⚠ {w}" for w in m.get("warnings", [])]


def diff_one_file(
    path: str,
    old: dict | None,
    new: dict | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    old_path: str | None = None,
) -> str:
    """Return a markdown section describing the change to a single file.

    old_path: the file's previous path, when it was renamed.
    """
    if old is None and new is None:
        return ""
    title = _title(path, old_path)

    if old is None:
        return "\n".join([f"### {title} (new file)", *_summary_lines(new), *_warning_lines(new)])

    if new is None:
        was = f"{_vol(old['volume_mm3'])}, {_fmt_dims(_dims(old))}, {old['solid_count']} solid(s)"
        return "\n".join([f"### {title} (deleted)", f"- Was: {was}"])

    rows = []
    noted = []  # sub-threshold deltas: real but too small to call a change
    info = []  # context that isn't a geometry change (format, units)

    if old.get("format") and new.get("format") and old["format"] != new["format"]:
        info.append(f"- Format: {old['format'].upper()} → {new['format'].upper()}")

    old_vol, new_vol = old["volume_mm3"], new["volume_mm3"]
    if old_vol is None or new_vol is None:
        if (old_vol is None) != (new_vol is None):
            rows.append(f"- Volume: {_vol(old_vol)} → {_vol(new_vol)}")
    else:
        vol_delta = new_vol - old_vol
        if _changed_volume(old_vol, new_vol, thresholds):
            pct = f", {vol_delta / old_vol * 100:+.1f}%" if old_vol else ""
            rows.append(
                f"- Volume: {_fmt(old_vol)} → {_fmt(new_vol)} mm³ "
                f"({'+' if vol_delta >= 0 else ''}{_fmt(vol_delta)} mm³{pct})"
            )
        elif abs(vol_delta) > 1e-9:
            noted.append(f"Δvolume {_fmt_small(vol_delta)} mm³ (below noise threshold)")

    if old["solid_count"] != new["solid_count"]:
        rows.append(f"- Solid count: {old['solid_count']} → {new['solid_count']}")
    if old.get("open_bodies", 0) != new.get("open_bodies", 0):
        rows.append(f"- Open (non-closed) bodies: {old.get('open_bodies', 0)} → {new.get('open_bodies', 0)}")

    old_dims, new_dims = _dims(old), _dims(new)
    if any(_changed_length(o, n, thresholds) for o, n in zip(old_dims, new_dims)):
        rows.append(
            f"- Bounding box: {tuple(round(d, 2) for d in old_dims)} → "
            f"{tuple(round(d, 2) for d in new_dims)} mm (dx, dy, dz)"
        )
    else:
        max_bbox_delta = max(abs(n - o) for o, n in zip(old_dims, new_dims))
        if max_bbox_delta > 1e-9:
            noted.append(f"Δbbox {_fmt_small(max_bbox_delta)} mm max axis (below noise threshold)")

    old_com, new_com = old["com"], new["com"]
    if old_com is not None and new_com is not None:
        if any(_changed_length(o, n, thresholds) for o, n in zip(old_com, new_com)):
            rows.append(
                f"- Centre of mass: {tuple(round(c, 2) for c in old_com)} → "
                f"{tuple(round(c, 2) for c in new_com)} mm"
            )
        else:
            max_com_delta = max(abs(n - o) for o, n in zip(old_com, new_com))
            if max_com_delta > 1e-9:
                noted.append(f"Δcentre of mass {_fmt_small(max_com_delta)} mm max axis (below noise threshold)")

    if old.get("valid") is not False and new.get("valid") is False:
        rows.append("- Validity: valid → **invalid**")
    # A format change already explains differing unit notes (STEP declares units, STL can't).
    if not info and old.get("units_note") != new.get("units_note") and new.get("units_note"):
        info.append(f"- Units: {old.get('units_note') or 'unknown'} → {new['units_note']}")

    if not rows:
        header = f"### {title} — no significant change"
        return "\n".join([header, *info, *[f"- {n}" for n in noted], *_warning_lines(new)])

    return "\n".join([f"### {title} (changed)", *rows, *info, *_warning_lines(new)])


def build_report(
    results: dict[str, tuple[dict | None, dict | None]],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    renamed_from: dict[str, str] | None = None,
) -> str:
    """results: path -> (old_metrics_or_None, new_metrics_or_None).
    renamed_from: new path -> old path, for files renamed in this change."""
    renamed_from = renamed_from or {}
    sections = [
        diff_one_file(path, old, new, thresholds, renamed_from.get(path))
        for path, (old, new) in results.items()
    ]
    sections = [s for s in sections if s]

    header = REPORT_MARKER + "\n## theseus report\n"
    if not sections:
        return header + "\nNo CAD file changes to report.\n"

    note = (
        "\n*Volume/bounding-box based diff — not a topological diff. "
        "Two different parts with equal volume will report as unchanged.*\n"
    )
    return header + "\n" + "\n\n".join(sections) + "\n" + note
