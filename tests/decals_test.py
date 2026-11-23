"""decals: a painted surface's printed marks are cut out at their size in metres as decal entries carrying their
source's licence and credit, and wear is never cut. Red when: the texel density is misread (a mark's size in metres
wrong), a soft wear smudge, a trim sheet's full-width strip or a dot under 4 mm is cut as a decal, the licence or the
credit is not carried, the cache is not emptied, or a decal written does not pass verify's decal check."""
import json
import os

import numpy as np
from PIL import Image

from score_lib import decals
from score_lib import entry as entries
from score_lib.blobs import file_sha256
from score_lib.blobs.local import LocalBlobStore

SURFACE = "surface/simready_m_opaque_plastic_sign_a01_0badcafe@1"
CREDIT = ("sign_a01 by NVIDIA Corporation, from huggingface.co/datasets/nvidia/simready-assets at revision abc, "
          "CC BY 4.0")
SIDE = 1024  # on a 1 x 1 m quad: 1,024 px/m


def painted_albedo():
    """A light grey map with a crisp two-ink mark (100 x 60 px), a soft wear smudge, a full-width strip and a 2 px
    dot."""
    rgb = np.full((SIDE, SIDE, 3), 200.0)
    rgb[100:160, 100:150] = (10, 10, 10)
    rgb[100:160, 150:200] = (200, 20, 20)
    rows, cols = np.mgrid[0:SIDE, 0:SIDE]
    rgb -= (60 * np.exp(-((rows - 600) ** 2 + (cols - 600) ** 2) / (2 * 30.0 ** 2)))[..., None]
    rgb[900:930, :] = (20, 40, 180)
    rgb[800:802, 200:202] = (0, 0, 0)
    return Image.fromarray(rgb.clip(0, 255).astype(np.uint8))


def quad_model(path):
    """A 1 x 1 m quad whose one face, a material-bind GeomSubset, is bound to a Material naming SURFACE by
    score:surface."""
    from pxr import Sdf, Usd, UsdGeom, UsdShade
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    mesh = UsdGeom.Mesh.Define(stage, "/Root/quad")
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                            UsdGeom.Tokens.faceVarying).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    material = UsdShade.Material.Define(stage, "/Root/Looks/sign")
    material.GetPrim().CreateAttribute("score:surface", Sdf.ValueTypeNames.String).Set(SURFACE)
    binding = UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    face = binding.CreateMaterialBindSubset("sign_face", [0])
    UsdShade.MaterialBindingAPI.Apply(face.GetPrim()).Bind(material)
    stage.Save()


def held(blobs, folder, files):
    """{name: {sha256, bytes, role}} of files put into the blob store."""
    rows = {}
    for name, role in files.items():
        path = folder / name
        rows[name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size, "role": role}
        blobs.put(rows[name]["sha256"], path)
    return rows


def library(root, blobs, folder):
    """A painted surface (its albedo, orm and surface.json) and the model entry requiring it."""
    painted_albedo().save(folder / "albedo.png")
    Image.new("RGB", (SIDE // 2, SIDE // 2), (255, 128, 0)).save(folder / "orm.png")
    (folder / "surface.json").write_text(json.dumps({"material": "m_opaque__plastic__sign_a01",
                                                     "source": "nvidia_simready", "tiling": False}))
    quad_model(folder / "model.usda")
    base = {"schema": entries.SCHEMA, "version": 1, "state": "draft", "picture": None, "seen_in": [], "usd": None,
            "provenance": {"world": "world1", "taken_from": "nvidia simready at revision abc", "commit": "abc",
                           "made_with": [{"by": "nvidia_simready", "carried": True, "what": "the sign maps"},
                                         {"by": "score", "what": "its look and record"}]},
            "licence": {"id": "CC-BY-4.0", "source": "nvidia_simready", "credit": CREDIT,
                        "url": "https://creativecommons.org/licenses/by/4.0/"}}
    _, surface_id, _ = entries.parse_address(SURFACE)
    entries.write(root, {**base, "address": SURFACE, "kind": "surface", "id": surface_id, "title": "sign",
                         "requires": [], "tags": {"painted_for": ["sign_a01"]},
                         "files": held(blobs, folder, {"albedo.png": "texture", "orm.png": "texture",
                                                       "surface.json": "record"})})
    entries.write(root, {**base, "address": "model/simready_sign_a01@1", "kind": "model", "id": "simready_sign_a01",
                         "title": "sign", "requires": [SURFACE], "tags": {},
                         "usd": {"root": "model.usda", "use": "reference", "layer": "base"},
                         "files": held(blobs, folder, {"model.usda": "usd"})})


def test_a_painted_surfaces_print_is_cut_at_its_size_with_its_credit_and_its_wear_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("SCORE_LIB_CACHE", str(tmp_path / "cache"))
    root, made = tmp_path / "library", tmp_path / "made"
    made.mkdir()
    blobs = LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"])
    library(root, blobs, made)
    report = decals.cut_all(root, blobs, [SURFACE], subset="sign_face", apply=True)
    assert report["refused"] == []
    row = report["cut"][0]
    assert row["px_per_m"] == SIDE
    assert len(row["decals"]) == 1, row  # the smudge, the strip and the dot are not decals
    assert row["dropped"]["strip"] == 1 and row["dropped"]["small"] >= 1
    assert row["dropped"]["soft"] + row["dropped"]["faint"] >= 1
    decal = row["decals"][0]
    assert np.allclose(decal["size_m"], [0.1, 0.06], atol=0.003) and decal["ink"] == "many"
    record = entries.load_address(root, report["written"][0])
    assert record["licence"]["id"] == "CC-BY-4.0" and record["licence"]["credit"].startswith("mark 1 of ")
    assert CREDIT in record["licence"]["credit"] and "cut out as a decal" in record["licence"]["credit"]
    assert record["tags"]["family"] == ["decal"] and record["tags"]["class"] == ["plastic"]
    assert record["tags"]["size_m"] == ["{0:.3f}x{1:.3f}".format(*decal["size_m"])]
    assert decals.check_decal(blobs, record) == []
    assert list((tmp_path / "cache").iterdir()) == []  # the bytes passed through a cache emptied after
