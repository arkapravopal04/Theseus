"""Load CAD files in a child process with a timeout.

OpenCascade is C++: a malformed STEP file can segfault the interpreter or send a reader into
an endless loop. In-process, one such file takes the whole report down with it; in a child
process it costs a single "skipped" line.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile

import theseus

# The child must import this same theseus package, even when it isn't pip-installed (the
# GitHub Action runs from a checkout of this repo, with the workspace as its cwd).
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(theseus.__file__)))

DEFAULT_TIMEOUT_S = 300


def _failure(message: str, old_path: str | None, new_path: str | None) -> dict:
    """A result in which every version present failed with `message`."""
    return {"old": None, "new": None, "precise": None, "precise_error": None, "not_cad": False,
            "images": {}, "images_error": None,
            "old_error": message if old_path else None, "new_error": message if new_path else None}


def _saved_analysis(path: str, images_error: str) -> dict | None:
    """The analysis the worker saved before drawing images, if it got that far. The worker
    saves it before rendering starts, so its presence means the failure came while drawing."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    if not result.get("images"):
        result["images_error"] = images_error
    return result


def analyze_isolated(
    old_path: str | None, new_path: str | None, timeout: float = DEFAULT_TIMEOUT_S, assume_units: str | None = None,
    image_dir: str | None = None, title: str | None = None,
) -> dict:
    """theseus.analyze.analyze_pair (plus render_images), run in a child process with a timeout.
    A crash or hang while loading comes back as an error on each version rather than taking this
    process down; one while drawing images keeps the analysis and loses only the pictures."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_PACKAGE_PARENT, env.get("PYTHONPATH")) if p)

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "result.json")
        cmd = [sys.executable, "-m", "theseus._worker", out]
        for flag, value in (("--old", old_path), ("--new", new_path), ("--units", assume_units), ("--images", image_dir),
                            ("--title", title)):
            if value:
                cmd += [flag, value]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            saved = _saved_analysis(out, f"rendering timed out after {timeout:g}s — text report only")
            return saved or _failure(f"timed out after {timeout:g}s while loading", old_path, new_path)

        if proc.returncode < 0:
            try:
                name = signal.Signals(-proc.returncode).name
            except ValueError:
                name = f"signal {-proc.returncode}"
            saved = _saved_analysis(out, f"the renderer crashed ({name}) — text report only")
            return saved or _failure(f"the geometry kernel crashed while loading ({name}) — the file may be malformed",
                                     old_path, new_path)
        if not os.path.exists(out):
            tail = proc.stderr.decode(errors="replace").strip().splitlines()
            return _failure(f"loader failed: {tail[-1] if tail else f'exit code {proc.returncode}'}", old_path, new_path)

        with open(out, encoding="utf-8") as f:
            return json.load(f)
