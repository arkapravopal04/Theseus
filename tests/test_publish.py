"""Publishing report images to the theseus-assets branch, against an in-memory fake of GitHub's Git Data API."""

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from theseus import ci
from theseus.publish import ASSETS_BRANCH, PublishError, publish_images, slug


class FakeGitHub:
    """Just enough of the Git Data API (blobs, trees, commits, one ref) to check what ends up on
    the branch. Responses are what the `--jq` filters theseus uses would print."""

    def __init__(self, deny=False, races=0):
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.tip = None
        self.deny = deny
        self.races = races  # how many ref updates to reject because "someone else" pushed first
        self.calls = []

    @staticmethod
    def _sha(data) -> str:
        return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def files(self) -> dict:
        return dict(self.trees[self.commits[self.tip]["tree"]]) if self.tip else {}

    def _commit(self, files: dict, parents: list) -> str:
        tree = self._sha(files)
        self.trees[tree] = dict(files)
        sha = self._sha([tree, parents, len(self.commits)])
        self.commits[sha] = {"tree": tree, "parents": parents}
        return sha

    def __call__(self, cmd, **kwargs):
        method, endpoint = cmd[3], cmd[4]
        payload = None
        if "--input" in cmd:
            with open(cmd[cmd.index("--input") + 1], encoding="utf-8") as f:
                payload = json.load(f)
        self.calls.append((method, endpoint))
        if self.deny:
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403: Resource not accessible by integration")
        out = self._handle(method, endpoint, payload)
        if out is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 404: Not Found")
        if isinstance(out, SimpleNamespace):
            return out
        return SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")

    def _handle(self, method, endpoint, payload):
        path = endpoint.split("/git/", 1)[1]
        if method == "GET" and path == f"ref/heads/{ASSETS_BRANCH}":
            return self.tip
        if method == "POST" and path == "blobs":
            sha = self._sha(payload["content"])
            self.blobs[sha] = payload["content"]
            return sha
        if method == "GET" and path.startswith("commits/"):
            return self.commits[path.split("/")[1]]["tree"]
        if method == "GET" and path.startswith("trees/"):
            return "\n".join(self.trees[path.split("/")[1].split("?")[0]])
        if method == "POST" and path == "trees":
            files = dict(self.trees[payload["base_tree"]]) if "base_tree" in payload else {}
            for entry in payload["tree"]:
                if entry["sha"] is None:
                    files.pop(entry["path"], None)
                else:
                    files[entry["path"]] = entry["sha"]
            sha = self._sha(files)
            self.trees[sha] = files
            return sha
        if method == "POST" and path == "commits":
            sha = self._sha([payload["tree"], payload["parents"], len(self.commits)])
            self.commits[sha] = {"tree": payload["tree"], "parents": payload["parents"]}
            return sha
        if method == "PATCH" and path == f"refs/heads/{ASSETS_BRANCH}":
            if self.races:
                # Another PR's run published first: move the branch, reject this fast-forward.
                self.races -= 1
                files = self.files()
                files["pr-99/abcdef0/other.step/diff.png"] = "elsewhere"
                self.tip = self._commit(files, [self.tip])
                return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 422: Update is not a fast forward")
            assert self.commits[payload["sha"]]["parents"] == [self.tip]
            self.tip = payload["sha"]
            return "{}"
        if method == "POST" and path == "refs":
            self.tip = payload["sha"]
            return "{}"
        raise AssertionError(f"unexpected call {method} {endpoint}")


@pytest.fixture
def pngs(tmp_path):
    def make(*names):
        out = {}
        for name in names:
            p = tmp_path / name.replace("/", "_")
            p.write_bytes(os.urandom(64))
            out[name] = str(p)
        return out
    return make


def test_first_publish_creates_the_branch(pngs):
    gh = FakeGitHub()
    urls = publish_images("o/r", "pr-7/1234567", pngs("part.step/diff.png", "part.step/before.png"), run=gh)
    assert set(gh.files()) == {"pr-7/1234567/part.step/diff.png", "pr-7/1234567/part.step/before.png"}
    assert urls["part.step/diff.png"] == f"https://github.com/o/r/raw/{ASSETS_BRANCH}/pr-7/1234567/part.step/diff.png"
    assert gh.commits[gh.tip]["parents"] == []  # an orphan branch: no relation to the user's code


def test_republishing_replaces_the_prs_old_images_only(pngs):
    gh = FakeGitHub()
    publish_images("o/r", "pr-7/aaaaaaa", pngs("a.step/diff.png"), run=gh)
    publish_images("o/r", "pr-8/bbbbbbb", pngs("b.step/diff.png"), run=gh)
    publish_images("o/r", "pr-7/ccccccc", pngs("a.step/after.png"), run=gh)
    assert set(gh.files()) == {"pr-8/bbbbbbb/b.step/diff.png", "pr-7/ccccccc/a.step/after.png"}


def test_concurrent_publish_is_retried_not_clobbered(pngs):
    gh = FakeGitHub()
    publish_images("o/r", "pr-7/aaaaaaa", pngs("a.step/diff.png"), run=gh)
    gh.races = 1
    publish_images("o/r", "pr-7/bbbbbbb", pngs("a.step/diff.png"), run=gh)
    assert set(gh.files()) == {"pr-99/abcdef0/other.step/diff.png", "pr-7/bbbbbbb/a.step/diff.png"}


def test_read_only_token_raises(pngs):
    with pytest.raises(PublishError, match="403"):
        publish_images("o/r", "pr-7/aaaaaaa", pngs("a.step/diff.png"), run=FakeGitHub(deny=True))


def test_slug():
    assert slug("cad/Bracket v2.STEP") == "cad-Bracket-v2.STEP"


def _local_images(tmp_path):
    folder = tmp_path / "imgs"
    folder.mkdir()
    files = {}
    for kind in ("before", "after", "diff"):
        p = folder / f"{kind}.png"
        p.write_bytes(b"png")
        files[kind] = str(p)
    return {"cad/part.step": files}


def _git_repo(tmp_path, monkeypatch):
    for var, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_COMMITTER_NAME", "t"),
                       ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(var, value)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], check=True)


def test_ci_links_published_images(tmp_path, monkeypatch):
    _git_repo(tmp_path, monkeypatch)
    gh = FakeGitHub()
    links, footer = ci.publish(_local_images(tmp_path), "inline", "o/r", "7", "HEAD", "https://run", run=gh)
    assert footer is None
    assert links["cad/part.step"]["diff"].endswith("/cad-part.step/diff.png")
    assert links["cad/part.step"]["diff"].startswith(f"https://github.com/o/r/raw/{ASSETS_BRANCH}/pr-7/")


def test_ci_falls_back_to_the_artifact(tmp_path, monkeypatch):
    _git_repo(tmp_path, monkeypatch)
    links, footer = ci.publish(_local_images(tmp_path), "inline", "o/r", "7", "HEAD", "https://run",
                               run=FakeGitHub(deny=True))
    assert links == {}
    assert ci.ARTIFACT_NAME in footer and "https://run" in footer


def test_artifact_mode_never_calls_github(tmp_path):
    gh = FakeGitHub()
    links, footer = ci.publish(_local_images(tmp_path), "artifact", "o/r", "7", "HEAD", None, run=gh)
    assert links == {} and ci.ARTIFACT_NAME in footer and not gh.calls
