"""Child-process side of theseus.isolate: analyze one file's old and new versions and write the
result (or the errors) as JSON.

The analysis is saved before any image is drawn, so if rendering crashes the process or runs
past the timeout, the parent still has the text report (see theseus.isolate).

Usage: python -m theseus._worker <result.json> [--old PATH] [--new PATH] [--units UNIT]
                                 [--images DIR] [--title NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from theseus.analyze import analyze_pair, render_images


def _save(result: dict, path: str) -> None:
    # Write-then-rename: the parent never reads a half-written file, even if we die mid-write.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m theseus._worker")
    parser.add_argument("out")
    parser.add_argument("--old", default=None)
    parser.add_argument("--new", default=None)
    parser.add_argument("--units", default=None)
    parser.add_argument("--images", default=None)
    parser.add_argument("--title", default=None)
    args = parser.parse_args(argv)

    result, models = analyze_pair(args.old, args.new, args.units)
    _save(result, args.out)
    if args.images:
        render_images(result, models, args.images, args.title or os.path.basename(args.new or args.old))
        _save(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
