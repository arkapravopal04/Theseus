"""The `theseus` command.

    theseus app                     drag-and-drop comparison in the browser
    theseus compare OLD NEW         compare two files, open the report
    theseus diff [BASE] [HEAD]      every CAD file changed in this git repo, in one report
    theseus setup git               make `git diff` show CAD changes as text
    theseus setup github            add the pull-request workflow to this repo
    theseus textconv FILE           a CAD file as text (what `setup git` uses)
    theseus [OLD] NEW --out r.md    the original interface: a markdown report

Exit codes: 0 success, 2 a file couldn't be read/written (argparse also uses 2 for bad usage).
"""

from __future__ import annotations

import argparse
import os
import sys

import theseus
from theseus.analyze import analyze_models
from theseus.compare import SENSITIVITY, build_report, thresholds_for
from theseus.export import export_mesh
from theseus.formats import extensions
from theseus.formats.units import MM_PER_UNIT
from theseus.load import LoadError, load_model
from theseus.render import render_preview

EXIT_OK = 0
EXIT_ERROR = 2

COMMANDS = ("app", "compare", "diff", "setup", "textconv")


def _add_comparison_options(parser: argparse.ArgumentParser) -> None:
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


def _add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", metavar="DIR", default=None,
                        help="folder to write report.html and its images into (default: a theseus folder in "
                        "your temp directory, reused each time)")
    parser.add_argument("--no-open", action="store_true", help="don't open the report in the browser")
    parser.add_argument("--no-images", action="store_true", help="text report only (faster)")
    _add_comparison_options(parser)


def _thresholds(args):
    return thresholds_for(args.sensitivity, args.abs_threshold_mm, args.rel_threshold)


def _finish(report: str, lines: list[str], errors: list[str], no_open: bool) -> int:
    from theseus.local import open_in_browser

    for line in lines or ["no CAD file changes found"]:
        print(line)
    for error in errors:
        print(f"skipped: {error}", file=sys.stderr)
    print(f"report: {report}")
    if not no_open:
        open_in_browser(report)
    return EXIT_OK


def _compare(args) -> int:
    from theseus.local import compare_files, default_out_dir, summary_lines

    for path in (args.old, args.new):
        if path and not os.path.isfile(path):
            print(f"error: no such file: {path}", file=sys.stderr)
            return EXIT_ERROR
    thresholds = _thresholds(args)
    out = args.out or default_out_dir("compare-" + os.path.basename(args.new))
    report, results, errors = compare_files(args.old, args.new, out, units=args.units, thresholds=thresholds,
                                            images=not args.no_images)
    if not results:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    return _finish(report, summary_lines(results, {}, thresholds), errors, args.no_open)


def _diff(args) -> int:
    import subprocess

    from theseus.gitops import repo_root
    from theseus.local import default_out_dir, diff_repo, summary_lines

    try:
        root = repo_root()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("error: not inside a git repository (use `theseus compare OLD NEW` for two files)", file=sys.stderr)
        return EXIT_ERROR
    thresholds = _thresholds(args)
    out = args.out or default_out_dir("diff-" + os.path.basename(root))
    try:
        report, results, renamed, errors = diff_repo(args.base, args.head, out, units=args.units,
                                                     thresholds=thresholds, images=not args.no_images)
    except subprocess.CalledProcessError as e:
        print(f"error: git failed: {(e.stderr or b'').decode(errors='replace').strip() or e}", file=sys.stderr)
        return EXIT_ERROR
    return _finish(report, summary_lines(results, renamed, thresholds), errors, args.no_open)


def _app(args) -> int:
    import webbrowser

    from theseus.app import App

    app = App(_thresholds(args), args.units, port=args.port)
    print(f"theseus app running at {app.url}")
    print("Drop two versions of a part on that page. Press Ctrl+C here to stop.")
    if not args.no_open:
        webbrowser.open(app.url)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        print()
    return EXIT_OK


def _setup(args) -> int:
    import subprocess

    from theseus.gitops import repo_root
    from theseus.integrate import SetupError, setup_git, setup_github

    try:
        root = repo_root()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("error: run this inside the git repository you want to set up", file=sys.stderr)
        return EXIT_ERROR
    try:
        done = setup_git(root) if args.target == "git" else setup_github(root, force=args.force)
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    for line in done:
        print(line)
    if args.target == "git":
        print("Done: `git diff`, `git log -p` and git GUIs now show CAD changes as text. "
              "Commit .gitattributes so everyone gets it; each person runs `theseus setup git` once.")
    else:
        print("Next: commit and push it, e.g.\n"
              "  git add .github/workflows/theseus.yml\n"
              "  git commit -m \"Review CAD changes with theseus\" && git push\n"
              "Every pull request that changes a CAD file then gets a report comment.")
    return EXIT_OK


def _textconv(args) -> int:
    from theseus.textconv import summary

    text = summary(args.file)
    sys.stdout.buffer.write(text.encode("utf-8"))  # git reads bytes; keep µ and Ø intact on Windows too
    sys.stdout.flush()
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="theseus",
        description="See exactly what changed between two versions of a CAD file — which face, which hole, "
        f"by how much — without CAD software. Reads {', '.join(e.lstrip('.').upper() for e in extensions())}.",
        epilog="The original interface still works: `theseus [OLD] NEW --out report.md` writes a markdown report.",
    )
    parser.add_argument("--version", action="version", version=f"theseus {theseus.__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    p = sub.add_parser("app", help="compare files by dragging them into a browser window (no command line needed)",
                       description="Opens a page in your browser where you drop two versions of a part. "
                       "Everything runs on this computer.")
    p.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    p.add_argument("--no-open", action="store_true", help="just print the address")
    _add_comparison_options(p)
    p.set_defaults(run=_app)

    p = sub.add_parser("compare", help="compare two files and open the report in your browser",
                       description="Compare two versions of a CAD file and open a report with before/after/"
                       "difference images and an interactive 3D view.")
    p.add_argument("old", help="the old version")
    p.add_argument("new", help="the new version")
    _add_report_options(p)
    p.set_defaults(run=_compare)

    p = sub.add_parser("diff", help="report every CAD file changed in this git repo (any host)",
                       description="Compare every CAD file that changed in this git repository. With no "
                       "arguments: everything on this branch plus uncommitted changes, compared with where the "
                       "branch started from the main branch.")
    p.add_argument("base", nargs="?", default=None,
                   help="revision to compare from (default: where this branch forked from main/master)")
    p.add_argument("head", nargs="?", default=None, help="revision to compare to (default: the files on disk now)")
    _add_report_options(p)
    p.set_defaults(run=_diff)

    p = sub.add_parser("setup", help="set up `git diff` (git) or pull-request reports (github) for this repo",
                       description="git: make `git diff` and `git log -p` show CAD changes as readable text. "
                       "github: add the workflow that comments on pull requests.")
    p.add_argument("target", choices=["git", "github"])
    p.add_argument("--force", action="store_true", help="github: replace an existing workflow file")
    p.set_defaults(run=_setup)

    p = sub.add_parser("textconv", help="print a CAD file as text (used by `git diff` after `theseus setup git`)")
    p.add_argument("file")
    p.set_defaults(run=_textconv)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        return _legacy(argv)
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    return args.run(args)


def _legacy(argv: list[str]) -> int:
    """`theseus [OLD] NEW --out report.md`: the original interface, a markdown report."""
    parser = argparse.ArgumentParser(
        prog="theseus",
        description="Markdown report of a CAD file change (the original theseus interface; see `theseus --help` "
        "for the others).",
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
    _add_comparison_options(parser)
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
