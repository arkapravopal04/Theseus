"""Everything the report needs about one file's change: metrics for each version and the
precise geometric comparison between them.

The CLI calls analyze_models() in-process; the GitHub Action calls analyze_pair() inside a
child process (theseus.isolate), where a crash or hang in the geometry kernel can't take the
whole run down. The result is plain JSON-serialisable data either way.
"""

from __future__ import annotations

import os

from theseus.diff import precise_diff
from theseus.diff.motion import detect_motion
from theseus.formats import detect
from theseus.load import LoadError, load_model
from theseus.metrics import compute_metrics
from theseus.model import Model


def _try_alignment(old: Model, new: Model, result: dict, models: dict | None) -> None:
    """If the part was moved or mirrored as well as edited, report the edit: replace the
    comparison, and the old version's metrics and model, with their lined-up equivalents."""
    from theseus.diff.align import compare_aligned

    aligned = compare_aligned(old, new, len(result["precise"]["findings"]), result["precise"]["max_deviation_mm"])
    if aligned is None:
        return
    moved, precise, alignment = aligned
    if precise.identical:
        # Only a move after all, one a little too rough for detect_motion's exact fit.
        result["precise"]["motion"] = alignment.to_dict()
        return
    result["precise"] = {**precise.to_dict(), "motion": None, "alignment": alignment.to_dict()}
    result["old"] = compute_metrics(moved)
    if models is not None:
        models["old"] = moved


def analyze_models(old: Model | None, new: Model | None, models: dict | None = None) -> dict:
    """models: if given, models["old"] is replaced when the old version gets aligned to the new
    one, so the pictures show the same comparison as the text."""
    result = {
        "old": compute_metrics(old) if old else None,
        "new": compute_metrics(new) if new else None,
        "precise": None,
        "precise_error": None,
    }
    if old and new:
        # A quick first look decides what to compare: measuring every spline face of a part that
        # turns out to have moved takes minutes, all thrown away. Only what's reported is measured finely.
        exact = old.kind == new.kind == "brep"
        try:
            result["precise"] = precise_diff(old, new, coarse=exact).to_dict()
        except Exception as e:  # noqa: BLE001 — the metrics diff still stands without it
            result["precise_error"] = f"precise comparison failed: {type(e).__name__}: {e}"
        if result["precise"] and result["precise"]["method"] != "byte-identical":
            first_look_differs = not result["precise"]["identical"]
            if first_look_differs:
                try:
                    result["precise"]["motion"] = detect_motion(old, new, result["old"], result["new"])
                except Exception:  # noqa: BLE001 — not knowing it moved just means reporting it as changed
                    result["precise"]["motion"] = None
                if not result["precise"]["motion"]:
                    try:
                        _try_alignment(old, new, result, models)
                    except Exception:  # noqa: BLE001 — the unaligned comparison still stands
                        pass
            if exact and not result["precise"].get("motion") and "alignment" not in result["precise"]:
                try:
                    result["precise"] = precise_diff(old, new).to_dict()
                except Exception as e:  # noqa: BLE001 — as above
                    result["precise"], result["precise_error"] = None, f"precise comparison failed: {type(e).__name__}: {e}"
                else:
                    if first_look_differs:
                        result["precise"]["motion"] = None
    return result


def analyze_pair(old_path: str | None, new_path: str | None,
                 units: str | None = None) -> tuple[dict, dict[str, Model | None]]:
    """Load whichever versions exist and analyze them. Load failures are reported per version
    in old_error/new_error; not_cad is set when no version present is CAD content at all.

    Returns (result, models): the models are for render_images, kept separate so a caller can
    save the result before drawing — pictures must never cost the text report."""
    models: dict[str, Model | None] = {"old": None, "new": None}
    errors: dict[str, str | None] = {"old": None, "new": None}
    present = not_cad = 0
    for side, path in (("old", old_path), ("new", new_path)):
        if path is None:
            continue
        present += 1
        try:
            if os.path.isfile(path) and detect(path) is None:
                not_cad += 1
                errors[side] = f"{path} is not a recognised CAD file"
                continue
            models[side] = load_model(path, units)
        except LoadError as e:
            errors[side] = str(e)
        except Exception as e:  # noqa: BLE001 — any loader bug should become one skipped file
            errors[side] = f"unexpected {type(e).__name__}: {e}"

    result = analyze_models(models["old"], models["new"], models)
    result.update(old_error=errors["old"], new_error=errors["new"], not_cad=bool(present) and not_cad == present,
                  images={}, images_error=None)
    return result, models


def render_images(result: dict, models: dict[str, Model | None], image_dir: str, title: str) -> None:
    """Draw the file's images into image_dir (see theseus.images), recording their names in
    result["images"] or why they couldn't be drawn in result["images_error"]."""
    if result["old_error"] or result["new_error"] or not (models["old"] or models["new"]):
        return
    try:
        from theseus.images import render_file_images

        result["images"] = render_file_images(models["old"], models["new"], result["precise"], image_dir, title=title)
    except Exception as e:  # noqa: BLE001 — the text report stands without pictures
        result["images_error"] = f"{type(e).__name__}: {e}"
