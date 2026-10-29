"""The images for one file's change: before, after, the difference, and a sheet of standard views.

All of a file's images share one camera (framing both versions), so they line up when shown
side by side. Markers on the difference image are numbered like the findings in the report.
"""

from __future__ import annotations

import os

from theseus.diff import report_order
from theseus.diff.visual import REMOVED, diff_visual, union_bounds
from theseus.model import Model
from theseus.render import Camera, Marker, render_scene, render_views, reserved_right
from theseus.render.scene import default_deflection, model_layer
from theseus.viewer import MAX_TRIANGLES, triangle_count, write_viewer

MAX_MARKERS = 10  # matches the number of findings listed in the report


def finding_markers(precise: dict | None) -> list[Marker]:
    """Numbered markers for the findings the report lists (in its order), where they have a place.
    Group summaries ("4 faces re-trimmed…") point at no single spot and get no marker."""
    if not precise:
        return []
    findings = report_order(precise["findings"])[:MAX_MARKERS]
    return [Marker(tuple(f["at"]), str(i + 1)) for i, f in enumerate(findings)
            if f.get("at") and f.get("kind") != "retrim"]


def numbered_findings(precise: dict | None) -> list[dict]:
    if not precise:
        return []
    findings = report_order(precise["findings"])[:MAX_MARKERS]
    return [{"n": i + 1, "what": f["what"], "at": f["at"] if f.get("kind") != "retrim" else None}
            for i, f in enumerate(findings)]


def render_file_images(old: Model | None, new: Model | None, precise: dict | None, out_dir: str,
                       title: str = "") -> dict[str, str]:
    """Render whichever images apply into out_dir, plus an interactive viewer page.
    Returns {"before"|"after"|"diff"|"views"|"viewer": file name}."""
    os.makedirs(out_dir, exist_ok=True)
    lo, hi = union_bounds(old, new)
    deflection = default_deflection(lo, hi)
    camera = Camera.fit(lo, hi)
    images = {}

    views = {}
    for name, model, label in (("before", old, "Before"), ("after", new, "After")):
        if model is not None:
            views[name] = [model_layer(model, deflection)]
            render_scene(views[name], os.path.join(out_dir, f"{name}.png"), camera, title=label)
            images[name] = f"{name}.png"
    legend = []

    if old is not None and new is not None:
        markers = finding_markers(precise)
        if precise and precise.get("motion"):
            markers = []  # the face-by-face findings of a moved part are just the move itself
            layers = [model_layer(old, deflection, colour=REMOVED, opacity=0.3), model_layer(new, deflection)]
            legend, scale_bar, detail, diff_title = [(REMOVED, "before (ghost)"), ((0.78, 0.80, 0.84), "after")], None, None, "Moved"
        else:
            visual = diff_visual(old, new, precise)
            layers, legend, scale_bar, detail = visual.layers, visual.legend, visual.scale_bar, visual.detail
            diff_title = "Difference" if visual.mode != "identical" else "Identical"
        diff_camera = Camera.fit(lo, hi, reserve_right=reserved_right(scale_bar is not None, detail is not None))
        render_scene(layers, os.path.join(out_dir, "diff.png"), diff_camera, markers=markers,
                     scale_bar=scale_bar, title=diff_title, legend=legend, detail=detail)
        render_views(layers, os.path.join(out_dir, "views.png"), lo, hi, markers=markers, legend=legend)
        images["diff"], images["views"] = "diff.png", "views.png"
        views["diff"] = layers

    if triangle_count(*views.values()) <= MAX_TRIANGLES:
        findings = [] if precise and precise.get("motion") else numbered_findings(precise)
        write_viewer(os.path.join(out_dir, "viewer.html"), title or "theseus", views, (lo, hi), findings, legend)
        images["viewer"] = "viewer.html"
    return images
