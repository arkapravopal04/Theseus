# Changelog

Releases are tagged `vX.Y.Z`; the floating `v1` tag (what `uses: arkapravopal04/theseus@v1`
resolves to) is moved to each new 1.x release by `.github/workflows/release.yml`.

## 1.1.0 — unreleased

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
