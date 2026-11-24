"""hardware: one piece of an adopted model's hardware is cut out in its own frame (out of its face along +z, its back
at z 0, centred) at its size in metres, carrying its source's licence and credit. Red when: the piece's mesh is read
without its prim's transform or the stage's units (its size or place wrong), its facing is not the axis out of its
face (a door's lock read as facing into the door), its back is not at z 0, the licence or the credit is not carried,
the cache is not emptied, or the entry written does not pass verify's schema and admission checks."""
import json
import os
import struct

import numpy as np

from score_lib import entry as entries
from score_lib import hardware, verify
from score_lib.blobs import file_sha256
from score_lib.blobs.local import LocalBlobStore

MODEL = "model/simready_locker_a01@1"
LOCK = "/Root/lock/mesh"
CREDIT = ("locker_a01 by NVIDIA Corporation, from huggingface.co/datasets/nvidia/simready-assets at revision abc, "
          "CC BY 4.0")


def box_mesh(stage, path, low, high):
    """A box mesh prim at `path` from `low` to `high` (stage units)."""
    from pxr import UsdGeom
    mesh = UsdGeom.Mesh.Define(stage, path)
    corners = [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    mesh.CreatePointsAttr(corners)
    quads = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    mesh.CreateFaceVertexIndicesAttr([index for quad in quads for index in quad])
    return mesh


def locker(path):
    """A locker in centimetres (metres per unit 0.01): its body 60 x 50 x 180 about the origin, and a lock 3 x 1 x 5
    on its front (-y), moved there by its Xform (its points about its own origin)."""
    from pxr import Gf, Usd, UsdGeom
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    box_mesh(stage, "/Root/body", (-30, -25, 0), (30, 25, 180))
    UsdGeom.Xform.Define(stage, "/Root/lock").AddTranslateOp().Set(Gf.Vec3d(10, -25.5, 100))
    box_mesh(stage, LOCK, (-1.5, -0.5, -2.5), (1.5, 0.5, 2.5))
    stage.Save()


def library(root, blobs, folder):
    """The locker as an adopted model entry carrying NVIDIA's licence."""
    locker(folder / "model.usda")
    path = folder / "model.usda"
    files = {"model.usda": {"sha256": file_sha256(path), "bytes": path.stat().st_size, "role": "usd"}}
    blobs.put(files["model.usda"]["sha256"], path)
    entries.write(root, {
        "schema": entries.SCHEMA, "address": MODEL, "kind": "model", "id": "simready_locker_a01", "version": 1,
        "title": "locker", "state": "draft", "picture": None, "seen_in": [], "requires": [], "tags": {},
        "usd": {"root": "model.usda", "use": "reference", "layer": "base"}, "files": files,
        "provenance": {"world": "world1", "taken_from": "nvidia simready at revision abc", "commit": "abc",
                       "made_with": [{"by": "nvidia_simready", "carried": True, "what": "the locker"},
                                     {"by": "score", "what": "its record"}]},
        "licence": {"id": "CC-BY-4.0", "source": "nvidia_simready", "credit": CREDIT,
                    "url": "https://creativecommons.org/licenses/by/4.0/"}})


def glb_points(path):
    """The positions of a one-mesh .glb as written by hardware.glb_bytes."""
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    document = json.loads(data[20:20 + length])
    count = document["accessors"][0]["count"]
    return np.frombuffer(data[20 + length + 8:20 + length + 8 + count * 12], "<f4").reshape(-1, 3)


def test_a_piece_is_cut_out_of_its_face_at_its_size_with_its_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("SCORE_LIB_CACHE", str(tmp_path / "cache"))
    root, made = tmp_path / "library", tmp_path / "made"
    made.mkdir()
    blobs = LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"])
    library(root, blobs, made)
    report = hardware.cut_all(root, blobs, [{"address": MODEL, "prim": LOCK, "kind": "lock", "moves": "turn"}],
                              apply=True)
    assert report["refused"] == []
    row = report["cut"][0]
    assert row["facing"] == "-y" and np.allclose(row["size_m"], [0.03, 0.05, 0.01], atol=1e-4), row
    record = entries.load_address(root, report["written"][0])
    assert record["tags"]["family"] == ["hardware"] and record["tags"]["kind"] == ["lock"]
    assert record["tags"]["moves"] == ["turn"] and record["tags"]["size_m"] == ["0.0300x0.0500x0.0100"]
    assert CREDIT in record["licence"]["credit"] and "cut out as a hardware piece" in record["licence"]["credit"]
    piece = tmp_path / "piece.glb"
    blobs.get(record["files"]["hardware.glb"]["sha256"], piece)
    points = glb_points(piece)
    assert np.allclose(points.min(0), [-0.015, -0.025, 0.0], atol=1e-5)
    assert np.allclose(points.max(0), [0.015, 0.025, 0.01], atol=1e-5)
    faults = verify.check_schemas(root, [record]) + verify.check_admission(root, [record], verify.Sources.load())
    assert faults == []
    assert list((tmp_path / "cache").iterdir()) == []  # the bytes passed through a cache emptied after
