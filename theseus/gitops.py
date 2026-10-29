"""Git plumbing shared by the GitHub Action and the CLI: which CAD files changed between two
revisions, and their exact content on each side — following renames and resolving Git LFS.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from theseus.formats import is_candidate
from theseus.model import LoadError

ZERO_SHA = "0" * 40
LFS_POINTER_PREFIX = b"version https://git-lfs"


@dataclass(frozen=True)
class Change:
    status: str  # "A" added, "M" modified, "D" deleted, "R" renamed
    path: str  # path on the new side (for a delete, the deleted path)
    old_path: str | None  # path on the old side; None for an added file


def _git(*args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=input, capture_output=True, check=True).stdout


def merge_base(a: str, b: str) -> str:
    """The commit a branch forked from. Diffing against it (not the base branch's tip) keeps
    changes that landed on the base branch meanwhile out of the PR's report."""
    try:
        return _git("merge-base", a, b).decode().strip()
    except subprocess.CalledProcessError:
        return a  # unrelated histories or a shallow clone: best effort


def changed_files(base: str, head: str | None = "HEAD", keep: Callable[[str], bool] = is_candidate) -> list[Change]:
    """CAD files that differ between two revisions, with renames detected (so a renamed part is
    diffed against its own history instead of showing up as a brand-new file).

    head=None compares against the working tree instead of a commit: uncommitted edits, and new
    CAD files that haven't been `git add`ed yet (a fresh export is usually untracked)."""
    tokens = _git("diff", "--name-status", "-z", "-M", base, *([head] if head else [])).decode().split("\0")
    changes = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i][0]
        if status in "RC":
            old, new = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old = new = tokens[i + 1]
            i += 2
        if not (keep(new) or keep(old)):
            continue
        if status == "R":
            changes.append(Change("R", new, old))
        elif status in "AC":  # a copy is a new file as far as review is concerned
            changes.append(Change("A", new, None))
        elif status == "D":
            changes.append(Change("D", old, old))
        else:  # M, T (type change)
            changes.append(Change("M", new, old))
    if head is None:
        untracked = _git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
        changes += [Change("A", p, None) for p in untracked if p and keep(p)]
    return changes


def repo_root() -> str:
    return _git("rev-parse", "--show-toplevel").decode().strip()


def _lfs_size(pointer: bytes) -> int | None:
    m = re.search(rb"^size (\d+)", pointer, re.MULTILINE)
    return int(m.group(1)) if m else None


def _resolve_lfs(pointer: bytes, path: str) -> bytes:
    if shutil.which("git-lfs") is None:
        raise LoadError(f"{path} is stored in Git LFS and git-lfs isn't installed — skipping")
    proc = subprocess.run(["git", "lfs", "smudge", "--", path], input=pointer, capture_output=True)
    if proc.returncode != 0 or proc.stdout.startswith(LFS_POINTER_PREFIX):
        lines = proc.stderr.decode(errors="replace").strip().splitlines()
        detail = next((line for line in lines if "error" in line.lower()), lines[-1] if lines else "")
        raise LoadError(f"{path} is stored in Git LFS and its content couldn't be fetched "
                        f"(does the checkout step have `lfs: true`?)" + (f": {detail}" if detail else ""))
    return proc.stdout


def materialize(rev: str | None, path: str, dest_dir: str, max_bytes: int) -> str | None:
    """Write `path` as of `rev` (None: the working tree, relative to the repo root) into dest_dir,
    keeping its file name so its extension still identifies the format. Returns the written path,
    or None if the file doesn't exist there.

    Reads the raw blob with `git cat-file` rather than `git show`, so a textconv diff driver
    configured for CAD files can never substitute its text summary for the real geometry.
    """
    limit_mb = max_bytes // (1024 * 1024)
    if rev is None:
        if not os.path.isfile(path):
            return None
        if os.path.getsize(path) > max_bytes:
            raise LoadError(f"{path} exceeds {limit_mb}MB, skipping")
        with open(path, "rb") as f:
            content = f.read()
    else:
        spec = f"{rev}:{path}"
        try:
            size = int(_git("cat-file", "-s", spec))
        except subprocess.CalledProcessError:
            return None
        if size > max_bytes:
            raise LoadError(f"{path} exceeds {limit_mb}MB, skipping")
        content = _git("cat-file", "blob", spec)
    if content.startswith(LFS_POINTER_PREFIX):
        real_size = _lfs_size(content)
        if real_size is not None and real_size > max_bytes:
            raise LoadError(f"{path} (Git LFS) exceeds {limit_mb}MB, skipping")
        content = _resolve_lfs(content, path)

    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, os.path.basename(path))
    with open(out, "wb") as f:
        f.write(content)
    return out
