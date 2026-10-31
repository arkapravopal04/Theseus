# Changelog

Releases are tagged `vX.Y.Z`; the floating `v1` tag (what `uses: arkapravopal04/theseus@v1`
resolves to) is moved to each new 1.x release by `.github/workflows/release.yml`.

## 1.2.0 — unreleased

### Smarter diffs
- Mirror images get their own verdict ("mirrored about XY; opposite hand"), proved the same way as
  a move. A part with a symmetry plane is its own mirror image, so it still reads as moved.
- A part moved or mirrored as well as edited is lined up first, so the report lists the edit rather
  than every face. The report states the transform and how much of the part then agrees, and
  declines to line up when the fit isn't clearly good.
- Holes and fillets are named: "through hole along Z Ø8.000 → 10.000", "blind hole … depth 12.000 →
  15.000", counterbores, countersinks, "removed fillet R3.000". A hole split into two half cylinders
  by an exporter is one hole. Unrecognised faces are reported as before.

### Fixed (found on real exports)
- A plane at exactly 45° could read as a large offset change on an unchanged face.
- A face split differently by another exporter no longer reads as added or removed. A part differing
  only that way reads "same geometry, its faces divided differently".
- Annotation and construction geometry in a STEP file (sheets, wires, PMI points) no longer counts
  towards the bounding box or the comparison, and isn't drawn. A change in the count of open bodies
  still shows.
- A trimmed spline face's bounding box is now the face's, not the whole surface it was cut from.
- Spline faces no longer report large deviations caused by a projection settling on the wrong spot.
- A boolean that crashes or hangs in the geometry kernel now costs only the material picture: it runs
  in its own process with a time limit, and the difference image falls back to a deviation map.
- `theseus OLD NEW --images` draws the lined-up version, and titles the 3D viewer with the file's name.
- STEP files holding only triangles (AP242 tessellated) are compared as meshes, rather than reporting
  a volume of 0 and a bounding box of -inf, or crashing.
- Comparing plainly different spline faces is several times faster, and a part that turns out to have
  moved is no longer measured finely before being lined up.
- A file hitting its time limit could hang: the limit now also ends the processes it started. The
  default limit is now 600 s, for all of one file: loading, comparing and drawing.
- An added or removed face reports how far the geometry really moved, not the face's size. Features
  are only reported as changed if measurably off the other version.

## 1.1.0 — never tagged

### Use it however you work
- `theseus app`: drop two versions of a part into a browser window and get the report — no
  command line, no git. Runs on this computer only (127.0.0.1, a random per-session token in
  every URL); dropped files are deleted as soon as they've been compared.
- `theseus compare OLD NEW`: the same report (summary, findings, images, 3D viewer) as a web page,
  opened in your browser.
- `theseus setup git`: plain `git diff`, `git log -p` and git GUIs show CAD files as readable
  text — every face with its dimensions to the nanometre, so a 2 µm larger hole is one changed
  line. Works with any git host (GitLab, Bitbucket, your own server) and offline; Git LFS files
  are resolved.
- `theseus diff [BASE] [HEAD]`: the full visual report for every CAD file changed in a local
  repo — by default this branch plus uncommitted and not-yet-added files.
- `theseus setup github`: writes the pull-request workflow (with `lfs: true` when the repo uses
  Git LFS).
- `theseus --version`; `theseus OLD NEW --out report.md` keeps working as before.
- The Action caches its installed dependencies (~1.4 GB), so runs after the first start in
  seconds instead of minutes. It now checks for Python 3.11+ up front with a clear error.
- Install with `pipx install theseus-cad` (PyPI name; the command is still `theseus`). Python
  3.11+ is required (the geometry kernel has no wheels for older versions). CI now tests on
  Windows, macOS and Linux.
- Each file's findings now lead with what changed on its faces ("Ø8 hole → Ø8.5, moved 0.5 mm"),
  followed by summaries such as vertex counts, even when a corner vertex moved further.
- Windows: a native crash while loading or rendering is recognised (access violation, stack
  overflow) and reported like a crash on Linux/macOS; reports print correctly to Windows
  terminals and pipes; the renderer check also rejects machines whose OpenGL is too old for VTK.
- Beta feedback: issue templates for wrong results and files that won't load (they ask you not
  to attach confidential CAD files).

### See the change
- Before / after / difference images for every changed file, inline in the PR comment. Added
  material is green and removed material red (exact OpenCascade booleans for B-reps, manifold3d
  for closed meshes); changes too small to see as material — down to a micron — are shown as a
  signed deviation map with a scale bar, numbered markers and a zoomed detail inset.
- A sheet of standard views (front, top, right, isometric) per file, and an interactive 3D viewer
  page: before / after / overlay / difference, section plane, measuring, click-to-fly findings.
- Rigid-motion detection: a part that was only translated or rotated reads as
  "moved (+5.000 mm X; shape unchanged)" — proven by an exact comparison after moving it back,
  never guessed. Metrics now include the inertia tensor.
- New comment layout: a summary table (file, result, largest difference, what changed), then one
  expandable section per file; findings are numbered ①②③ to match the image markers.
- Images are hosted on an orphan `theseus-assets` branch through the Git Data API (one folder per
  PR, replaced on each push; concurrent runs retry instead of clobbering). Without
  `contents: write` or on fork PRs they fall back to the `theseus-images` workflow artifact.
  Action input `images: inline|artifact|off`; CLI `--images DIR`.
- Headless CAD-style rendering with VTK (model edges, not triangle edges; one orthographic camera
  shared by all of a file's images), starting a private Xvfb when there's no display, with a
  depth-sorted matplotlib fallback.
- Pictures never cost the text report: each file's analysis is saved before its images are drawn,
  so a renderer crash or timeout leaves the file in the report with "images unavailable — text
  report only". VTK is tried once in a throwaway process before being used, so a stale `$DISPLAY`
  falls back to the matplotlib renderer instead of crashing the CLI.
- Fixed: `rtree` (needed by the precise diff) was missing from the declared dependencies, so a
  fresh install failed on mesh comparisons.

### Precise geometric diff
- Two versions are now compared geometrically, not just by volume/bounding box/centre of mass:
  exact B-reps face by face (surface type and parameters — plane offset, cylinder axis and
  diameter, sphere, cone, torus; free-form faces by projection) and vertex by vertex; meshes
  per vertex (same triangulation) or by two-way surface deviation, measured exactly against
  B-rep surfaces where one side is a STEP/IGES/BREP. Every difference says where it is.
- Every comparison states its detection limit — kernel precision, the file's declared precision
  (STEP uncertainty), the digits its coordinates were written with, float precision, or a mesh's
  per-triangle chord error — and files read as *changed*, *micro-change* or *identical (to within …)*.
- `--sensitivity engineering|fine|exact` (action input `sensitivity`) sets how big a difference
  must be to be headlined as a change; smaller real differences are still listed.
- Fixed: a change had to exceed both 0.01 mm *and* 0.1% to count, so on a 200 mm part a 0.19 mm
  face offset was reported as "no significant change". Thresholds now gate on the detection
  limit, then 0.01 mm *or* 0.1%; centre-of-mass shifts are judged in absolute terms (their
  coordinates depend on the origin).
- Bounding boxes are now the geometry's exact extent (OCCT pads the default by shape tolerances,
  which made tolerance-only re-exports look resized); volumes use adaptive integration with a
  reported error bound.


### Fixed
- PNG previews were squashed into a cube for any non-cubic part (`ndarray.ptp()` was removed in
  NumPy 2 and the error was silently swallowed).
- Inside-out STL bodies reported negative volume; they are now flipped to face outward (and the
  report says so).
- Open (non-watertight) meshes reported a made-up volume: STL went through OpenCascade sewing,
  which closes any shell into a "solid". Open geometry now reports volume as n/a with a warning.
- Large STL files are no longer converted to one B-rep face per triangle (minutes and gigabytes
  for a typical 3D-print file); meshes are read directly with trimesh.
- Renamed CAD files showed up as brand-new files; they are now diffed against their old path.
- Uppercase extensions (`PART.STEP`) did not trigger the workflow's path filter.
- Every push posted a new PR comment; theseus now edits its own comment in place.
- One malformed file could crash or hang the whole run; each file now loads in a child process
  with a timeout, and failures become a "Skipped" line.
- Report bodies were passed as a command-line argument (OS length limits) and could exceed
  GitHub's 65,536-character comment limit; long reports are trimmed with a pointer to the job summary.
- Error messages named temporary files instead of the file in the repo.
- `.glb` exports were written in millimetres in a format defined in metres (a 20 mm part measured
  20 m in viewers).
- `cache: pip` in action.yml made setup-python fail in repos without a requirements file.

### Added
- Formats: 3MF, OBJ, PLY, OFF, GLB (alongside STEP, IGES, BREP, STL), detected by content as well as
  extension. Non-CAD files sharing an extension (compiled `.obj` object files) are ignored.
- Units: STEP/IGES/3MF/GLB report their declared unit ("file units: inch, converted to mm");
  `--units` / the `units` action input declare the unit of unitless formats.
- Metrics: surface area, open-body count, and an OpenCascade validity check (invalid geometry is
  flagged in the report).
- Git LFS: LFS-stored CAD files are fetched with `git lfs smudge` instead of being skipped.
- Diffs run against the merge base, so changes merged into the base branch meanwhile aren't
  attributed to the PR.
- Push events are supported (report goes to the job summary).
- The full report is written to the workflow run's job summary.
- Action inputs: `timeout-seconds`, `max-file-mb`, `units`, `python-version`.
- CLI exits with status 2 (and a message) when a file can't be read or written.
- Release workflow; this changelog.

### Changed
- `load_shape()` now only accepts B-rep formats (STEP/IGES/BREP); use `load_model()` for any format.
- Metrics dicts carry `schema`, `format`, `kind`, `units`, `units_note`, `area_mm2`, `open_bodies`,
  `valid` and `warnings`; `volume_mm3` and `com` are `None` for geometry with no closed body.
- The GitHub Action logic moved from `bot.py` to `theseus/ci.py` (`bot.py` still works).

## 1.0.0
- First release: STEP/IGES/BREP/STL volume, bounding box, centre of mass and solid-count diff as a
  PR comment or CLI report, PNG preview and `.glb` export.
