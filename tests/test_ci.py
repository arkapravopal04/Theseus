"""The GitHub Action path (theseus.ci, theseus.gitops, theseus.isolate) against real git repos."""

import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from theseus import ci
from theseus.compare import REPORT_MARKER
from theseus.formats import paths_filter
from theseus.gitops import Change, changed_files
from theseus.isolate import analyze_isolated

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty git repo as the cwd, with the Action's env vars cleared."""
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "theseus-test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")
    for var in ("BASE_SHA", "BEFORE_SHA", "HEAD_SHA", "PR_NUMBER", "GITHUB_REPOSITORY", "THESEUS_MAX_FILE_MB"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.chdir(path)
    git("init", "-q", "-b", "main")
    return path


def commit(message: str) -> str:
    git("add", "-A")
    git("commit", "-q", "-m", message)
    return git("rev-parse", "HEAD")


def lfs_pointer(n: int) -> str:
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{n:064d}\nsize 1234\n"


def run_action(capsys) -> str:
    assert ci.main() == 0
    return capsys.readouterr().out


def test_changed_files_follows_renames_and_case(repo):
    shutil.copy(fixture("cube20.step"), "OLD.STEP")
    shutil.copy(fixture("cube20.stl"), "keep.stl")
    (repo / "notes.txt").write_text("not cad")
    base = commit("base")
    git("mv", "OLD.STEP", "NEW.STEP")
    os.remove("keep.stl")
    (repo / "notes.txt").write_text("still not cad")
    commit("change")
    changes = changed_files(base)
    assert Change("R", "NEW.STEP", "OLD.STEP") in changes
    assert Change("D", "keep.stl", "keep.stl") in changes
    assert all(c.path != "notes.txt" for c in changes)


def test_end_to_end_report(repo, capsys, monkeypatch):
    shutil.copy(fixture("cube20.step"), "part.step")
    shutil.copy(fixture("cube20.step"), "OLDNAME.STEP")
    shutil.copy(fixture("cube20.stl"), "gone.stl")
    (repo / "big.step").write_text(lfs_pointer(0))
    base = commit("base")

    shutil.copy(fixture("bracket_fillets.step"), "part.step")
    git("mv", "OLDNAME.STEP", "NEWNAME.STEP")
    git("rm", "-q", "gone.stl")
    (repo / "broken.step").write_text("ISO-10303-21;\ngarbage\n")
    (repo / "build.obj").write_bytes(b"\x4c\x01\x03\x00" + bytes(64))  # compiled object, not CAD
    (repo / "big.step").write_text(lfs_pointer(1))
    commit("change")

    monkeypatch.setenv("BASE_SHA", base)
    out = run_action(capsys)

    assert out.startswith(REPORT_MARKER)
    assert "`NEWNAME.STEP` (renamed from `OLDNAME.STEP`) — identical (byte-for-byte)" in out
    assert "`gone.stl` (deleted)" in out
    assert "`part.step` (changed)" in out and "Volume: 8000.000 → 7340.362" in out
    skipped = out.split("### Skipped", 1)[1]
    assert "broken.step (new version): failed to parse broken.step" in skipped
    assert "big.step is stored in Git LFS" in skipped
    assert "build.obj" not in out  # a non-CAD file with a CAD extension is ignored, not "skipped"
    assert "/tmp" not in out  # errors name the repo file, not the temp copy
    with open(os.environ["GITHUB_STEP_SUMMARY"], encoding="utf-8") as f:
        assert f.read().strip() == out.strip()


def test_unreadable_new_version_is_skipped_not_reported_deleted(repo, capsys, monkeypatch):
    shutil.copy(fixture("cube20.step"), "part.step")
    base = commit("base")
    (repo / "part.step").write_text("ISO-10303-21;\ntruncated export\n")
    commit("broken export")
    monkeypatch.setenv("BASE_SHA", base)
    out = run_action(capsys)
    assert "(deleted)" not in out
    assert "part.step (new version): failed to parse" in out


def test_diff_uses_merge_base(repo, capsys, monkeypatch):
    """Changes merged into the base branch after the PR branched aren't the PR's."""
    shutil.copy(fixture("cube20.step"), "part.step")
    commit("fork point")
    git("checkout", "-q", "-b", "feature")
    shutil.copy(fixture("bracket_fillets.step"), "part.step")
    commit("feature change")
    git("checkout", "-q", "main")
    shutil.copy(fixture("cube20.stl"), "landed_on_main.stl")
    base_tip = commit("unrelated change on main")
    git("checkout", "-q", "feature")

    monkeypatch.setenv("BASE_SHA", base_tip)
    out = run_action(capsys)
    assert "part.step" in out
    assert "landed_on_main.stl" not in out


def test_push_event(repo, capsys, monkeypatch):
    shutil.copy(fixture("cube20.step"), "part.step")
    before = commit("before")
    shutil.copy(fixture("bracket_fillets.step"), "part.step")
    after = commit("after")
    monkeypatch.setenv("BEFORE_SHA", before)
    monkeypatch.setenv("HEAD_SHA", after)
    assert "`part.step` (changed)" in run_action(capsys)


def test_new_branch_push_has_nothing_to_diff(repo, capsys, monkeypatch):
    monkeypatch.setenv("BEFORE_SHA", "0" * 40)
    assert run_action(capsys) == ""


def test_oversized_file_is_skipped(repo, capsys, monkeypatch):
    shutil.copy(fixture("cube20.step"), "part.step")
    base = commit("base")
    shutil.copy(fixture("bracket_fillets.step"), "part.step")  # ~73KB
    commit("change")
    monkeypatch.setenv("BASE_SHA", base)
    monkeypatch.setenv("THESEUS_MAX_FILE_MB", "0.05")
    assert "part.step exceeds" in run_action(capsys)


def test_isolated_timeout_becomes_an_error_not_a_crash():
    result = analyze_isolated(fixture("cube20.step"), fixture("bracket_fillets.step"), timeout=0.01)
    assert result["new"] is None and "timed out" in result["new_error"] and "timed out" in result["old_error"]


def test_isolated_analysis_matches_in_process():
    result = analyze_isolated(fixture("cube20.step"), fixture("bracket_fillets.step"))
    assert result["old_error"] is None and result["new_error"] is None
    assert result["old"]["volume_mm3"] == pytest.approx(8000, abs=1e-3)
    assert result["precise"]["method"] == "exact B-rep" and not result["precise"]["identical"]


def test_isolated_crash_is_reported_per_version(monkeypatch):
    import subprocess as sp
    import sys

    from theseus import isolate

    real_run = sp.run

    def segfaulting_child(cmd, **kwargs):
        return real_run([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"], **kwargs)

    monkeypatch.setattr(isolate.subprocess, "run", segfaulting_child)
    result = isolate.analyze_isolated(None, fixture("cube20.step"))
    assert "crashed" in result["new_error"] and "SIGSEGV" in result["new_error"]
    assert result["old_error"] is None


class FakeGh:
    def __init__(self, existing_ids):
        self.existing_ids = existing_ids
        self.calls = []
        self.bodies = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        body_arg = next((a for a in cmd if a.startswith("body=@")), None)
        if body_arg:
            with open(body_arg[len("body=@"):], encoding="utf-8") as f:
                self.bodies.append(f.read())
        return SimpleNamespace(stdout="\n".join(self.existing_ids))


def test_upsert_edits_existing_comment():
    gh = FakeGh(["111", "222"])
    ci.upsert_comment("o/r", "7", "report", run=gh)
    assert gh.calls[-1][:4] == ["gh", "api", "-X", "PATCH"]
    assert gh.calls[-1][4] == "repos/o/r/issues/comments/111"
    assert gh.bodies == ["report"]


def test_upsert_creates_comment_when_none_exists():
    gh = FakeGh([])
    ci.upsert_comment("o/r", "7", "report", run=gh)
    assert gh.calls[-1][:5] == ["gh", "api", "-X", "POST", "repos/o/r/issues/7/comments"]


def test_upsert_without_create_never_posts_new_comment():
    gh = FakeGh([])
    ci.upsert_comment("o/r", "7", "report", create=False, run=gh)
    assert len(gh.calls) == 1  # only the lookup


def test_long_report_is_trimmed_to_fit_a_comment():
    metrics = {"volume_mm3": 1.0, "bbox": (0, 0, 0, 1, 1, 1), "com": (0.5, 0.5, 0.5), "solid_count": 1}
    results = {f"parts/{'x' * 200}/{i}.step": (None, metrics) for i in range(1000)}
    comment = ci.fit_comment(results, {}, [], "https://github.com/o/r/actions/runs/1")
    assert len(comment) <= ci.COMMENT_BUDGET
    assert comment.startswith(REPORT_MARKER)
    assert re.search(r"and \d+ more file\(s\)", comment)
    assert "actions/runs/1" in comment


def _globs_in(path: str) -> list[str]:
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return re.findall(r"- '(\*\*\.[^']+)'", f.read())


@pytest.mark.parametrize("path", [".github/workflows/theseus.yml", "README.md"])
def test_documented_paths_filter_matches_supported_formats(path):
    assert _globs_in(path) == paths_filter()
