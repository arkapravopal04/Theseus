"""GitHub Action entry point: diff changed CAD files in a PR and post one PR comment.

Expects to run inside a checkout with full history (actions/checkout fetch-depth: 0).
Env vars:
  GH_TOKEN   - token for `gh pr comment` (github.token is enough for same-repo PRs)
  BASE_SHA   - base commit of the PR (github.event.pull_request.base.sha)
  PR_NUMBER  - pull request number, for `gh pr comment`
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from theseus.compare import build_report
from theseus.load import CAD_EXTENSIONS, LoadError, load_shape
from theseus.metrics import compute_metrics

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass  # stdout isn't reconfigurable (e.g. redirected to a non-text stream)

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB — skip anything bigger with a note
LFS_POINTER_PREFIX = b"version https://git-lfs"


def changed_cad_files(base_sha: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.splitlines() if p.lower().endswith(CAD_EXTENSIONS)]


def show_old_version(base_sha: str, path: str) -> str | None:
    """Returns a temp file path with the file's content at base_sha, or None if it didn't exist."""
    result = subprocess.run(
        ["git", "show", f"{base_sha}:{path}"],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        return None  # file didn't exist at base (new file)

    content = result.stdout
    if content.startswith(LFS_POINTER_PREFIX):
        raise LoadError(f"{path} is a Git LFS pointer at base — LFS content isn't fetched, skipping")
    if len(content) > MAX_FILE_BYTES:
        raise LoadError(f"{path} (old version) exceeds {MAX_FILE_BYTES // (1024*1024)}MB, skipping")

    suffix = os.path.splitext(path)[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return tmp_path


def check_new_version(path: str) -> None:
    if not os.path.exists(path):
        return  # deleted in this PR
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise LoadError(f"{path} exceeds {MAX_FILE_BYTES // (1024*1024)}MB, skipping")
    with open(path, "rb") as f:
        head = f.read(len(LFS_POINTER_PREFIX))
    if head == LFS_POINTER_PREFIX:
        raise LoadError(f"{path} is a Git LFS pointer — LFS content isn't fetched, skipping")


def metrics_or_error(path: str | None) -> tuple[dict | None, str | None]:
    """Returns (metrics, error_message). metrics is None if the file doesn't exist (deleted/new)."""
    if path is None:
        return None, None
    try:
        shape = load_shape(path)
        return compute_metrics(shape), None
    except LoadError as e:
        return None, str(e)


def main() -> int:
    base_sha = os.environ.get("BASE_SHA")
    if not base_sha:
        print("BASE_SHA not set, nothing to diff", file=sys.stderr)
        return 0

    try:
        files = changed_cad_files(base_sha)
    except subprocess.CalledProcessError as e:
        print(f"git diff failed: {e}", file=sys.stderr)
        return 0

    if not files:
        print("no CAD files changed")
        return 0

    results: dict[str, tuple[dict | None, dict | None]] = {}
    errors: list[str] = []

    for path in files:
        try:
            old_tmp = show_old_version(base_sha, path)
            check_new_version(path)
        except LoadError as e:
            errors.append(str(e))
            continue

        try:
            old_metrics, old_err = metrics_or_error(old_tmp)
            new_metrics, new_err = metrics_or_error(path if os.path.exists(path) else None)
        finally:
            if old_tmp:
                os.remove(old_tmp)

        if old_err:
            errors.append(f"{path} (old version): {old_err}")
        if new_err:
            errors.append(f"{path} (new version): {new_err}")

        # A file that legitimately has no metrics on either side (both errored) is
        # skipped from the report; one side failing still reports the other side.
        if old_metrics is None and new_metrics is None and (old_err or new_err):
            continue

        results[path] = (old_metrics, new_metrics)

    report = build_report(results)
    if errors:
        report += "\n### Skipped\n" + "\n".join(f"- {e}" for e in errors) + "\n"

    print(report)

    pr_number = os.environ.get("PR_NUMBER")
    if not pr_number:
        print("PR_NUMBER not set, skipping comment", file=sys.stderr)
        return 0

    try:
        subprocess.run(
            ["gh", "pr", "comment", pr_number, "--body", report],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Fork PRs get a read-only token and this will 403 — that's expected, not a failure.
        print(f"could not post PR comment (likely a fork PR with read-only token): {e}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
