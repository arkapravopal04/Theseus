"""GitHub Action entry point: diff every CAD file changed by a pull request (or push) and keep a
single, up-to-date report comment on the PR.

Expects a checkout with full history (actions/checkout fetch-depth: 0). Env vars (action.yml
sets these):
  BASE_SHA             base commit of the PR (pull_request events)
  BEFORE_SHA, HEAD_SHA commit range of a push (push events)
  PR_NUMBER            pull request to comment on; without it the report is only printed
                       and written to the job summary
  GH_TOKEN             token for `gh` (github.token is enough for same-repo PRs)
  THESEUS_TIMEOUT      seconds allowed for one file: loading, comparing, drawing (default 600)
  THESEUS_MAX_FILE_MB  skip files bigger than this (default 50)
  THESEUS_UNITS        length unit of unitless formats like STL (default: assume mm)
  THESEUS_SENSITIVITY  engineering (default) | fine | exact — how big a difference must be to
                       be called "changed" rather than a micro-change
  THESEUS_IMAGES       inline (default: images in the comment, hosted on the theseus-assets
                       branch — needs contents: write) | artifact (images only in the run's
                       artifact) | off
  THESEUS_IMAGE_DIR    where images are written (action.yml uploads it as an artifact)
Actions itself provides GITHUB_REPOSITORY, GITHUB_STEP_SUMMARY, GITHUB_SERVER_URL, GITHUB_RUN_ID.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from theseus.compare import DEFAULT_THRESHOLDS, REPORT_MARKER, SENSITIVITY, Thresholds, build_report
from theseus.gitops import ZERO_SHA, Change, changed_files, materialize, merge_base
from theseus.isolate import DEFAULT_TIMEOUT_S, analyze_isolated
from theseus.model import LoadError
from theseus.publish import PublishError, publish_images, slug

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass  # stdout isn't reconfigurable (e.g. redirected to a non-text stream)

DEFAULT_MAX_FILE_MB = 50
# GitHub rejects comment bodies over 65,536 characters; leave room for the overflow note.
COMMENT_BUDGET = 60_000

Results = dict[str, tuple[dict | None, dict | None, dict | None]]  # old, new, precise
Images = dict[str, dict[str, str]]  # path -> {"before"/"after"/"diff"/"views": file or URL}
ARTIFACT_NAME = "theseus-images"


def _log(msg: str) -> None:
    print(f"theseus: {msg}", file=sys.stderr, flush=True)


def diff_range() -> tuple[str, str] | None:
    """(old, new) revisions to compare for this event, or None if there's nothing to diff."""
    base = os.environ.get("BASE_SHA")
    if base:
        # actions/checkout puts the PR's merge commit at HEAD; diffing from the merge base also
        # stays correct if a workflow checks out the PR head instead.
        return merge_base(base, "HEAD"), "HEAD"
    before = os.environ.get("BEFORE_SHA")
    if before and before != ZERO_SHA:
        return before, os.environ.get("HEAD_SHA") or "HEAD"
    return None


def analyze(
    changes: list[Change], base: str, head: str | None, *, timeout: float, max_bytes: int, units: str | None,
    image_root: str | None = None,
) -> tuple[Results, dict[str, str], list[str], Images]:
    """Returns (results, renamed_from, skipped-file messages, rendered image files)."""
    results: Results = {}
    renamed_from: dict[str, str] = {}
    errors: list[str] = []
    images: Images = {}

    with tempfile.TemporaryDirectory() as tmp:
        for i, change in enumerate(changes):
            _log(f"[{i + 1}/{len(changes)}] {change.path}")
            old_file = new_file = None
            try:
                if change.old_path:
                    old_file = materialize(base, change.old_path, os.path.join(tmp, f"{i}-old"), max_bytes)
                if change.status != "D":
                    new_file = materialize(head, change.path, os.path.join(tmp, f"{i}-new"), max_bytes)
            except LoadError as e:
                errors.append(str(e))
                continue

            if old_file is None and new_file is None:
                continue
            image_dir = os.path.join(image_root, slug(change.path)) if image_root else None
            result = analyze_isolated(old_file, new_file, timeout, units, image_dir, change.path)
            if result["not_cad"]:
                continue  # e.g. a compiled .obj object file: not ours to report on
            old_err, new_err = result["old_error"], result["new_error"]

            # Messages name the temp copy; point them at the file in the repo instead.
            if old_err:
                errors.append(f"{change.old_path} (old version): {old_err.replace(old_file, change.old_path)}")
            if new_err:
                errors.append(f"{change.path} (new version): {new_err.replace(new_file, change.path)}")
            # A version that exists but failed to load must not read as "new file"/"deleted":
            # report the file only under Skipped.
            if old_err or new_err or (result["old"] is None and result["new"] is None):
                continue
            if result["precise_error"]:
                errors.append(f"{change.path}: {result['precise_error']} — showing metrics only")
            results[change.path] = (result["old"], result["new"], result["precise"])
            if change.status == "R":
                renamed_from[change.path] = change.old_path
            if result["images_error"]:
                errors.append(f"{change.path}: images unavailable ({result['images_error']}) — text report only")
            if result["images"]:
                images[change.path] = {k: os.path.join(image_dir, v) for k, v in result["images"].items()}

    return results, renamed_from, errors, images


def render_report(results: Results, renamed_from: dict[str, str], errors: list[str],
                  thresholds: Thresholds = DEFAULT_THRESHOLDS, images: Images | None = None,
                  footer: str | None = None) -> str:
    report = build_report(results, thresholds, renamed_from, images, footer)
    if errors:
        report += "\n### Skipped\n" + "\n".join(f"- {e}" for e in errors) + "\n"
    return report


def fit_comment(results: Results, renamed_from: dict[str, str], errors: list[str], run_url: str | None,
                thresholds: Thresholds = DEFAULT_THRESHOLDS, images: Images | None = None,
                footer: str | None = None) -> str:
    """The report, trimmed file by file to fit in a PR comment when a change touches many files."""
    report = render_report(results, renamed_from, errors, thresholds, images, footer)
    if len(report) <= COMMENT_BUDGET:
        return report

    paths = list(results)
    where = f"the [workflow run's job summary]({run_url})" if run_url else "the workflow run's job summary"
    for keep in range(len(paths) - 1, -1, -1):
        subset = {p: results[p] for p in paths[:keep]}
        trimmed = render_report(subset, renamed_from, errors[:20], thresholds, images, footer)
        trimmed += f"\n*…and {len(paths) - keep} more file(s) — full report in {where}.*\n"
        if len(trimmed) <= COMMENT_BUDGET:
            return trimmed
    return f"{REPORT_MARKER}\n## theseus report\n\nReport too large for a comment — see {where}.\n"


def upsert_comment(repo: str, pr: str, body: str, *, create: bool = True, run=subprocess.run) -> None:
    """Edit theseus's existing comment on the PR in place, or post one if there isn't one yet,
    so a PR with ten pushes has one current report instead of ten stale ones."""
    found = run(
        ["gh", "api", "--paginate", f"repos/{repo}/issues/{pr}/comments",
         "--jq", f'.[] | select(.body | contains("{REPORT_MARKER}")) | .id'],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if not found and not create:
        return

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        body_file = f.name
    try:
        if found:
            cmd = ["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{found[0]}"]
        else:
            cmd = ["gh", "api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments"]
        # Body via a file: comment text can be far longer than an OS argument may be.
        run([*cmd, "-F", f"body=@{body_file}", "--silent"], check=True)
    finally:
        os.remove(body_file)


def main() -> int:
    rng = diff_range()
    if rng is None:
        _log("no base revision to diff against (BASE_SHA/BEFORE_SHA unset), nothing to do")
        return 0
    base, head = rng

    try:
        changes = changed_files(base, head)
    except subprocess.CalledProcessError as e:
        _log(f"git diff failed: {(e.stderr or b'').decode(errors='replace').strip() or e}")
        _log("is the checkout shallow? actions/checkout needs `fetch-depth: 0`")
        return 0

    sensitivity = os.environ.get("THESEUS_SENSITIVITY") or "engineering"
    if sensitivity not in SENSITIVITY:
        _log(f"unknown sensitivity {sensitivity!r} (expected one of {', '.join(SENSITIVITY)}); using engineering")
        sensitivity = "engineering"
    thresholds = SENSITIVITY[sensitivity]

    images_mode = os.environ.get("THESEUS_IMAGES") or "inline"
    image_root = None
    if images_mode != "off":
        from theseus.render import display

        display.backend()  # start a virtual display once, for every worker to share
        image_root = os.environ.get("THESEUS_IMAGE_DIR") or tempfile.mkdtemp(prefix="theseus-images-")

    results, renamed_from, errors, local_images = analyze(
        changes, base, head,
        timeout=float(os.environ.get("THESEUS_TIMEOUT") or DEFAULT_TIMEOUT_S),
        max_bytes=int(float(os.environ.get("THESEUS_MAX_FILE_MB") or DEFAULT_MAX_FILE_MB) * 1024 * 1024),
        units=os.environ.get("THESEUS_UNITS") or None,
        image_root=image_root,
    )

    repo, pr = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("PR_NUMBER")
    server, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and run_id else None
    image_links, footer = publish(local_images, images_mode, repo, pr, head, run_url)

    report = render_report(results, renamed_from, errors, thresholds, image_links, footer)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report + "\n")

    if not (repo and pr):
        _log("not a pull request (PR_NUMBER unset), skipping comment")
        return 0
    try:
        # With nothing to report, only refresh an earlier comment (the CAD change may have
        # been reverted) — never open a new one.
        upsert_comment(repo, pr, fit_comment(results, renamed_from, errors, run_url, thresholds, image_links, footer),
                       create=bool(results or errors))
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Fork PRs get a read-only token, so this 403s — expected, not a failure.
        _log(f"could not post PR comment (likely a fork PR with a read-only token): {e}")
    return 0


def publish(local_images: Images, mode: str, repo: str | None, pr: str | None, head: str,
            run_url: str | None, run=subprocess.run) -> tuple[Images, str | None]:
    """(image links for the report, footer note). Inline mode puts the images on the assets
    branch; if that isn't possible, the report points at the run's artifact instead."""
    if not local_images:
        return {}, None
    if mode == "inline" and repo and pr:
        try:
            sha = subprocess.run(["git", "rev-parse", "--short=7", head], capture_output=True, text=True,
                                 check=True).stdout.strip()
            # Images only: GitHub serves raw HTML as plain text, so the 3D viewer stays in the artifact.
            files = {f"{slug(path)}/{os.path.basename(f)}": f for path, kinds in local_images.items()
                     for kind, f in kinds.items() if kind != "viewer"}
            urls = publish_images(repo, f"pr-{pr}/{sha}", files, run=run)
            links = {}
            for path, kinds in local_images.items():
                links[path] = {kind: urls[f"{slug(path)}/{os.path.basename(f)}"] for kind, f in kinds.items()
                               if kind != "viewer"}
                if "viewer" in kinds and run_url:
                    links[path]["viewer"] = run_url
            note = None
            if run_url and any("viewer" in kinds for kinds in local_images.values()):
                note = (f"🧊 Interactive 3D viewers (`viewer.html` per file) are in the `{ARTIFACT_NAME}` artifact "
                        f"of the [workflow run]({run_url}).")
            return links, note
        except (PublishError, subprocess.CalledProcessError, OSError) as e:
            _log(f"could not publish images inline ({e}); they're in the run's artifact instead")
    where = f"[workflow run]({run_url})" if run_url else "workflow run"
    return {}, (f"🖼 Before/after/difference images and an interactive 3D viewer for each file are attached "
                f"to the {where} as the `{ARTIFACT_NAME}` artifact.")


if __name__ == "__main__":
    sys.exit(main())
