"""Length units: the user-facing names --units accepts, and their size in millimetres."""

from __future__ import annotations

from theseus.model import LoadError

MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8, "um": 0.001}

# trimesh's own unit names (from 3MF/glTF metadata) mapped onto ours.
_TRIMESH_NAMES = {
    "millimeter": "mm", "millimeters": "mm", "mm": "mm",
    "centimeter": "cm", "centimeters": "cm", "cm": "cm",
    "meter": "m", "meters": "m", "m": "m",
    "inch": "in", "inches": "in", "in": "in",
    "foot": "ft", "feet": "ft", "ft": "ft",
    "micron": "um", "microns": "um", "micrometer": "um", "micrometers": "um",
}


def to_mm_factor(units: str) -> float:
    try:
        return MM_PER_UNIT[units]
    except KeyError:
        raise LoadError(f"unknown unit {units!r} (expected one of {', '.join(MM_PER_UNIT)})") from None


def from_trimesh_name(name: str | None) -> str | None:
    if not name:
        return None
    return _TRIMESH_NAMES.get(name.strip().lower())
