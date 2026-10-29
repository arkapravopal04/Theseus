"""Fallback renderer: matplotlib Agg, no GPU or X display needed.

It projects with the same Camera as the VTK backend and draws triangles far-to-near
(painter's algorithm). Back faces of opaque layers are culled (meshes are consistently
oriented, so they're never visible), and edges are tested against a coarse depth buffer so
hidden edges aren't drawn. Long overlapping triangles can still be misordered — hence it's
only the fallback.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402

from theseus.render.scene import EDGE_COLOUR, Camera, Layer  # noqa: E402


def _face_colours(layer: Layer, camera: Camera) -> np.ndarray:
    tri = layer.vertices[layer.faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(lengths == 0, 1, lengths)
    key = camera.direction * 0.8 + camera.up * 0.5 + camera.right * 0.3
    key /= np.linalg.norm(key)
    shade = 0.30 + 0.55 * np.abs(normals @ key) + 0.20 * np.abs(normals @ camera.direction)
    if layer.scalars is not None:
        lo, hi = layer.value_range
        values = layer.scalars[layer.faces].mean(axis=1)
        base = layer.colormap(np.clip((values - lo) / ((hi - lo) or 1), 0, 1))[:, :3]
    else:
        base = np.tile(layer.colour, (len(layer.faces), 1))
    rgb = np.clip(base * shade[:, None], 0, 1)
    return np.column_stack([rgb, np.full(len(rgb), layer.opacity)])


_DEPTH_GRID = 4  # depth-buffer cell size in pixels
_MAX_SPLATS = 3_000_000


def _depth_buffer(tris: list[np.ndarray], camera: Camera) -> np.ndarray:
    """Nearest surface depth per coarse pixel cell, from points scattered over every triangle."""
    width, height = camera.size
    gw, gh = width // _DEPTH_GRID + 1, height // _DEPTH_GRID + 1
    buffer = np.full(gw * gh, -np.inf)
    if not tris:
        return buffer.reshape(gh, gw)
    tri = np.concatenate(tris)
    flat = camera.project(tri.reshape(-1, 3)).reshape(-1, 3, 2) / _DEPTH_GRID
    e1, e2 = flat[:, 1] - flat[:, 0], flat[:, 2] - flat[:, 0]
    area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])  # 2-D cross product
    counts = np.clip((area * 8).astype(int), 3, None)  # ~8 per cell: almost none left empty
    if counts.sum() > _MAX_SPLATS:
        counts = np.maximum((counts * _MAX_SPLATS / counts.sum()).astype(int), 3)
    owner = np.repeat(np.arange(len(tri)), counts)
    rng = np.random.default_rng(0)
    u, v = rng.random(len(owner)), rng.random(len(owner))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    pts = tri[owner, 0] + u[:, None] * (tri[owner, 1] - tri[owner, 0]) + v[:, None] * (tri[owner, 2] - tri[owner, 0])
    xy = (camera.project(pts) / _DEPTH_GRID).astype(int)
    inside = (xy[:, 0] >= 0) & (xy[:, 0] < gw) & (xy[:, 1] >= 0) & (xy[:, 1] < gh)
    np.maximum.at(buffer, xy[inside, 1] * gw + xy[inside, 0], camera.depth(pts[inside]))
    return buffer.reshape(gh, gw)


def _visible(segments: np.ndarray, depth: np.ndarray, camera: Camera) -> np.ndarray:
    """Which edge segments have their midpoint in front of (or on) the nearest surface."""
    mid = segments.mean(axis=1)
    xy = (camera.project(mid) / _DEPTH_GRID).astype(int)
    xy[:, 0] = np.clip(xy[:, 0], 0, depth.shape[1] - 1)
    xy[:, 1] = np.clip(xy[:, 1], 0, depth.shape[0] - 1)
    tolerance = 3 * _DEPTH_GRID / camera.pixels_per_unit  # a surface sloping across one cell
    return camera.depth(mid) >= depth[xy[:, 1], xy[:, 0]] - tolerance


def render(layers: list[Layer], camera: Camera) -> np.ndarray:
    width, height = camera.size
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.set_axis_off()

    polys, colours, depths, opaque = [], [], [], []
    for layer in layers:
        if not len(layer.faces):
            continue
        tri = layer.vertices[layer.faces]
        face_colours = _face_colours(layer, camera)
        if layer.opacity >= 1:
            facing = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]) @ camera.direction >= 0
            tri, face_colours = tri[facing], face_colours[facing]
            opaque.append(tri)
        polys.append(camera.project(tri.reshape(-1, 3)).reshape(-1, 3, 2))
        colours.append(face_colours)
        depths.append(camera.depth(tri.mean(axis=1)))
    if polys:
        polys, colours, depths = np.concatenate(polys), np.concatenate(colours), np.concatenate(depths)
        order = np.argsort(depths)  # far first
        ax.add_collection(PolyCollection(polys[order], facecolors=colours[order], edgecolors="none",
                                         antialiaseds=False))
    depth = _depth_buffer(opaque, camera)
    for layer in layers:
        if layer.edges is not None and len(layer.edges):
            shown = layer.edges[_visible(layer.edges, depth, camera)]
            seg = camera.project(shown.reshape(-1, 3)).reshape(-1, 2, 2)
            ax.add_collection(LineCollection(seg, colors=[(*EDGE_COLOUR, min(1.0, layer.opacity + 0.25))],
                                             linewidths=0.9))

    fig.canvas.draw()
    pixels = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    return pixels
