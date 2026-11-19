"""score-lib take-in: loose made files in as draft entries, their blobs put, verify's rules for them, and fetch's
--file."""
import json
import pathlib
import subprocess

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


def test_a_kit_piece_a_surface_and_motion_clips_take_their_roles_and_tags(library, tmp_path):
    blobs = from_environment()
    folder = loose(tmp_path / "loose")
    (folder / "piece.usda").write_text("#usda 1.0\n")
    (folder / "walking.npz").write_bytes(b"PK a clip")
    listed = listing(folder)
    listed["entries"] = [
        {"kind": "kit", "id": "door_frame_1", "title": "Door frame", "tags": {"kit": ["piece"]},
         "files": {"piece.usda": {"path": str(folder / "piece.usda"), "from": "model/door_frame_1@1/model.usdc"}},
         "made_with": [{"what": "model/door_frame_1@1", "source": MADE}]},
        {"kind": "character", "id": "clips", "title": "Clips",
         "files": {"motions/walking.npz": {"path": str(folder / "walking.npz"), "from": "motions/walking.npz"}},
         "made_with": [{"what": "motions/walking.npz", "source": MADE}]},
        {"kind": "surface", "id": "concrete_maps", "title": "Concrete maps",
         "files": {"material.usda": {"path": str(folder / "piece.usda"), "from": "material.usda"},
                   "colour.jpg": {"path": str(folder / "walking.npz"), "from": "Concrete_Color.jpg"}},
         "made_with": [{"what": "the maps", "source": MADE}]}]
    assert take_in.take_in(library, blobs, listed)["new"] == ["kit/door_frame_1@1", "character/clips@1",
                                                              "surface/concrete_maps@1"]
    surface = entries.load_address(library, "surface/concrete_maps@1")
    assert {name: about["role"] for name, about in surface["files"].items()} == {"colour.jpg": "texture",
                                                                                "material.usda": "usd"}
    kit = entries.load_address(library, "kit/door_frame_1@1")
    clips = entries.load_address(library, "character/clips@1")
    assert kit["files"]["piece.usda"]["role"] == "usd" and kit["tags"] == {"kit": ["piece"]}
    assert clips["files"]["motions/walking.npz"]["role"] == "motion"
    records = entries.all_entries(library)
    assert verify.check_taken_in(blobs, records) == [] and verify.check_schemas(library, records) == []


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
    listed["entries"][1]["kind"] = "effect"
    listed["entries"].append({"kind": "sound", "id": "odd", "title": "Odd", "made_with": [{"what": "x", "source": CC0}],
                              "files": {"audio/odd.txt": {"path": str(folder / "hum.ogg"), "from": "odd.txt"}}})
    report = take_in.take_in(library, from_environment(), listed)
    assert [row["id"] for row in report["refused"]] == ["sound/hum.recordings", "effect/box.gltf", "sound/odd"]
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
    # score_lib #1: a file name written by hand into entry.json (no take-in) never lands outside the entry's folder
    path = entries.folder_of(library, "model", "box.gltf", 1) / "entry.json"
    record = json.loads(path.read_text())
    held = record["files"]["gltf/box.bin"]
    for name in ("../../../../../../escaped.bin", "/tmp/escaped.bin", "C:/escaped.bin", "gltf/./box.bin", "a\\b.bin"):
        path.write_text(json.dumps({**record, "files": {name: held}}))
        for link in (False, True):
            with pytest.raises(ValueError):
                fetch(library, blobs, ["model/box.gltf@1"], tmp_path / "escape", link=link)
    assert not (tmp_path / "escaped.bin").exists() and not pathlib.Path("/tmp/escaped.bin").exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "linked/score_lib/model/box.gltf/1").mkdir(parents=True)
    (tmp_path / "linked/score_lib/model/box.gltf/1/gltf").symlink_to(outside)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        fetch(library, blobs, ["model/box.gltf@1"], tmp_path / "linked")
    assert not any(outside.iterdir())
    path.write_text(json.dumps({**record, "files": {"../../../../../../escaped.bin": held}}))
    assert any("escaped.bin" in fault for _, fault in verify.check_schemas(library, [entries.load(path)]))


def a_take(folder):
    """One Pixal3D take as work/pixal holds it: raw, finished and final models, its record, its camera folder."""
    (folder / "cup-s1.svviews").mkdir(parents=True, exist_ok=True)
    for name, data in (("cup-s1.glb", b"glTF raw"), ("cup-s1-12000.glb", b"glTF thin"), ("cup-s1-final.glb", b"glTF up"),
                       ("cup-s1.json", b"{}"), ("cup-s1.svviews/input.png", b"\x89PNG close-up"),
                       ("cup-s1.svviews/transforms.json", b"{\"frames\": []}")):
        (folder / name).write_bytes(data)
    files = {path.relative_to(folder).as_posix(): {"path": str(path), "from": f"work/pixal/{path.relative_to(folder)}"}
             for path in sorted(folder.rglob("*")) if path.is_file()}
    return {"world": "world1", "commit": "0" * 40, "taken_from": "a test's work/pixal",
            "entries": [{"kind": "take", "id": "cup_s1", "title": "cup-s1", "files": files,
                         "made_with": [{"what": "cup-s1", "source": "pixal3d"}]}]}


def git(folder, *words):
    subprocess.run(["git", "-C", str(folder), *words], check=True, capture_output=True)


def test_a_take_keeps_its_camera_folder_and_is_saved_to_origin(library, tmp_path):
    """Break: take_in.role_of: 'CAMERA_ROLES if path.parent.suffix == ".svviews" else TAKE_ROLES' -> 'TAKE_ROLES'
    (the close-up is refused), or taken_in_saved's 'handover.saved(...)' -> None (the take reaches no remote)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    for words in (["init", "-q"], ["remote", "add", "origin", str(remote)], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "start"]):
        git(library, *words)
    blobs = from_environment()
    report = take_in.taken_in_saved(library, blobs, a_take(tmp_path / "pixal"))
    assert report["new"] == ["take/cup_s1@1"] and report["saved"]["pushed"], report
    record = entries.load_address(library, "take/cup_s1@1")
    assert record["files"]["cup-s1.svviews/input.png"]["role"] == "closeup"
    assert record["files"]["cup-s1.svviews/transforms.json"]["role"] == "record"
    assert record["files"]["cup-s1-final.glb"]["role"] == "gltf"
    records = entries.all_entries(library)
    assert verify.check_taken_in(blobs, records) == [] and verify.check_schemas(library, records) == []
    pushed = subprocess.run(["git", "-C", str(remote), "show", "--stat=200", "--format=", "HEAD"],
                            capture_output=True, text=True).stdout
    assert "entries/take/cup_s1/1/entry.json" in pushed and "index/take.json" in pushed, pushed
    into = tmp_path / "back"
    fetch(library, blobs, ["take/cup_s1@1"], into, names=["cup-s1.svviews/input.png"])
    assert (into / "score_lib/take/cup_s1/1/cup-s1.svviews/input.png").read_bytes() == b"\x89PNG close-up"
    assert take_in.taken_in_saved(library, blobs, a_take(tmp_path / "pixal"))["saved"] is None
