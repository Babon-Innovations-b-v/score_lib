"""score-lib game-attach: a game engine's converted file kept beside its entry, outside the digest, and fetched back
at its path under the engine's piece cache."""
import json
import shutil

from conftest import LIBRARY
from score_lib import entry as entries
from score_lib import game, take_in, verify
from score_lib.blobs import from_environment
from score_lib.cli import main
from score_lib.fetch import fetch

CC0 = "CC0 1.0"


def test_a_kept_unity_file_stays_beside_its_entry_and_outside_its_digest(tmp_path):
    """Break: entry.UNSTAMPED: drop "unity" (keeping a bundle moves the locked lamp's digest, so a piece list naming it
    goes stale); game.checked_path: let a path outside pieces/ through (the ../ row is kept and fetched outside the
    cache); fetch: the unity block left unlaid (the bundle is not at its cache path) or laid with unity "only" its own
    files too (the lamp's glb is fetched with it); game.attached: a row put again when held (unchanged is empty)."""
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    shutil.copyfile(LIBRARY / "LICENSE", root / "LICENSE")
    (tmp_path / "lamp.glb").write_bytes(b"glTF a lamp")
    blobs = from_environment()
    report = take_in.take_in(root, blobs, {"world": "world1", "commit": "0" * 40, "taken_from": "a test", "entries": [
        {"kind": "model", "id": "lamp", "title": "lamp", "made_with": [{"what": "lamp", "source": CC0, "carried": True}],
         "files": {"lamp.glb": {"path": str(tmp_path / "lamp.glb"), "from": "lamp.glb"}}}]})
    assert not report["refused"], report
    before = entries.digest(entries.load_address(root, "model/lamp@1"))
    bundle = tmp_path / "lamp.bundle"
    bundle.write_bytes(b"UnityFS a converted lamp")
    path = f"pieces/model/lamp@1/{before}/2-6000.3.28f1-StandaloneWindows64.bundle"
    kept = game.attached(root, blobs, [{"address": "model/lamp@1", "path": path, "file": str(bundle)},
                                       {"address": "model/lamp@1", "path": "pieces/../x.bundle", "file": str(bundle)},
                                       {"address": "model/none@1", "path": "pieces/none.bundle", "file": str(bundle)}])
    assert kept["kept"] == [path] and [row["path"] for row in kept["refused"]] == ["pieces/../x.bundle",
                                                                                   "pieces/none.bundle"], kept
    record = entries.load_address(root, "model/lamp@1")
    assert record["unity"] == {path: {"sha256": game.file_sha256(bundle), "bytes": bundle.stat().st_size}}
    assert entries.digest(record) == before, "keeping a Unity file moved the entry's digest"
    assert verify.verify(root, blobs) == []
    assert game.attached(root, blobs, [{"address": "model/lamp@1", "path": path, "file": str(bundle)}])["unchanged"] == [path]
    into = tmp_path / "Places"
    fetch(root, blobs, ["model/lamp@1"], into, unity="only")
    assert (into / path).read_bytes() == bundle.read_bytes() and not (into / "score_lib").exists()
    listing = tmp_path / "attach.json"
    listing.write_text(json.dumps([{"address": "model/lamp@1", "path": path, "file": str(bundle)}]))
    try:
        main(["--root", str(root), "game-attach", "--list", str(listing)])
    except SystemExit as stopped:
        assert not stopped.code, stopped
    try:
        main(["--root", str(root), "fetch", "model/lamp@1", "--into", str(tmp_path / "again"), "--unity"])
    except SystemExit as stopped:
        assert not stopped.code, stopped
    assert (tmp_path / "again" / path).is_file() and (tmp_path / "again/score_lib/model/lamp/1/lamp.glb").is_file()
