"""verify: a clean export passes, and each failure of spec section 8 is triggered once."""
import json
import os

from score_lib import entry as entries
from score_lib import index, locks
from score_lib.blobs.local import LocalBlobStore
from score_lib.verify import verify


def blobs():
    return LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"])


def messages(world):
    return [f"{address}: {message}" for address, message in verify(world.root, blobs())]


def test_a_clean_export_passes(world):
    world.export()
    assert messages(world) == []


def test_1_invalid_entry(world):
    world.export()
    path = entries.folder_of(world.root, "kit", "room", 1) / "entry.json"
    record = json.loads(path.read_text())
    record["state"] = "finished"
    path.write_text(entries.dumps(record))
    assert any("kit/room@1: schema" in each for each in messages(world))


def test_2_index_out_of_step(world):
    world.export()
    path = world.root / "index/model.json"
    path.write_text(entries.dumps({"schema": "score-lib-index/1", "kind": "model", "entries": []}))
    assert any("index/model.json: index out of step" in each for each in messages(world))


def test_3_locked_digest_differs(world):
    world.export()
    locks.lock_many(world.root, ["kit/room@1"] + entries.load_address(world.root, "kit/room@1")["requires"])
    path = entries.folder_of(world.root, "kit", "room", 1) / "entry.json"
    record = json.loads(path.read_text())
    record["title"] = "Another title"
    path.write_text(entries.dumps(record))
    index.rebuild(world.root)
    assert any("kit/room@1: content digest differs" in each for each in messages(world))


def test_4_blob_missing(world):
    world.export()
    record = entries.load_address(world.root, "sound/hum@1")
    path = blobs().path(record["files"]["sound.json"]["sha256"])
    path.chmod(0o644)
    path.unlink()
    assert any("sound/hum@1: blob missing: sound.json" in each for each in messages(world))


def test_4_blob_corrupt(world):
    world.export()
    record = entries.load_address(world.root, "sound/hum@1")
    path = blobs().path(record["files"]["sound.json"]["sha256"])
    path.chmod(0o644)
    path.write_text("{}")
    assert any("sound/hum@1: blob corrupt" in each for each in messages(world))


def test_5_licence_does_not_match_its_components(world):
    world.export()
    path = entries.folder_of(world.root, "sound", "hum", 1) / "entry.json"
    record = json.loads(path.read_text())
    record["licence"]["commercial_ok"] = False
    path.write_text(entries.dumps(record))
    index.rebuild(world.root)
    assert any("sound/hum@1: licence: commercial_ok" in each for each in messages(world))


def test_6_locked_requires_a_draft(world):
    world.export()
    path = entries.folder_of(world.root, "layer", "base.room", 1) / "entry.json"
    record = json.loads(path.read_text())
    record["state"] = "locked"
    record["locked"] = "2026-10-19"
    path.write_text(entries.dumps(record))
    locks.append(world.root, {"address": record["address"], "digest": entries.digest(record), "date": "2026-10-19"})
    index.rebuild(world.root)
    assert any("layer/base.room@1: locked but requires draft model/crate_1@1" in each for each in messages(world))


def test_7_usd_path_does_not_resolve(world):
    world.export()
    path = entries.folder_of(world.root, "layer", "base.room", 1) / "entry.json"
    record = json.loads(path.read_text())
    record["requires"] = [each for each in record["requires"] if not each.startswith("model/")]
    path.write_text(entries.dumps(record))
    index.rebuild(world.root)
    assert any("layer/base.room@1: usd: layer.usda names score_lib/model/crate_1/1/model.usdc" in each
               for each in messages(world))
