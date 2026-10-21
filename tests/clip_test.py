"""Preview clips on the small fixture world: what a clip opens by shelf, a clip put into its draft and kept by a
re-export of the same content, and refused for a locked entry."""
import json

from score_lib import clip
from score_lib import entry as entries
from score_lib import locks
from score_lib.blobs import from_environment
from score_lib.web3d import build
from pxr import Sdf, Vt


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
    # a picture-role file is fetched too (a sky's dome picture is one its sky.usda opens)
    assert (build.entry_folder(world.scratch / "clips", effect) / "preview.png").exists()
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


def test_a_sparse_effect_plays_one_loop_from_where_it_shows_most():
    """The breath (score's data/effects/flat.json): 4000 cards let out over 0.3 of a 4 s life from 0.8 s into every
    20 s, faded in to 0.1 of a life and out by its end, baked at 4 samples a second; its stage spans 600 s."""
    import numpy
    from pxr import Usd, UsdGeom
    count, lifetime, every, at, rate = 4000, 4.0, 20.0, 0.8, 24.0
    births = at + numpy.arange(count) / count * lifetime * (1 - 0.7)
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/breath", "Xform").CreateAttribute("score:effect:loop", Sdf.ValueTypeNames.Float).Set(every)
    opacity = UsdGeom.Points.Define(stage, "/breath/breath_points").GetPrim().CreateAttribute(
        "primvars:displayOpacity", Sdf.ValueTypeNames.FloatArray)
    for second in numpy.arange(0.0, 3 * every, 0.25):
        life = ((second - births) % every) / lifetime
        alive = 0.06 * numpy.interp(life, [0.0, 0.1, 1.0], [0.0, 1.0, 0.0]) * (life < 1) * (second >= births)
        opacity.Set(Vt.FloatArray(alive.tolist()), second * rate)
    played = clip.busiest_loop(stage, {"start": 0.0, "end": 600 * rate, "per_second": rate})
    assert sum(opacity.Get(0.0)) == 0  # the old clip's first frame: nothing let out yet
    assert at * rate < played["start"] < (at + lifetime) * rate and played["end"] - played["start"] == every * rate
    assert sum(opacity.Get(played["start"])) == max(sum(opacity.Get(code)) for code in opacity.GetTimeSamples())
    print("breath plays", played)
