"""Draw layers with VTK into an RGB array: real depth buffering, flat CAD-style shading,
model edges on top, correct transparency (depth peeling), 2x supersampled."""

from __future__ import annotations

import numpy as np
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 — registers the OpenGL render window
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkLookupTable, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkLight,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkRenderWindow,
    vtkWindowToImageFilter,
)

from theseus.render.scene import EDGE_COLOUR, Camera, Layer

SUPERSAMPLE = 2


def _cells(indices: np.ndarray, per_cell: int) -> vtkCellArray:
    offsets = np.arange(0, (len(indices) + 1) * per_cell, per_cell, dtype=np.int64)
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(offsets, deep=True),
                  numpy_to_vtkIdTypeArray(np.ascontiguousarray(indices, dtype=np.int64).ravel(), deep=True))
    return cells


def _points(vertices: np.ndarray) -> vtkPoints:
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(vertices, dtype=np.float64), deep=True))
    return points


def _lookup_table(colormap, value_range) -> vtkLookupTable:
    table = vtkLookupTable()
    table.SetNumberOfTableValues(256)
    table.SetRange(*value_range)
    for i, rgba in enumerate(colormap(np.linspace(0, 1, 256))):
        table.SetTableValue(i, *rgba)
    table.Build()
    return table


def _surface_actor(layer: Layer) -> vtkActor:
    poly = vtkPolyData()
    poly.SetPoints(_points(layer.vertices))
    poly.SetPolys(_cells(layer.faces, 3))
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(poly)
    # Push surfaces slightly back so edges lying exactly on them aren't half-hidden.
    mapper.SetResolveCoincidentTopologyToPolygonOffset()
    if layer.scalars is not None:
        poly.GetPointData().SetScalars(numpy_to_vtk(np.ascontiguousarray(layer.scalars, dtype=np.float64), deep=True))
        mapper.SetLookupTable(_lookup_table(layer.colormap, layer.value_range))
        mapper.SetScalarRange(*layer.value_range)
        mapper.ScalarVisibilityOn()
    else:
        mapper.ScalarVisibilityOff()
    actor = vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetInterpolationToFlat()
    prop.SetColor(*layer.colour)
    prop.SetOpacity(layer.opacity)
    prop.SetAmbient(0.28)
    prop.SetDiffuse(0.70)
    prop.SetSpecular(0.08)
    prop.BackfaceCullingOff()
    return actor


def _edge_actor(layer: Layer) -> vtkActor | None:
    if layer.edges is None or not len(layer.edges):
        return None
    poly = vtkPolyData()
    poly.SetPoints(_points(layer.edges.reshape(-1, 3)))
    poly.SetLines(_cells(np.arange(len(layer.edges) * 2).reshape(-1, 2), 2))
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(poly)
    actor = vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*EDGE_COLOUR)
    prop.SetLineWidth(1.4 * SUPERSAMPLE)
    prop.SetOpacity(min(1.0, layer.opacity + 0.25))
    prop.LightingOff()
    return actor


def render(layers: list[Layer], camera: Camera) -> np.ndarray:
    """(height, width, 3) uint8 image of the layers through `camera`."""
    width, height = camera.size
    renderer = vtkRenderer()
    renderer.SetBackground(1, 1, 1)
    if any(layer.opacity < 1 for layer in layers):
        renderer.SetUseDepthPeeling(1)
        renderer.SetMaximumNumberOfPeels(12)
    for layer in layers:
        if len(layer.faces):
            renderer.AddActor(_surface_actor(layer))
        edges = _edge_actor(layer)
        if edges is not None:
            renderer.AddActor(edges)

    # A key light from over the viewer's shoulder plus the default headlight's fill.
    renderer.RemoveAllLights()
    for position, intensity in (((0.6, 0.9, 1.0), 0.62), ((0.0, 0.0, 1.0), 0.30)):
        light = vtkLight()
        light.SetLightTypeToCameraLight()
        light.SetPosition(*position)
        light.SetFocalPoint(0, 0, 0)
        light.SetIntensity(intensity)
        renderer.AddLight(light)

    cam = renderer.GetActiveCamera()
    cam.ParallelProjectionOn()
    cam.SetFocalPoint(*camera.center)
    distance = camera.half_height * 20 + 1.0
    cam.SetPosition(*(camera.center + camera.direction * distance))
    cam.SetViewUp(*camera.up)
    cam.SetParallelScale(camera.half_height)
    cam.SetClippingRange(distance * 0.01, distance * 2.0 + camera.half_height * 40)

    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetMultiSamples(0)
    window.SetAlphaBitPlanes(1)
    window.AddRenderer(renderer)
    window.SetSize(width * SUPERSAMPLE, height * SUPERSAMPLE)
    window.Render()

    grab = vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.SetInputBufferTypeToRGB()
    grab.ReadFrontBufferOff()
    grab.Update()
    image = grab.GetOutput()
    w, h, _ = image.GetDimensions()
    pixels = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(h, w, -1)[::-1, :, :3].astype(np.float32)
    window.Finalize()

    # Box-filter the supersampled image down: cheap, robust anti-aliasing.
    s = SUPERSAMPLE
    pixels = pixels[: height * s, : width * s].reshape(height, s, width, s, 3).mean(axis=(1, 3))
    return pixels.round().astype(np.uint8)
