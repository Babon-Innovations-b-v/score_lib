"""score-lib take-in: loose made files in as draft entries, their blobs put, verify's rules for them, and fetch's
--file."""
import json
import pathlib

import pytest

from score_lib import entry as entries
from score_lib import take_in, verify
from score_lib.blobs import from_environment
from score_lib.fetch import MissingFile, fetch

CC0 = "CC0 1.0"
MADE = ("made for the first world (the game 2099) by this framework's routes; released with the repository under "
        "MIT, though whether a model made with AI tools can carry copyright at all is an open question")


def loose(folder):
    """A sound and a glTF model (its .bin and one picture) as loose files."""
    folder.mkdir(parents=True)
    (folder / "hum.ogg").write_bytes(b"OggS a hum")
    (folder / "box.bin").write_bytes(b"\0" * 24)
    (folder / "box.png").write_bytes(b"\x89PNG a picture")
    (folder / "box.gltf").write_text(json.dumps({"buffers": [{"uri": "box.bin"}],
                                                 "images": [{"uri": "textures/box.png"}]}))
    return folder


def listing(folder):
    return {"world": "world1", "commit": "0" * 40, "taken_from": "a test's loose files",
            "entries": [
                {"kind": "sound", "id": "hum.recordings", "title": "Hum",
                 "files": {"audio/recordings/hum.ogg": {"path": str(folder / "hum.ogg"), "from": "sound/hum.ogg"}},
                 "made_with": [{"what": "sound/hum.ogg", "source": CC0, "carried": True}]},
                {"kind": "model", "id": "box.gltf", "title": "Box",
                 "files": {"gltf/box.gltf": {"path": str(folder / "box.gltf"), "from": "models/box/box.gltf"},
                           "gltf/box.bin": {"path": str(folder / "box.bin"), "from": "models/box/box.bin"},
                           "gltf/textures/box.png": {"path": str(folder / "box.png"),
                                                     "from": "models/box/textures/box.png"}},
                 "made_with": [{"what": "models/box/box.gltf", "source": MADE}]}]}


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    return root


def test_take_in_writes_drafts_and_blobs_once(library, tmp_path):
    blobs = from_environment()
    listed = listing(loose(tmp_path / "loose"))
    report = take_in.take_in(library, blobs, listed)
    assert report["new"] == ["sound/hum.recordings@1", "model/box.gltf@1"], report
    record = entries.load_address(library, "model/box.gltf@1")
    assert record["state"] == "draft" and record["usd"] is None and record["picture"] is None
    assert record["files"]["gltf/box.bin"]["role"] == "gltf"
    assert record["files"]["gltf/textures/box.png"]["role"] == "texture"
    assert record["provenance"]["made_with"] == [{"what": "models/box/box.gltf", "by": "framework_models"}]
    assert all(blobs.has(described["sha256"]) for described in record["files"].values())
    assert (library / "index/model.json").is_file()
    again = take_in.take_in(library, blobs, listed)
    assert sorted(again["unchanged"]) == ["model/box.gltf@1", "sound/hum.recordings@1"], again


def test_a_changed_draft_is_written_over_and_a_locked_one_gets_a_version(library, tmp_path):
    blobs = from_environment()
    folder = loose(tmp_path / "loose")
    take_in.take_in(library, blobs, listing(folder))
    (folder / "hum.ogg").write_bytes(b"OggS another hum")
    assert take_in.take_in(library, blobs, listing(folder))["changed draft"] == ["sound/hum.recordings@1"]
    record = entries.load_address(library, "sound/hum.recordings@1")
    entries.write(library, dict(record, state="locked", locked="2026-10-20"))
    (folder / "hum.ogg").write_bytes(b"OggS a third hum")
    assert take_in.take_in(library, blobs, listing(folder))["new version"] == ["sound/hum.recordings@2"]


def test_refused_unknown_source_kind_and_ending(library, tmp_path):
    folder = loose(tmp_path / "loose")
    listed = listing(folder)
    listed["entries"][0]["made_with"] = [{"what": "x", "source": "a stranger's sounds"}]
    listed["entries"][1]["kind"] = "surface"
    listed["entries"].append({"kind": "sound", "id": "odd", "title": "Odd", "made_with": [{"what": "x", "source": CC0}],
                              "files": {"audio/odd.txt": {"path": str(folder / "hum.ogg"), "from": "odd.txt"}}})
    report = take_in.take_in(library, from_environment(), listed)
    assert [row["id"] for row in report["refused"]] == ["sound/hum.recordings", "surface/box.gltf", "sound/odd"]
    assert "not in sources.toml" in report["refused"][0]["why"]
    assert not list((library / "entries").rglob("entry.json"))


def test_verify_holds_taken_in_entries_whole(library, tmp_path):
    blobs = from_environment()
    take_in.take_in(library, blobs, listing(loose(tmp_path / "loose")))
    records = entries.all_entries(library)
    assert verify.check_taken_in(blobs, records) == []
    assert verify.check_schemas(library, records) == []
    model = entries.load_address(library, "model/box.gltf@1")
    del model["files"]["gltf/textures/box.png"]
    model["files"]["gltf/box.bin"]["role"] = "texture"
    faults = [message for _, message in verify.check_taken_in(blobs, [model])]
    assert any("files_from names gltf/textures/box.png" in fault for fault in faults), faults
    assert any("gltf/box.bin: role texture" in fault for fault in faults), faults
    assert any("names textures/box.png, which the entry does not hold" in fault for fault in faults), faults


def test_fetch_one_file(library, tmp_path):
    blobs = from_environment()
    take_in.take_in(library, blobs, listing(loose(tmp_path / "loose")))
    into = tmp_path / "cache"
    fetch(library, blobs, ["model/box.gltf@1"], into, names=["gltf/box.bin"])
    held = sorted(path.relative_to(into).as_posix() for path in into.rglob("*") if path.is_file())
    assert held == ["score_lib/model/box.gltf/1/gltf/box.bin"], held
    with pytest.raises(MissingFile):
        fetch(library, blobs, ["model/box.gltf@1"], into, names=["gltf/nothing.bin"])
    assert pathlib.Path(into / "score_lib/model/box.gltf/1/gltf/box.bin").read_bytes() == b"\0" * 24
