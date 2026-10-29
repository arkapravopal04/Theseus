"""Render CAD geometry to PNG images, headless, in a CAD-like style.

render_scene() draws layers (see theseus.render.scene) through a camera with the best backend
this machine supports (theseus.render.display), then overlays numbered markers, a legend, a
colour scale and a title with matplotlib — using the same camera projection, so annotations
land on the geometry whichever backend drew it.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # before pyplot is imported anywhere: headless machines have no display

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from OCP.TopoDS import TopoDS_Shape  # noqa: E402
from theseus.model import Model  # noqa: E402
from theseus.render import display  # noqa: E402
from theseus.render.scene import Camera, Layer, Marker, ScaleBar, model_bounds, model_layer  # noqa: E402

__all__ = ["Camera", "Layer", "Marker", "ScaleBar", "draw", "render_preview", "render_scene", "render_views",
           "reserved_right"]

_TEXT = "#111827"

# Figure-fraction layout of the optional extras, on the right-hand side of a view.
_SCALE_BAR_BOX = (0.875, 0.14, 0.022, 0.56)
_INSET_BOX = (0.505, 0.25, 0.345, 0.47)


def reserved_right(scale_bar: bool, detail: bool) -> float:
    """How much of the image's width to keep clear of the part for the scale bar and inset —
    pass to Camera.fit(reserve_right=...)."""
    if detail:
        return 1 - _INSET_BOX[0] + 0.01
    return 1 - _SCALE_BAR_BOX[0] + 0.07 if scale_bar else 0.0


def draw(layers: list[Layer], camera: Camera) -> np.ndarray:
    """Raw pixels (height, width, 3) of the layers, from the best available backend."""
    if display.backend() == "vtk":
        try:
            from theseus.render import vtk_backend

            return vtk_backend.render(layers, camera)
        except Exception:  # noqa: BLE001 — a broken GL stack shouldn't cost the image
            pass
    from theseus.render import mpl_backend

    return mpl_backend.render(layers, camera)


def _annotate(ax, camera: Camera, markers, legend, title, offset=(0, 0)) -> None:
    width, height = camera.size
    for marker in markers:
        x, y = camera.project(marker.point)[0]
        x, y = np.clip(x, 14, width - 14) + offset[0], np.clip(y, 14, height - 14) + offset[1]
        ax.text(x, y, marker.label, ha="center", va="center", fontsize=8.5, color="white", weight="bold",
                bbox=dict(boxstyle="circle,pad=0.3", fc="#1f2937", ec="white", lw=1.2), zorder=5)
    if title:
        ax.text(offset[0] + 12, offset[1] + 12, title, va="top", ha="left", fontsize=11, weight="bold", color=_TEXT)
    if legend:
        ax.legend(handles=[Patch(facecolor=c, edgecolor="#374151", label=label) for c, label in legend],
                  loc="lower left", fontsize=8.5, frameon=False)


def _scale_bar(fig, bar: ScaleBar) -> None:
    cax = fig.add_axes(_SCALE_BAR_BOX)
    mappable = ScalarMappable(Normalize(*bar.value_range), bar.colormap)
    cb = fig.colorbar(mappable, cax=cax)
    cb.ax.tick_params(labelsize=7.5, colors=_TEXT)
    if bar.tick_format:
        ticks = np.linspace(*bar.value_range, 5)
        cb.set_ticks(ticks)
        cb.set_ticklabels([bar.tick_format(t) for t in ticks])
    cb.ax.set_title(bar.title, fontsize=8, color=_TEXT, pad=6)


def _inset(fig, ax, layers: list[Layer], camera: Camera, focus, radius: float, markers) -> None:
    """A zoomed detail view of `focus`, for changes too small to see at full-part scale."""
    width, height = camera.size
    inset_size = (int(_INSET_BOX[2] * width), int(_INSET_BOX[3] * height))
    zoom = Camera.fit(np.asarray(focus) - radius, np.asarray(focus) + radius, size=inset_size, margin=1.0)
    zoom = Camera(zoom.center, camera.direction, camera.up, zoom.half_height, inset_size)
    iax = fig.add_axes(_INSET_BOX)
    iax.imshow(draw(layers, zoom), interpolation="none")
    iax.set_xlim(0, inset_size[0])
    iax.set_ylim(inset_size[1], 0)
    iax.set_xticks([])
    iax.set_yticks([])
    for spine in iax.spines.values():
        spine.set_edgecolor("#374151")
        spine.set_linewidth(1.2)
    near = [m for m in markers if np.linalg.norm(np.asarray(m.point) - focus) <= radius * 1.5]
    _annotate(iax, zoom, near, (), "detail")
    # Ring the detail's location on the main view.
    x, y = camera.project(focus)[0]
    r = max(radius * camera.pixels_per_unit, 10)
    ax.add_patch(plt.Circle((x, y), r, fill=False, ec="#374151", lw=1.2, ls="--"))


def render_scene(
    layers: list[Layer],
    out_path: str,
    camera: Camera,
    markers: list[Marker] = (),
    scale_bar: ScaleBar | None = None,
    title: str | None = None,
    legend: list[tuple[tuple, str]] = (),
    detail: tuple[tuple[float, float, float], float] | None = None,
) -> None:
    """Draw and annotate one view to a PNG. `detail` = (point, radius) adds a zoomed inset."""
    pixels = draw(layers, camera)
    width, height = camera.size
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.imshow(pixels, interpolation="none")
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.set_axis_off()
    _annotate(ax, camera, markers, legend, title)
    if detail is not None:
        _inset(fig, ax, layers, camera, np.asarray(detail[0], dtype=float), detail[1], markers)
    if scale_bar:
        _scale_bar(fig, scale_bar)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def render_views(
    layers: list[Layer],
    out_path: str,
    lo,
    hi,
    markers: list[Marker] = (),
    legend: list[tuple[tuple, str]] = (),
    tile: tuple[int, int] = (600, 450),
) -> None:
    """A 2x2 sheet of the standard views — front, top, right, isometric — like a drawing."""
    fig = plt.figure(figsize=(2 * tile[0] / 100, 2 * tile[1] / 100), dpi=100)
    for i, view in enumerate(("front", "top", "right", "iso")):
        camera = Camera.fit(lo, hi, view, tile)
        ax = fig.add_axes((0.5 * (i % 2), 0.5 * (1 - i // 2), 0.5, 0.5))
        ax.imshow(draw(layers, camera), interpolation="none")
        ax.set_xlim(0, tile[0])
        ax.set_ylim(tile[1], 0)
        ax.set_axis_off()
        _annotate(ax, camera, markers, legend if i == 3 else (), view.capitalize() if view != "iso" else "Isometric")
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def render_preview(source: Model | TopoDS_Shape, out_path: str, deflection: float | None = None) -> None:
    """Render one model or shape to a PNG from the isometric view. Raises ValueError if it has no faces."""
    model = source if isinstance(source, Model) else Model(path="", format="brep", kind="brep", shape=source)
    layer = model_layer(model, deflection)
    if not len(layer.faces):
        raise ValueError("shape produced no triangulated faces to render")
    lo, hi = model_bounds(model)
    render_scene([layer], out_path, Camera.fit(lo, hi))
