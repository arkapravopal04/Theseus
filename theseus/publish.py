"""Host report images on the repo itself, so a PR comment can show them inline.

GitHub comments can't carry uploaded files from an Action, so images go on a dedicated orphan
branch, `theseus-assets`, written through the Git Data API (blobs → tree → commit → ref) with
`gh api`. Nothing touches the checkout or any of the user's branches. Each PR has one folder;
publishing replaces its previous contents, so the branch only ever holds the latest images per PR.

Needs `contents: write`. Fork PRs get a read-only token, so this raises PublishError and the
caller falls back to the workflow artifact.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile

ASSETS_BRANCH = "theseus-assets"
_ATTEMPTS = 3  # the branch can move under us when several PRs publish at once


class PublishError(Exception):
    pass


def slug(path: str) -> str:
    """A file path as one URL-safe folder name: cad/Bracket v2.STEP -> cad-Bracket-v2.STEP."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-") or "file"


def _api(run, method: str, endpoint: str, payload: dict | None = None, jq: str | None = None) -> str:
    cmd = ["gh", "api", "-X", method, endpoint]
    body = None
    try:
        if payload is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(payload, f)
                body = f.name
            cmd += ["--input", body]
        if jq:
            cmd += ["--jq", jq]
        proc = run(cmd, capture_output=True, text=True)
    finally:
        if body:
            os.remove(body)
    if proc.returncode != 0:
        raise PublishError(f"gh api {method} {endpoint}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def _tip(run, repo: str) -> str | None:
    try:
        return _api(run, "GET", f"repos/{repo}/git/ref/heads/{ASSETS_BRANCH}", jq=".object.sha") or None
    except PublishError as e:
        if "404" in str(e) or "Not Found" in str(e):
            return None
        raise


def publish_images(repo: str, folder: str, files: dict[str, str], run=subprocess.run) -> dict[str, str]:
    """Make `folder` on the assets branch hold exactly `files` ({path in folder: local file}),
    replacing whatever else was under the folder's first path component (the PR's older
    images). Returns {path in folder: URL that renders in comments}."""
    root = folder.split("/", 1)[0] + "/"
    blobs = {}
    for rel, local in files.items():
        with open(local, "rb") as f:
            content = base64.b64encode(f.read()).decode("ascii")
        blobs[f"{folder}/{rel}"] = _api(run, "POST", f"repos/{repo}/git/blobs",
                                        {"content": content, "encoding": "base64"}, jq=".sha")

    for attempt in range(_ATTEMPTS):
        tip = _tip(run, repo)
        entries = [{"path": p, "mode": "100644", "type": "blob", "sha": sha} for p, sha in blobs.items()]
        tree_request = {"tree": entries}
        if tip:
            base_tree = _api(run, "GET", f"repos/{repo}/git/commits/{tip}", jq=".tree.sha")
            existing = _api(run, "GET", f"repos/{repo}/git/trees/{base_tree}?recursive=1",
                            jq='.tree[] | select(.type == "blob") | .path').splitlines()
            # Drop this PR's previous images ("sha": null deletes a path from the base tree).
            entries += [{"path": p, "mode": "100644", "type": "blob", "sha": None}
                        for p in existing if p.startswith(root) and p not in blobs]
            tree_request["base_tree"] = base_tree
        tree = _api(run, "POST", f"repos/{repo}/git/trees", tree_request, jq=".sha")
        commit = _api(run, "POST", f"repos/{repo}/git/commits",
                      {"message": f"theseus: images for {folder}", "tree": tree, "parents": [tip] if tip else []},
                      jq=".sha")
        try:
            if tip:
                # Not forced: if another run moved the branch since we read it, rebuild on top.
                _api(run, "PATCH", f"repos/{repo}/git/refs/heads/{ASSETS_BRANCH}", {"sha": commit, "force": False})
            else:
                _api(run, "POST", f"repos/{repo}/git/refs", {"ref": f"refs/heads/{ASSETS_BRANCH}", "sha": commit})
            break
        except PublishError:
            if attempt == _ATTEMPTS - 1:
                raise
    return {rel: f"https://github.com/{repo}/raw/{ASSETS_BRANCH}/{folder}/{rel}" for rel in files}
