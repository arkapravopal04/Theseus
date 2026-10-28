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


class NotCADError(Exception):
    """The file has a CAD extension but isn't CAD content (e.g. a compiled .obj object file)."""


def metrics_isolated(
    path: str, timeout: float = DEFAULT_TIMEOUT_S, assume_units: str | None = None
) -> tuple[dict | None, str | None]:
    """Returns (metrics, error); exactly one is None. Raises NotCADError for non-CAD content."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_PACKAGE_PARENT, env.get("PYTHONPATH")) if p)

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "result.json")
        cmd = [sys.executable, "-m", "theseus._worker", path, out]
        if assume_units:
            cmd += ["--units", assume_units]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return None, f"timed out after {timeout:g}s while loading"

        if proc.returncode < 0:
            try:
                name = signal.Signals(-proc.returncode).name
            except ValueError:
                name = f"signal {-proc.returncode}"
            return None, f"the geometry kernel crashed while loading ({name}) — the file may be malformed"
        if not os.path.exists(out):
            tail = proc.stderr.decode(errors="replace").strip().splitlines()
            return None, f"loader failed: {tail[-1] if tail else f'exit code {proc.returncode}'}"

        with open(out, encoding="utf-8") as f:
            result = json.load(f)
    if result.get("not_cad"):
        raise NotCADError(result["error"])
    return result.get("metrics"), result.get("error")
