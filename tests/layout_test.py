"""A model's faces given far less texture detail than its own are listed (layout.py): a face packed at a twentieth of
the density (the old bake's back), or laid on a picture a sixteenth the size, fails the model; a layout at one
density passes. Red if the measure ignores each picture's size, or its median or its limit moves."""
import numpy as np
import pytest
from PIL import Image
from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt

from score_lib import layout
from score_lib.preview import mesh

FULL = [(0, 0), (1, 0), (1, 1), (0, 1)]


def shrunk(corners, share):
    """UV corners scaled about their middle by `share` (the old bake's shrink_hidden)."""
    middle = np.mean(corners, axis=0)
    return [tuple(middle + (np.array(corner) - middle) * share) for corner in corners]


def textured_material(stage, name, picture):
    material = UsdShade.Material.Define(stage, f"/model/Looks/{name}")
    surface = UsdShade.Shader.Define(stage, f"/model/Looks/{name}/surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    reader = UsdShade.Shader.Define(stage, f"/model/Looks/{name}/texture")
    reader.CreateIdAttr("UsdUVTexture")
    reader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(picture)
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(reader.ConnectableAPI(), "rgb")
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    return material


def three_squares(folder, uv_squares, pictures):
    """Three 1 m squares side by side, square n laid on UV corners `uv_squares[n]` of picture `pictures[n]` (a side
    in pixels; one file per side)."""
    stage = Usd.Stage.CreateNew(str(folder / "model.usda"))
    prim = UsdGeom.Mesh.Define(stage, "/model/geo")
    prim.CreatePointsAttr([(x + offset, y, 0) for offset in range(3) for x, y in FULL])
    prim.CreateFaceVertexCountsAttr([4, 4, 4])
    prim.CreateFaceVertexIndicesAttr(list(range(12)))
    UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(
        [corner for square in uv_squares for corner in square])
    binding = UsdShade.MaterialBindingAPI.Apply(prim.GetPrim())
    for side in sorted(set(pictures)):
        Image.new("RGB", (side, side), (200, 60, 40)).save(folder / f"base_{side}.png")
        faces = [number for number, each in enumerate(pictures) if each == side]
        subset = binding.CreateMaterialBindSubset(f"side_{side}", Vt.IntArray(faces))
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(textured_material(stage, f"side_{side}",
                                                                                  f"base_{side}.png"))
    stage.Save()
    return folder / "model.usda"


@pytest.mark.parametrize("case, uv_squares, pictures, sparse", [
    ("one density", [FULL, FULL, FULL], [1024, 1024, 1024], 0.0),
    ("back at a twentieth", [FULL, FULL, shrunk(FULL, 0.05)], [1024, 1024, 1024], 1 / 3),
    ("back at a quarter", [FULL, FULL, shrunk(FULL, 0.26)], [1024, 1024, 1024], 0.0),
    ("a larger picture", [FULL, FULL, FULL], [512, 512, 1024], 0.0),
    ("a sixteenth the picture", [FULL, FULL, FULL], [1024, 1024, 64], 1 / 3),
    ("two pictures, one density", [FULL, FULL, shrunk(FULL, 0.5)], [1024, 1024, 2048], 0.0),
])
def test_faces_far_below_their_models_detail_are_listed(tmp_path, case, uv_squares, pictures, sparse):
    triangles, _, _, skin = mesh.collect(three_squares(tmp_path, uv_squares, pictures))
    share, _ = layout.sparse_share(triangles, skin, layout.picture_sizes(skin))
    record = {"address": "model/squares@1"}
    assert share == pytest.approx(sparse, abs=1e-6), case
    assert bool(layout.faults(record, {"sparse": share})) == (sparse > layout.LEAST), case
