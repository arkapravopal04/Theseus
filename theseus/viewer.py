"""A self-contained interactive 3D page for one file's change: rotate, zoom, switch between
before / after / overlay / difference, slice with a section plane, measure, and jump to each
numbered finding.

The geometry is embedded in the page (the same layers the report images are drawn from), so the
file works offline once three.js is cached; three.js itself loads from jsDelivr at a pinned version.
"""

from __future__ import annotations

import base64
import html
import json

import numpy as np

from theseus.render.scene import EDGE_COLOUR, Layer

THREE_VERSION = "0.160.0"
MAX_TRIANGLES = 1_500_000  # beyond this the page gets too heavy to open comfortably


def _b64(array: np.ndarray, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


def _layer_json(layer: Layer) -> dict:
    out = {
        "positions": _b64(layer.vertices, np.float32),
        "indices": _b64(layer.faces, np.uint32),
        "color": list(layer.colour),
        "opacity": layer.opacity,
        "colors": None,
        "edges": _b64(layer.edges.reshape(-1, 3), np.float32) if layer.edges is not None and len(layer.edges) else None,
    }
    if layer.scalars is not None:
        lo, hi = layer.value_range
        rgb = layer.colormap(np.clip((layer.scalars - lo) / ((hi - lo) or 1), 0, 1))[:, :3]
        out["colors"] = _b64(rgb, np.float32)
    return out


def triangle_count(*layer_lists: list[Layer]) -> int:
    return sum(len(layer.faces) for layers in layer_lists for layer in layers)


def write_viewer(out_path: str, title: str, views: dict[str, list[Layer]], bounds, findings: list[dict],
                 legend: list[tuple[tuple, str]], summary: str = "") -> None:
    """views: {"before"|"after"|"diff": layers}. findings: [{"n": 1, "what": ..., "at": [x, y, z] | None}]."""
    data = {
        "title": title,
        "summary": summary,
        "views": {name: [_layer_json(layer) for layer in layers] for name, layers in views.items()},
        "bounds": [list(map(float, bounds[0])), list(map(float, bounds[1]))],
        "findings": findings,
        "legend": [["#%02x%02x%02x" % tuple(int(c * 255) for c in colour), label] for colour, label in legend],
        "edgeColor": "#%02x%02x%02x" % tuple(int(c * 255) for c in EDGE_COLOUR),
    }
    page = _TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__THREE__", THREE_VERSION)
    # Every "<" escaped (still valid JSON): no file name can close or open a tag in the page.
    page = page.replace("__DATA__", json.dumps(data).replace("<", "\\u003c"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — theseus 3D diff</title>
<style>
  :root { --bg: #f6f7f9; --panel: #ffffff; --text: #111827; --muted: #6b7280; --line: #e5e7eb; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0f1115; --panel: #171a21; --text: #e5e7eb; --muted: #9ca3af; --line: #2a2f3a; --accent: #60a5fa; } }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text); font: 14px/1.4 system-ui, sans-serif; }
  #app { display: grid; grid-template-columns: 1fr 320px; height: 100%; }
  #stage { position: relative; overflow: hidden; }
  #stage canvas { display: block; }
  #bar { position: absolute; top: 12px; left: 12px; right: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .group { display: flex; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  .group button { border: 0; background: none; color: var(--text); padding: 7px 12px; cursor: pointer; font: inherit; }
  .group button + button { border-left: 1px solid var(--line); }
  .group button.on { background: var(--accent); color: #fff; }
  .group label { padding: 7px 10px; display: flex; gap: 6px; align-items: center; }
  #measure-out { position: absolute; bottom: 12px; left: 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; display: none; }
  #side { border-left: 1px solid var(--line); background: var(--panel); padding: 16px; overflow-y: auto; }
  h1 { font-size: 15px; margin: 0 0 4px; word-break: break-all; }
  .muted { color: var(--muted); }
  ol { padding-left: 0; list-style: none; }
  li.finding { padding: 8px; border-radius: 6px; cursor: pointer; display: flex; gap: 8px; }
  li.finding:hover { background: var(--bg); }
  .num { flex: none; width: 22px; height: 22px; border-radius: 50%; background: #1f2937; color: #fff; font-size: 12px; font-weight: 600; display: grid; place-items: center; }
  .legend span { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
  .marker { position: absolute; transform: translate(-50%, -50%); pointer-events: none; }
  @media (max-width: 760px) { #app { grid-template-columns: 1fr; grid-template-rows: 60% 40%; } #side { border-left: 0; border-top: 1px solid var(--line); } }
</style>
</head>
<body>
<div id="app">
  <div id="stage">
    <div id="bar">
      <div class="group" id="views"></div>
      <div class="group">
        <label>Section <select id="axis"><option value="">off</option><option>X</option><option>Y</option><option>Z</option></select></label>
        <label><input id="cut" type="range" min="0" max="1000" value="500" disabled></label>
      </div>
      <div class="group"><button id="measure">Measure</button><button id="reset">Reset view</button></div>
    </div>
    <div id="measure-out"></div>
  </div>
  <aside id="side">
    <h1 id="title"></h1>
    <div class="muted" id="summary"></div>
    <div class="legend" id="legend"></div>
    <h3>Findings</h3>
    <ol id="findings"></ol>
    <p class="muted">Drag to rotate · right-drag to pan · scroll to zoom. Units: mm.</p>
  </aside>
</div>
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@__THREE__/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@__THREE__/examples/jsm/" } }
</script>
<script id="data" type="application/json">__DATA__</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const data = JSON.parse(document.getElementById("data").textContent);
const stage = document.getElementById("stage");
document.getElementById("title").textContent = data.title;
document.getElementById("summary").textContent = data.summary;
document.getElementById("legend").innerHTML = data.legend.map(([c, l]) => `<div><span style="background:${c}"></span>${l}</div>`).join("");

const decode = (b64, Type) => { const bin = atob(b64); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); return new Type(bytes.buffer); };

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.localClippingEnabled = true;
stage.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(getComputedStyle(document.body).getPropertyValue("--bg").trim() || "#f6f7f9");
scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.6));
const key = new THREE.DirectionalLight(0xffffff, 1.6); scene.add(key);

const [lo, hi] = data.bounds.map(v => new THREE.Vector3(...v));
const center = lo.clone().add(hi).multiplyScalar(0.5);
const radius = Math.max(hi.clone().sub(lo).length() / 2, 1e-6);
const camera = new THREE.PerspectiveCamera(35, 1, radius / 1000, radius * 100);
const controls = new OrbitControls(camera, renderer.domElement);
camera.up.set(0, 0, 1);
function resetView() {
  controls.target.copy(center);
  camera.position.copy(center).add(new THREE.Vector3(1, -1, 0.8).normalize().multiplyScalar(radius * 3.2));
  controls.update();
}
resetView();

const clip = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);
const groups = {};
for (const [name, layers] of Object.entries(data.views)) {
  const group = new THREE.Group();
  for (const layer of layers) {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(decode(layer.positions, Float32Array), 3));
    geom.setIndex(new THREE.BufferAttribute(decode(layer.indices, Uint32Array), 1));
    if (layer.colors) geom.setAttribute("color", new THREE.BufferAttribute(decode(layer.colors, Float32Array), 3));
    geom.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ color: layer.colors ? 0xffffff : new THREE.Color(...layer.color),
      vertexColors: !!layer.colors, flatShading: true, roughness: 0.7, metalness: 0, side: THREE.DoubleSide,
      transparent: layer.opacity < 1, opacity: layer.opacity, depthWrite: layer.opacity >= 1,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
    group.add(new THREE.Mesh(geom, mat));
    if (layer.edges) {
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.BufferAttribute(decode(layer.edges, Float32Array), 3));
      group.add(new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: data.edgeColor,
        transparent: layer.opacity < 1, opacity: Math.min(1, layer.opacity + 0.25) })));
    }
  }
  groups[name] = group;
  scene.add(group);
}
// Overlay: the old version as a red ghost over the new one.
if (groups.before && groups.after) {
  const ghost = groups.before.clone(true);
  ghost.traverse(o => { if (o.material) { o.material = o.material.clone(); o.material.transparent = true;
    o.material.opacity = o.isLineSegments ? 0.35 : 0.3; o.material.depthWrite = false;
    // Drawn slightly toward the camera: where nothing changed, the ghost and the new part share
    // surfaces exactly and would otherwise flicker against each other.
    o.material.polygonOffset = true; o.material.polygonOffsetFactor = -2; o.material.polygonOffsetUnits = -2;
    if (o.isMesh) o.material.color = new THREE.Color(0.86, 0.22, 0.2); } });
  const overlay = new THREE.Group(); overlay.add(ghost, groups.after.clone(true));
  groups.overlay = overlay; scene.add(overlay);
}
for (const o of Object.values(groups)) o.traverse(m => { if (m.material) m.material.clippingPlanes = []; });

const labels = { before: "Before", after: "After", overlay: "Overlay", diff: "Difference" };
const viewBar = document.getElementById("views");
const order = ["before", "after", "overlay", "diff"].filter(n => groups[n]);
function show(name) {
  for (const [n, g] of Object.entries(groups)) g.visible = n === name;
  for (const b of viewBar.children) b.classList.toggle("on", b.dataset.view === name);
}
for (const n of order) { const b = document.createElement("button"); b.textContent = labels[n]; b.dataset.view = n;
  b.onclick = () => show(n); viewBar.appendChild(b); }
show(groups.diff ? "diff" : order[order.length - 1]);

// Section plane.
const axisSel = document.getElementById("axis"), cut = document.getElementById("cut");
function applyCut() {
  const axis = axisSel.value; cut.disabled = !axis;
  const planes = axis ? [clip] : [];
  if (axis) { const i = "XYZ".indexOf(axis); const n = new THREE.Vector3(); n.setComponent(i, -1);
    const t = cut.value / 1000; clip.normal.copy(n); clip.constant = lo.getComponent(i) + t * (hi.getComponent(i) - lo.getComponent(i)); }
  for (const g of Object.values(groups)) g.traverse(m => { if (m.material) m.material.clippingPlanes = planes; });
}
axisSel.onchange = applyCut; cut.oninput = applyCut;

// Findings: numbered markers that follow the model, and a list that flies the camera to each.
const markers = [];
const list = document.getElementById("findings");
for (const f of data.findings) {
  const li = document.createElement("li"); li.className = "finding";
  li.innerHTML = `<span class="num">${f.n}</span><span></span>`; li.lastChild.textContent = f.what;
  list.appendChild(li);
  if (!f.at) continue;
  const el = document.createElement("div"); el.className = "marker num"; el.textContent = f.n; stage.appendChild(el);
  const p = new THREE.Vector3(...f.at); markers.push([el, p]);
  li.onclick = () => { const d = camera.position.clone().sub(controls.target).normalize().multiplyScalar(radius * 0.9);
    controls.target.copy(p); camera.position.copy(p).add(d); controls.update(); };
}
if (!data.findings.length) list.innerHTML = '<li class="muted">No geometric differences.</li>';

// Measure: click two points on the model.
const ray = new THREE.Raycaster(); let measuring = false, picks = [], line = null;
const out = document.getElementById("measure-out");
document.getElementById("measure").onclick = e => { measuring = !measuring; e.target.classList.toggle("on", measuring);
  picks = []; if (line) { scene.remove(line); line = null; } out.style.display = measuring ? "block" : "none";
  out.textContent = "Click two points"; };
document.getElementById("reset").onclick = resetView;
renderer.domElement.addEventListener("click", e => {
  if (!measuring) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const visible = Object.values(groups).filter(g => g.visible);
  const hit = ray.intersectObjects(visible, true).find(h => h.object.isMesh);
  if (!hit) return;
  picks.push(hit.point.clone()); if (picks.length > 2) picks = [hit.point.clone()];
  if (line) { scene.remove(line); line = null; }
  if (picks.length === 2) {
    line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(picks), new THREE.LineBasicMaterial({ color: 0x2563eb, depthTest: false }));
    scene.add(line);
    const d = picks[0].distanceTo(picks[1]);
    out.textContent = `${d.toFixed(d < 1 ? 4 : 3)} mm  (Δ ${picks[1].clone().sub(picks[0]).toArray().map(v => v.toFixed(3)).join(", ")})`;
  } else out.textContent = "Click a second point";
});

function resize() { const w = stage.clientWidth, h = stage.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); }
new ResizeObserver(resize).observe(stage); resize();
renderer.setAnimationLoop(() => {
  key.position.copy(camera.position);
  renderer.render(scene, camera);
  for (const [el, p] of markers) { const v = p.clone().project(camera);
    el.style.left = `${(v.x + 1) / 2 * stage.clientWidth}px`; el.style.top = `${(1 - v.y) / 2 * stage.clientHeight}px`;
    el.style.display = v.z < 1 ? "grid" : "none"; }
});
window.theseusReady = true;
</script>
</body>
</html>
"""
