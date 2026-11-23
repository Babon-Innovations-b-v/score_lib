"""The mesh preview draws only what a render shows: a guide mesh (a model's collision hull) is left out, and a
textured triangle shows its texture pixel by pixel (red if one sample at its centre paints it whole), and a small near
triangle shows over a big far one whose middle lies nearer (red if triangles are painted by their middles' depth)."""
import numpy as np
import pytest
from PIL import Image
from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt

from score_lib.preview import mesh


def triangle(stage, path, size, purpose):
    prim = UsdGeom.Mesh.Define(stage, path)
    prim.CreatePointsAttr([(0, 0, 0), (size, 0, 0), (0, size, 0)])
    prim.CreateFaceVertexCountsAttr([3])
    prim.CreateFaceVertexIndicesAttr([0, 1, 2])
    prim.CreatePurposeAttr(purpose)


def test_collect_leaves_out_guide_meshes(tmp_path):
    path = tmp_path / "model.usda"
    stage = Usd.Stage.CreateNew(str(path))
    triangle(stage, "/model/geo", 1.0, UsdGeom.Tokens.default_)
    triangle(stage, "/model/look", 1.0, UsdGeom.Tokens.render)
    triangle(stage, "/model/collision", 50.0, UsdGeom.Tokens.guide)
    triangle(stage, "/model/stand_in", 50.0, UsdGeom.Tokens.proxy)
    stage.Save()
    triangles, colours, _, _ = mesh.collect(path)
    assert len(triangles) == 2 and len(colours) == 2
    assert float(abs(triangles).max()) == 1.0


def test_a_material_subset_paints_its_own_faces(tmp_path):
    path = tmp_path / "model.usda"
    stage = Usd.Stage.CreateNew(str(path))
    prim = UsdGeom.Mesh.Define(stage, "/model/geo")
    prim.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)])
    prim.CreateFaceVertexCountsAttr([3, 3])
    prim.CreateFaceVertexIndicesAttr([0, 1, 2, 1, 3, 2])
    material = UsdShade.Material.Define(stage, "/model/Looks/leaf")
    surface = UsdShade.Shader.Define(stage, "/model/Looks/leaf/surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set((1.0, 1.0, 1.0))
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    binding = UsdShade.MaterialBindingAPI.Apply(prim.GetPrim())
    subset = binding.CreateMaterialBindSubset("leaf", Vt.IntArray([1]))
    UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)
    stage.Save()
    _, colours, _, _ = mesh.collect(path)
    assert colours[0].tolist() == mesh.GREY.tolist() and colours[1].tolist() == [1.0, 1.0, 1.0]


def textured_square(path, texture):
    """A 1 m square of two triangles wearing `texture` across its whole UV square."""
    stage = Usd.Stage.CreateNew(str(path))
    prim = UsdGeom.Mesh.Define(stage, "/model/geo")
    prim.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    prim.CreateFaceVertexCountsAttr([3, 3])
    prim.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
    corners = {0: (0, 0), 1: (1, 0), 2: (1, 1), 3: (0, 1)}
    UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(
        [corners[index] for index in (0, 1, 2, 0, 2, 3)])
    material = UsdShade.Material.Define(stage, "/model/Looks/skin")
    surface = UsdShade.Shader.Define(stage, "/model/Looks/skin/surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    reader = UsdShade.Shader.Define(stage, "/model/Looks/skin/texture")
    reader.CreateIdAttr("UsdUVTexture")
    reader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(texture.name)
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        reader.ConnectableAPI(), "rgb")
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(prim.GetPrim()).Bind(material)
    stage.Save()


@pytest.mark.parametrize("painted, dark_shown", [("left_quarter_black", True), ("all_red", False)])
def test_a_texture_is_drawn_pixel_by_pixel(tmp_path, painted, dark_shown):
    texture = np.zeros((64, 64, 3), dtype=np.uint8)
    texture[:, :, 0] = 255
    if painted == "left_quarter_black":  # both triangles' UV centres (u 1/3 and 2/3) fall on the red
        texture[:, :16] = 0
    Image.fromarray(texture).save(tmp_path / "base.png")
    textured_square(tmp_path / "model.usda", tmp_path / "base.png")
    mesh.render(tmp_path / "model.usda", tmp_path / "preview.png", size=96)
    drawn = np.asarray(Image.open(tmp_path / "preview.png").convert("RGB")).reshape(-1, 3).astype(int)
    red = (drawn[:, 0] > 100) & (drawn[:, 1] < 40)
    dark = drawn.max(axis=1) < 30
    assert red.sum() > 500 and (dark.sum() > 500) == dark_shown


def test_a_near_part_shows_over_a_far_face_by_its_depth_at_each_pixel():
    """A big face sloping from near (its far corner off to the side) to far behind a small upright triangle: its
    middle lies nearer than the small one's, but where the two overlap the small one is nearer, so it shows there."""
    big = [[-4.0, -1.0, 10.0], [1.0, -1.0, -1.0], [1.0, 1.0, -1.0]]
    small = [[0.6, -0.2, 0.0], [0.9, -0.2, 0.0], [0.75, 0.2, 0.0]]
    view = np.array([big, small], dtype=np.float64)
    assert view[0, :, 2].mean() > view[1, :, 2].mean()
    ids = mesh.triangle_ids(view, 200, np.array([-1.5, 0.0]), 30.0)
    column, row = int(100 + (0.75 + 1.5) * 30), int(100 - 0.0 * 30)
    assert ids[row, column] == 1
