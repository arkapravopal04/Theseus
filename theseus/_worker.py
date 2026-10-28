"""Child-process side of theseus.isolate: load one file, write its metrics (or the error) as JSON.

Usage: python -m theseus._worker <cad-file> <result.json> [--units UNIT]
"""

from __future__ import annotations

import argparse
import json
import sys

from theseus.formats import detect
from theseus.load import LoadError, load_model
from theseus.metrics import compute_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m theseus._worker")
    parser.add_argument("path")
    parser.add_argument("out")
    parser.add_argument("--units", default=None)
    args = parser.parse_args(argv)

    try:
        if detect(args.path) is None:
            result = {"error": f"{args.path} is not a recognised CAD file", "not_cad": True}
        else:
            result = {"metrics": compute_metrics(load_model(args.path, args.units))}
    except LoadError as e:
        result = {"error": str(e)}
    except Exception as e:  # noqa: BLE001 — any loader bug should become one skipped file
        result = {"error": f"unexpected {type(e).__name__}: {e}"}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
