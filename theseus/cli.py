"""Command-line entry point: python -m theseus old.step new.step --out report.md --png preview.png"""

from __future__ import annotations

import argparse
import sys

from theseus.compare import DEFAULT_THRESHOLDS, Thresholds, build_report
from theseus.export import export_mesh
from theseus.load import LoadError, load_shape
from theseus.metrics import compute_metrics
from theseus.render import render_preview


def _metrics_or_none(path: str | None) -> dict | None:
    if path is None:
        return None
    shape = load_shape(path)
    return compute_metrics(shape)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="theseus",
        description="Diff and preview STEP/IGES/BREP/STL CAD files without needing CAD software.",
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
        old_metrics = _metrics_or_none(args.old)
        new_metrics = _metrics_or_none(args.new)
    except LoadError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    report = build_report({args.new: (old_metrics, new_metrics)}, thresholds)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"wrote {args.out}")

    if args.png or args.glb:
        shape = load_shape(args.new)
        if args.png:
            render_preview(shape, args.png)
            print(f"wrote {args.png}")
        if args.glb:
            export_mesh(shape, args.glb)
            print(f"wrote {args.glb}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
