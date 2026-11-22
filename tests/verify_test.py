"""verify: a clean export passes, and each failure of spec section 8 is triggered once."""
import json
import os

import pytest

from score_lib import entry as entries
from score_lib import index, locks
from score_lib.blobs.local import LocalBlobStore
from score_lib import schema
from score_lib.admission import Sources
from score_lib.verify import lost_notes, verify


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


def test_a_withdrawn_draft_is_frozen_and_passes(world):
    world.export()
    locks.withdraw(world.root, "kit/room@1")
    index.rebuild(world.root)
    assert entries.load_address(world.root, "kit/room@1")["state"] == "withdrawn"
    assert [row["address"] for row in locks.read(world.root)] == ["kit/room@1"]
    assert messages(world) == []


def lock_and_edit(world, change):
    """Lock kit/room@1 with what it requires, change its entry.json, rebuild the index."""
    locks.lock_many(world.root, ["kit/room@1"] + entries.load_address(world.root, "kit/room@1")["requires"])
    path = entries.folder_of(world.root, "kit", "room", 1) / "entry.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(entries.dumps(record))
    index.rebuild(world.root)
    return record


def test_3_lock_undone_by_setting_draft(world):
    world.export()
    lock_and_edit(world, lambda record: (record.pop("locked"), record.update(state="draft", title="Another title")))
    assert any("kit/room@1: in index/locks.json but its state is draft" in each for each in messages(world))


def test_3_second_lock_row(world):
    world.export()
    record = lock_and_edit(world, lambda record: record.update(title="Another title"))
    locks.append(world.root, {"address": "kit/room@1", "digest": entries.digest(record), "date": "2026-10-19"})
    index.rebuild(world.root)
    assert any("kit/room@1: 2 rows in index/locks.json" in each for each in messages(world))


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


def rewrite_entry(world, kind, entry_id, change):
    """Change one entry.json in place and rebuild the index."""
    path = entries.folder_of(world.root, kind, entry_id, 1) / "entry.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(entries.dumps(record))
    index.rebuild(world.root)


def test_5_entry_names_a_licence_of_its_own(world):
    world.export()
    rewrite_entry(world, "sound", "hum", lambda record: record.update(licence={"spdx": "CC-BY-4.0"}))
    found = messages(world)
    assert any("sound/hum@1: names a licence of its own" in each for each in found)
    assert any("sound/hum@1: schema:" in each for each in found)


def test_5_source_not_mit(world):
    world.export()
    rewrite_entry(world, "sound", "hum", lambda record: record["provenance"]["made_with"].append(
        {"what": "the fonts", "by": "ofl_fonts", "carried": True}))
    assert any("sound/hum@1: not MIT: the fonts: carries fonts" in each for each in messages(world))


def test_5_source_row_no_longer_admitted(world):
    world.export()
    rows = [dict(row, output=False, copy=False) for row in Sources.load().rows]
    record = entries.load_address(world.root, "sound/hum@1")
    assert any("made by CC0 recordings" in each for each in Sources(rows).check(record["provenance"]["made_with"]))


def test_5_algorithm_carries_an_outside_file(world):
    world.export()
    kept = entries.load_address(world.root, "algorithm/demo@1")["files"]["presets/world1/data/demo/preset.json"]
    rewrite_entry(world, "algorithm", "demo",
                  lambda record: record["files"].update({"presets/world1/data/demo/outside.json": kept}))
    assert any("algorithm/demo@1: not MIT: carries presets/world1/data/demo/outside.json" in each
               for each in messages(world))


def test_5_licence_file_not_mit(world):
    world.export()
    (world.root / "LICENSE").write_text("All rights reserved\n")
    assert any("LICENSE: the library's one licence file" in each for each in messages(world))


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


LICENSED = {"id": "outside_set", "name": "an outside set under CC BY 4.0", "output": True, "copy": True,
            "licence": "CC-BY-4.0", "why": "CC BY 4.0: copied in with its credit, never under MIT"}
BLOCK = {"id": "CC-BY-4.0", "source": "outside_set", "credit": "a part by its maker, CC BY 4.0",
         "url": "https://creativecommons.org/licenses/by/4.0/"}
CARRIED = [{"what": "its files", "by": "outside_set", "carried": True}]


@pytest.mark.parametrize("made_with, licence, fault", [
    (CARRIED, BLOCK, None),
    ([{"what": "a recipe fitted to it", "by": "outside_set"}], None, None),
    (CARRIED, None, "names no licence block"),
    (CARRIED, dict(BLOCK, id="CC-BY-SA-4.0"), "names 'CC-BY-SA-4.0', but outside_set's row names CC-BY-4.0"),
    (CARRIED, dict(BLOCK, source="score"), "names source 'score', but its bytes are outside_set's"),
    ([{"what": "made by score", "by": "score"}], BLOCK, "names a licence of its own"),
    (CARRIED + [{"what": "other files", "by": "other_set", "carried": True}], BLOCK, "carries the bytes of 2 sources"),
])
def test_5_a_licence_block_exactly_when_it_carries_a_licensed_source(made_with, licence, fault):
    """Break: admission.licence_faults returning [] (a carried CC BY part goes out with no credit, or an MIT entry
    claims a licence), or its source/id comparison dropped (a block naming another licence passes)."""
    rows = Sources.load().rows + [LICENSED, dict(LICENSED, id="other_set")]
    found = Sources(rows).licence_faults(made_with, licence)
    assert (found == []) if fault is None else any(fault in each for each in found), found


def test_5_the_schema_takes_a_licence_block(world):
    """Break: entry.schema.json's licence property removed or `not: required licence` put back."""
    world.export()
    record = entries.load_address(world.root, "sound/hum@1")
    assert schema.faults("entry", dict(record, licence=BLOCK)) == []
    assert schema.faults("entry", dict(record, licence=dict(BLOCK, url="http://x"))) != []
    assert schema.faults("entry", dict(record, replaced_by="kit/room@1")) != []  # only a withdrawn entry names one


def test_a_withdrawn_entry_names_its_replacement(world):
    """Break: locks.withdraw ignoring replaced_by, replacement_fault returning None (a missing, withdrawn, self or
    other-kind target), replaced_by left out of entry.UNSTAMPED (its locks row's digest no longer holds), index.row
    dropping it, or verify not checking a replaced_by written past withdraw (missing, withdrawn, other kind)."""
    world.export()
    old, new = "surface/unused_paint@1", "surface/panel_paint@1"
    locks.withdraw(world.root, old)
    rows = locks.read(world.root)
    locks.withdraw(world.root, old, replaced_by=new)
    record = entries.load_address(world.root, old)
    assert record["state"] == "withdrawn" and record["replaced_by"] == new
    assert locks.read(world.root) == rows and rows[-1]["digest"] == entries.digest(record)
    for target, refusal in (("surface/none@1", "not in the library"), (new, "a loop"),
                            ("hum", "not an entry address"), ("model/crate_1@1", "is a model, not a surface")):
        with pytest.raises(locks.LockRefused, match=refusal):
            locks.withdraw(world.root, new, replaced_by=target)
    with pytest.raises(locks.LockRefused, match="withdrawn itself"):  # panel_paint -> unused_paint -> panel_paint
        locks.withdraw(world.root, new, replaced_by=old)
    with pytest.raises(locks.LockRefused, match="only a locked entry or a draft"):
        locks.withdraw(world.root, old)
    index.rebuild(world.root)
    row = next(each for each in index.kind_index(world.root, "surface")["entries"] if each["address"] == old)
    assert row["replaced_by"] == new
    assert messages(world) == []
    for target, fault in (("model/crate_1@1", "is a model, not a surface"), ("surface/none@1", "not in the library"),
                          (old, "a loop")):
        entries.write(world.root, dict(record, replaced_by=target))
        index.rebuild(world.root)
        assert any(each.startswith(f"{old}: ") and fault in each for each in messages(world)), (target, messages(world))


def test_a_withdrawn_entry_whose_bytes_are_lost_is_a_note_not_a_failure(world):
    """Break: verify.check_blobs not skipping a lost entry (its missing blob fails verify); lost_notes giving none (the
    loss goes unsaid); `lost` left out of entry.UNSTAMPED (its locks row's digest no longer holds); the schema's
    `lost` rule dropped (a current entry marked lost by hand passes); locks.mark_lost taking a current entry."""
    world.export()
    address = "sound/hum@1"
    with pytest.raises(locks.LockRefused, match="only a withdrawn entry"):
        locks.mark_lost(world.root, address, "its blobs are gone")
    locks.withdraw(world.root, address)
    rows = locks.read(world.root)
    record = locks.mark_lost(world.root, address, "none of its blobs is on any store", date="2026-11-22")
    index.rebuild(world.root)
    assert locks.read(world.root) == rows and rows[-1]["digest"] == entries.digest(record)
    path = blobs().path(record["files"]["sound.json"]["sha256"])
    path.chmod(0o644)
    path.unlink()
    assert messages(world) == []
    notes = lost_notes(world.root)
    assert notes == [(address, f"bytes lost since 2026-11-22: none of its blobs is on any store "
                               f"({len(record['files'])} files, kept for traceability)")], notes
    current = entries.load_address(world.root, "kit/room@1")
    rewrite_entry(world, "kit", "room", lambda changed: changed.update(lost={"date": "2026-11-22", "why": "by hand"}))
    assert any(each.startswith("kit/room@1: schema") for each in messages(world)), messages(world)
    assert lost_notes(world.root) == notes and current["state"] != "withdrawn"
