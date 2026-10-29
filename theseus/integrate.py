"""`theseus setup git` and `theseus setup github`: wire theseus into a repository in one command.

`setup git` makes plain `git diff`, `git log -p` and any git GUI show CAD files as readable text
(through `theseus textconv`), on any host — GitLab, Bitbucket, a server in the office, offline.
`setup github` writes the workflow that puts a report comment on every pull request.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

from theseus.formats import extensions, paths_filter

ATTRIBUTES_BEGIN = "# >>> theseus: show CAD file changes as text in git diff"
ATTRIBUTES_END = "# <<< theseus"
WORKFLOW_PATH = os.path.join(".github", "workflows", "theseus.yml")
ACTION = "arkapravopal04/theseus@v1"


def _case_glob(ext: str) -> str:
    # .gitattributes patterns are case-sensitive; PART.STEP needs to match as well as part.step.
    return "*." + "".join(f"[{c}{c.upper()}]" if c.isalpha() else c for c in ext[1:])


def attributes_block() -> str:
    lines = [ATTRIBUTES_BEGIN] + [f"{_case_glob(ext)} diff=theseus" for ext in extensions()] + [ATTRIBUTES_END]
    return "\n".join(lines) + "\n"


def textconv_command() -> str:
    """How git should call us: the `theseus` command if it's on PATH, else this interpreter."""
    if shutil.which("theseus"):
        return "theseus textconv"
    exe = sys.executable.replace("\\", "/")  # git runs textconv through sh, even on Windows
    return f"{shlex.quote(exe)} -m theseus textconv"


def _replace_block(text: str, block: str) -> str:
    if ATTRIBUTES_BEGIN in text and ATTRIBUTES_END in text:
        start = text.index(ATTRIBUTES_BEGIN)
        end = text.index(ATTRIBUTES_END, start) + len(ATTRIBUTES_END)
        if text[end:end + 1] == "\n":
            end += 1
        return text[:start] + block + text[end:]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + block


def setup_git(root: str) -> list[str]:
    """Add the diff driver to .gitattributes and git config. Safe to run again (it updates its
    own block instead of duplicating it). Returns what was done, one line each."""
    path = os.path.join(root, ".gitattributes")
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    new = _replace_block(old, attributes_block())
    done = []
    if new != old:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        done.append(f"{'updated' if old else 'created'} .gitattributes ({len(extensions())} CAD file types)")
    else:
        done.append(".gitattributes already up to date")

    command = textconv_command()
    for key, value in (("diff.theseus.textconv", command), ("diff.theseus.cachetextconv", "true")):
        subprocess.run(["git", "-C", root, "config", key, value], check=True)
    done.append(f"git config diff.theseus.textconv = {command}")
    return done


def _uses_lfs(root: str) -> bool:
    path = os.path.join(root, ".gitattributes")
    return os.path.exists(path) and "filter=lfs" in open(path, encoding="utf-8").read()


def workflow_yaml(lfs: bool = False) -> str:
    paths = "\n".join(f"      - '{glob}'" for glob in paths_filter())
    checkout_with = "{ fetch-depth: 0, lfs: true }" if lfs else "{ fetch-depth: 0 }"
    return f"""\
# Posts a theseus report on every pull request that changes a CAD file: what changed, where,
# and by how much, with before/after/difference images. Written by `theseus setup github`.
name: theseus
on:
  pull_request:
    # Path filters are case-sensitive; [sS]-style classes match PART.STEP and part.step alike.
    paths:
{paths}
permissions:
  contents: write        # images are stored on a separate `theseus-assets` branch
  pull-requests: write   # to post the report comment
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {checkout_with}   # full history: the diff needs the base commit
      - uses: {ACTION}
"""


class SetupError(Exception):
    pass


def setup_github(root: str, force: bool = False) -> list[str]:
    path = os.path.join(root, WORKFLOW_PATH)
    lfs = _uses_lfs(root)
    content = workflow_yaml(lfs)
    if os.path.exists(path):
        if open(path, encoding="utf-8").read() == content:
            return [f"{WORKFLOW_PATH} already up to date"]
        if not force:
            raise SetupError(f"{WORKFLOW_PATH} already exists and differs; re-run with --force to replace it")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return [f"wrote {WORKFLOW_PATH}" + (" (with Git LFS enabled, since this repo uses it)" if lfs else "")]
