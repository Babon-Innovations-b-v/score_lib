"""The mesh preview draws only what a render shows: a guide mesh (a model's collision hull) is left out."""
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
    triangles, colours, _ = mesh.collect(path)
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
    _, colours, _ = mesh.collect(path)
    assert colours[0].tolist() == mesh.GREY.tolist() and colours[1].tolist() == [1.0, 1.0, 1.0]
