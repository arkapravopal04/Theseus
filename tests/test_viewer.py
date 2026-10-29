"""The interactive 3D viewer page: its embedded geometry, and (when a browser is available) that it works."""

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.request

import numpy as np
import pytest

from theseus.images import render_file_images
from theseus.load import load_model
from theseus.render.scene import model_layer
from theseus.viewer import THREE_VERSION, write_viewer

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
CHECK_SCRIPT = os.path.join(os.path.dirname(__file__), "viewer_check.js")


def _data(page: str) -> dict:
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))


def test_embedded_geometry_round_trips(tmp_path):
    layer = model_layer(load_model(os.path.join(FIXTURES, "bracket_fillets.step")))
    out = tmp_path / "viewer.html"
    write_viewer(str(out), "bracket.step", {"after": [layer]}, (layer.vertices.min(0), layer.vertices.max(0)), [], [])
    page = out.read_text(encoding="utf-8")
    data = _data(page)
    decoded = np.frombuffer(base64.b64decode(data["views"]["after"][0]["positions"]), dtype=np.float32).reshape(-1, 3)
    assert np.allclose(decoded, layer.vertices, atol=1e-4)
    faces = np.frombuffer(base64.b64decode(data["views"]["after"][0]["indices"]), dtype=np.uint32).reshape(-1, 3)
    assert (faces == layer.faces).all()
    assert f"three@{THREE_VERSION}" in page


def test_markup_in_names_cannot_break_out_of_the_page(tmp_path):
    layer = model_layer(load_model(os.path.join(FIXTURES, "cube20.step")))
    out = tmp_path / "viewer.html"
    nasty = "</script><script>alert(1)</script>.step"
    write_viewer(str(out), nasty, {"after": [layer]}, ((0, 0, 0), (20, 20, 20)),
                 [{"n": 1, "what": nasty, "at": None}], [])
    page = out.read_text(encoding="utf-8")
    assert "<script>alert(1)" not in page
    assert _data(page)["title"] == nasty


def test_images_include_a_viewer_with_numbered_findings(tmp_path):
    from theseus.analyze import analyze_models

    old = load_model(os.path.join(FIXTURES, "cube20.step"))
    new = load_model(os.path.join(FIXTURES, "bracket_fillets.step"))
    files = render_file_images(old, new, analyze_models(old, new)["precise"], str(tmp_path), title="part.step")
    assert files["viewer"] == "viewer.html"
    data = _data((tmp_path / "viewer.html").read_text(encoding="utf-8"))
    assert set(data["views"]) == {"before", "after", "diff"}
    assert [f["n"] for f in data["findings"]] == list(range(1, len(data["findings"]) + 1))


def _three_source():
    local = os.environ.get("THESEUS_TEST_THREE_DIR")
    if local:
        return local
    try:
        urllib.request.urlopen(f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js", timeout=5)
        return ""
    except Exception:  # noqa: BLE001
        return None


def test_viewer_works_in_a_browser(tmp_path):
    """Load the page in Chromium, switch views, cut a section, jump to a finding and measure."""
    if not shutil.which("node"):
        pytest.skip("node isn't installed")
    if subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("playwright isn't installed")
    three = _three_source()
    if three is None:
        pytest.skip("three.js unreachable (no network) and THESEUS_TEST_THREE_DIR unset")

    from theseus.analyze import analyze_models

    old = load_model(os.path.join(FIXTURES, "cube20.step"))
    new = load_model(os.path.join(FIXTURES, "bracket_fillets.step"))
    render_file_images(old, new, analyze_models(old, new)["precise"], str(tmp_path), title="part.step")
    proc = subprocess.run(["node", CHECK_SCRIPT, str(tmp_path / "viewer.html"), three, str(tmp_path / "shot")],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["errors"] == []
    assert result["views"] == ["Before", "After", "Overlay", "Difference"]
    assert result["findings"] >= 1
    assert result["measured"].endswith(")") and "mm" in result["measured"]
