"""Format registry: the one place that knows which files theseus reads and how.

Every other module (CLI, GitHub Action, workflow path filter, tests) gets its list of
supported extensions from here, so adding a format is one entry in FORMATS.

Detection checks the file's content, not just its extension: PART.STEP, a STEP file saved as
.stp.bak, or a binary STL with a "solid" header all resolve correctly, and a non-CAD file
that happens to share an extension (a compiled .obj object file) is recognised as not CAD.
"""

from __future__ import annotations

import os
import re
import struct
import zipfile
from dataclasses import dataclass
from typing import Callable

from theseus.formats.brep import load_brep, load_iges, load_step
from theseus.formats.mesh import load_mesh
from theseus.formats.units import MM_PER_UNIT
from theseus.model import LoadError, Model

_HEAD_BYTES = 4096


def _sniff_step(path: str, head: bytes, size: int) -> bool:
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISO-10303-21")


def _sniff_iges(path: str, head: bytes, size: int) -> bool:
    # Fixed 80-column records; column 73 holds the section letter, and a file opens with its
    # Start (S) section — or, when that's empty, the Global (G) section.
    first = head.split(b"\n", 1)[0].rstrip(b"\r")
    return len(first) >= 73 and first[72:73] in (b"S", b"G")


def _sniff_brep(path: str, head: bytes, size: int) -> bool:
    return b"DBRep_DrawableShape" in head or b"CASCADE Topology" in head


def _sniff_stl(path: str, head: bytes, size: int) -> bool:
    if size >= 84:
        (count,) = struct.unpack("<I", head[80:84]) if len(head) >= 84 else (None,)
        if count is not None and 84 + 50 * count == size:
            return True  # binary STL: the size check is exact, whatever the header says
    lowered = head.lstrip().lower()
    return lowered.startswith(b"solid") and b"facet" in lowered


def _sniff_3mf(path: str, head: bytes, size: int) -> bool:
    if not head.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.lower() == "3d/3dmodel.model" for n in z.namelist())
    except zipfile.BadZipFile:
        return False


def _sniff_obj(path: str, head: bytes, size: int) -> bool:
    return re.search(rb"^v[ \t]+[-+\d.]", head, re.MULTILINE) is not None


def _sniff_ply(path: str, head: bytes, size: int) -> bool:
    return head.startswith((b"ply\n", b"ply\r\n"))


def _sniff_off(path: str, head: bytes, size: int) -> bool:
    return re.match(rb"\s*[A-Z]{0,4}OFF\b", head) is not None


def _sniff_glb(path: str, head: bytes, size: int) -> bool:
    return head.startswith(b"glTF")


@dataclass(frozen=True)
class Format:
    name: str
    extensions: tuple[str, ...]
    kind: str  # "brep" | "mesh"
    loader: Callable[[str, str, str | None], Model]
    sniff: Callable[[str, bytes, int], bool]
    # The extension is also used by common non-CAD files, so require the content to match.
    ambiguous_extension: bool = False
    has_units: bool = False  # the file declares its own length unit


FORMATS: tuple[Format, ...] = (
    Format("step", (".step", ".stp", ".p21"), "brep", load_step, _sniff_step, has_units=True),
    Format("iges", (".iges", ".igs"), "brep", load_iges, _sniff_iges, has_units=True),
    Format("brep", (".brep", ".brp"), "brep", load_brep, _sniff_brep),
    Format("stl", (".stl",), "mesh", load_mesh, _sniff_stl),
    Format("3mf", (".3mf",), "mesh", load_mesh, _sniff_3mf, has_units=True),
    Format("glb", (".glb",), "mesh", load_mesh, _sniff_glb, has_units=True),
    Format("ply", (".ply",), "mesh", load_mesh, _sniff_ply),
    Format("obj", (".obj",), "mesh", load_mesh, _sniff_obj, ambiguous_extension=True),
    Format("off", (".off",), "mesh", load_mesh, _sniff_off, ambiguous_extension=True),
)

_BY_EXTENSION = {ext: f for f in FORMATS for ext in f.extensions}


def extensions() -> tuple[str, ...]:
    """Every supported extension, lowercase, with the leading dot."""
    return tuple(_BY_EXTENSION)


def is_candidate(path: str) -> bool:
    """Cheap, name-only check for whether a path might be a CAD file (case-insensitive)."""
    return os.path.splitext(path)[1].lower() in _BY_EXTENSION


def paths_filter() -> list[str]:
    """GitHub Actions `paths:` globs for every extension. Those globs are case-sensitive, and
    CAD exports are often uppercase (PART.STEP from SolidWorks), so each letter becomes a
    character class: '**.[sS][tT][eE][pP]' matches .step, .STEP and .Step alike."""
    return ["**." + "".join(f"[{c}{c.upper()}]" if c.isalpha() else c for c in ext[1:]) for ext in extensions()]


def detect(path: str) -> Format | None:
    """Work out a file's format. Returns None if it isn't a CAD file theseus can read."""
    with open(path, "rb") as f:
        head = f.read(_HEAD_BYTES)
    size = os.path.getsize(path)

    by_ext = _BY_EXTENSION.get(os.path.splitext(path)[1].lower())
    if by_ext is not None and by_ext.sniff(path, head, size):
        return by_ext
    # The content says otherwise (or there's no recognised extension): trust the content.
    for fmt in FORMATS:
        if fmt is not by_ext and fmt.sniff(path, head, size):
            return fmt
    if by_ext is not None and not by_ext.ambiguous_extension:
        return by_ext  # unrecognisable content: let the reader produce the real parse error
    return None


def load_model(path: str, assume_units: str | None = None) -> Model:
    """Load any supported CAD file. `assume_units` declares the length unit of formats that
    don't record one (STL/OBJ/PLY/OFF/BREP); formats that do record one ignore it.

    Raises LoadError on unreadable, unrecognised, or empty files.
    """
    if assume_units is not None and assume_units not in MM_PER_UNIT:
        raise LoadError(f"unknown unit {assume_units!r} (expected one of {', '.join(MM_PER_UNIT)})")
    if not os.path.isfile(path):
        raise LoadError(f"{path}: no such file")

    fmt = detect(path)
    if fmt is None:
        raise LoadError(f"{path} is not a recognised CAD file")
    model = fmt.loader(path, fmt.name, None if fmt.has_units else assume_units)

    if model.kind == "brep" and (model.shape is None or model.shape.IsNull()):
        # A successful read status only means the file parsed, not that it produced geometry.
        raise LoadError(f"{path} parsed but contains no geometry (null shape)")
    return model
