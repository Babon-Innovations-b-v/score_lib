"""score-lib kit-parts: a taken-in kit piece whose parts are unbound GeomSubsets and one of them a decal made again
part by part (each part a placeholder material named after it, the decal's faces off), taken in over its draft, and
a second run listing nothing."""
import json

import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdShade

from score_lib import entry as entries
from score_lib import kit_parts, take_in, verify
from score_lib.blobs import from_environment

CC0 = "CC0 1.0"


def piece(path):
    """Three quads of their own corners in a row (x 0..3), each a part: Trim, M_Decal_White (the middle one, its
    corners 4..7), Glass; vertex normals and texture coordinates, Y up."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    top = UsdGeom.Xform.Define(stage, "/wall")
    stage.SetDefaultPrim(top.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/wall/geo")
    points = [(x + dx, y, 0.0) for x in range(3) for dx, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([4, 4, 4])
    mesh.CreateFaceVertexIndicesAttr(list(range(12)))
    mesh.CreateNormalsAttr([(0.0, 0.0, 1.0)] * 12)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
        [(x / 3, y) for x, y, _ in points])
    for name, face in (("Trim", 0), ("M_Decal_White", 1), ("Glass", 2)):
        UsdGeom.Subset.CreateGeomSubset(mesh, name, UsdGeom.Tokens.face, [face], "materialBind")
    stage.Save()
    return path


def listing(folder):
    (folder / "piece.json").write_text(json.dumps({"parts": ["Glass", "M_Decal_White", "Trim"],
                                                   "size_m": [3.0, 1.0, 0.0]}))
    return {"world": "kits", "commit": "0" * 40, "taken_from": "a test's kit piece",
            "entries": [{"kind": "kit", "id": "wall", "title": "Wall", "tags": {"kit": ["piece"]},
                         "files": {"piece.usdc": {"path": str(piece(folder / "piece.usdc")), "from": "Wall.gltf"},
                                   "piece.json": {"path": str(folder / "piece.json"), "from": "its record"}},
                         "made_with": [{"what": "Wall.gltf: its geometry", "source": CC0, "carried": True}]}]}


def test_a_piece_is_made_again_part_by_part_without_its_decal(tmp_path):
    library = tmp_path / "library"
    (library / "entries").mkdir(parents=True)
    blobs = from_environment()
    folder = tmp_path / "loose"
    folder.mkdir()
    assert take_in.take_in(library, blobs, listing(folder))["new"] == ["kit/wall@1"]
    written, left = kit_parts.listings(library, blobs, tmp_path / "work")
    assert left == [] and list(written.values()) == [1]
    made = json.loads(open(next(iter(written))).read())
    assert take_in.take_in(library, blobs, made)["changed draft"] == ["kit/wall@1"]

    record = entries.load_address(library, "kit/wall@1")
    assert record["provenance"]["made_with"][-1]["what"] == kit_parts.STEP["what"]
    usd = tmp_path / "work/made/kit/wall/piece.usdc"
    stage = Usd.Stage.Open(str(usd))
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/wall/geo"))
    assert list(mesh.GetFaceVertexCountsAttr().Get()) == [4, 4]
    assert list(mesh.GetFaceVertexIndicesAttr().Get()) == list(range(8))  # the decal's own corners 4..7 gone
    assert len(mesh.GetNormalsAttr().Get()) == len(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").Get()) == 8
    parts = {subset.GetPrim().GetName(): list(subset.GetIndicesAttr().Get())
             for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh)}
    assert parts == {"Trim": [0], "Glass": [1]}
    for name in parts:
        bound = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(f"/wall/geo/{name}")).ComputeBoundMaterial()[0]
        assert str(bound.GetPath()) == f"/wall/Looks/{name}" and not bound.GetSurfaceOutput().HasConnectedSource()
    glass = np.asarray(mesh.GetPointsAttr().Get())[4:]
    assert glass[:, 0].min() == 2.0  # the glass face keeps its own corners after the renumbering
    assert np.isclose(np.asarray(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").Get())[4:, 0].min(), 2 / 3)
    own = json.loads((tmp_path / "work/made/kit/wall/piece.json").read_text())
    assert own["parts"] == ["Glass", "Trim"] and own["size_m"] == [3.0, 1.0, 0.0]
    assert verify.check_taken_in(blobs, entries.all_entries(library)) == []
    assert kit_parts.listings(library, blobs, tmp_path / "again") == ({}, [])
