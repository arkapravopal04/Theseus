"""Command-line entry point: theseus old.step new.step --out report.md --png preview.png

Exit codes: 0 success, 2 a file couldn't be read/written (argparse also uses 2 for bad usage).
"""

from __future__ import annotations

import argparse
import sys

from theseus.compare import DEFAULT_THRESHOLDS, Thresholds, build_report
from theseus.export import export_mesh
from theseus.formats import extensions
from theseus.formats.units import MM_PER_UNIT
from theseus.load import LoadError, load_model
from theseus.metrics import compute_metrics
from theseus.render import render_preview

EXIT_OK = 0
EXIT_ERROR = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="theseus",
        description="Diff and preview CAD files without needing CAD software. "
        f"Reads: {' '.join(e.lstrip('.') for e in extensions())}.",
    )
    parser.add_argument("old", nargs="?", help="path to the old/base version (omit for a new file)")
    parser.add_argument("new", help="path to the new version")
    parser.add_argument("--out", default="report.md", help="markdown report output path")
    parser.add_argument("--png", default=None, help="PNG preview output path (renders the new file)")
    parser.add_argument(
        "--glb", default=None,
        help="glTF binary output path (renders the new file) — commit this to GitHub for an "
        "interactive rotate/zoom viewer, no CAD software needed",
    )
    parser.add_argument(
        "--units", choices=list(MM_PER_UNIT), default=None,
        help="length unit of formats that don't record one (STL, OBJ, PLY, OFF, BREP); "
        "STEP/IGES/3MF/GLB declare their own and ignore this (default: assume mm)",
    )
    parser.add_argument(
        "--abs-threshold-mm", type=float, default=DEFAULT_THRESHOLDS.abs_mm,
        help=f"absolute noise floor in mm for length-valued metrics (bbox, centre of mass); "
        f"volume uses this value cubed as its own floor (default: {DEFAULT_THRESHOLDS.abs_mm})",
    )
    parser.add_argument(
        "--rel-threshold", type=float, default=DEFAULT_THRESHOLDS.rel,
        help=f"relative noise floor as a fraction, e.g. 0.001 = 0.1%% (default: {DEFAULT_THRESHOLDS.rel})",
    )
    args = parser.parse_args(argv)
    thresholds = Thresholds(abs_mm=args.abs_threshold_mm, rel=args.rel_threshold)

    try:
        old_model = load_model(args.old, args.units) if args.old else None
        new_model = load_model(args.new, args.units)
    except LoadError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR

    old_metrics = compute_metrics(old_model) if old_model else None
    report = build_report({args.new: (old_metrics, compute_metrics(new_model))}, thresholds)
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.out}")

        if args.png:
            render_preview(new_model, args.png)
            print(f"wrote {args.png}")
        if args.glb:
            export_mesh(new_model, args.glb)
            print(f"wrote {args.glb}")
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
