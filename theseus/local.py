"""Reports on your own machine: `theseus compare`, `theseus diff` and `theseus app` all end here,
in a folder holding report.html plus each file's images and 3D viewer."""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import webbrowser

from theseus.ci import DEFAULT_MAX_FILE_MB, Images, Results, analyze
from theseus.compare import DEFAULT_THRESHOLDS, Thresholds, file_section
from theseus.fmt import fmt_len
from theseus.gitops import _git, changed_files, merge_base, repo_root
from theseus.isolate import DEFAULT_TIMEOUT_S, analyze_isolated
from theseus.publish import slug
from theseus.report_html import STATUS_LABEL, render_html

REPORT_NAME = "report.html"


def default_out_dir(name: str) -> str:
    """Somewhere out of the way that's the same each time, so reports don't pile up."""
    return os.path.join(tempfile.gettempdir(), "theseus", slug(name))


def _start_display() -> None:
    from theseus.render import display

    display.backend()  # start a virtual display once (if needed), for every worker to share


def _relative(images: Images, out_dir: str) -> Images:
    return {path: {kind: os.path.relpath(f, out_dir).replace(os.sep, "/") for kind, f in kinds.items()}
            for path, kinds in images.items()}


def write_report(out_dir: str, results: Results, renamed_from: dict[str, str], errors: list[str], images: Images,
                 thresholds: Thresholds, title: str, subtitle: str) -> str:
    page = render_html(results, thresholds, renamed_from, _relative(images, out_dir), errors, title, subtitle)
    path = os.path.join(out_dir, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def summary_lines(results: Results, renamed_from: dict[str, str], thresholds: Thresholds) -> list[str]:
    """One line per file for the terminal: name, verdict, largest difference, headline."""
    lines = []
    for path, (old, new, precise) in results.items():
        s = file_section(path, old, new, thresholds, renamed_from.get(path), precise)
        if s is None:
            continue
        largest = f", largest {fmt_len(s.largest_mm)}" if s.largest_mm else ""
        lines.append(f"{path}: {STATUS_LABEL[s.status]}{largest}" + (f" — {s.headline}" if s.headline else ""))
    return lines


def compare_files(old: str | None, new: str | None, out_dir: str, *, units: str | None = None,
                  thresholds: Thresholds = DEFAULT_THRESHOLDS, timeout: float = DEFAULT_TIMEOUT_S,
                  names: tuple[str | None, str | None] | None = None, images: bool = True) -> tuple[str, Results, list[str]]:
    """Compare two files into out_dir. `names` are what to call them in the report (the app's
    uploads live in a temp folder under names nobody chose). Returns (report path, results, errors)."""
    old_name, new_name = names or (old and os.path.basename(old), new and os.path.basename(new))
    name = new_name or old_name
    os.makedirs(out_dir, exist_ok=True)
    image_dir = os.path.join(out_dir, slug(name)) if images else None
    if images:
        _start_display()
    result = analyze_isolated(old, new, timeout, units, image_dir, name)

    errors: list[str] = []
    for label, err, path, shown in (("old", result["old_error"], old, old_name),
                                    ("new", result["new_error"], new, new_name)):
        if err:
            errors.append(f"{shown} ({label} version): {err.replace(path, shown)}")
    results: Results = {}
    if result["not_cad"]:
        errors.append(f"{name}: not a CAD file theseus can read")
    elif not errors:
        results[name] = (result["old"], result["new"], result["precise"])
        if result["precise_error"]:
            errors.append(f"{name}: {result['precise_error']} — showing metrics only")
        if result["images_error"]:
            errors.append(f"{name}: images unavailable ({result['images_error']}) — text report only")
    found = {name: {k: os.path.join(image_dir, v) for k, v in result["images"].items()}} if result["images"] else {}

    title = f"{old_name} → {new_name}" if old_name and new_name else (new_name or old_name)
    report = write_report(out_dir, results, {}, errors, found, thresholds, "theseus: " + title,
                          "Compared on this computer")
    return report, results, errors


def default_base() -> str:
    """Where the current branch forked from the default branch (origin/HEAD, main or master).
    On the default branch itself that's HEAD, so `theseus diff` shows uncommitted changes."""
    candidates = []
    try:
        candidates.append(_git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").decode().strip())
    except subprocess.CalledProcessError:
        pass
    candidates += ["origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        try:
            _git("rev-parse", "--verify", "--quiet", ref + "^{commit}")
        except subprocess.CalledProcessError:
            continue
        return merge_base(ref, "HEAD")
    return "HEAD"


def diff_repo(base: str | None, head: str | None, out_dir: str, *, units: str | None = None,
              thresholds: Thresholds = DEFAULT_THRESHOLDS, timeout: float = DEFAULT_TIMEOUT_S,
              images: bool = True) -> tuple[str, Results, dict[str, str], list[str]]:
    """Every CAD file changed between base and head (None: the working tree, including files
    not yet added). Must be called from inside the repository."""
    out_dir = os.path.abspath(out_dir)
    os.chdir(repo_root())
    base = base or default_base()
    changes = changed_files(base, head)
    os.makedirs(out_dir, exist_ok=True)
    if images and changes:
        _start_display()
    results, renamed_from, errors, found = analyze(
        changes, base, head, timeout=timeout, max_bytes=DEFAULT_MAX_FILE_MB * 1024 * 1024, units=units,
        image_root=os.path.join(out_dir, "images") if images else None)

    def describe(rev: str) -> str:
        try:
            return _git("rev-parse", "--short", rev).decode().strip()
        except subprocess.CalledProcessError:
            return rev

    repo = os.path.basename(os.getcwd())
    subtitle = f"{repo}: {describe(base)} → {describe(head) if head else 'working tree'}"
    report = write_report(out_dir, results, renamed_from, errors, found, thresholds, f"theseus: {repo}", subtitle)
    return report, results, renamed_from, errors


def open_in_browser(path: str) -> bool:
    try:
        return webbrowser.open(pathlib.Path(path).resolve().as_uri())
    except webbrowser.Error:
        return False
