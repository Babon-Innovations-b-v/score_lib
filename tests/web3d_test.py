"""Web 3D copies, the audit and the export's newer reads on the small fixture world: a copy recorded with both files'
hashes and kept by a re-export, finishes from the library, a place only others show exported, kit names of released
models, sub-outputs of the store, and the audit's counts."""
import json
import os
import struct

from conftest import FixtureStore, usd_text

from score_lib import entry as entries
from score_lib.blobs import from_environment
from score_lib.export.model import kit_names
from score_lib.source.store import Store, sub_output
from score_lib.web3d import finish
from score_lib.web3d import run as web3d_run
from score_lib.web3d.record import source_of

FAR_ROOT = """#usda 1.0
(
    defaultPrim = "far"
    subLayers = [@./layers/base.usda@]
)
"""
FAR_BASE = """#usda 1.0

def Xform "far"
{
    def Mesh "block"
    {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    }
}
"""


def glb_document(path):
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length])


def test_a_sub_output_reads_one_folder():
    fixture = FixtureStore(os.environ["SCORE_STORE"])
    output = fixture.put_output({"places/far/far.usda": b"x", "other.json": b"{}"})
    store = Store(os.environ["SCORE_STORE"])
    assert store.listing(sub_output(output, "places/far")) == {"far.usda": store.listing(output)["places/far/far.usda"]}


def test_kit_names_of_a_released_model():
    assert kit_names("fixture_crate_1", {"model": "models/camp_kit/crate_1.gltf"}) == ["crate_1", "fixture_crate_1"]
    assert kit_names("wall_1", {"model": "wall_1"}) == ["wall_1"]


def test_a_place_only_others_show_is_exported(world):
    neighbours = world.store.put_output({"places/far/far.usda": usd_text(FAR_ROOT, ".usda", world.scratch),
                                         "places/far/layers/base.usda": usd_text(FAR_BASE, ".usda", world.scratch)})
    world.store.accept("neighbours@room", neighbours, "2026-01-01T00:00:00+00:00")
    toml = world.root / "worlds/world1.toml"
    toml.write_text(toml.read_text() + 'shown = ["far"]\n')
    world.export(kinds=["layer", "place"])
    place = entries.load_address(world.root, "place/far@1")
    assert place["provenance"]["node"] == "neighbours@room" and place["provenance"]["folder"] == "places/far/"
    assert "layer/base.far@1" in place["requires"]


def test_the_web_copy_is_recorded_and_kept(world):
    world.export()
    report = web3d_run.run(world.root, from_environment(), world.scratch / "cache", kinds=("model", "place"))
    assert not report["failed"] and len(report["made"]) == 2
    model = entries.load_address(world.root, "model/crate_1@1")
    made = model["web3d"]
    assert made["source"] == source_of(model) and made["finishes"] == {"library bake": 1}
    blobs = from_environment()
    for name, key in (("web3d/full.glb", "full_sha256"), ("web3d/light.glb", "light_sha256")):
        assert model["files"][name]["role"] == "web3d" and model["files"][name]["sha256"] == made[key]
        assert blobs.has(made[key])
    document = glb_document(blobs.path(made["full_sha256"]))
    assert "EXT_meshopt_compression" in document["extensionsUsed"]
    assert {image["mimeType"] for image in document["images"]} == {"image/webp"}
    place = entries.load_address(world.root, "place/room@1")
    assert "model/crate_1@1" in place["web3d"]["from"]
    again = world.export()
    assert all(found["counts"]["changed draft"] == 0 for found in again["kinds"].values())
    assert entries.load_address(world.root, "model/crate_1@1")["web3d"] == made
    assert web3d_run.run(world.root, blobs, world.scratch / "cache", kinds=("model", "place"))["standing"] == 2
    blobs.path(made["full_sha256"]).unlink()  # a copy lost from the blob store is made again
    report = web3d_run.run(world.root, blobs, world.scratch / "cache", kinds=("model", "place"))
    assert [each["reason"] for each in report["made"]] == ["its copy is not in the blob store"]
    assert blobs.has(entries.load_address(world.root, "model/crate_1@1")["web3d"]["full_sha256"])


def test_a_picture_finish_is_replaced_by_the_nearest_library_surface(world):
    world.export(kinds=["surface"])
    library = finish.Library(world.root, from_environment())
    assert set(library.finishes) == {"panel_paint"}  # unused_paint: no place writes it, so it has no material
    variant, distance = library.nearest((0.3, 0.4, 0.5), glowing=False)
    assert variant == "panel_paint" and distance < 1.0


def test_the_audit_counts_the_world_against_the_library(world):
    from score_lib import audit
    world.export()
    report = audit.audit(world.context())
    for kind in ("model", "surface", "effect", "sound", "layer", "place", "kit"):
        found = report["kinds"][kind]
        assert found["missing"] == 0 and found["in"] == found["world"], kind


def seamed_box(folder, size):
    """A glTF box of `size` whose six faces each have their own UV square (every edge a seam) and a colour picture."""
    import numpy as np
    from PIL import Image
    corners, uvs, indices = [], [], []
    for axis in range(3):
        for side in (-0.5, 0.5):
            for first, second in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
                point = [first, second]
                point.insert(axis, side)
                corners.append([value * extent for value, extent in zip(point, size)])
            uvs += [(0, 0), (1, 0), (1, 1), (0, 1)]
            start = len(corners) - 4
            indices += [start, start + 1, start + 2, start, start + 2, start + 3]
    positions = np.array(corners, np.float32).tobytes()
    texcoords = np.array(uvs, np.float32).tobytes()
    triangles = np.array(indices, np.uint16).tobytes()
    (folder / "scene.bin").write_bytes(positions + texcoords + triangles)
    Image.new("RGB", (64, 64), (200, 80, 40)).save(folder / "cube_base_color.png")
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
             {"buffer": 0, "byteOffset": len(positions), "byteLength": len(texcoords)},
             {"buffer": 0, "byteOffset": len(positions) + len(texcoords), "byteLength": len(triangles)}]
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "cube"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}], "images": [{"uri": "cube_base_color.png"}],
        "buffers": [{"uri": "scene.bin", "byteLength": len(positions) + len(texcoords) + len(triangles)}],
        "bufferViews": views,
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3",
                       "min": [-extent / 2 for extent in size], "max": [extent / 2 for extent in size]},
                      {"bufferView": 1, "componentType": 5126, "count": 24, "type": "VEC2"},
                      {"bufferView": 2, "componentType": 5123, "count": 36, "type": "SCALAR"}]}
    (folder / "scene.gltf").write_text(json.dumps(document))
    return folder / "scene.gltf"


def test_the_light_copy_keeps_its_seams(tmp_path):
    """The light preview never collapses across UV seams: -sa smeared every baked finish into streaks (2026-10-20)."""
    from score_lib.web3d import pack
    source = tmp_path / "source"
    source.mkdir()
    made = pack.packed(seamed_box(source, (1.6, 1.6, 0.02)), tmp_path, tmp_path / "cache")
    for name in ("light", "full"):
        document = glb_document(made[name])
        indices = [document["accessors"][primitive["indices"]]["count"]
                   for mesh in document["meshes"] for primitive in mesh["primitives"]]
        assert sum(indices) == 36, name
