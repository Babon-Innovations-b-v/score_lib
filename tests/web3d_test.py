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
