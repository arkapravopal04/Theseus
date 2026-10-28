"""Child-process side of theseus.isolate: analyze one file's old and new versions and write the
result (or the errors) as JSON.

Usage: python -m theseus._worker <result.json> [--old PATH] [--new PATH] [--units UNIT]
"""

from __future__ import annotations

import argparse
import json
import sys

from theseus.analyze import analyze_pair


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m theseus._worker")
    parser.add_argument("out")
    parser.add_argument("--old", default=None)
    parser.add_argument("--new", default=None)
    parser.add_argument("--units", default=None)
    args = parser.parse_args(argv)

    result = analyze_pair(args.old, args.new, args.units)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
