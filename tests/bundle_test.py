"""score-lib bundle and take-bundle: a contributor's entries packed with their bytes, then checked, put in every blob
store and traced on merge.

Red when: a packed bundle no longer takes in whole (its blobs in both stores, each entry's contribution block
completed); a bundle opens outside the review environment; an absolute path, a '..', a link, a member over the cap, a
zip bomb, a file ending its kind does not list, an executable under a picture's name, a blob whose bytes do not match
its record or a record that is not the pull request's is let through.
"""
import io
import json
import struct
import tarfile

import numpy as np
import pytest
import soundfile

from score_lib import bundle, take_in
from score_lib import entry as entries
from score_lib.blobs import key_of
from score_lib.blobs.local import LocalBlobStore

COMMIT = "a" * 40


def glb():
    """A one-triangle binary glTF."""
    points = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 36}],
                           "bufferViews": [{"buffer": 0, "byteLength": 36}],
                           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]}).encode()
    document += b" " * (-len(document) % 4)
    body = struct.pack("<II", len(document), 0x4E4F534A) + document + struct.pack("<II", 36, 0x004E4942) + points
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def contributed(root, folder, handle="ada-lovelace"):
    """Take in a sound and a model as one contributor's; their addresses."""
    folder.mkdir(parents=True)
    soundfile.write(folder / "hum.ogg", np.sin(np.linspace(0, 400, 8000)) * 0.5, 8000, format="OGG")
    (folder / "box.glb").write_bytes(glb())
    listing = {"world": "world1", "commit": "0" * 40, "taken_from": "a contributor's own files",
               "contributor": {"handle": handle, "claim": "my own work"},
               "entries": [{"kind": "sound", "id": "hum", "title": "Hum",
                            "files": {"audio/hum.ogg": {"path": str(folder / "hum.ogg"), "from": "hum.ogg"}},
                            "made_with": [{"what": "hum.ogg", "source": "CC0 1.0", "carried": True}]},
                           {"kind": "model", "id": "box", "title": "Box",
                            "files": {"gltf/box.glb": {"path": str(folder / "box.glb"), "from": "box.glb"}},
                            "made_with": [{"what": "box.glb", "source": "CC0 1.0", "carried": True}]}]}
    report = take_in.take_in(root, LocalBlobStore(folder.parent / "contributor_blobs"), listing)
    assert not report["refused"], report
    return report["new"]


def tar_of(path, members):
    """A gzip tar of (name, bytes or None for a link) members."""
    with tarfile.open(path, "w:gz") as packed:
        for name, data in members:
            member = tarfile.TarInfo(name)
            if data is None:
                member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
            else:
                member.size = len(data)
            packed.addfile(member, io.BytesIO(data or b""))
    return path


def test_a_packed_bundle_takes_in_whole_and_every_bad_bundle_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    addresses = contributed(root, tmp_path / "loose")
    packed = bundle.pack(root, LocalBlobStore(tmp_path / "contributor_blobs"), addresses, tmp_path / "mine.tgz")
    assert packed["entries"] == 2 and packed["fits"]["pull request attachment"]

    stores = [LocalBlobStore(tmp_path / "store"), LocalBlobStore(tmp_path / "hub")]
    monkeypatch.delenv(bundle.REVIEW_SETTING, raising=False)
    with pytest.raises(bundle.Refused, match="review environment"):
        bundle.take(root, stores, [str(tmp_path / "mine.tgz")], 7, "ada-lovelace", COMMIT)
    monkeypatch.setenv(bundle.REVIEW_SETTING, "a test")
    with pytest.raises(bundle.Refused, match="bundle is"):
        bundle.take(root, stores, [str(tmp_path / "mine.tgz")], 7, "someone-else", COMMIT)

    report = bundle.take(root, stores, [str(tmp_path / "mine.tgz")], 7, "ada-lovelace", COMMIT, "2026-11-19")
    assert sorted(report["entries"]) == sorted(addresses) and report["blobs"] == 2
    record = entries.load_address(root, "model/box@1")
    assert record["contribution"] == {"handle": "ada-lovelace", "claim": "my own work", "pr": 7, "signed_off": COMMIT,
                                      "date": "2026-11-19", "bundle_sha256": packed["sha256"]}
    for store in stores:
        assert all((store.folder / key_of(about["sha256"])).is_file() for about in record["files"].values())

    sound = entries.load_address(root, "sound/hum@1")
    sha = sound["files"]["audio/hum.ogg"]["sha256"]
    hum = (tmp_path / "loose" / "hum.ogg").read_bytes()
    good = [("bundle.json", json.dumps({"schema": bundle.SCHEMA, "contributor": "ada-lovelace",
                                        "entries": {"sound/hum@1": ""}}).encode()),
            ("entries/sound/hum/1/entry.json", entries.dumps(sound).encode()), (f"blobs/{sha}", hum)]
    bad = {"absolute": [*good, ("/etc/cron.d/x", b"x")], "parent": [*good, ("entries/../../x", b"x")],
           "link": [*good, ("blobs/" + "b" * 64, None)], "unknown name": [*good, ("payload.sh", b"x")],
           "other bytes": [*good[:2], (f"blobs/{sha}", hum[:-1] + b"x")],
           "extra blob": [*good, ("blobs/" + "c" * 64, b"x")]}
    for why, members in bad.items():
        with pytest.raises(bundle.Refused):
            opened = bundle.unpacked(tar_of(tmp_path / f"{why}.tgz", members), tmp_path / why)
            bundle.checked(root, opened, "ada-lovelace")
    with pytest.raises(bundle.Refused, match="packed size"):
        bundle.unpacked(tar_of(tmp_path / "bomb.tgz", [("blobs/" + "d" * 64, b"\0" * 10_000_000)]),
                        tmp_path / "bomb")
    monkeypatch.setattr(bundle, "MEMBER_BYTES", 4)
    with pytest.raises(bundle.Refused, match="over 4"):
        bundle.unpacked(tar_of(tmp_path / "big.tgz", good), tmp_path / "big")
    with pytest.raises(bundle.Refused, match="holds no .blend"):
        bundle.allowed_ending("model", "gltf/box.blend")
    with pytest.raises(bundle.Refused, match="holds no .py"):
        bundle.allowed_ending("model", "gltf/run.py")
    bundle.allowed_ending("algorithm", "code/run.py")
    with pytest.raises(bundle.Refused, match="executable"):
        bundle.first_bytes_fit("textures/a.png", b"\x7fELF\x02\x01")
    with pytest.raises(bundle.Refused, match="not a .png"):
        bundle.first_bytes_fit("textures/a.png", b"GIF89a")
    for name in ("/abs.png", "a/../b.png", "a\\b.png", "a//b.png"):
        with pytest.raises(bundle.Refused):
            bundle.safe_name(name)
