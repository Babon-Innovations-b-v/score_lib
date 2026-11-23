"""score-lib concept on the fixture world: a pick locks the picked draw as concept/<place> with its prompt, the marks
and notes that led to it and the draw history; a second run changes nothing; an unlock withdraws it; a later pick is
the next version; only the entry and the index are committed and pushed."""
import json
import os
import pathlib

from conftest import git, sha256
from handover_test import with_remote

from score_lib import entry as entries
from score_lib import index, schema
from score_lib.blobs import from_environment
from score_lib.cli import main


def drawn(world, at, png, base=None, marks=(), note=""):
    """A draw of the room as score's concept review keeps it; its record."""
    objects = pathlib.Path(os.environ["SCORE_STORE"])
    sha = sha256(png)
    (objects / "blobs" / sha).write_bytes(png)
    record = {"place": "room", "sha": sha, "name": "room-concept.png", "at": at, "by": "joey", "euros": 0.15,
              "yes": f"concept-room-{at}", "base": base, "marks": list(marks), "note": note,
              "wording": f"one concept painting of the room ({note or 'first'})"}
    folder = objects / "concepts/room/draws"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{at}.json").write_text(json.dumps(record))
    return record


def picked(sha, at):
    folder = pathlib.Path(os.environ["SCORE_STORE"]) / "concepts/room/picks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{at}.json").write_text(json.dumps({"place": "room", "sha": sha, "by": "joey", "at": at}))


def handed(world, capsys):
    main(["--root", str(world.root), "concept", "--world", "world1", "--place", "room", "--commit", world.commit,
          "--store", os.environ["SCORE_STORE"]])
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_pick_locks_the_concept_an_unlock_withdraws_it_and_a_new_pick_is_the_next_version(world, capsys):
    origin = with_remote(world)
    (world.root / "loose.txt").write_text("another agent's work, not committed")
    first = drawn(world, "2026-10-20T10:00:00", b"first picture")
    mark = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.2, "note": "a wider door"}
    second = drawn(world, "2026-10-20T10:05:00", b"second picture", base=first["sha"], marks=[mark], note="warmer")
    drawn(world, "2026-10-20T10:07:00", b"third picture, not picked")
    assert handed(world, capsys) == {"place": "room", "did": "nothing", "address": None}

    picked(second["sha"], "2026-10-20T10:06:00")
    report = handed(world, capsys)
    assert report["did"] == "locked" and report["address"] == "concept/room@1" and report["saved"]["pushed"]
    record = entries.load_address(world.root, "concept/room@1")
    assert record["state"] == "locked" and schema.faults("entry", record) == []
    assert record["files"]["concept.png"]["sha256"] == second["sha"] and record["picture"]["file"] == "concept.png"
    assert from_environment().has(second["sha"])
    told = json.loads(from_environment().path(record["files"]["concept.json"]["sha256"]).read_text())
    assert [draw["sha"] for draw in told["led_to_it"]] == [first["sha"], second["sha"]]
    assert told["led_to_it"][1]["marks"] == [mark] and told["led_to_it"][1]["note"] == "warmer"
    assert [draw["sha"] for draw in told["history"]] == [first["sha"], second["sha"]]
    assert record["provenance"]["by"] == told["picked"]["by"] == "Babon Innovations B.V." and "joey" not in json.dumps(
        [record, told])  # a person's name never goes out in a published record
    assert index.stale(world.root) == []
    shown = git(world.root, "show", "--stat", "--format=", "HEAD")
    assert "entries/concept/room/1/entry.json" in shown and "loose.txt" not in shown
    assert git(origin, "log", "-1", "--format=%s", "main").strip().startswith("concept: locked concept/room@1")

    assert handed(world, capsys)["did"] == "standing"

    picked(None, "2026-10-20T10:08:00")
    assert handed(world, capsys) == {"place": "room", "did": "withdrawn", "address": "concept/room@1",
                                     "saved": {"committed": git(world.root, "rev-parse", "HEAD").strip(),
                                               "pushed": True, "why": ""}}
    assert entries.load_address(world.root, "concept/room@1")["state"] == "withdrawn"

    picked(first["sha"], "2026-10-20T10:09:00")
    report = handed(world, capsys)
    assert report["did"] == "locked" and report["address"] == "concept/room@2"
    assert entries.load_address(world.root, "concept/room@2")["files"]["concept.png"]["sha256"] == first["sha"]


def test_a_second_pick_while_locked_withdraws_the_older_version(world, capsys):
    """The owner picking the same draw twice (cockpit, 2026-10-21) leaves one locked concept, the newest."""
    with_remote(world)
    draw = drawn(world, "2026-10-20T10:00:00", b"the picture")
    picked(draw["sha"], "2026-10-20T10:01:00")
    assert handed(world, capsys)["address"] == "concept/room@1"
    picked(draw["sha"], "2026-10-20T10:03:00")
    report = handed(world, capsys)
    assert report["did"] == "locked" and report["address"] == "concept/room@2"
    states = {record["address"]: record["state"] for record in entries.versions(world.root, "concept", "room")}
    assert states == {"concept/room@1": "withdrawn", "concept/room@2": "locked"}
    assert "entries/concept/room/1/entry.json" in git(world.root, "show", "--stat", "--format=", "HEAD")
