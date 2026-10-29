"""Number formatting for reports: lengths in a unit that fits their size, and coordinates
with enough decimals to actually show a change."""

from __future__ import annotations

import math


def fmt_len(mm: float, signed: bool = False) -> str:
    """0.19 -> "0.190 mm", 0.002 -> "2.0 µm", 3e-7 -> "0.3 nm"... so a 2 µm change never
    prints as "0.000 mm"."""
    sign = "+" if signed else ""
    a = abs(mm)
    if a >= 0.1 or a == 0:
        return f"{mm:{sign}.3f} mm"
    if a >= 1e-4:
        return f"{mm * 1e3:{sign}.1f} µm"
    return f"{mm * 1e6:{sign}.1f} nm"


def decimals_for(delta: float, minimum: int = 3, maximum: int = 7) -> int:
    """Decimal places needed for two coordinates `delta` apart to print differently."""
    if delta <= 0:
        return minimum
    return max(minimum, min(maximum, math.ceil(-math.log10(delta)) + 1))


def fmt_point(p, decimals: int = 3) -> str:
    return "(" + ", ".join(f"{c:.{decimals}f}" for c in p) + ")"
