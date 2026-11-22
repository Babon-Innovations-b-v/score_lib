"""score-lib adopted match: each older model and surface is paired with the adopted entry of the same kind of object or
finish, or refused naming the clash; --apply withdraws the accepted ones naming their replacement. Red when a pair of
another kind, setting or colour is accepted, a synonym or Wikidata class is missed, an adopted or already replaced
entry is proposed, or --apply leaves an accepted entry standing or without its replacement."""
import json

import pytest

from score_lib import adopted, cli
from score_lib import entry as entries
from score_lib import index

SOURCE = "example_assets"
LICENCE = {"id": "CC-BY-4.0", "source": SOURCE, "credit": "a test's credit",
           "url": "https://creativecommons.org/licenses/by/4.0/"}


def put(root, kind, entry_id, title=None, tags=None, set_entry=False, state="draft", **extra):
    """One entry record written into the library root; its address."""
    record = {"schema": entries.SCHEMA, "address": entries.address_of(kind, entry_id, 1), "kind": kind,
              "id": entry_id, "version": 1, "title": title or entry_id, "state": state, "files": {}, "requires": [],
              "seen_in": [], "tags": tags or {}, "picture": None, "usd": None,
              "provenance": {"made_with": [{"what": "its files", "by": SOURCE if set_entry else "score",
                                            **({"carried": True} if set_entry else {})}]}, **extra}
    if set_entry:
        record["licence"] = LICENCE
    entries.write(root, record)
    return record["address"]


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for name in ("handTool_wrench_a01", "handTool_wrench_a02", "bed_hospital_a01", "chair_office_a01"):
        put(root, "model", "demo_" + name.lower(), name, set_entry=True)
    put(root, "model", "demo_safety_fe_a01", "safety_FE_a01", {"wikidata_class": ["fire extinguisher"]},
        set_entry=True)
    put(root, "model", "demo_old_wrench", "handTool_wrench_old", set_entry=True, state="withdrawn")
    put(root, "surface", "demo_aluminum_anodized_red", "Aluminum_Anodized_Red", set_entry=True)
    put(root, "surface", "demo_paint_metal_yellow", "Paint_Metal_Yellow", set_entry=True)
    put(root, "surface", "demo_rubber_gray_logo", "Rubber_Gray_Logo", {"painted_for": ["crate_a01"]}, set_entry=True)
    return root


ROWS = [  # (our kind, id, tags, expected status, expected new id or None, a word the reason holds)
    ("model", "wrench_1", {}, "accepted", "demo_handtool_wrench_a01", "equal candidates"),
    ("model", "spanner_2.workshop", {}, "accepted", "demo_handtool_wrench_a01", "wrench"),
    ("model", "extinguisher_1.lab", {}, "accepted", "demo_safety_fe_a01", "extinguisher"),
    ("model", "bed_1.flat", {}, "refused", "demo_bed_hospital_a01", "medical"),
    ("model", "chair_1.engineroom", {}, "refused", "demo_chair_office_a01", "office"),
    ("model", "far_ac_unit", {}, "no match", None, "names"),
    ("model", "far_bed", {}, "refused", "demo_bed_hospital_a01", "far copy"),
    ("model", "kenney_car_police", {"place": ["street"]}, "no match", None, "names"),
    ("surface", "anodized_red", {"family": ["aluminium"]}, "accepted", "demo_aluminum_anodized_red", "red"),
    ("surface", "anodized_blue", {"family": ["aluminium"]}, "refused", "demo_aluminum_anodized_red", "colour"),
    ("surface", "painted_metal", {"family": ["painted"]}, "refused", "demo_paint_metal_yellow", "palette"),
    ("surface", "rubber_grey", {"family": ["rubber"]}, "refused", "demo_rubber_gray_logo", "painted for one model"),
    ("surface", "label_waste", {"family": ["print"]}, "no match", None, "names"),
]


def test_older_entries_pair_with_the_same_kind_and_apply_withdraws_them_naming_it(library, tmp_path):
    for kind, entry_id, tags, *_ in ROWS:
        put(library, kind, entry_id, tags=tags)
    put(library, "model", "wrench_9", state="withdrawn", replaced_by="model/demo_handtool_wrench_a01@1")
    index.rebuild(library)
    found = {row["old"]: row for row in adopted.match(library, source_ids=[SOURCE])["proposals"]}
    assert sorted(found) == sorted(entries.address_of(kind, entry_id, 1) for kind, entry_id, *_ in ROWS)
    for kind, entry_id, _, status, new, word in ROWS:
        row = found[entries.address_of(kind, entry_id, 1)]
        assert (row["status"], row["new"]) == (status, new and entries.address_of(kind, new, 1)), row
        assert word in row["reason"], row
    proposals = tmp_path / "proposals.json"
    cli.main(["--root", str(library), "adopted", "match", "--source", SOURCE, "--proposals", str(proposals),
              "--apply"])
    report = json.loads(proposals.read_text())
    accepted = {row["old"]: row["new"] for row in report["proposals"] if row["status"] == "accepted"}
    assert sorted(report["withdrawn"]) == sorted(accepted) and len(accepted) == 4
    for old, new in accepted.items():
        record = entries.load_address(library, old)
        assert (record["state"], record["replaced_by"]) == ("withdrawn", new)
    rows = {row["address"]: row for row in json.loads((library / "index/model.json").read_text())["entries"]}
    assert rows["model/wrench_1@1"]["replaced_by"] == "model/demo_handtool_wrench_a01@1"
    again = adopted.match(library, source_ids=[SOURCE])
    assert again["counts"]["accepted"] == 0 and not set(accepted) & {row["old"] for row in again["proposals"]}
