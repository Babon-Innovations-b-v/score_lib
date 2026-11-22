"""score-lib adopted match: each older model and surface is paired with the adopted entry of the same kind of object or
finish, or refused naming the clash; --apply withdraws the accepted ones naming their replacement. Red when a pair of
another kind, setting or colour is accepted (an object sharing only a word, a compound naming another thing, a finish
of another state: the rehearsal's lamps for a head light), a synonym or Wikidata class is missed, an adopted or already
replaced entry is proposed, or --apply leaves an accepted entry standing or without its replacement."""
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
    # The rehearsal's adopted models (score #155): their Wikidata class decides, a stray tag value is not a class.
    put(root, "model", "demo_head_light", "bodyPart_headLight_left_a01",
        {"wikidata_class": ["headlamp", "wikidata_class"], "description": ["lamp mounted in the front of a vehicle"]},
        set_entry=True)
    put(root, "model", "demo_electric_panel", "warehouse_EF_electricPanel_a01",
        {"wikidata_class": ["control panel"], "description": ["control panel"]}, set_entry=True)
    put(root, "model", "demo_mug", "dishware_mug_c01", {"wikidata_class": ["drinking vessel"]}, set_entry=True)
    for title in ("m_opaque__metal__chrome_a01", "m_trans__glass__clean_a01", "opaque__plastic__emissive_red_led_a",
                  "m_opaque__plastic__black_a01", "m_opaque__metal__paintedRough_white_a01"):
        put(root, "surface", "demo_" + title.lower().strip("_"), title, set_entry=True)
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
    # The rehearsal's wrong pairs (score #155): a shared word is not the same thing, a qualifier makes another one.
    ("model", "bedside_lamp_1", {}, "refused", "demo_head_light", "it is headlight)"),
    ("model", "kenney_road_traffic_light", {}, "refused", "demo_head_light", "traffic light"),
    ("model", "bay_panel_1", {}, "refused", "demo_electric_panel", "bay panel"),
    ("model", "ceiling_light_panel_1.storage", {}, "refused", "demo_electric_panel", "light panel"),
    ("model", "electrical_panel_1.workshop", {}, "accepted", "demo_electric_panel", "within its Wikidata class"),
    ("model", "mug_1.quarters", {}, "accepted", "demo_mug", "within its Wikidata class"),
    ("surface", "rusted_metal", {}, "refused", "demo_paint_metal_yellow", "not the same finish (metal"),
    ("surface", "chrome_rusted", {}, "refused", "demo_m_opaque__metal__chrome_a01", "rusted"),
    ("surface", "glass_tinted", {"family": ["glass"]}, "refused", "demo_m_trans__glass__clean_a01", "tinted"),
    ("surface", "glass_fibre_panel", {"family": ["composite"]}, "no match", None, "names"),
    ("surface", "clear_glass", {"family": ["glass"]}, "accepted", "demo_m_trans__glass__clean_a01", "glass"),
    ("surface", "plastic_red", {"family": ["plastic"]}, "refused", "demo_opaque__plastic__emissive_red_led_a",
     "emissive"),
    ("surface", "leather_black", {"family": ["plastic"]}, "refused", "demo_m_opaque__plastic__black_a01", "leather"),
    ("surface", "hull_white_gloss", {"family": ["painted"]}, "refused", "demo_m_opaque__metal__paintedrough_white_a01",
     "gloss"),
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
    assert sorted(report["withdrawn"]) == sorted(accepted) and len(accepted) == 7
    for old, new in accepted.items():
        record = entries.load_address(library, old)
        assert (record["state"], record["replaced_by"]) == ("withdrawn", new)
    rows = {row["address"]: row for row in json.loads((library / "index/model.json").read_text())["entries"]}
    assert rows["model/wrench_1@1"]["replaced_by"] == "model/demo_handtool_wrench_a01@1"
    again = adopted.match(library, source_ids=[SOURCE])
    assert again["counts"]["accepted"] == 0 and not set(accepted) & {row["old"] for row in again["proposals"]}
