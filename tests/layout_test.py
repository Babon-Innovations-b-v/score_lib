"""A model's faces given far less texture detail than its own are listed (layout.py): a face packed at a twentieth of
the density (the old bake's back), or laid on a picture a sixteenth the size, fails the model; a layout at one
density passes, kept as USD or as glTF. Red if the measure ignores each picture's size, or its median or its limit
moves, or a glTF's triangles or texture coordinates are read wrong."""
import json

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


def three_squares_gltf(folder, uv_squares, pictures):
    """The same three squares as one .gltf, a primitive per square, its positions interleaved with its normals
    (a strided view) and its texture coordinates in their own view."""
    sides = sorted(set(pictures))
    for side in sides:
        Image.new("RGB", (side, side), (200, 60, 40)).save(folder / f"base_{side}.png")
    blob, views, accessors, primitives = b"", [], [], []

    def added(values, stride=None):
        nonlocal blob
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(values.tobytes()),
                      **({"byteStride": stride} if stride else {})})
        blob += values.tobytes()
        return len(views) - 1

    for number, square in enumerate(uv_squares):
        points = np.array([(x + number, y, 0) for x, y in FULL], dtype=np.float32)
        interleaved = np.hstack([points, np.tile(np.float32([0, 0, 1]), (4, 1))])
        accessors.append({"bufferView": added(interleaved, 24), "componentType": 5126, "count": 4,
                          "type": "VEC3"})
        accessors.append({"bufferView": added(np.array(square, dtype=np.float32)), "componentType": 5126,
                          "count": 4, "type": "VEC2"})
        accessors.append({"bufferView": added(np.array([0, 1, 2, 0, 2, 3], dtype=np.uint16)),
                          "componentType": 5123, "count": 6, "type": "SCALAR"})
        primitives.append({"attributes": {"POSITION": 3 * number, "TEXCOORD_0": 3 * number + 1},
                           "indices": 3 * number + 2, "material": sides.index(pictures[number])})
    (folder / "model.bin").write_bytes(blob)
    document = {"asset": {"version": "2.0"}, "buffers": [{"uri": "model.bin", "byteLength": len(blob)}],
                "bufferViews": views, "accessors": accessors, "meshes": [{"primitives": primitives}],
                "images": [{"uri": f"base_{side}.png"} for side in sides],
                "textures": [{"source": number} for number in range(len(sides))],
                "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": number}}}
                              for number in range(len(sides))]}
    (folder / "model.gltf").write_text(json.dumps(document))
    return folder / "model.gltf"


def measured(folder, kept, uv_squares, pictures):
    if kept == "usd":
        triangles, _, _, skin = mesh.collect(three_squares(folder, uv_squares, pictures))
    else:
        triangles, skin = layout.gltf_collect(three_squares_gltf(folder, uv_squares, pictures))
    return layout.sparse_share(triangles, skin, layout.picture_sizes(skin))[0]


@pytest.mark.parametrize("kept", ["usd", "gltf"])
@pytest.mark.parametrize("case, uv_squares, pictures, sparse", [
    ("one density", [FULL, FULL, FULL], [1024, 1024, 1024], 0.0),
    ("back at a twentieth", [FULL, FULL, shrunk(FULL, 0.05)], [1024, 1024, 1024], 1 / 3),
    ("back at a quarter", [FULL, FULL, shrunk(FULL, 0.26)], [1024, 1024, 1024], 0.0),
    ("a larger picture", [FULL, FULL, FULL], [512, 512, 1024], 0.0),
    ("a sixteenth the picture", [FULL, FULL, FULL], [1024, 1024, 64], 1 / 3),
    ("two pictures, one density", [FULL, FULL, shrunk(FULL, 0.5)], [1024, 1024, 2048], 0.0),
])
def test_faces_far_below_their_models_detail_are_listed(tmp_path, kept, case, uv_squares, pictures, sparse):
    share = measured(tmp_path, kept, uv_squares, pictures)
    record = {"address": "model/squares@1"}
    assert share == pytest.approx(sparse, abs=1e-6), case
    assert bool(layout.faults(record, {"sparse": share})) == (sparse > layout.LEAST), case
