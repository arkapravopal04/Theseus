"""Pick a rendering backend that can actually draw on this machine.

VTK draws properly (a real depth buffer, anti-aliasing, millions of triangles in about a
second), but on Linux it needs an X display, and with no display and no EGL/OSMesa it
crashes the process outright rather than raising. So a backend is chosen up front:

  - macOS / Windows: VTK renders offscreen natively.
  - Linux with $DISPLAY: VTK.
  - Linux without one but with Xvfb installed (GitHub's Ubuntu runners have it): start a
    private virtual display for this process and its children, then VTK.
  - otherwise: the matplotlib backend, which always works but sorts whole triangles by depth.

Having a display isn't proof VTK can use it (a stale $DISPLAY from an SSH session, missing GL
libraries), and finding out in-process would kill the process — so VTK is first tried once in a
throwaway process. The verdict is exported as THESEUS_RENDERER for worker processes to inherit;
setting THESEUS_RENDERER=vtk|mpl yourself overrides the choice (and skips the check).
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys

_xvfb: subprocess.Popen | None = None
_backend: str | None = None


def _start_xvfb() -> bool:
    global _xvfb
    read_fd, write_fd = os.pipe()
    try:
        # -displayfd: Xvfb picks a free display number and writes it to the pipe once ready.
        proc = subprocess.Popen(
            ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "4096x4096x24", "-nolisten", "tcp"],
            pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        return False
    os.close(write_fd)
    with os.fdopen(read_fd) as pipe:
        number = pipe.readline().strip()
    if not number.isdigit():
        proc.kill()
        return False
    _xvfb = proc
    os.environ["DISPLAY"] = f":{number}"  # inherited by worker processes too
    atexit.register(proc.terminate)
    return True


_PROBE = (
    "import vtkmodules.vtkRenderingOpenGL2\n"
    "from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow\n"
    "w = vtkRenderWindow(); w.SetOffScreenRendering(1); w.AddRenderer(vtkRenderer()); w.SetSize(8, 8); w.Render()\n"
)


def _vtk_works() -> bool:
    try:
        return subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def backend() -> str:
    """'vtk' or 'mpl'. May start an Xvfb server (once per process tree) as a side effect."""
    global _backend
    if _backend is None:
        forced = os.environ.get("THESEUS_RENDERER")
        if forced in ("vtk", "mpl"):
            _backend = forced
        else:
            has_display = (sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY"))
                           or (shutil.which("Xvfb") is not None and _start_xvfb()))
            _backend = "vtk" if has_display and _vtk_works() else "mpl"
            os.environ["THESEUS_RENDERER"] = _backend  # workers inherit the verdict instead of re-checking
    return _backend
