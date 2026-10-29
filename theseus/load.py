"""Loading entry points. New code should use load_model(); load_shape() is kept for callers
that specifically want an exact B-rep TopoDS_Shape (STEP/IGES/BREP)."""

from __future__ import annotations

from OCP.TopoDS import TopoDS_Shape

from theseus.formats import extensions, load_model
from theseus.model import LoadError, Model

CAD_EXTENSIONS = extensions()

__all__ = ["CAD_EXTENSIONS", "LoadError", "Model", "load_model", "load_shape"]


def load_shape(path: str) -> TopoDS_Shape:
    """Read a STEP/IGES/BREP file as an exact B-rep shape. Raises LoadError on failure, empty
    geometry, or a mesh format (which has no B-rep — use load_model for those)."""
    model = load_model(path)
    if model.kind != "brep":
        raise LoadError(f"{path} is a {model.format.upper()} mesh, not a B-rep; use load_model()")
    return model.shape
