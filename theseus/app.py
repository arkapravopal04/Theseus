"""`theseus app`: compare two CAD files by dragging them into a browser window.

A small web server on this computer only (127.0.0.1, a random port, and a random token in every
URL so other websites open in the same browser can't drive it). Dropped files are written to a
temporary folder, compared, and deleted; nothing is sent anywhere. Reports stay viewable until
the app is closed.
"""

from __future__ import annotations

import email.parser
import email.policy
import html
import json
import mimetypes
import os
import secrets
import shutil
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import theseus
from theseus.compare import DEFAULT_THRESHOLDS, Thresholds
from theseus.formats import extensions
from theseus.local import compare_files, summary_lines

MAX_UPLOAD_MB = 500


class App:
    def __init__(self, thresholds: Thresholds = DEFAULT_THRESHOLDS, units: str | None = None,
                 host: str = "127.0.0.1", port: int = 0):
        self.token = secrets.token_urlsafe(16)
        self.workdir = tempfile.mkdtemp(prefix="theseus-app-")
        self.thresholds, self.units = thresholds, units
        self._lock = threading.Lock()
        self._count = 0
        app = self

        class Handler(_Handler):
            owner = app

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/{self.token}/"

    def serve_forever(self) -> None:
        try:
            self.server.serve_forever()
        finally:
            self.close()

    def close(self) -> None:
        self.server.server_close()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def compare(self, uploads: dict[str, tuple[str, bytes]]) -> dict:
        """uploads: field ("old"/"new") -> (file name, content). Returns the JSON for the page."""
        with self._lock:
            self._count += 1
            run = f"r{self._count}"
        upload_dir = os.path.join(self.workdir, "uploads", run)
        out_dir = os.path.join(self.workdir, run)
        paths: dict[str, str | None] = {"old": None, "new": None}
        names: dict[str, str | None] = {"old": None, "new": None}
        try:
            for field, (name, content) in uploads.items():
                name = os.path.basename(name.replace("\\", "/")) or field
                folder = os.path.join(upload_dir, field)
                os.makedirs(folder)
                paths[field] = os.path.join(folder, name)
                names[field] = name
                with open(paths[field], "wb") as f:
                    f.write(content)
            if names["old"] and names["old"] == names["new"]:
                names["old"] += " (old)"
            report, results, errors = compare_files(paths["old"], paths["new"], out_dir, units=self.units,
                                                    thresholds=self.thresholds, names=(names["old"], names["new"]))
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)  # the uploads; the report folder stays
        return {"report": f"/{self.token}/{run}/report.html",
                "summary": summary_lines(results, {}, self.thresholds), "errors": errors}


def parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """Files from a multipart/form-data body: field name -> (file name, content). (The `cgi`
    module that used to do this is gone from Python 3.13; the email parser reads the same format.)"""
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    files = {}
    if not message.is_multipart():
        return files
    for part in message.iter_parts():
        field = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if field and filename:
            files[field] = (filename, part.get_payload(decode=True) or b"")
    return files


class _Handler(BaseHTTPRequestHandler):
    owner: App

    def log_message(self, format, *args):  # keep the terminal for results, not access logs
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _route(self) -> str | None:
        """The path after the token, or None if the token is missing or wrong."""
        path = urllib.parse.urlsplit(self.path).path
        prefix = f"/{self.owner.token}/"
        return path[len(prefix):] if path.startswith(prefix) else None

    def do_GET(self):
        rest = self._route()
        if rest is None:
            return self._send(404, b"not found", "text/plain")
        if rest == "":
            page = _PAGE.replace("{version}", html.escape(theseus.__version__)).replace(
                "{accept}", ",".join(extensions())).replace("{max_mb}", str(MAX_UPLOAD_MB))
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        # A finished report or one of its images/viewer.
        root = os.path.realpath(self.owner.workdir)
        target = os.path.realpath(os.path.join(root, urllib.parse.unquote(rest)))
        if not target.startswith(root + os.sep) or os.sep + "uploads" + os.sep in target[len(root):] \
                or not os.path.isfile(target):
            return self._send(404, b"not found", "text/plain")
        with open(target, "rb") as f:
            body = f.read()
        kind = mimetypes.guess_type(target)[0] or "application/octet-stream"
        self._send(200, body, kind + ("; charset=utf-8" if kind.startswith("text/") else ""))

    def do_POST(self):
        if self._route() != "compare":
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_MB * 1024 * 1024:
            return self._json(413, {"error": f"files are larger than {MAX_UPLOAD_MB} MB in total"})
        body = self.rfile.read(length)
        uploads = {k: v for k, v in parse_multipart(self.headers.get("Content-Type", ""), body).items()
                   if k in ("old", "new")}
        if not uploads:
            return self._json(400, {"error": "drop at least one file"})
        try:
            self._json(200, self.owner.compare(uploads))
        except Exception as e:  # report it on the page rather than leave it spinning
            self._json(500, {"error": f"comparison failed: {e}"})


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>theseus — compare CAD files</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --text:#111827; --muted:#6b7280; --line:#d1d5db; --accent:#2563eb; --hover:#eff6ff; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1115; --card:#171a21; --text:#e5e7eb; --muted:#9ca3af;
  --line:#374151; --accent:#60a5fa; --hover:#1e293b; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }
main { max-width:860px; margin:0 auto; padding:32px 16px; }
h1 { margin:0 0 4px; font-size:24px; } p.lead { color:var(--muted); margin:0 0 24px; }
.drops { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:640px) { .drops { grid-template-columns:1fr; } }
.drop { background:var(--card); border:2px dashed var(--line); border-radius:12px; padding:28px 16px; text-align:center;
  cursor:pointer; min-height:150px; display:flex; flex-direction:column; justify-content:center; gap:6px; }
.drop.over { border-color:var(--accent); background:var(--hover); }
.drop.has { border-style:solid; border-color:var(--accent); }
.drop strong { font-size:16px; } .drop .file { color:var(--accent); word-break:break-all; } .drop .hint { color:var(--muted); font-size:13px; }
button { margin-top:20px; font:inherit; font-weight:600; padding:10px 22px; border:0; border-radius:8px;
  background:var(--accent); color:#fff; cursor:pointer; } button:disabled { opacity:.45; cursor:default; }
#status { margin-top:16px; } #status ul { padding-left:20px; } .error { color:#dc2626; }
.spinner { display:inline-block; width:14px; height:14px; border:2px solid var(--line); border-top-color:var(--accent);
  border-radius:50%; animation:spin 0.8s linear infinite; vertical-align:-2px; margin-right:6px; }
@keyframes spin { to { transform:rotate(360deg); } }
footer { color:var(--muted); font-size:13px; margin-top:32px; }
input[type=file] { display:none; }
a.open { display:inline-block; padding:9px 18px; border-radius:8px; background:var(--accent); color:#fff; text-decoration:none; font-weight:600; }
</style></head>
<body><main>
<h1>Compare two CAD files</h1>
<p class="lead">Drop the old and the new version of a part. theseus shows what changed, where, and by how much.</p>
<div class="drops">
  <label class="drop" id="drop-old"><strong>Old version</strong><span class="file">drop a file or click to choose</span>
    <span class="hint">optional — leave empty to just inspect the new file</span><input type="file" id="old" accept="{accept}"></label>
  <label class="drop" id="drop-new"><strong>New version</strong><span class="file">drop a file or click to choose</span>
    <span class="hint">STEP, IGES, BREP, STL, 3MF, OBJ, PLY, OFF, GLB</span><input type="file" id="new" accept="{accept}"></label>
</div>
<button id="go" disabled>Compare</button>
<div id="status"></div>
<footer>theseus {version} · Runs entirely on this computer: files are compared locally and deleted afterwards;
nothing is uploaded anywhere. Up to {max_mb} MB per comparison.</footer>
</main>
<script>
const files = { old: null, new: null };
const go = document.getElementById("go"), status = document.getElementById("status");
function set(which, file) {
  files[which] = file;
  const box = document.getElementById("drop-" + which);
  box.classList.toggle("has", !!file);
  box.querySelector(".file").textContent = file ? file.name : "drop a file or click to choose";
  go.disabled = !files.new && !files.old;
}
for (const which of ["old", "new"]) {
  const box = document.getElementById("drop-" + which), input = document.getElementById(which);
  input.addEventListener("change", () => set(which, input.files[0] || null));
  box.addEventListener("dragover", e => { e.preventDefault(); box.classList.add("over"); });
  box.addEventListener("dragleave", () => box.classList.remove("over"));
  box.addEventListener("drop", e => {
    e.preventDefault(); box.classList.remove("over");
    const dropped = [...e.dataTransfer.files];
    if (dropped.length >= 2) { set("old", dropped[0]); set("new", dropped[1]); }  // both at once: first is old
    else if (dropped.length === 1) set(which, dropped[0]);
  });
}
function escape(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
go.addEventListener("click", async () => {
  const form = new FormData();
  for (const which of ["old", "new"]) if (files[which]) form.append(which, files[which], files[which].name);
  go.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Comparing… (large or complex parts can take a minute)';
  try {
    const response = await fetch("compare", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    const items = data.summary.concat(data.errors).map(s => "<li>" + escape(s) + "</li>").join("");
    status.innerHTML = (items ? "<ul>" + items + "</ul>" : "") +
      '<p><a class="open" href="' + data.report + '">Open the full report →</a></p>';
  } catch (err) {
    status.innerHTML = '<p class="error">' + escape(String(err.message || err)) + "</p>";
  } finally {
    go.disabled = !files.new && !files.old;
  }
});
</script>
</body></html>
"""
