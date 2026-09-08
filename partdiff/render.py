"""Render a shape to a static PNG preview using matplotlib (no OpenGL/GPU needed).

matplotlib.use("Agg") must happen before pyplot is imported anywhere in the
process — mandatory in a headless container, otherwise it tries to open a
display and crashes.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from partdiff.mesh import extract_mesh  # noqa: E402
from OCP.TopoDS import TopoDS_Shape  # noqa: E402


def render_preview(shape: TopoDS_Shape, out_path: str, deflection: float = 0.5) -> None:
    """Render `shape` to `out_path` as a PNG. Raises ValueError if the shape has no faces."""
    vertices, faces = extract_mesh(shape, deflection)
    if len(faces) == 0:
        raise ValueError("shape produced no triangulated faces to render")

    triangles = vertices[faces]  # (M, 3, 3)

    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(
        triangles, facecolor="#a8c4e0", edgecolor="black", linewidths=0.1, alpha=0.95
    )
    ax.add_collection3d(coll)

    ax.set_xlim(vertices[:, 0].min(), vertices[:, 0].max())
    ax.set_ylim(vertices[:, 1].min(), vertices[:, 1].max())
    ax.set_zlim(vertices[:, 2].min(), vertices[:, 2].max())
    try:
        ax.set_box_aspect(
            (
                vertices[:, 0].ptp() or 1,
                vertices[:, 1].ptp() or 1,
                vertices[:, 2].ptp() or 1,
            )
        )
    except AttributeError:
        pass  # older matplotlib without set_box_aspect
    ax.set_axis_off()
    ax.view_init(elev=25, azim=-60)

    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
