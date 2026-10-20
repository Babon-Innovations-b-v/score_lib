"""The handover on the small fixture world: drafts and a report covering every accepted output, the same bytes on a
second run, a locked change as the next version, two handovers side by side, refusals as data, the words when there
is no store, the copies and quality hooks, and the held query."""
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest
from conftest import World, git

from score_lib import entry as entries
from score_lib import handover as handing
from score_lib import locks, schema
from score_lib.cli import main
from score_lib.held import held

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"
EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "schemas/handover.example.json"


def with_remote(world):
    """Make the library root a git repository with a bare origin; the origin's folder."""
    origin = world.base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(world.root, "init", "-q", "-b", "main")
    git(world.root, "config", "user.name", "test")
    git(world.root, "config", "user.email", "test@example.org")
    git(world.root, "add", ".")
    git(world.root, "commit", "-q", "-m", "library")
    git(world.root, "remote", "add", "origin", str(origin))
    git(world.root, "push", "-q", "origin", "main")
    return origin


def arguments(world, out, place="room", commit=None):
    """The handover command's arguments for the fixture world."""
    found = ["--root", str(world.root), "handover", "--world", "world1", "--seed", "3",
             "--commit", commit or world.commit, "--store", os.environ["SCORE_STORE"], "--repo", str(world.repo),
             "--out", str(out)]
    return found + (["--place", place] if place else [])


def handed(world, out, place="room", commit=None):
    """Run the handover through the command line; the report it wrote."""
    main(arguments(world, out, place, commit))
    return json.loads((pathlib.Path(out) / "handover.json").read_text())


def test_a_first_handover_writes_drafts_and_covers_every_output(world, tmp_path):
    origin = with_remote(world)
    report = handed(world, tmp_path / "out")
    assert schema.faults("handover", report) == []
    assert {row["node"] for row in report["covered"]} == {"assembly@room", "complete@room", "effects-layer@room",
                                                          "sound@room"}
    assert all(row["entries"] for row in report["covered"]), report["covered"]
    addresses = {row["address"] for row in report["entries"]}
    assert {"place/room@1", "model/crate_1@1", "layer/base.room@1", "sound/hum@1"} <= addresses
    assert not any(address.startswith("algorithm/") for address in addresses)
    assert {row["status"] for row in report["entries"]} == {"new"}
    assert {row["state"] for row in report["entries"]} == {"draft"}
    model = next(row for row in report["entries"] if row["address"] == "model/crate_1@1")
    assert model["node"] == "assembly@room" and model["output"] == world.assembly
    assert model["copy"]["role"] == "web3d" and model["copy_why"] == ""
    surface = next(row for row in report["entries"] if row["kind"] == "surface")
    assert surface["node"] is None and "data/library/materials.json" in surface["source"]
    assert report["saved"]["committed"] == git(world.root, "rev-parse", "HEAD") and report["saved"]["pushed"]
    assert git(origin, "rev-parse", "main") == report["saved"]["committed"]
    assert git(world.root, "log", "-1", "--format=%s") == f"handover: world1@room at score {world.commit[:12]}"
    assert report["command"][:2] == ["score-lib", "--root"]


def test_a_second_handover_is_unchanged_and_writes_the_same_bytes(world, tmp_path):
    with_remote(world)
    handed(world, tmp_path / "first")
    report = handed(world, tmp_path / "second")
    assert {row["status"] for row in report["entries"]} == {"unchanged"}
    handed(world, tmp_path / "third")
    second = (tmp_path / "second/handover.json").read_bytes().replace(b"/second", b"/third")
    assert second == (tmp_path / "third/handover.json").read_bytes()


def test_a_newer_score_commit_with_the_same_content_leaves_entries_unchanged(world, tmp_path):
    handed(world, tmp_path / "first")
    (world.repo / "README.md").write_text("a change elsewhere\n")
    git(world.repo, "add", ".")
    git(world.repo, "-c", "user.name=test", "-c", "user.email=test@example.org", "commit", "-q", "-m", "elsewhere")
    report = handed(world, tmp_path / "second", commit=git(world.repo, "rev-parse", "HEAD"))
    assert {row["status"] for row in report["entries"]} == {"unchanged"}
    assert 'commit = "%s"' % world.commit in (world.root / "worlds/world1.toml").read_text()


def test_the_route_given_is_stamped_into_each_made_entrys_provenance(world, tmp_path):
    out = tmp_path / "out"
    main(arguments(world, out) + ["--route", "r1"])
    rows = json.loads((out / "handover.json").read_text())["entries"]
    model = entries.load_address(world.root, "model/crate_1@1")
    assert model["provenance"]["route"] == "r1" and model["provenance"]["accept"]
    made = [entries.load_address(world.root, row["address"]) for row in rows if row["node"]]
    assert made and all(record["provenance"]["route"] == "r1" for record in made)


def test_a_locked_entry_with_changed_content_becomes_the_next_draft(world, tmp_path):
    handed(world, tmp_path / "first")
    locks.lock_many(world.root, ["model/crate_1@1"])
    world.publish_place("2026-01-02T00:00:00+00:00", crate_colour=(10, 200, 10))
    report = handed(world, tmp_path / "second")
    rows = {row["address"]: row for row in report["entries"]}
    assert rows["model/crate_1@2"]["status"] == "new version" and rows["model/crate_1@2"]["state"] == "draft"
    assert "model/crate_1@1" not in rows
    assert entries.load_address(world.root, "model/crate_1@1")["state"] == "locked"


def test_the_world_node_hands_the_world_kinds(world, tmp_path):
    report = handed(world, tmp_path / "out", place=None)
    assert report["place"] is None and report["covered"] == []
    assert {row["kind"] for row in report["entries"]} == {"algorithm"}
    assert {row["copy_why"] for row in report["entries"]} == {"a algorithm entry is not shown in 3D"}


def run_in_process(world, out, place):
    """Start the handover command as its own process."""
    code = "import sys; from score_lib.cli import main; main(sys.argv[1:])"
    environment = dict(os.environ, PYTHONPATH=str(SOURCE))
    return subprocess.Popen([sys.executable, "-c", code, *arguments(world, out, place)], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_two_handovers_side_by_side_both_land(world, tmp_path):
    with_remote(world)
    running = [run_in_process(world, tmp_path / "room", "room"), run_in_process(world, tmp_path / "world", None)]
    for process in running:
        _, errors = process.communicate(timeout=600)
        assert process.returncode == 0, errors
    room = json.loads((tmp_path / "room/handover.json").read_text())
    whole = json.loads((tmp_path / "world/handover.json").read_text())
    assert room["saved"]["committed"] and whole["saved"]["committed"]
    assert git(world.root, "status", "--porcelain", "--", "entries", "index", "worlds") == ""
    assert entries.load_address(world.root, "place/room@1") and entries.load_address(world.root, "algorithm/demo@1")


def test_a_held_lock_past_its_wait_stops_with_words(world, tmp_path, monkeypatch):
    monkeypatch.setenv("SCORE_LIB_LOCK_WAIT", "1")
    with handing.locked(world.root, 1):
        with pytest.raises(SystemExit, match="another handover held"):
            main(arguments(world, tmp_path / "out"))
    assert not (tmp_path / "out/handover.json").exists()


def test_a_refused_source_is_data_not_an_error(tmp_path):
    rules = [{"match": "models/", "licence": "a licence nobody wrote down", "source": "x", "author": "y"}]
    world = World(tmp_path, rules)
    report = handed(world, tmp_path / "out")
    refused = {row["id"]: row for row in report["refused"] if row["kind"] == "model"}
    assert "a licence nobody wrote down" in refused["crate_1"]["reason"]
    assert refused["crate_1"]["node"] == "assembly@room"
    assert schema.faults("handover", report) == []


def test_no_store_stops_with_words(world, tmp_path, monkeypatch):
    found = arguments(world, tmp_path / "out")
    found[found.index("--store") + 1] = str(tmp_path / "nowhere")
    with pytest.raises(SystemExit, match="no SCORE store at"):
        main(found)


def test_no_blob_store_stops_with_words(world, tmp_path, monkeypatch):
    monkeypatch.delenv("SCORE_LIB_BLOBS_DIR")
    with pytest.raises(SystemExit, match="SCORE_LIB_BLOBS_DIR"):
        main(arguments(world, tmp_path / "out"))


def test_the_copies_and_quality_hooks_are_called_when_the_library_has_them(world, tmp_path, monkeypatch):
    copies = types.ModuleType("score_lib.copies")
    copies.web_copy = lambda root, blobs, record: {"sha256": "a" * 64, "role": "gltf"}
    quality = types.ModuleType("score_lib.quality")
    quality.score_of = lambda root, blobs, record: {"score": 0.5, "measures": {"faces": 6}}
    monkeypatch.setitem(sys.modules, "score_lib.copies", copies)
    monkeypatch.setitem(sys.modules, "score_lib.quality", quality)
    report = handed(world, tmp_path / "out")
    model = next(row for row in report["entries"] if row["kind"] == "model")
    assert model["copy"] == {"sha256": "a" * 64, "role": "gltf"} and model["copy_why"] == ""
    assert model["quality"] == {"score": 0.5, "measures": {"faces": 6}}
    sound = next(row for row in report["entries"] if row["kind"] == "sound")
    assert sound["copy"] is None and sound["quality"]["score"] == 0.5


def test_held_lists_what_a_place_and_the_world_hold(world, tmp_path, capsys):
    handed(world, tmp_path / "room")
    handed(world, tmp_path / "world", place=None)
    room = {record["address"] for record in held(world.root, "world1", "room")}
    assert {"place/room@1", "model/crate_1@1"} <= room and not any(each.startswith("algorithm/") for each in room)
    assert {record["kind"] for record in held(world.root, "world1", None)} == {"algorithm"}
    assert held(world.root, "another_world", "room") == []
    capsys.readouterr()
    main(["--root", str(world.root), "held", "--world", "world1", "--place", "room"])
    rows = json.loads(capsys.readouterr().out)
    assert {row["address"] for row in rows} == room


def test_a_new_world_gets_its_toml(world, tmp_path):
    handed_report = handed(world, tmp_path / "out")
    assert handed_report["world"] == "world1"
    handing.record_world(world.root, "world2", "garage", 11, "abc")
    written = (world.root / "worlds/world2.toml").read_text()
    assert 'places = ["garage"]' in written and "seed = 11" in written and 'commit = "abc"' in written
    handing.record_world(world.root, "world2", "airlock", 12, "def")
    written = (world.root / "worlds/world2.toml").read_text()
    assert 'places = ["airlock", "garage"]' in written and "seed = 12" in written and 'commit = "abc"' in written


def test_the_example_report_matches_the_schema():
    assert schema.faults("handover", json.loads(EXAMPLE.read_text())) == []
