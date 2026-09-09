"""Geometric metrics for a loaded shape: volume, bounding box, centre of mass, solid count.

Note: "volume", not "mass" — we have no density, so don't call this mass anywhere.
Units follow whatever the source file used; OCCT does not convert units for you,
so a part authored in inches will report inch-scale numbers un-flagged.
"""

from __future__ import annotations

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape


def volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()  # for VolumeProperties, "mass" is the volume magnitude


def bounding_box(shape: TopoDS_Shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return (box.GetXMin(), box.GetYMin(), box.GetZMin(), box.GetXMax(), box.GetYMax(), box.GetZMax())


def center_of_mass(shape: TopoDS_Shape) -> tuple[float, float, float]:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    com = props.CentreOfMass()
    return (com.X(), com.Y(), com.Z())


def solid_count(shape: TopoDS_Shape) -> int:
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    return count


def compute_metrics(shape: TopoDS_Shape) -> dict:
    return {
        "volume_mm3": volume(shape),
        "bbox": bounding_box(shape),
        "com": center_of_mass(shape),
        "solid_count": solid_count(shape),
    }
