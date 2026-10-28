"""Everything the report needs about one file's change: metrics for each version and the
precise geometric comparison between them.

The CLI calls analyze_models() in-process; the GitHub Action calls analyze_pair() inside a
child process (theseus.isolate), where a crash or hang in the geometry kernel can't take the
whole run down. The result is plain JSON-serialisable data either way.
"""

from __future__ import annotations

import os

from theseus.diff import precise_diff
from theseus.formats import detect
from theseus.load import LoadError, load_model
from theseus.metrics import compute_metrics
from theseus.model import Model


def analyze_models(old: Model | None, new: Model | None) -> dict:
    result = {
        "old": compute_metrics(old) if old else None,
        "new": compute_metrics(new) if new else None,
        "precise": None,
        "precise_error": None,
    }
    if old and new:
        try:
            result["precise"] = precise_diff(old, new).to_dict()
        except Exception as e:  # noqa: BLE001 — the metrics diff still stands without it
            result["precise_error"] = f"precise comparison failed: {type(e).__name__}: {e}"
    return result


def analyze_pair(old_path: str | None, new_path: str | None, units: str | None = None) -> dict:
    """Load whichever versions exist and analyze them. Load failures are reported per version
    in old_error/new_error; not_cad is set when no version present is CAD content at all."""
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

    result = analyze_models(models["old"], models["new"])
    result.update(old_error=errors["old"], new_error=errors["new"], not_cad=bool(present) and not_cad == present)
    return result
