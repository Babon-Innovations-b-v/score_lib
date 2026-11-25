"""`score-lib hardware cut --from model/<id>@<v> --prim <mesh prim> --kind <kind> [--facing AXIS] [--moves HOW]
[--apply] [--save] [--report FILE]`: one small piece of an adopted model's hardware (a cam lock, a knob, a handle, a
hinge, a latch, a push button) cut out as a draft hardware entry (score #212), which score's finish route lays on a made model where
the close-up shows such a piece, in place of the blob a picture-made model draws there.

A hardware piece is a `model` entry tagged family `hardware`: `hardware.glb` (its mesh alone, triangles and normals, no
material: the route paints it with a library surface) in its own frame, and `hardware.json` {kind, size_m [wide, tall,
deep], moves, facing, from {model, prim}}. Its frame: +z out of the face it is mounted on (its `facing`), +y up (the
model's up, or its +y when it faces up or down), +x their cross; centred across and up, its back (the side on the face)
at z 0, metres. Its facing is the given axis, else its box's thinnest axis, pointing away from the model's middle (a
door's cam lock faces out of the door). Its licence block and credit come over from the model it was cut from, the cut
marked as a change.

Without `apply` the cut is read and reported, nothing written; with `apply` its bytes go into the blob store and its
draft entry is written; with `save` it is committed and pushed under the handover's lock. Bytes pass through a cache
folder emptied after each cut (the library's disk rule).
"""
import json
import os
import pathlib
import re
import shutil
import struct
import tempfile

import numpy as np

from score_lib import entry as entries
from score_lib import fetch, handover, index
from score_lib.blobs import MissingBlob, file_sha256, put_all

KINDS = ("lock", "knob", "handle", "hinge", "latch", "button")  # a button: a push button on a panel (score #229)
MOVES = ("none", "turn", "press", "slide")  # how the piece moves on its part: score #177's joints
AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
CUT_WHAT = "cut out as a hardware piece (score-lib hardware cut, score #212)"
FAMILY = "hardware"
MESH, RECORD = "hardware.glb", "hardware.json"
LIBRARY_BRANCH = "main"


class Refused(ValueError):
    """A piece that cannot be cut as given."""


def triangles(counts, indices):
    """A polygon mesh's faces as triangles (fans), [[a, b, c], ...]."""
    found, start = [], 0
    for count in counts:
        corners = indices[start:start + count]
        found += [[corners[0], corners[step], corners[step + 1]] for step in range(1, count - 1)]
        start += count
    return np.asarray(found, dtype=np.int64).reshape(-1, 3)


def prim_mesh(stage, path):
    """(points in metres in the stage's world, triangles) of the mesh prim at `path` (an instance proxy too)."""
    from pxr import Usd, UsdGeom
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdGeom.Mesh):
        raise Refused(f"{path}: no mesh prim there")
    mesh = UsdGeom.Mesh(prim)
    points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
    world = np.asarray(UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(prim), dtype=np.float64)
    points = (np.c_[points, np.ones(len(points))] @ world)[:, :3] * UsdGeom.GetStageMetersPerUnit(stage)
    faces = triangles(mesh.GetFaceVertexCountsAttr().Get(), list(mesh.GetFaceVertexIndicesAttr().Get()))
    return points, faces


def model_box(stage):
    """(low, high) in metres of the whole stage's renderable geometry."""
    from pxr import Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
    box = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    scale = UsdGeom.GetStageMetersPerUnit(stage)
    return np.asarray(box.GetMin()) * scale, np.asarray(box.GetMax()) * scale


def facing_of(points, middle, given=None):
    """The piece's facing (a unit axis): `given` (a key of AXES), else its box's thinnest axis pointing from the
    model's `middle` to the piece."""
    if given:
        return np.asarray(AXES[given], dtype=float)
    low, high = points.min(0), points.max(0)
    axis = int(np.argmin(high - low))
    way = np.zeros(3)
    way[axis] = 1.0 if (low[axis] + high[axis]) / 2 >= middle[axis] else -1.0
    return way


def own_frame(points, facing):
    """`points` in the piece's own frame (module docstring): +z its facing, +y the model's up (+z; its +y when it faces
    up or down), centred across and up, its back at z 0."""
    up = np.array([0.0, 0.0, 1.0]) if abs(facing[2]) < 0.5 else np.array([0.0, 1.0, 0.0])
    across = np.cross(up, facing)
    turned = points @ np.stack([across, up, facing]).T
    low, high = turned.min(0), turned.max(0)
    return turned - np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])


def vertex_normals(points, faces):
    """Each vertex's normal: the area-weighted sum of its faces' normals, of length 1."""
    crossed = np.cross(points[faces[:, 1]] - points[faces[:, 0]], points[faces[:, 2]] - points[faces[:, 0]])
    normals = np.zeros_like(points)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], crossed)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, length, out=np.zeros_like(normals), where=length > 0)


def glb_bytes(points, faces):
    """A binary glTF of one mesh (positions, normals, 32-bit indices), no material; glTF's Y up, so the piece's own
    frame (z out of its face) is written as is and read back in it by a reader that keeps glTF's axes."""
    positions = points.astype("<f4").tobytes()
    normals = vertex_normals(points, faces).astype("<f4").tobytes()
    indices = faces.astype("<u4").tobytes()
    blob = positions + normals + indices
    low, high = points.min(0).tolist(), points.max(0).tolist()
    document = {
        "asset": {"version": "2.0", "generator": "score-lib hardware cut"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "hardware"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(normals), "target": 34962},
                        {"buffer": 0, "byteOffset": len(positions) + len(normals), "byteLength": len(indices),
                         "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(points), "type": "VEC3", "min": low,
                       "max": high},
                      {"bufferView": 1, "componentType": 5126, "count": len(points), "type": "VEC3"},
                      {"bufferView": 2, "componentType": 5125, "count": int(faces.size), "type": "SCALAR"}]}
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    blob += b"\0" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def piece_record(kind, moves, facing_name, size, source, prim):
    """A piece's hardware.json."""
    return {"kind": kind, "size_m": size, "moves": moves, "facing": facing_name,
            "from": {"model": source["address"], "prim": prim}}


def credit_of(licence, prim, source):
    """A piece's credit: its source's credit (less its own change note) naming the prim and the cut as a change."""
    carried = licence["credit"].split("; changed:")[0]
    return f"{prim} cut from {carried}; changed: cut out as a hardware piece from {source} (score-lib hardware cut)"


def piece_entry(entry_id, source, cut, files):
    """A piece's draft entry record, its files {name: (sha256, bytes)}; its licence carried from its source."""
    provenance = source["provenance"]
    carried = [row for row in provenance.get("made_with", []) if row.get("carried")]
    record = {
        "schema": entries.SCHEMA, "address": entries.address_of("model", entry_id, 1), "kind": "model",
        "id": entry_id, "version": 1, "title": f"{cut['kind']} from {source['id']} (hardware)", "state": "draft",
        "picture": None, "picture_reason": "a hardware piece cut by score-lib hardware cut: no picture made yet",
        "files": {name: {"sha256": sha, "bytes": size, "role": "record" if name.endswith(".json") else "gltf"}
                  for name, (sha, size) in sorted(files.items())},
        "usd": None, "requires": [], "seen_in": [],
        "provenance": {"world": provenance.get("world", "world1"), "taken_from": provenance.get("taken_from"),
                       "files_from": {name: ("written by score-lib hardware cut" if name.endswith(".json") else
                                             f"cut from {source['address']}'s prim {cut['prim']}")
                                      for name in sorted(files)},
                       "made_with": sorted(carried + [{"by": "score", "what": CUT_WHAT}],
                                           key=lambda row: (row["by"], row["what"]))},
        "tags": {"family": [FAMILY], "kind": [cut["kind"]], "moves": [cut["moves"]], "cut_from": [source["id"]],
                 "size_m": ["{0:.4f}x{1:.4f}x{2:.4f}".format(*cut["size_m"])]}}
    if provenance.get("commit"):
        record["provenance"]["commit"] = provenance["commit"]
    if source.get("licence"):
        record["licence"] = {**source["licence"], "credit": credit_of(source["licence"], cut["prim"],
                                                                      source["address"])}
    return record


def source_stage(root, blobs, source, folder):
    """The source model's stage, its USD layers fetched into `folder`."""
    from pxr import Usd
    if source["kind"] != "model" or not source.get("usd"):
        raise Refused(f"{source['address']}: hardware is cut from a model entry with a USD")
    names = [name for name, described in source["files"].items() if described["role"] == "usd"]
    fetch.fetch(root, blobs, [source["address"]], folder, names=names)
    return Usd.Stage.Open(str(fetch.destination(folder, source, source["usd"]["root"])))


def cut_piece(root, blobs, address, prim, kind, cache, facing=None, moves="none"):
    """One piece cut: (its report row, its draft (record, {name: path})) with its files in `cache`."""
    if kind not in KINDS:
        raise Refused(f"{kind!r} is not one of {', '.join(KINDS)}")
    if moves not in MOVES:
        raise Refused(f"{moves!r} is not one of {', '.join(MOVES)}")
    source = entries.load_address(root, address)
    if source is None:
        raise Refused(f"{address}: the library holds no such entry")
    stage = source_stage(root, blobs, source, cache / "model")
    points, faces = prim_mesh(stage, prim)
    low, high = model_box(stage)
    way = facing_of(points, (low + high) / 2, facing)
    placed = own_frame(points, way)
    size = [round(float(side), 4) for side in placed.max(0) - placed.min(0)]
    folder = cache / "piece"
    folder.mkdir(parents=True)
    (folder / MESH).write_bytes(glb_bytes(placed, faces))
    facing_name = next(name for name, axis in AXES.items() if np.allclose(axis, way))
    (folder / RECORD).write_text(json.dumps(piece_record(kind, moves, facing_name, size, source, prim), indent=1,
                                            sort_keys=True) + "\n")
    sha = file_sha256(folder / MESH)
    name = re.sub(r"[^a-z0-9]+", "_", source["id"].removeprefix("simready_").lower()).strip("_")
    entry_id = f"hardware_{kind}_{name}_{sha[:8]}"
    files = {file_name: folder / file_name for file_name in (MESH, RECORD)}
    sized = {file_name: (file_sha256(path), path.stat().st_size) for file_name, path in files.items()}
    cut = {"kind": kind, "moves": moves, "size_m": size, "prim": prim}
    row = {"id": entry_id, "source": address, "prim": prim, "kind": kind, "moves": moves, "size_m": size,
           "facing": facing_name, "faces": int(len(faces))}
    return row, (piece_entry(entry_id, source, cut, sized), files)


def written(root, blobs, drafts):
    """The drafts' bytes into the blob store and their entries written; the addresses (an id the library already
    holds is left as it is)."""
    fresh = [(record, files) for record, files in drafts if not entries.versions(root, "model", record["id"])]
    put_all(blobs, [(record["files"][name]["sha256"], path) for record, files in fresh for name, path in files.items()])
    for record, _ in fresh:
        entries.write(root, record)
    return [record["address"] for record, _ in fresh]


def saved(root, addresses):
    """The pieces written committed and pushed under the handover's lock; the save block."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        index.rebuild(root)
        folders = sorted({f"entries/model/{entries.parse_address(address)[1]}" for address in addresses})
        message = f"hardware: {len(addresses)} hardware pieces cut from adopted models as drafts (score #212)"
        return handover.saved(root, message, (*folders, "index"), LIBRARY_BRANCH)


def cache_root():
    """The cache folder a cut passes through (emptied after each piece)."""
    base = pathlib.Path(os.environ.get("SCORE_LIB_CACHE") or pathlib.Path.home() / ".cache/score-lib")
    base.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix="hardware-", dir=base))


def cut_all(root, blobs, pieces, apply=False, save=False):
    """`hardware cut`: each piece ({address, prim, kind, facing, moves}) cut; {cut: [report rows], refused: [{source,
    prim, why}], written: [address]}, with `save` also {saved: block}."""
    if save and not apply:
        raise Refused("--save saves what --apply writes: give both")
    report = {"cut": [], "refused": [], "written": []}
    for piece in pieces:
        cache = cache_root()
        try:
            row, draft = cut_piece(root, blobs, piece["address"], piece["prim"], piece["kind"], cache,
                                   piece.get("facing"), piece.get("moves") or "none")
            report["cut"].append(row)
            if apply:
                report["written"] += written(root, blobs, [draft])
        except (Refused, fetch.MissingFile, MissingBlob) as why:
            report["refused"].append({"source": piece["address"], "prim": piece["prim"], "why": str(why)})
        finally:
            shutil.rmtree(cache, ignore_errors=True)
    if apply:
        index.rebuild(root)
    if save and report["written"]:
        report["saved"] = saved(root, report["written"])
    return report
