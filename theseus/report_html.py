"""The report as a web page, for comparing files on your own machine (`theseus compare`,
`theseus diff`, `theseus app`).

It is built from the same Sections as the PR comment (theseus.compare.file_section), so the
browser view and the comment always say the same thing.
"""

from __future__ import annotations

import html
import re

import theseus
from theseus.compare import DEFAULT_THRESHOLDS, Section, Thresholds, file_section
from theseus.fmt import fmt_len

_ORDER = ["changed", "moved", "new", "deleted", "micro", "unchanged", "identical"]
STATUS_LABEL = {"changed": "changed", "moved": "moved", "new": "new", "deleted": "deleted",
          "micro": "micro-change", "unchanged": "no significant change", "identical": "identical"}


def _inline(text: str) -> str:
    """The little markdown the report lines use (`code`, **bold**, *italic*), as HTML."""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)


def _bullets(lines: list[str]) -> str:
    """Two-level markdown bullet lists ("- a", "  - b") as nested <ul>s."""
    out, open_sub = ["<ul>"], False
    for line in lines:
        if line.startswith("  - "):
            if not open_sub:
                out.append("<ul>")
                open_sub = True
            out.append(f"<li>{_inline(line[4:])}</li>")
        elif line.startswith("- "):
            if open_sub:
                out.append("</ul>")
                open_sub = False
            out.append(f"<li>{_inline(line[2:])}")
    if open_sub:
        out.append("</ul>")
    out.append("</ul>")
    return "\n".join(out)


def _images(images: dict[str, str]) -> str:
    cells = [f'<figure><a href="{html.escape(images[k])}"><img src="{html.escape(images[k])}" alt="{label}"></a>'
             f"<figcaption>{label}</figcaption></figure>"
             for k, label in (("before", "Before"), ("after", "After"), ("diff", "Difference")) if k in images]
    links = [f'<a class="button" href="{html.escape(images[k])}">{label}</a>'
             for k, label in (("viewer", "Open interactive 3D view"), ("views", "Standard views")) if k in images]
    return (f'<div class="images">{"".join(cells)}</div>' if cells else "") + \
        (f'<p class="links">{" ".join(links)}</p>' if links else "")


def _section(s: Section, images: dict[str, str]) -> str:
    detail = s.suffix.strip(" —()")
    detail = "" if detail in (s.status, STATUS_LABEL[s.status]) else f'<p class="detail">{_inline(detail)}</p>'
    renamed = (f' <span class="detail">renamed from <code>{html.escape(s.old_path)}</code></span>'
               if s.old_path and s.old_path != s.path else "")
    return (f'<section class="file {s.status}" id="{html.escape(s.path)}">'
            f'<h2><code>{html.escape(s.path)}</code> <span class="badge {s.status}">{STATUS_LABEL[s.status]}</span>{renamed}</h2>'
            f"{detail}{_images(images)}{_bullets(s.lines)}</section>")


def render_html(
    results: dict[str, tuple],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    renamed_from: dict[str, str] | None = None,
    images: dict[str, dict[str, str]] | None = None,
    errors: list[str] = (),
    title: str = "theseus report",
    subtitle: str = "",
) -> str:
    """results: path -> (old_metrics, new_metrics[, precise]), as for compare.build_report.
    images: path -> {"before"/"after"/"diff"/"views"/"viewer": link relative to the page}."""
    renamed_from, images = renamed_from or {}, images or {}
    sections = [file_section(p, v[0], v[1], thresholds, renamed_from.get(p), v[2] if len(v) > 2 else None)
                for p, v in results.items()]
    sections = sorted((s for s in sections if s), key=lambda s: _ORDER.index(s.status))

    rows = "".join(
        f'<tr><td><a href="#{html.escape(s.path)}"><code>{html.escape(s.path)}</code></a></td>'
        f'<td><span class="badge {s.status}">{STATUS_LABEL[s.status]}</span></td>'
        f"<td>{fmt_len(s.largest_mm) if s.largest_mm else '—'}</td><td>{_inline(s.headline)}</td></tr>"
        for s in sections)
    body = (f"<table><thead><tr><th>File</th><th>Result</th><th>Largest difference</th><th>What changed</th></tr>"
            f"</thead><tbody>{rows}</tbody></table>" + "".join(_section(s, images.get(s.path, {})) for s in sections)
            if sections else "<p>No CAD file changes to report.</p>")
    if errors:
        body += "<section class='file'><h2>Skipped</h2>" + _bullets([f"- {e}" for e in errors]) + "</section>"

    return _PAGE.format(title=html.escape(title), subtitle=html.escape(subtitle), body=body,
                        version=html.escape(theseus.__version__))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#111827; --muted:#6b7280; --line:#e5e7eb; --accent:#2563eb;
        --changed:#b45309; --moved:#7c3aed; --new:#047857; --deleted:#b91c1c; --micro:#0369a1; --same:#6b7280; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#171a21; --text:#e5e7eb; --muted:#9ca3af;
        --line:#2a2f3a; --accent:#60a5fa; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0; font-size:22px; }} .subtitle {{ color:var(--muted); margin:4px 0 20px; word-break:break-all; }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px;
        overflow:hidden; margin-bottom:24px; }}
th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:13px; color:var(--muted); font-weight:600; }}
.file {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 20px; margin-bottom:20px; }}
.file h2 {{ font-size:17px; margin:0 0 4px; word-break:break-all; }} .detail {{ color:var(--muted); margin:0 0 12px; }}
.badge {{ display:inline-block; font-size:12px; font-weight:600; padding:2px 8px; border-radius:999px; color:#fff;
         background:var(--same); vertical-align:2px; }}
.badge.changed {{ background:var(--changed); }} .badge.moved {{ background:var(--moved); }} .badge.new {{ background:var(--new); }}
.badge.deleted {{ background:var(--deleted); }} .badge.micro {{ background:var(--micro); }}
.images {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin:8px 0; }}
figure {{ margin:0; }} figure img {{ width:100%; border:1px solid var(--line); border-radius:8px; background:#fff; }}
figcaption {{ font-size:13px; color:var(--muted); text-align:center; }}
.links {{ display:flex; gap:8px; flex-wrap:wrap; }}
.button {{ display:inline-block; padding:7px 12px; border-radius:8px; background:var(--accent); color:#fff; text-decoration:none; }}
a {{ color:var(--accent); }} code {{ font-size:0.92em; }} ul ul {{ margin:4px 0; }}
footer {{ color:var(--muted); font-size:13px; margin-top:24px; }}
@media (max-width:640px) {{ th:nth-child(3), td:nth-child(3) {{ display:none; }} }}
</style></head>
<body><main>
<h1>{title}</h1><p class="subtitle">{subtitle}</p>
{body}
<footer>theseus {version} · everything here was computed on this computer; no files were uploaded anywhere.</footer>
</main></body></html>
"""
