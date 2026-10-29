"""The commands for engineers' own machines: compare, diff, app, setup git/github, textconv."""

import json
import os
import subprocess
import sys
import threading
import urllib.request
import uuid

import pytest
import yaml

import theseus
from theseus import integrate
from theseus.app import App, parse_multipart
from theseus.cli import EXIT_ERROR, EXIT_OK, main
from theseus.formats import paths_filter
from theseus.textconv import summary

sys.path.insert(0, os.path.dirname(__file__))
from test_precision import plate, write_step  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(theseus.__file__)))


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo whose first commit holds PART.STEP (a plate with a Ø8 hole); cwd is the repo."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    write_step(plate(hole_r=4.0), str(root / "PART.STEP"))
    git("add", ".", cwd=root)
    git("commit", "-qm", "v1", cwd=root)
    monkeypatch.chdir(root)
    # git runs the textconv driver as a separate process: point it at this checkout.
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in (PACKAGE_PARENT, os.environ.get("PYTHONPATH")) if p))
    monkeypatch.setattr(integrate.shutil, "which", lambda name: None)
    return root


# --- textconv ---------------------------------------------------------------------------------

def test_textconv_is_stable_across_reexports(tmp_path):
    a, b = str(tmp_path / "a.step"), str(tmp_path / "b.step")
    write_step(plate(), a)
    write_step(plate(), b)
    assert summary(a) == summary(b)


def test_textconv_shows_a_2_micron_hole_change_as_one_face_line(tmp_path):
    a, b = str(tmp_path / "a.step"), str(tmp_path / "b.step")
    write_step(plate(hole_r=4.0), a)
    write_step(plate(hole_r=4.001), b)
    old, new = summary(a).splitlines(), summary(b).splitlines()
    faces = old.index("faces:")
    changed = [(o, n) for o, n in zip(old[faces:], new[faces:]) if o != n]
    assert changed == [("  cylinder along Z Ø8.000000 at (50.000000, 25.000000)",
                        "  cylinder along Z Ø8.002000 at (50.000000, 25.000000)")]


def test_textconv_of_a_mesh_and_of_a_non_cad_file(tmp_path):
    text = summary(fixture("cube20.stl"))
    assert "triangles: 12" in text and "geometry fingerprint:" in text
    junk = tmp_path / "notes.step"
    junk.write_text("not a step file")
    assert summary(str(junk)).startswith("theseus: couldn't read this file as CAD")


def test_textconv_command_prints_utf8(capsysbinary):
    assert main(["textconv", fixture("cube20.step")]) == EXIT_OK
    assert "volume: 8000.000000 mm³" in capsysbinary.readouterr().out.decode("utf-8")


# --- setup git / github -----------------------------------------------------------------------

def test_setup_git_makes_git_diff_show_the_change(repo):
    assert main(["setup", "git"]) == EXIT_OK
    write_step(plate(hole_r=4.002), str(repo / "PART.STEP"))
    diff = git("diff", "PART.STEP", cwd=repo)
    assert "-  cylinder along Z Ø8.000000 at (50.000000, 25.000000)" in diff
    assert "+  cylinder along Z Ø8.004000 at (50.000000, 25.000000)" in diff
    assert "ISO-10303" not in diff  # the summary, not the raw STEP text


def test_setup_git_is_idempotent_and_keeps_other_attributes(repo):
    (repo / ".gitattributes").write_text("*.png binary\n")
    main(["setup", "git"])
    main(["setup", "git"])
    text = (repo / ".gitattributes").read_text()
    assert text.startswith("*.png binary\n") and text.count(integrate.ATTRIBUTES_BEGIN) == 1
    assert "*.[sS][tT][eE][pP] diff=theseus" in text
    assert git("config", "diff.theseus.cachetextconv", cwd=repo).strip() == "true"


def test_setup_github_writes_a_working_workflow(repo, capsys):
    assert main(["setup", "github"]) == EXIT_OK
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "theseus.yml").read_text())
    assert workflow[True]["pull_request"]["paths"] == paths_filter()  # YAML 1.1 reads `on` as True
    assert workflow["permissions"] == {"contents": "write", "pull-requests": "write"}
    steps = workflow["jobs"]["review"]["steps"]
    assert steps[0]["with"] == {"fetch-depth": 0} and steps[1]["uses"] == integrate.ACTION
    assert "git add .github/workflows/theseus.yml" in capsys.readouterr().out


def test_setup_github_does_not_overwrite_without_force(repo, capsys):
    path = repo / ".github" / "workflows" / "theseus.yml"
    path.parent.mkdir(parents=True)
    path.write_text("name: mine\n")
    assert main(["setup", "github"]) == EXIT_ERROR
    assert "--force" in capsys.readouterr().err and path.read_text() == "name: mine\n"
    assert main(["setup", "github", "--force"]) == EXIT_OK
    assert integrate.ACTION in path.read_text()


def test_setup_github_enables_lfs_when_the_repo_uses_it(repo):
    (repo / ".gitattributes").write_text("*.step filter=lfs diff=lfs merge=lfs -text\n")
    main(["setup", "github"])
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "theseus.yml").read_text())
    assert workflow["jobs"]["review"]["steps"][0]["with"] == {"fetch-depth": 0, "lfs": True}


def test_setup_outside_a_repo_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert main(["setup", "git"]) == EXIT_ERROR
    assert "git repository" in capsys.readouterr().err


# --- diff -------------------------------------------------------------------------------------

def test_diff_reports_working_tree_changes_and_new_files(repo, tmp_path, capsys):
    write_step(plate(hole_x=50.5), str(repo / "PART.STEP"))
    (repo / "cad").mkdir()
    (repo / "cad" / "new_part.stl").write_bytes(open(fixture("cube20.stl"), "rb").read())  # untracked
    out = tmp_path / "out"
    assert main(["diff", "--no-open", "--no-images", "--out", str(out)]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "PART.STEP: changed, largest 0.500 mm" in printed and "cad/new_part.stl: new" in printed
    page = (out / "report.html").read_text(encoding="utf-8")
    assert "working tree" in page and "cad/new_part.stl" in page and "→ (50.500, 25.000)" in page


def test_diff_between_two_commits(repo, tmp_path):
    write_step(plate(hole_r=4.5), str(repo / "PART.STEP"))
    git("commit", "-qam", "v2", cwd=repo)
    git("mv", "PART.STEP", "Bracket.STEP", cwd=repo)
    git("commit", "-qm", "rename", cwd=repo)
    out = tmp_path / "out"
    assert main(["diff", "HEAD~2", "HEAD", "--no-open", "--no-images", "--out", str(out)]) == EXIT_OK
    page = (out / "report.html").read_text(encoding="utf-8")
    assert "Bracket.STEP" in page and "renamed from <code>PART.STEP</code>" in page and "Ø8.000 → 9.000" in page


def test_diff_with_nothing_changed(repo, tmp_path, capsys):
    assert main(["diff", "--no-open", "--out", str(tmp_path / "out")]) == EXIT_OK
    assert "no CAD file changes found" in capsys.readouterr().out
    assert "No CAD file changes to report" in (tmp_path / "out" / "report.html").read_text(encoding="utf-8")


# --- compare ----------------------------------------------------------------------------------

def test_compare_writes_an_html_report_with_images(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    out = tmp_path / "out"
    assert main(["compare", fixture("cube20.step"), fixture("bracket_fillets.step"), "--out", str(out)]) == EXIT_OK
    page = (out / "report.html").read_text(encoding="utf-8")
    assert "<table>" in page and 'class="badge changed"' in page and f"theseus {theseus.__version__}" in page
    for name in ("before.png", "after.png", "diff.png", "views.png", "viewer.html"):
        assert f'href="bracket_fillets.step/{name}"' in page
        assert (out / "bracket_fillets.step" / name).exists()
    assert opened == [(out / "report.html").resolve().as_uri()]


def test_compare_missing_file(tmp_path, capsys):
    assert main(["compare", fixture("nope.step"), fixture("cube20.step"), "--no-open"]) == EXIT_ERROR
    assert "no such file" in capsys.readouterr().err


def test_compare_unreadable_file(tmp_path, capsys):
    junk = tmp_path / "junk.step"
    junk.write_text("ISO-10303-21; garbage")
    args = ["compare", fixture("cube20.step"), str(junk), "--no-open", "--no-images", "--out", str(tmp_path / "o")]
    assert main(args) == EXIT_ERROR
    assert "junk.step (new version)" in capsys.readouterr().err


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"theseus {theseus.__version__}"
    assert main([]) == EXIT_OK
    help_text = capsys.readouterr().out
    for command in ("app", "compare", "diff", "setup", "textconv"):
        assert f"    {command}" in help_text


# --- app --------------------------------------------------------------------------------------

def _multipart(files: dict[str, str]) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    body = b""
    for field, path in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                 f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        body += open(path, "rb").read() + b"\r\n"
    return f"multipart/form-data; boundary={boundary}", body + f"--{boundary}--\r\n".encode()


def test_parse_multipart_keeps_binary_content_exact():
    content_type, body = _multipart({"old": fixture("cube20.stl"), "new": fixture("cube20.step")})
    files = parse_multipart(content_type, body)
    assert files["old"] == ("cube20.stl", open(fixture("cube20.stl"), "rb").read())
    assert files["new"][0] == "cube20.step"


@pytest.fixture
def app():
    server = App()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.server.shutdown()
    thread.join(10)


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_app_compares_dropped_files_and_deletes_them(app):
    status, page = _get(app.url)
    assert status == 200 and b"Compare two CAD files" in page

    content_type, body = _multipart({"old": fixture("cube20.step"), "new": fixture("bracket_fillets.step")})
    request = urllib.request.Request(app.url + "compare", data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(request) as r:
        data = json.load(r)
    assert data["summary"][0].startswith("bracket_fillets.step: changed")
    host = app.url.split(f"/{app.token}/")[0]
    status, report = _get(host + data["report"])
    assert status == 200 and b"cube20.step \xe2\x86\x92 bracket_fillets.step" in report
    status, png = _get(host + data["report"].replace("report.html", "bracket_fillets.step/diff.png"))
    assert status == 200 and png.startswith(b"\x89PNG")

    uploads = os.path.join(app.workdir, "uploads")
    assert not os.path.exists(uploads) or not os.listdir(uploads)  # nothing dropped is kept


def test_app_needs_the_token_and_stays_in_its_folder(app):
    host = app.url.split(f"/{app.token}/")[0]
    assert _get(host + "/")[0] == 404
    assert _get(host + "/wrong-token/")[0] == 404
    assert _get(app.url + "..%2F..%2F..%2Fetc%2Fpasswd")[0] == 404
    request = urllib.request.Request(host + "/compare", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(request)
    assert e.value.code == 404


def test_app_rejects_an_empty_drop(app):
    content_type, body = "multipart/form-data; boundary=x", b"--x--\r\n"
    request = urllib.request.Request(app.url + "compare", data=body, headers={"Content-Type": content_type})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(request)
    assert e.value.code == 400
