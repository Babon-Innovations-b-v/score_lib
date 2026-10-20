"""Preview clips on the small fixture world: what a clip opens by shelf, a clip put into its draft and kept by a
re-export of the same content, and refused for a locked entry."""
import json

from score_lib import clip
from score_lib import entry as entries
from score_lib import locks
from score_lib.blobs import from_environment


def addresses(world, kind):
    return [record["address"] for record in entries.all_entries(world.root, kind)]


def test_the_plan_opens_a_model_and_an_effect_and_skips_a_sound(world):
    world.export()
    model, effect, sound = addresses(world, "model")[0], addresses(world, "effect")[0], addresses(world, "sound")[0]
    rows = {row["address"]: row for row in clip.plan(world.root, from_environment(), [model, effect, sound],
                                                     world.scratch / "clips")}
    assert rows[model]["why"] == "" and rows[model]["shelf"] == "model" and rows[model]["focus"] is None
    assert "clip.usda" in rows[model]["open"]
    assert rows[effect]["why"] == "" and rows[effect]["end"] >= rows[effect]["start"]
    assert rows[sound]["why"] == "no clip for this sound"
    saved = json.loads((world.scratch / "clips" / clip.PLAN).read_text())
    assert [row["address"] for row in saved] == [model, effect, sound]


def test_a_clip_is_kept_by_a_re_export_and_refused_when_locked(world, tmp_path):
    world.export()
    model = addresses(world, "model")[0]
    film = tmp_path / "clip.mp4"
    film.write_bytes(b"not a real film, only its bytes")
    row = {"address": model, "file": str(film), "shelf": "model", "frames": 96, "seconds": 4.0, "size": [1280, 720],
           "made_by": "score library-clips"}
    report = clip.attach(world.root, from_environment(), [row], "clips: test")
    assert report["attached"] == [model] and not report["refused"]
    record = entries.load_address(world.root, model)
    assert clip.stands(record) and record["files"][clip.FILE]["role"] == "clip"
    assert clip.plan(world.root, from_environment(), [model], tmp_path / "again")[0]["why"] == "its clip stands"
    again = world.export()
    assert all(found["counts"]["changed draft"] == 0 for found in again["kinds"].values())
    assert entries.load_address(world.root, model)["clip"] == record["clip"]
    locks.lock(world.root, model)
    refused = clip.attach(world.root, from_environment(), [row], "clips: test")["refused"]
    assert refused == [{"address": model, "why": "locked: only a draft takes a clip"}]
