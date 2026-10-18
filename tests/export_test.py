"""The exporters on a small place stage made in the test: the entries, the rewritten paths, idempotence, refusal on
an unknown licence and a new version on a locked change."""
import hashlib
import json
import os
import pathlib

from conftest import MANIFEST_TEXT, World

from score_lib import entry as entries
from score_lib import locks
from score_lib.blobs.local import LocalBlobStore


def blob_text(record, name):
    store = LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"])
    return store.path(record["files"][name]["sha256"]).read_text()


def tree_digest(folder):
    """One hash of every file's path and bytes under a folder."""
    digest = hashlib.sha256()
    for path in sorted(pathlib.Path(folder).rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(folder)).encode() + path.read_bytes())
    return digest.hexdigest()


def test_every_kind_writes_its_entries(world):
    report = world.export()
    counts = {kind: found["counts"]["new"] for kind, found in report["kinds"].items()}
    assert counts["model"] == 1 and counts["effect"] == 1 and counts["sound"] == 1 and counts["place"] == 1
    assert counts["surface"] == 2 and counts["kit"] == 1 and counts["algorithm"] == 2
    assert counts["layer"] == 3  # base, effects, sound; the edit slot rides in the place


def test_model_entry(world):
    world.export()
    model = entries.load_address(world.root, "model/crate_1@1")
    assert set(model["files"]) >= {"model.usdc", "textures/crate_1_base_color.png", "model.json", "preview.png"}
    assert model["licence"]["spdx"] == "MIT" and model["licence"]["commercial_ok"]
    assert "copyright" in model["licence"]["note"]
    assert model["usd"]["default_prim"] == "/crate_1" and model["usd"]["use"] == "reference"
    assert model["seen_in"] == ["room"] and model["tags"]["model"] == ["piece"]
    assert model["tags"]["place_kind"] == ["inside", "moon"]
    assert model["provenance"]["model_sha256"] == hashlib.sha256(b"crate gltf").hexdigest()


def test_effect_and_sound_paths_are_rewritten(world):
    world.export()
    effect = entries.load_address(world.root, "effect/steam@1")
    assert "@./clip.usdc@" in blob_text(effect, "effect.usda")
    assert json.loads(blob_text(effect, "settings.json"))["kind"] == "steam_jet"
    assert effect["requires"] == []  # the fixture library holds no particle_effects algorithm
    sound = entries.load_address(world.root, "sound/hum@1")
    assert "@./audio/generated/places/hum.wav@" in blob_text(sound, "sound.usda")
    assert sound["licence"]["spdx"] == "CC0-1.0"


def test_layers_point_at_library_addresses(world):
    world.export()
    base = entries.load_address(world.root, "layer/base.room@1")
    text = blob_text(base, "layer.usda")
    assert "@score_lib/model/crate_1/1/model.usdc@" in text
    assert "model/crate_1@1" in base["requires"]
    effects = entries.load_address(world.root, "layer/effects.room@1")
    assert "@score_lib/effect/steam/1/clip.usdc@" in blob_text(effects, "layer.usda")
    assert "held.json" in effects["files"]
    place = entries.load_address(world.root, "place/room@1")
    root_text = blob_text(place, "room.usda")
    assert "@score_lib/layer/base.room/1/layer.usda@" in root_text and "@./edit.usda@" in root_text
    assert place["tags"]["complete"] == ["named_gaps"] and "gaps.json" in place["files"]


def test_two_runs_write_the_same_bytes(world):
    world.export()
    first = tree_digest(world.root / "entries")
    report = world.export()
    assert tree_digest(world.root / "entries") == first
    assert all(found["counts"]["new"] == 0 and found["counts"]["changed draft"] == 0
               for found in report["kinds"].values())


def test_dry_run_writes_nothing(world):
    report = world.export(dry_run=True)
    assert report["kinds"]["model"]["counts"]["new"] == 1
    assert not any((world.root / "entries").rglob("entry.json"))


def test_unknown_licence_is_refused(tmp_path):
    rules = [{"match": "models/", "licence": "a licence nobody wrote down", "source": "x", "author": "y"}]
    world = World(tmp_path, rules)
    report = world.export()
    refused = report["kinds"]["model"]["refused"]
    assert refused and "a licence nobody wrote down" in refused[0]["reason"]
    assert not (world.root / "entries/model").exists()
    layer = entries.load_address(world.root, "layer/base.room@1")
    assert "assets/crate_1.usdc" in layer["files"]  # the layer keeps the refused model as its own file


def test_locked_change_becomes_a_new_version(world):
    world.export(kinds=["model"])
    locks.lock_many(world.root, ["model/crate_1@1"])
    locked = entries.load_address(world.root, "model/crate_1@1")
    world.publish_place("2026-02-01T00:00:00+00:00", crate_colour=(20, 200, 60))
    report = world.export(kinds=["model"])
    assert report["kinds"]["model"]["addresses"]["new version"] == ["model/crate_1@2"]
    assert entries.load_address(world.root, "model/crate_1@1") == locked
    assert entries.load_address(world.root, "model/crate_1@2")["state"] == "draft"
    assert locks.read(world.root)[0]["address"] == "model/crate_1@1"


def test_manifest_text_is_known():
    from score_lib.licences import Licences
    assert Licences.load().lookup(MANIFEST_TEXT)["spdx"] == "MIT"


def test_layers_and_places_carry_pictures(world):
    world.export()
    for address in ("layer/base.room@1", "layer/effects.room@1"):
        layer = entries.load_address(world.root, address)
        assert layer["picture"]["made_by"] == "preview/layer_map.py" and "preview.png" in layer["files"]
    sound = entries.load_address(world.root, "layer/sound.room@1")  # one room sound, no positions to map
    assert sound["picture"]["made_by"] == "preview/audio.py"
    place = entries.load_address(world.root, "place/room@1")
    assert place["picture"]["made_by"] == "preview/plan.py"  # no light pass in the fixture: its kit from above


def test_place_takes_its_plan_cast_and_light_look(world):
    from conftest import png
    plan = world.store.put_output({"plan.json": b'{"items": []}', "contract.json": b"{}"})
    cast = world.store.put_output({"cast.json": b'{"characters": []}', "measures.json": b"{}"})
    light = world.store.put_output({"looks/room/shot0-000-look.png": png((10, 20, 30)), "lamps.json": b"{}",
                                    "measures.json": b'{"brightness": {}}'})
    for stage, output in (("plan", plan), ("cast", cast), ("light", light)):
        world.store.accept(f"{stage}@room", output, "2026-01-02T00:00:00+00:00")
    world.export()
    place = entries.load_address(world.root, "place/room@1")
    assert {"plan/plan.json", "cast/cast.json", "cast/measures.json", "picture.png"} <= set(place["files"])
    assert place["picture"] == {"file": "picture.png", "made_by": "source picture",
                                "of": "light@room: looks/room/shot0-000-look.png"}


def test_home_folder_is_left_out_of_text_records(world, tmp_path):
    from score_lib.export.run import Candidate, home_free
    source = tmp_path / "origin.json"
    source.write_text(json.dumps({"from": f"{pathlib.Path.home()}/work/room"}))
    made = Candidate(kind="place", base_id="room", identity="x", title="Room", places=["room"],
                     files={"origin.json": source}, roles={"origin.json": "record"}, known={"origin.json": "a" * 64})
    home_free(world.context(), made)
    assert json.loads(made.files["origin.json"].read_text()) == {"from": "~/work/room"}
    assert made.files_from["origin.json"]["sha256"] == "a" * 64 and "origin.json" not in made.known
