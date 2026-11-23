"""score-lib adopted recipes: score's look-alike results written into adopted surfaces (score #155).

The breaks that turn it red: a pass not setting surface.json's recipe, likeness and a null gap, or made_with not
gaining the recipe as our own work; a fail not naming its measures past their bounds; a locked entry rewritten in
place instead of getting its next version as a draft; a non-adopted or painted-for-one-model surface written; a
report-only run writing anything; a second run writing again; a save not reaching origin, or one that does not
reach it not stopping the run."""
import json
import subprocess

import pytest

from conftest import git

from score_lib import adopted_recipes, handover, locks
from score_lib import entry as entries
from score_lib.blobs import file_sha256, from_environment

ALGORITHM = "algorithm/surface_recipes@2"
VIEW_PASS = {"delta_e": 0.0, "lightness": 0.0, "highlight": 0.0, "detail": 1.0, "map_lab": [100.0, 0.0, 0.0],
             "recipe_lab": [100.0, 0.0, 0.0]}
VIEW_FAIL = {"delta_e": 5.436, "lightness": 4.407, "highlight": -9.566, "detail": 0.495,
             "map_lab": [23.721, -0.115, -0.896], "recipe_lab": [22.719, -0.131, -0.862]}
RECIPE = {"recipe": "composite", "settings": {"roughness": 0.32, "colour": [0.04, 0.05, 0.05]},
          "wear_levels": {"new": 0.0, "worked": 0.4, "worn": 0.8}}
LICENCE = {"credit": "a test", "id": "CC-BY-4.0", "source": "nvidia_simready",
           "url": "https://creativecommons.org/licenses/by/4.0/"}
ADOPTED_MADE = [{"by": "nvidia_simready", "carried": True, "what": "the maps of an asset"},
                {"by": "score", "what": "its look and record"}]


def surface(root, blobs, folder, entry_id, made_with=ADOPTED_MADE, licence=LICENCE, tags=None):
    """A draft surface entry holding a surface.json (its recipe a gap), its blob put."""
    path = folder / f"{entry_id}.json"
    path.write_text(entries.dumps({"source": "nvidia_simready", "family": "image", "tiling": True,
                                   "recipe": None, "recipe_gap": None, "maps": {"albedo": "albedo.png"}}))
    sha256 = file_sha256(path)
    blobs.put(sha256, path)
    record = {"schema": entries.SCHEMA, "address": f"surface/{entry_id}@1", "kind": "surface", "id": entry_id,
              "version": 1, "title": entry_id, "state": "draft", "picture": None, "picture_reason": "none",
              "files": {"surface.json": {"sha256": sha256, "bytes": path.stat().st_size, "role": "record"}},
              "usd": None, "requires": [], "tags": tags or {"maps_key": ["k"]}, "seen_in": [],
              "provenance": {"world": "world1", "commit": "0" * 40, "taken_from": "a test",
                             "made_with": made_with, "files_from": {"surface.json": "written by a test"}}}
    if licence:
        record["licence"] = licence
    entries.write(root, record)


def result(folder, entry_id, outcome):
    """One likeness.json of the check's shape."""
    address = f"surface/{entry_id}@1"
    found = {"address": address, "error": "the library holds no albedo.png", "rounds": 0}
    if outcome != "unread":
        view = VIEW_PASS if outcome == "pass" else VIEW_FAIL
        found = {"address": address, "family_from": "name", "tile_metres_from": "assumed", "rounds": 6,
                 "statistics": {}, "recipe": RECIPE,
                 "likeness": {"measures": {"overview": VIEW_PASS, "close": view}, "pass": outcome == "pass",
                              "algorithm": ALGORITHM}}
    (folder / f"{entry_id}@1").mkdir(parents=True)
    (folder / f"{entry_id}@1/likeness.json").write_text(json.dumps(found))


def surface_json(blobs, root, address, scratch):
    record = entries.load_address(root, address)
    blobs.get(record["files"]["surface.json"]["sha256"], scratch / "read.json")
    return record, json.loads((scratch / "read.json").read_text())


def test_look_alike_results_become_recipes_or_gaps(tmp_path):
    root, blobs, made, run = tmp_path / "library", from_environment(), tmp_path / "made", tmp_path / "run"
    made.mkdir()
    rows = {"passing": "pass", "failing": "fail", "unreadable": "unread", "ours": "pass", "painted": "fail"}
    for entry_id in rows:
        surface(root, blobs, made, entry_id, **({"made_with": [{"by": "score", "what": "a bake"}], "licence": None}
                                                if entry_id == "ours" else {}),
                **({"tags": {"painted_for": ["an_asset"]}} if entry_id == "painted" else {}))
        result(run, entry_id, rows[entry_id])
    (run / "summary.json").write_text(json.dumps({
        "passed": ["surface/passing@1", "surface/ours@1"], "failed": ["surface/failing@1", "surface/painted@1"],
        "unread": ["surface/unreadable@1", "surface/missing@1"]}))
    locks.lock(root, "surface/failing@1", "2026-11-23")
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.org")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "library")
    git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(root, "push", "-q", "origin", "main")
    before = git(root, "status", "--porcelain", "--", "entries", "index")

    looked = adopted_recipes.write_recipes(root, None, run)
    assert looked["outcomes"] == {"pass": 1, "fail": 1, "unread": 1}, looked
    assert {row["address"]: row["status"] for row in looked["planned"]} == {
        "surface/passing@1": "in place", "surface/failing@1": "new version", "surface/unreadable@1": "in place"}
    assert sorted(row["address"] for row in looked["refused"]) == [
        "surface/missing@1", "surface/ours@1", "surface/painted@1"], looked["refused"]
    assert git(root, "status", "--porcelain", "--", "entries", "index") == before

    report = adopted_recipes.write_recipes(root, blobs, run, apply=True, save=True, batch=2)
    assert report["in place"] == ["surface/passing@1", "surface/unreadable@1"], report
    assert report["new version"] == ["surface/failing@2"], report
    assert len(report["saved"]) == 2 and all(block["pushed"] for block in report["saved"]), report["saved"]
    assert git(root, "status", "--porcelain", "--", "entries", "index") == before
    assert git(tmp_path / "origin.git", "log", "-1", "--format=%s", "main").startswith("adopted recipes:")

    record, passing = surface_json(blobs, root, "surface/passing@1", tmp_path)
    assert passing["recipe"] == RECIPE and passing["recipe_gap"] is None
    assert passing["likeness"] == {"measures": {"overview": VIEW_PASS, "close": VIEW_PASS}, "pass": True,
                                   "algorithm": ALGORITHM}
    assert {"what": adopted_recipes.RECIPE_WHAT, "by": "score"} in record["provenance"]["made_with"]
    assert record["requires"] == [ALGORITHM] and record["state"] == "draft"
    assert record["files"]["surface.json"]["bytes"] == (tmp_path / "read.json").stat().st_size

    record, failing = surface_json(blobs, root, "surface/failing@2", tmp_path)
    assert record["state"] == "draft" and "locked" not in record
    assert entries.load_address(root, "surface/failing@1")["state"] == "locked"
    assert failing["recipe"] is None and failing["likeness"]["pass"] is False
    assert "close detail 0.495, bound 0.8 to 1.25" in failing["recipe_gap"], failing["recipe_gap"]
    assert "delta_e" not in failing["recipe_gap"] and "overview" not in failing["recipe_gap"]
    assert adopted_recipes.RECIPE_WHAT not in json.dumps(record["provenance"]["made_with"])

    record, unreadable = surface_json(blobs, root, "surface/unreadable@1", tmp_path)
    assert unreadable["recipe"] is None and unreadable["likeness"] is None
    assert "the library holds no albedo.png" in unreadable["recipe_gap"]
    for left in ("ours", "painted"):
        assert "likeness" not in surface_json(blobs, root, f"surface/{left}@1", tmp_path)[1]

    again = adopted_recipes.write_recipes(root, blobs, run, apply=True)
    assert sorted(again["unchanged"]) == ["surface/failing@2", "surface/passing@1", "surface/unreadable@1"], again
    assert again["in place"] == again["new version"] == []

    git(root, "remote", "remove", "origin")
    changed = json.loads((run / "passing@1/likeness.json").read_text())
    changed["recipe"] = {**RECIPE, "settings": {**RECIPE["settings"], "roughness": 0.5}}
    (run / "passing@1/likeness.json").write_text(json.dumps(changed))
    with pytest.raises(handover.HandoverError, match="did not reach origin"):
        adopted_recipes.write_recipes(root, blobs, run, apply=True, save=True)
