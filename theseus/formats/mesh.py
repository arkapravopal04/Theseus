"""Triangle-mesh readers (STL, OBJ, PLY, OFF, 3MF, GLB) via trimesh.

Meshes are kept as meshes rather than converted to B-rep: volume and centre of mass come
straight from the divergence theorem over the triangles, which is exact for the mesh and
fast even for multi-million-triangle 3D-print files.
"""

from __future__ import annotations

import numpy as np
import trimesh

from theseus.formats.units import from_trimesh_name, to_mm_factor
from theseus.model import LoadError, Model


def _orient_bodies(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, list[trimesh.Trimesh], int]:
    """Split into connected bodies and make every closed body face outward.

    An inside-out body (common from some exporters) has negative signed volume; left alone it
    would cancel out real material in the totals. Returns (mesh, bodies, flipped_count).
    """
    # repair=False: trimesh's default fills holes while splitting, which would invent material
    # and report a volume for a mesh that is really open.
    bodies = list(mesh.split(only_watertight=False, repair=False)) if len(mesh.faces) else []
    flipped = 0
    rewound = False
    for body in bodies:
        if not body.is_watertight:
            continue
        if not body.is_winding_consistent:
            trimesh.repair.fix_winding(body)
            rewound = True
        if body.volume < 0:
            body.invert()
            flipped += 1
    if flipped or rewound:
        mesh = trimesh.util.concatenate(bodies)
    return mesh, bodies, flipped


def load_mesh(path: str, fmt: str, assume_units: str | None) -> Model:
    try:
        loaded = trimesh.load(path, file_type=fmt, force="mesh")
    except Exception as e:  # noqa: BLE001 — trimesh raises a wide variety of parse errors
        raise LoadError(f"failed to parse {path}: {e}") from e
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise LoadError(f"{path} parsed but contains no triangles")

    file_units = from_trimesh_name(getattr(loaded, "units", None))
    if file_units is not None:
        units, note = file_units, f"file units: {file_units}" + ("" if file_units == "mm" else ", converted to mm")
    elif assume_units is not None:
        units, note = assume_units, f"unitless format, declared {assume_units}"
    else:
        units, note = "mm", "unitless format, assumed mm"
    if units != "mm":
        loaded.apply_scale(to_mm_factor(units))

    if not np.isfinite(loaded.vertices).all():
        raise LoadError(f"{path} contains non-finite vertex coordinates")

    mesh, bodies, flipped = _orient_bodies(loaded)
    model = Model(path, fmt, "mesh", mesh=mesh, units_note=note, bodies=bodies)
    if flipped:
        model.warnings.append(f"{flipped} inside-out bod{'y' if flipped == 1 else 'ies'} flipped to face outward")
    return model
