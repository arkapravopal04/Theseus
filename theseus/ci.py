"""GitHub Action entry point: diff every CAD file changed by a pull request (or push) and keep a
single, up-to-date report comment on the PR.

Expects a checkout with full history (actions/checkout fetch-depth: 0). Env vars (action.yml
sets these):
  BASE_SHA             base commit of the PR (pull_request events)
  BEFORE_SHA, HEAD_SHA commit range of a push (push events)
  PR_NUMBER            pull request to comment on; without it the report is only printed
                       and written to the job summary
  GH_TOKEN             token for `gh` (github.token is enough for same-repo PRs)
  THESEUS_TIMEOUT      seconds allowed to load one file version (default 300)
  THESEUS_MAX_FILE_MB  skip files bigger than this (default 50)
  THESEUS_UNITS        length unit of unitless formats like STL (default: assume mm)
Actions itself provides GITHUB_REPOSITORY, GITHUB_STEP_SUMMARY, GITHUB_SERVER_URL, GITHUB_RUN_ID.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from theseus.compare import REPORT_MARKER, build_report
from theseus.gitops import ZERO_SHA, Change, changed_files, materialize, merge_base
from theseus.isolate import DEFAULT_TIMEOUT_S, NotCADError, metrics_isolated
from theseus.model import LoadError

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass  # stdout isn't reconfigurable (e.g. redirected to a non-text stream)

DEFAULT_MAX_FILE_MB = 50
# GitHub rejects comment bodies over 65,536 characters; leave room for the overflow note.
COMMENT_BUDGET = 60_000

Results = dict[str, tuple[dict | None, dict | None]]


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
    changes: list[Change], base: str, head: str, *, timeout: float, max_bytes: int, units: str | None
) -> tuple[Results, dict[str, str], list[str]]:
    """Returns (results, renamed_from, skipped-file messages)."""
    results: Results = {}
    renamed_from: dict[str, str] = {}
    errors: list[str] = []

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

            try:
                old_metrics, old_err = metrics_isolated(old_file, timeout, units) if old_file else (None, None)
                new_metrics, new_err = metrics_isolated(new_file, timeout, units) if new_file else (None, None)
            except NotCADError:
                continue  # e.g. a compiled .obj object file: not ours to report on

            # Messages name the temp copy; point them at the file in the repo instead.
            if old_err:
                errors.append(f"{change.old_path} (old version): {old_err.replace(old_file, change.old_path)}")
            if new_err:
                errors.append(f"{change.path} (new version): {new_err.replace(new_file, change.path)}")
            # A version that exists but failed to load must not read as "new file"/"deleted":
            # report the file only under Skipped.
            if old_err or new_err or (old_metrics is None and new_metrics is None):
                continue
            results[change.path] = (old_metrics, new_metrics)
            if change.status == "R":
                renamed_from[change.path] = change.old_path

    return results, renamed_from, errors


def render_report(results: Results, renamed_from: dict[str, str], errors: list[str]) -> str:
    report = build_report(results, renamed_from=renamed_from)
    if errors:
        report += "\n### Skipped\n" + "\n".join(f"- {e}" for e in errors) + "\n"
    return report


def fit_comment(results: Results, renamed_from: dict[str, str], errors: list[str], run_url: str | None) -> str:
    """The report, trimmed file by file to fit in a PR comment when a change touches many files."""
    report = render_report(results, renamed_from, errors)
    if len(report) <= COMMENT_BUDGET:
        return report

    paths = list(results)
    where = f"the [workflow run's job summary]({run_url})" if run_url else "the workflow run's job summary"
    for keep in range(len(paths) - 1, -1, -1):
        subset = {p: results[p] for p in paths[:keep]}
        trimmed = render_report(subset, renamed_from, errors[:20])
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

    results, renamed_from, errors = analyze(
        changes, base, head,
        timeout=float(os.environ.get("THESEUS_TIMEOUT") or DEFAULT_TIMEOUT_S),
        max_bytes=int(float(os.environ.get("THESEUS_MAX_FILE_MB") or DEFAULT_MAX_FILE_MB) * 1024 * 1024),
        units=os.environ.get("THESEUS_UNITS") or None,
    )
    report = render_report(results, renamed_from, errors)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report + "\n")

    repo, pr = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("PR_NUMBER")
    if not (repo and pr):
        _log("not a pull request (PR_NUMBER unset), skipping comment")
        return 0

    server, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and run_id else None
    try:
        # With nothing to report, only refresh an earlier comment (the CAD change may have
        # been reverted) — never open a new one.
        upsert_comment(repo, pr, fit_comment(results, renamed_from, errors, run_url),
                       create=bool(results or errors))
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Fork PRs get a read-only token, so this 403s — expected, not a failure.
        _log(f"could not post PR comment (likely a fork PR with a read-only token): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
