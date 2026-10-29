"""Turn the analysis of each changed file (metrics per version, plus the precise geometric
comparison from theseus.diff) into a human-readable markdown report.

Two numbers decide how a difference is reported:
  - the detection limit (floor): below it a difference can't be told apart from numerical
    noise, so it isn't a difference at all. It comes from the comparison itself — kernel
    precision for exact B-reps, float precision or chord error for meshes.
  - the engineering threshold (Thresholds): above the floor, a difference bigger than this is
    "changed"; smaller ones are still reported, as "micro-changes". Nothing real is hidden.

So every file reads as exactly one of: changed, micro-change, or identical (within the floor).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from theseus.fmt import decimals_for, fmt_len

DEFAULT_ABS_THRESHOLD_MM = 0.01
DEFAULT_REL_THRESHOLD = 0.001  # 0.1%

# Hidden marker so the GitHub Action can find and update its own comment instead of
# posting a new one on every push.
REPORT_MARKER = "<!-- theseus-report -->"

MAX_LISTED_FINDINGS = 10


@dataclass(frozen=True)
class Thresholds:
    """How big a difference must be to be called a change rather than a micro-change.

    A length (a face offset, a bounding-box dimension, a hole diameter) is changed if it moves
    more than abs_mm — or, for sizes, by more than `rel` of the size. Volume is judged by the
    same yardstick: its change spread over the part's surface (the average thickness of
    material added or removed) against abs_mm, or relative to the volume against `rel`.
    """

    abs_mm: float = DEFAULT_ABS_THRESHOLD_MM
    rel: float = DEFAULT_REL_THRESHOLD


DEFAULT_THRESHOLDS = Thresholds()

SENSITIVITY = {
    "engineering": DEFAULT_THRESHOLDS,  # 10 µm / 0.1% — typical machining tolerances
    "fine": Thresholds(abs_mm=0.001, rel=0.0001),  # 1 µm / 0.01%
    "exact": Thresholds(abs_mm=0.0, rel=0.0),  # anything above the detection limit
}


def thresholds_for(sensitivity: str = "engineering", abs_mm: float | None = None, rel: float | None = None) -> Thresholds:
    base = SENSITIVITY[sensitivity]
    return Thresholds(abs_mm=base.abs_mm if abs_mm is None else abs_mm, rel=base.rel if rel is None else rel)


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


def _fmt_dims(dims, decimals: int = 2) -> str:
    return f"{tuple(round(d, decimals) for d in dims)} mm (dx, dy, dz)"


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


def _surface_area(m: dict) -> float:
    if m.get("area_mm2"):
        return m["area_mm2"]
    dx, dy, dz = _dims(m)  # older metrics without area: the bounding box's surface
    return max(2 * (dx * dy + dy * dz + dz * dx), 1e-12)


def _grade(delta: float, size: float, floor: float, thresholds: Thresholds) -> str | None:
    """None (noise), "micro" or "changed" for a length-valued difference."""
    delta = abs(delta)
    if delta <= floor:
        return None
    if delta > thresholds.abs_mm or (size > 0 and delta / size > thresholds.rel):
        return "changed"
    return "micro"


def _metric_rows(old: dict, new: dict, thresholds: Thresholds, floor: float) -> tuple[list[str], list[str]]:
    """(changed rows, micro notes) from the aggregate metrics."""
    rows, micro = [], []

    old_vol, new_vol = old["volume_mm3"], new["volume_mm3"]
    if (old_vol is None) != (new_vol is None):
        rows.append(f"- Volume: {_vol(old_vol)} → {_vol(new_vol)}")
    elif old_vol is not None:
        delta = new_vol - old_vol
        area = _surface_area(new)
        biggest = max(abs(old_vol), abs(new_vol))
        noise = biggest * max(old.get("volume_rel_error", 0), new.get("volume_rel_error", 0), 1e-12)
        if abs(delta) > max(floor * area, noise):
            # A volume change is judged as the average thickness of material it represents.
            if abs(delta) / area > thresholds.abs_mm or abs(delta) / biggest > thresholds.rel:
                pct = f", {delta / old_vol * 100:+.{max(1, decimals_for(abs(delta / old_vol * 100), 1))}f}%" \
                    if old_vol else ""
                rows.append(f"- Volume: {_fmt(old_vol)} → {_fmt(new_vol)} mm³ "
                            f"({'+' if delta >= 0 else ''}{_fmt(delta)} mm³{pct})")
            else:
                micro.append(f"Δvolume {_fmt_small(delta)} mm³ (≈ {fmt_len(abs(delta) / area)} averaged over the surface)")

    if old["solid_count"] != new["solid_count"]:
        rows.append(f"- Solid count: {old['solid_count']} → {new['solid_count']}")
    if old.get("open_bodies", 0) != new.get("open_bodies", 0):
        rows.append(f"- Open (non-closed) bodies: {old.get('open_bodies', 0)} → {new.get('open_bodies', 0)}")

    old_dims, new_dims = _dims(old), _dims(new)
    grades = [_grade(n - o, max(o, n), floor, thresholds) for o, n in zip(old_dims, new_dims)]
    worst = max(abs(n - o) for o, n in zip(old_dims, new_dims))
    if "changed" in grades:
        d = max(2, decimals_for(min(abs(n - o) for o, n in zip(old_dims, new_dims) if abs(n - o) > floor)))
        rows.append(f"- Bounding box: {tuple(round(v, d) for v in old_dims)} → {_fmt_dims(new_dims, d)}")
    elif "micro" in grades:
        micro.append(f"Δbbox {fmt_len(worst, signed=False)} max axis")

    old_com, new_com = old["com"], new["com"]
    if old_com is not None and new_com is not None:
        shift = max(abs(n - o) for o, n in zip(old_com, new_com))
        # Not relative: a centre of mass's coordinates depend on where the origin is.
        grade = _grade(shift, 0.0, floor, thresholds)
        if grade == "changed":
            d = max(2, decimals_for(shift))
            rows.append(f"- Centre of mass: {tuple(round(c, d) for c in old_com)} → "
                        f"{tuple(round(c, d) for c in new_com)} mm")
        elif grade == "micro":
            micro.append(f"Δcentre of mass {fmt_len(shift)} max axis")

    if old.get("valid") is not False and new.get("valid") is False:
        rows.append("- Validity: valid → **invalid**")
    return rows, micro


def _info_lines(old: dict, new: dict) -> list[str]:
    """Context that isn't a geometry change (format, units)."""
    info = []
    if old.get("format") and new.get("format") and old["format"] != new["format"]:
        info.append(f"- Format: {old['format'].upper()} → {new['format'].upper()}")
    # A format change already explains differing unit notes (STEP declares units, STL can't).
    elif old.get("units_note") != new.get("units_note") and new.get("units_note"):
        info.append(f"- Units: {old.get('units_note') or 'unknown'} → {new['units_note']}")
    return info


_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩"  # match the numbered markers on the difference image


def _finding_lines(findings: list[dict], thresholds: Thresholds, floor: float) -> list[str]:
    lines = []
    for i, f in enumerate(findings[:MAX_LISTED_FINDINGS]):
        small = _grade(f["magnitude_mm"], 0.0, floor, thresholds) == "micro"
        lines.append(f"  - {_NUMBERS[i]} {f['what']}" + (" *(micro)*" if small else ""))
    if len(findings) > MAX_LISTED_FINDINGS:
        rest = findings[MAX_LISTED_FINDINGS:]
        lines.append(f"  - …and {len(rest)} smaller difference{'s' if len(rest) > 1 else ''} "
                     f"(largest {fmt_len(rest[0]['magnitude_mm'])})")
    return lines


_AXIS_NAMES = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}


def _axis_text(axis) -> str:
    for vec, name in _AXIS_NAMES.items():
        if all(abs(a - v) < 1e-9 for a, v in zip(axis, vec)):
            return name
    return "(" + ", ".join(f"{a:.3f}" for a in axis) + ")"


def motion_text(motion: dict, floor: float = 0.0) -> str:
    """'+5.000 mm X', 'by (1.250, -3.500, 7.000) mm, rotated 30° about (0.577, 0.577, 0.577)'."""
    t = motion["translation"]
    moving = [i for i, v in enumerate(t) if abs(v) > max(floor, 1e-12)]
    parts = []
    if len(moving) == 1:
        parts.append(f"{fmt_len(t[moving[0]], signed=True)} {'XYZ'[moving[0]]}")
    elif moving:
        d = max(3, decimals_for(min(abs(t[i]) for i in moving)))
        parts.append("by (" + ", ".join(f"{v:+.{d}f}" for v in t) + ") mm")
    if motion["rotation_deg"]:
        parts.append(f"rotated {motion['rotation_deg']:.4g}° about {_axis_text(motion['axis'])}")
    return ", ".join(parts) or "repositioned"


def _limit_text(precise: dict) -> str:
    return f"{precise['method']}; detection limit: {precise['floor_note']}"


@dataclass
class Section:
    """One file's part of the report."""

    path: str
    old_path: str | None
    status: str  # new | deleted | changed | moved | micro | identical | unchanged
    suffix: str  # what follows the file name in its heading, e.g. " (changed)"
    lines: list[str]  # the body, as markdown bullets
    headline: str = ""  # one line for the summary table
    largest_mm: float | None = None

    def heading(self, html_names: bool = False) -> str:
        if html_names:
            name = f"<code>{escape(self.path)}</code>"
            if self.old_path and self.old_path != self.path:
                name += f" (renamed from <code>{escape(self.old_path)}</code>)"
            return name + escape(self.suffix)
        return _title(self.path, self.old_path) + self.suffix

    def markdown(self) -> str:
        return "\n".join([f"### {self.heading()}", *self.lines])


def _headline(findings: list[dict], rows: list[str]) -> str:
    if findings:
        return findings[0]["what"]
    return rows[0][2:] if rows else ""


def file_section(
    path: str,
    old: dict | None,
    new: dict | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    old_path: str | None = None,
    precise: dict | None = None,
) -> Section | None:
    """Classify one file's change and build its report section (None if neither version exists).

    precise: theseus.diff.PreciseDiff.to_dict() for the two versions, when available. Without
    it only aggregate metrics are compared, and small differences are listed as notes under
    "no significant change".
    """
    if old is None and new is None:
        return None

    if old is None:
        dims = _fmt_dims(_dims(new))
        return Section(path, old_path, "new", " (new file)", [*_summary_lines(new), *_warning_lines(new)],
                       headline=f"new — {_vol(new['volume_mm3'])}, {dims}")

    if new is None:
        was = f"{_vol(old['volume_mm3'])}, {_fmt_dims(_dims(old))}, {old['solid_count']} solid(s)"
        return Section(path, old_path, "deleted", " (deleted)", [f"- Was: {was}"], headline=f"deleted (was {was})")

    floor = precise["floor_mm"] if precise else 0.0
    scale = max(max(abs(v) for v in old["bbox"]), max(abs(v) for v in new["bbox"]), 1.0)
    rows, micro = _metric_rows(old, new, thresholds, max(floor, scale * 1e-12))
    info = _info_lines(old, new)
    warnings = _warning_lines(new)

    if precise is None:
        if not rows:
            return Section(path, old_path, "unchanged", " — no significant change",
                           [*info, *[f"- {m} (below noise threshold)" for m in micro], *warnings],
                           headline="no significant change")
        return Section(path, old_path, "changed", " (changed)", [*rows, *info, *warnings], headline=_headline([], rows))

    findings = sorted(precise["findings"], key=lambda f: -f["magnitude_mm"])
    notes = [f"- {n}" for n in precise.get("notes", [])]
    if precise["identical"] and not rows:
        # Metric differences above the floor can't coexist with identical geometry; whatever
        # remains is integration noise, so the precise verdict stands.
        within = "byte-for-byte" if precise["method"] == "byte-identical" else \
            f"to within {fmt_len(precise['floor_mm'])}, {precise['method']}"
        return Section(path, old_path, "identical", f" — identical ({within})", [*notes, *info, *warnings],
                       headline="identical" + (" (re-export)" if precise["method"] != "byte-identical" else ""))

    if precise.get("motion"):
        how = (f"identical once moved back — {precise['method']}, "
               f"detection limit {fmt_len(precise['floor_mm'])}")
        moved = motion_text(precise["motion"], floor)
        return Section(path, old_path, "moved", f" — moved ({moved}; shape unchanged)",
                       [f"- Shape: {how}", *info, *warnings], headline=f"moved {moved}, shape unchanged")

    largest = max([precise["max_deviation_mm"]] + [f["magnitude_mm"] for f in findings])
    changed_findings = [f for f in findings if _grade(f["magnitude_mm"], 0.0, floor, thresholds) == "changed"]
    geometry = []
    if findings:
        n = len(findings)
        geometry = [f"- Geometry: {n} difference{'s' if n > 1 else ''} ({_limit_text(precise)}):",
                    *_finding_lines(findings, thresholds, floor)]
    if rows or changed_findings:
        return Section(path, old_path, "changed", " (changed)",
                       [*rows, *geometry, *[f"- {m}" for m in micro], *notes, *info, *warnings],
                       headline=_headline(findings, rows), largest_mm=largest)

    suffix = (f" — micro-change only (largest {fmt_len(largest)}, under the "
              f"{fmt_len(thresholds.abs_mm)} reporting threshold)")
    return Section(path, old_path, "micro", suffix, [*geometry, *[f"- {m}" for m in micro], *notes, *info, *warnings],
                   headline=_headline(findings, rows), largest_mm=largest)


def diff_one_file(
    path: str,
    old: dict | None,
    new: dict | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    old_path: str | None = None,
    precise: dict | None = None,
) -> str:
    """Return a markdown section describing the change to a single file (see file_section)."""
    section = file_section(path, old, new, thresholds, old_path, precise)
    return section.markdown() if section else ""


# Order of files in the summary: what needs a reviewer's eyes first.
_STATUS_ORDER = ["changed", "moved", "new", "deleted", "micro", "unchanged", "identical"]
_STATUS_LABEL = {"changed": "**changed**", "moved": "moved", "new": "new", "deleted": "deleted",
                 "micro": "micro-change", "unchanged": "no significant change", "identical": "identical"}
_OPEN = {"changed", "moved", "new", "deleted"}  # sections expanded by default
_HEADLINE_CHARS = 110


def _cell(text: str) -> str:
    text = text if len(text) <= _HEADLINE_CHARS else text[: _HEADLINE_CHARS - 1] + "…"
    return text.replace("|", "\\|").replace("\n", " ")


def _image_block(images: dict[str, str]) -> list[str]:
    shown = [(label, images[key]) for key, label in (("before", "Before"), ("after", "After"), ("diff", "Difference"))
             if key in images]
    if not shown:
        return []
    lines = ["", "| " + " | ".join(label for label, _ in shown) + " |",
             "|" + ":-:|" * len(shown),
             "| " + " | ".join(f"[![{label}]({url})]({url})" for label, url in shown) + " |", ""]
    extras = [f"[{label}]({images[key]})" for key, label in (("views", "standard views"), ("viewer", "interactive 3D viewer"))
              if key in images]
    if extras:
        lines += ["Also: " + " · ".join(extras), ""]
    return lines


def build_report(
    results: dict[str, tuple],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    renamed_from: dict[str, str] | None = None,
    images: dict[str, dict[str, str]] | None = None,
    footer: str | None = None,
) -> str:
    """results: path -> (old_metrics_or_None, new_metrics_or_None[, precise_or_None]).
    renamed_from: new path -> old path, for files renamed in this change.
    images: path -> {"before"/"after"/"diff"/"views"/"viewer": URL or relative path}.
    footer: a closing note (e.g. where to download images that couldn't be shown inline)."""
    renamed_from = renamed_from or {}
    images = images or {}
    sections = [file_section(path, value[0], value[1], thresholds, renamed_from.get(path),
                             value[2] if len(value) > 2 else None)
                for path, value in results.items()]
    sections = sorted((s for s in sections if s), key=lambda s: _STATUS_ORDER.index(s.status))

    out = [REPORT_MARKER, "## theseus report", ""]
    if not sections:
        out.append("No CAD file changes to report.")
    else:
        out += ["| File | Result | Largest difference | What changed |", "|---|---|---|---|"]
        for s in sections:
            largest = fmt_len(s.largest_mm) if s.largest_mm else "—"
            out.append(f"| `{_cell(s.path)}` | {_STATUS_LABEL[s.status]} | {largest} | {_cell(s.headline)} |")
        for s in sections:
            out += ["", f"<details{' open' if s.status in _OPEN else ''}>",
                    f"<summary>{s.heading(html_names=True)}</summary>", "",
                    *_image_block(images.get(s.path, {})), *s.lines, "", "</details>"]
    if footer:
        out += ["", footer]
    return "\n".join(out) + "\n"
