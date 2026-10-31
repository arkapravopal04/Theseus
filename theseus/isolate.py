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

DEFAULT_TIMEOUT_S = 600  # for all of one file: loading, comparing, and drawing


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


# How Windows reports a native crash: the NTSTATUS code as the exit code.
_WINDOWS_CRASHES = {0xC0000005: "access violation", 0xC00000FD: "stack overflow", 0xC0000409: "stack buffer overrun",
                    0xC000001D: "illegal instruction", 0xC0000094: "integer division by zero"}


def _crash_name(returncode: int) -> str | None:
    """What killed the child, if it crashed rather than exiting: a POSIX signal (negative return
    code) or a Windows exception code. None for an ordinary exit."""
    if returncode < 0:
        try:
            return signal.Signals(-returncode).name
        except ValueError:
            return f"signal {-returncode}"
    code = returncode & 0xFFFFFFFF
    if code >= 0xC0000000:
        return _WINDOWS_CRASHES.get(code, f"exception 0x{code:08X}")
    return None


def run_with_timeout(cmd: list[str], timeout: float, env: dict | None = None) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True, timeout=...), except that a timeout kills the child's own
    children too. Killing only the child leaves them holding its output pipes (the worker runs
    booleans in a process of their own), and waiting on those pipes would then hang for as long as
    they live, which the timeout exists to prevent. Raises subprocess.TimeoutExpired, like run()."""
    posix = os.name == "posix"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=posix)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if posix:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        proc.kill()
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


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
            proc = run_with_timeout(cmd, timeout, env)
        except subprocess.TimeoutExpired:
            saved = _saved_analysis(out, f"rendering timed out after {timeout:g}s — text report only")
            return saved or _failure(f"timed out after {timeout:g}s while loading", old_path, new_path)

        crash = _crash_name(proc.returncode)
        if crash:
            saved = _saved_analysis(out, f"the renderer crashed ({crash}) — text report only")
            return saved or _failure(f"the geometry kernel crashed while loading ({crash}) — the file may be malformed",
                                     old_path, new_path)
        tail = proc.stderr.decode(errors="replace").strip().splitlines()
        reason = tail[-1] if tail else f"exit code {proc.returncode}"
        if proc.returncode != 0:
            saved = _saved_analysis(out, f"rendering failed: {reason} — text report only")
            return saved or _failure(f"loader failed: {reason}", old_path, new_path)
        if not os.path.exists(out):
            return _failure(f"loader failed: {reason}", old_path, new_path)

        with open(out, encoding="utf-8") as f:
            return json.load(f)
