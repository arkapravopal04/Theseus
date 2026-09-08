# partdiff

See what changed in a CAD file without opening CAD software. `partdiff` reads
STEP/IGES/BREP/STL files with the OpenCascade geometry kernel, computes volume /
bounding box / centre of mass / solid count, and reports the difference
between two versions — as a CLI, or as an automatic comment on pull requests
that touch `.step`/`.stp`/`.iges`/`.igs`/`.brep`/`.brp`/`.stl` files.

## Why

Reviewing a CAD file change on GitHub normally means downloading both
versions and opening them in AutoCAD/Fusion/SolidWorks just to see if
anything actually changed. This gives a numeric summary — did the volume
change, did the bounding box change, did a solid get added or removed —
straight in the PR, for anyone, with no CAD software installed.

## CLI usage

```bash
pip install -r requirements.txt
python -m partdiff old.step new.step --out report.md --png preview.png
```

`old` may be omitted to just report on a single (new) file.

## Use as a GitHub Action

```yaml
name: partdiff
on:
  pull_request:
    paths: ['**.step', '**.stp', '**.iges', '**.igs', '**.brep', '**.brp', '**.stl']
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

`fetch-depth: 0` is required: without full history, the base commit isn't in
the checkout and the diff can't read the old version of the file.

## How it works

1. `git diff --name-only` finds changed CAD files between the PR's base and head.
2. The old version is pulled with `git show <base>:<path>` to a temp file; the new
   version is read from the working tree.
3. Each version is loaded into OpenCascade (`OCP`) and reduced to a handful of
   metrics: volume, bounding box, centre of mass, solid count.
4. The two metric sets are diffed and formatted as one markdown comment,
   posted with `gh pr comment`.

## Limitations

- **STEP, IGES, BREP, and STL only.** No DWG, no SLDPRT, no native format for any specific
  CAD package — those are proprietary formats with no free reader.
- **STL has no exact topology.** It's a bare triangle mesh, so volume/bounding-box/centre-of-
  mass are computed by sewing the triangles into a closed solid first. An open or
  non-manifold mesh (a surface, not a watertight body) can't be closed into a solid — its
  volume won't be meaningful, though bounding box and preview rendering still work.
- **Volume-based, not topological.** This compares numbers derived from the geometry, not
  the geometry itself. Two different parts that happen to have the same volume, bounding
  box, and centre of mass will be reported as unchanged.
- **"Volume", not "mass".** There's no density information in a STEP file, so nothing here
  is a mass or weight calculation.
- **No Git LFS.** If your CAD files are stored in LFS, the checkout only contains pointer
  files unless LFS is explicitly fetched; partdiff detects this and skips the file with a
  note rather than reporting garbage.
- **Small changes are treated as noise.** Differences under 0.01mm or 0.1% are not reported,
  to avoid flagging tessellation/floating-point wobble as a real change.
- Files over 50MB are skipped rather than loaded.

## License

MIT for this project's code. The geometry kernel underneath (OpenCascade Technology,
via the [OCP](https://github.com/CadQuery/OCP) bindings) is **LGPL-2.1**.
