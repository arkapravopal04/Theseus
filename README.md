# theseus

See what changed in a CAD file without opening CAD software. `theseus` reads STEP, IGES,
BREP, STL, 3MF, OBJ, PLY, OFF and GLB files, computes volume / surface area / bounding box /
centre of mass / body counts, and reports the difference between two versions — as a CLI, or
as an automatic, always-current comment on pull requests that touch CAD files.

## Why

Reviewing a CAD file change on GitHub normally means downloading both versions and opening them
in AutoCAD/Fusion/SolidWorks just to see if anything actually changed. This gives a numeric
summary — did the volume change, did the bounding box change, did a solid get added or removed,
is the new geometry even valid — straight in the PR, for anyone, with no CAD software installed.

## Install

**As a GitHub Action** — drop this in `.github/workflows/theseus.yml`:

```yaml
name: theseus
on:
  pull_request:
    # [sS]-style classes because path filters are case-sensitive and CAD exports are often
    # uppercase (PART.STEP).
    paths:
      - '**.[sS][tT][eE][pP]'
      - '**.[sS][tT][pP]'
      - '**.[pP]21'
      - '**.[iI][gG][eE][sS]'
      - '**.[iI][gG][sS]'
      - '**.[bB][rR][eE][pP]'
      - '**.[bB][rR][pP]'
      - '**.[sS][tT][lL]'
      - '**.3[mM][fF]'
      - '**.[gG][lL][bB]'
      - '**.[pP][lL][yY]'
      - '**.[oO][bB][jJ]'
      - '**.[oO][fF][fF]'
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }   # required — the diff needs the base commit
      - uses: arkapravopal04/theseus@v1
```

That's the whole setup. `fetch-depth: 0` is required: without full history the base commit
isn't in the checkout and the old version of each file can't be read. Every PR that touches a
CAD file gets one comment, which is updated in place on each push rather than re-posted. The
full report is also written to the workflow run's summary page.

Optional inputs (`with:`):

| Input | Default | |
|---|---|---|
| `units` | `mm` | Length unit of formats that don't record one (STL, OBJ, PLY, OFF, BREP): `mm`, `cm`, `m`, `in`, `ft`, `um` |
| `timeout-seconds` | `300` | Time allowed to load one version of one file before it's skipped |
| `max-file-mb` | `50` | Skip files larger than this |
| `python-version` | `3.11` | |

CAD files in **Git LFS**: add `lfs: true` to the checkout step's `with:` and theseus fetches the
real content for both versions. The action also runs on `push` events (the report then goes to
the run summary only).

**As a CLI** — installs straight from GitHub, no cloning needed:

```bash
pip install git+https://github.com/arkapravopal04/theseus.git
```

```bash
theseus old.step new.step --out report.md --png preview.png
```

`old` may be omitted to just report on a single (new) file. `--glb` also exports a `.glb` mesh —
commit it to GitHub for a free interactive rotate/zoom viewer, no CAD software needed. `--units in`
declares the unit of unitless formats. The exit status is 2 if a file can't be read or written.

## Formats and units

| Format | Extensions | Geometry | Units |
|---|---|---|---|
| STEP | `.step` `.stp` `.p21` | exact (B-rep) | from the file, converted to mm |
| IGES | `.iges` `.igs` | exact (B-rep) | from the file, converted to mm |
| OpenCascade BREP | `.brep` `.brp` | exact (B-rep) | none recorded — assumed mm, or `--units` |
| STL | `.stl` (ASCII or binary) | triangle mesh | none recorded — assumed mm, or `--units` |
| 3MF | `.3mf` | triangle mesh | from the file, converted to mm |
| glTF binary | `.glb` | triangle mesh | metres (per the glTF spec), converted to mm |
| OBJ, PLY, OFF | `.obj` `.ply` `.off` | triangle mesh | none recorded — assumed mm, or `--units` |

Formats are recognised by content, not just extension: `PART.STEP`, a STEP file saved as
`part.step.bak`, and binary STLs whose header starts with "solid" all load correctly, and a
compiled `.obj` object file in the same repo is ignored rather than reported as a broken model.
Every report says where its units came from ("file units: inch, converted to mm" or "unitless
format, assumed mm").

## How it works

1. `git diff --name-status -M` finds changed CAD files between the PR's merge base and head,
   following renames (a renamed part is diffed against its own history).
2. Each version is read as a raw blob (`git cat-file`), with Git LFS pointers resolved.
3. Each version is loaded in its own child process with a timeout — a malformed file that
   crashes or hangs the geometry kernel becomes one "Skipped" line, not a failed run.
   Exact formats are loaded with OpenCascade (`OCP`); meshes with `trimesh`.
4. The metric sets are diffed and formatted as one markdown comment, edited in place on each push.

## Limitations

- **No native CAD formats.** No DWG, SLDPRT, IPT, F3D, CATPart or other proprietary format —
  there's no free reader for them. Export STEP alongside your native files.
- **Volume-based, not topological.** This compares numbers derived from the geometry, not the
  geometry itself. Two different parts that happen to have the same volume, bounding box, and
  centre of mass will be reported as unchanged.
- **Small changes are treated as noise.** A metric change counts only if it exceeds both 0.01 mm
  (that length cubed for volume) and 0.1% — tunable with `--abs-threshold-mm` / `--rel-threshold`.
  On large parts the 0.1% floor can hide real changes; sub-threshold deltas are still listed.
- **Open geometry has no volume.** A mesh with holes or a surface-only STEP/IGES has no inside, so
  its volume and centre of mass are reported as n/a (with a warning) rather than a made-up number;
  the bounding box, area and previews still work. Inside-out mesh bodies are flipped and noted.
- **"Volume", not "mass".** CAD exchange files carry no density, so nothing here is a mass or
  weight calculation.
- **Fork PRs** get a read-only token, so the comment can't be posted; the report is still in the
  run summary.

## License

MIT for this project's code. The geometry kernel underneath (OpenCascade Technology, via the
[OCP](https://github.com/CadQuery/OCP) bindings) is **LGPL-2.1**.
