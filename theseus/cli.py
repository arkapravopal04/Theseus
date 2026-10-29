"""Command-line entry point: theseus old.step new.step --out report.md --png preview.png

Exit codes: 0 success, 2 a file couldn't be read/written (argparse also uses 2 for bad usage).
"""

from __future__ import annotations

import argparse
import os
import sys

from theseus.analyze import analyze_models
from theseus.compare import SENSITIVITY, build_report, thresholds_for
from theseus.export import export_mesh
from theseus.formats import extensions
from theseus.formats.units import MM_PER_UNIT
from theseus.load import LoadError, load_model
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
        "--images", default=None, metavar="DIR",
        help="write before/after/difference/standard-view PNGs to DIR and show them in the report",
    )
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
        "--sensitivity", choices=list(SENSITIVITY), default="engineering",
        help="how big a difference must be to be reported as a change rather than a micro-change: "
        "engineering = 10 µm or 0.1%%, fine = 1 µm or 0.01%%, exact = anything above the detection "
        "limit. Differences above the detection limit are always listed (default: engineering)",
    )
    parser.add_argument(
        "--abs-threshold-mm", type=float, default=None,
        help="override the sensitivity's length threshold in mm",
    )
    parser.add_argument(
        "--rel-threshold", type=float, default=None,
        help="override the sensitivity's relative threshold, as a fraction (0.001 = 0.1%%)",
    )
    args = parser.parse_args(argv)
    thresholds = thresholds_for(args.sensitivity, args.abs_threshold_mm, args.rel_threshold)

    try:
        old_model = load_model(args.old, args.units) if args.old else None
        new_model = load_model(args.new, args.units)
    except LoadError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR

    result = analyze_models(old_model, new_model)
    if result["precise_error"]:
        print(f"warning: {result['precise_error']} — reporting metrics only", file=sys.stderr)
    try:
        images = {}
        if args.images:
            from theseus.images import render_file_images

            files = render_file_images(old_model, new_model, result["precise"], args.images, title=args.new)
            report_dir = os.path.dirname(os.path.abspath(args.out))
            images = {args.new: {kind: os.path.relpath(os.path.join(args.images, f), report_dir).replace(os.sep, "/")
                                 for kind, f in files.items()}}
            print(f"wrote {len(files)} images to {args.images}")
        report = build_report({args.new: (result["old"], result["new"], result["precise"])}, thresholds,
                              images=images)
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
