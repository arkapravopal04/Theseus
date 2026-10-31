"""Child-process side of theseus.diff.visual's booleans: cut two shapes both ways.

OpenCascade's boolean operations can crash outright on real geometry, and can run for minutes.
Neither can be caught from Python, so they run here, where either only costs the material
picture: the caller falls back to a deviation map.

Usage: python -m theseus._boolean_worker <a.brep> <b.brep> <a_minus_b.brep> <b_minus_a.brep> <fuzzy>
"""

from __future__ import annotations

import sys

from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape

from theseus.diff.visual import _brep_cut


def _read(path: str) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, path, BRep_Builder()):
        raise RuntimeError(f"could not read {path}")
    return shape


def main(argv: list[str]) -> int:
    a_path, b_path, a_minus_b, b_minus_a, fuzzy = argv
    a, b = _read(a_path), _read(b_path)
    BRepTools.Write_s(_brep_cut(a, b, float(fuzzy)), a_minus_b)
    BRepTools.Write_s(_brep_cut(b, a, float(fuzzy)), b_minus_a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
