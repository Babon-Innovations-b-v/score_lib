"""score-lib adopted match: each older model and surface is paired with the adopted entry of the same kind of object or
finish, or refused naming the clash; --apply withdraws the accepted ones naming their replacement. Red when a pair of
another kind, setting or colour is accepted (an object sharing only a word, a compound naming another thing, a finish
of another state: the rehearsal's lamps for a head light), a synonym or Wikidata class is missed, an adopted or already
replaced entry is proposed, or --apply leaves an accepted entry standing or without its replacement; red too when a
tie is settled by taking the first of equal candidates rather than by evidence (shared words, a name adding nothing,
the only one within twice our size, the same maps), when a pair more than twice or half our size is accepted, when a
size is misread from a record, a glTF's node transforms or a USD stage, or when the full run's wrong pairs come back
(score #155: a crate for the first of 59 boxes, a bowl stack for one bowl, a plant pot for a potted plant, a car's
radiator, a floor lamp for a floor, a tilt truck classed as a dumpster, a khaki case for a blue box, a suit's or a
tarp's fabric for a blanket's or a body's)."""
import hashlib
import json

import pytest

from score_lib import adopted, cli
from score_lib import entry as entries
from score_lib import index
from score_lib.blobs.local import LocalBlobStore

SOURCE = "example_assets"
LICENCE = {"id": "CC-BY-4.0", "source": SOURCE, "credit": "a test's credit",
           "url": "https://creativecommons.org/licenses/by/4.0/"}


def put(root, kind, entry_id, title=None, tags=None, set_entry=False, state="draft", files=None, **extra):
    """One entry record written into the library root; its address."""
    record = {"schema": entries.SCHEMA, "address": entries.address_of(kind, entry_id, 1), "kind": kind,
              "id": entry_id, "version": 1, "title": title or entry_id, "state": state, "files": files or {},
              "requires": [],
              "seen_in": [], "tags": tags or {}, "picture": None, "usd": None,
              "provenance": {"made_with": [{"what": "its files", "by": SOURCE if set_entry else "score",
                                            **({"carried": True} if set_entry else {})}]}, **extra}
    if set_entry:
        record["licence"] = LICENCE
    entries.write(root, record)
    return record["address"]


def held(tmp_path, name, data, role):
    """One file's bytes put into the test's blob store (SCORE_LIB_BLOBS_DIR); its record row."""
    source = tmp_path / "made" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    LocalBlobStore(tmp_path / "blobs").put(sha, source)
    return {name: {"bytes": len(data), "role": role, "sha256": sha}}


def sized(tmp_path, sides):
    """A model's files giving its size by its record (`model.json` `size_m`)."""
    return held(tmp_path, "model.json", json.dumps({"size_m": sides}).encode(), "record")


# A glTF box 0.5 x 0.2 x 0.1 under a node scaled by 2 and turned a quarter about z: 0.4 x 1.0 x 0.2 in the world.
GLTF = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"children": [1], "scale": [2, 2, 2]}, {"mesh": 0, "rotation": [0, 0, 0.7071068, 0.7071068]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"componentType": 5126, "count": 8, "type": "VEC3", "min": [-0.25, -0.1, 0], "max":
                       [0.25, 0.1, 0.1]}]}
# A USD cube of 0.45 m in centimetres.
USDA = """#usda 1.0
(
    defaultPrim = "Box"
    metersPerUnit = 0.01
)
def Cube "Box"
{
    double size = 45
}
"""


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for name in ("handTool_wrench_a01", "handTool_wrench_a02", "bed_hospital_a01", "chair_office_a01"):
        put(root, "model", "demo_" + name.lower(), name, set_entry=True)
    put(root, "model", "demo_safety_fe_a01", "tools_fireExtinguisher_co2_a01",
        {"wikidata_class": ["fire extinguisher"]}, set_entry=True)
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
    # The full run's ties and wrong pairs (score #155): two boxes told apart only by size (blue's record says 1.5 m,
    # gray's USD 0.45 m), a single bowl, a potted plant, a car's radiator, a floor, a tilt truck classed as a dumpster.
    put(root, "model", "demo_box_blue_a01", "box_blue_a01", {"wikidata_class": ["box"]}, set_entry=True,
        files=sized(tmp_path, [1.5, 1.0, 1.0]))
    put(root, "model", "demo_box_gray_a01", "box_gray_a01", {"wikidata_class": ["box"]}, set_entry=True,
        files=held(tmp_path, "box.usda", USDA.encode(), "usd"), usd={"root": "box.usda"})
    put(root, "model", "demo_bowl", "dishware_bowl_a01", {"wikidata_class": ["bowl"]}, set_entry=True)
    put(root, "model", "demo_potted_plant", "decor_plant_big_potted_c01", {"wikidata_class": ["pot plant"]},
        set_entry=True)
    put(root, "model", "demo_car_radiator", "coolingSystem_radiator_a01", {"wikidata_class": ["radiator"]},
        set_entry=True)
    put(root, "model", "demo_floor", "environment_floor_a01", {"wikidata_class": ["floor"]}, set_entry=True)
    put(root, "model", "demo_tilt_truck", "tiltTruck_gray_a01", {"wikidata_class": ["dumpster"]}, set_entry=True)
    put(root, "model", "demo_stool", "stool_a01", {"wikidata_class": ["stool"]}, set_entry=True)
    put(root, "model", "demo_bar_stool", "stool_bar_a01", {"wikidata_class": ["stool"]}, set_entry=True)
    # One finish taken from two models with the same maps, and one with other maps.
    for number, (colour, maps) in enumerate((("orange", "k2"), ("orange", "k2"), ("gray", "k3"), ("gray", "k4"))):
        put(root, "surface", f"demo_m_opaque__plastic__{colour}_a01_{number}", f"m_opaque__plastic__{colour}_a01",
            {"maps_key": [maps]}, set_entry=True)
    put(root, "surface", "demo_fabric_blanket_white", "opaque__fabric__blanket_white_a", set_entry=True)
    put(root, "surface", "demo_fabric_body", "m_opaque__fabric__body_b01", set_entry=True)
    return root


ROWS = [  # (our kind, id, tags, expected status, expected new id or None, a word the reason holds)
    ("model", "wrench_1", {}, "refused", None, "tied, nothing tells them apart"),
    ("model", "spanner_2.workshop", {}, "refused", None, "tied"),
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
    ("surface", "hull_white_gloss_paint", {"family": ["painted"]}, "refused",
     "demo_m_opaque__metal__paintedrough_white_a01", "gloss"),
    # The full run's ties and wrong pairs (score #155).
    ("model", "crate_1.garage", {}, "refused", None, "tied, nothing tells them apart (2 equal"),
    ("model", "crate_2", {}, "accepted", "demo_box_gray_a01", "the only one of 2 within 2 times its size"),
    ("model", "crate_4", {}, "accepted", "demo_box_blue_a01", "the only one of 2 within 2 times its size"),
    ("model", "box_blue_1", {}, "refused", "demo_box_blue_a01", "not its size (ours is 5.00 m long, it is 1.50 m)"),
    ("model", "case_khaki_trellis.storage", {}, "refused", "demo_box_blue_a01", "not the same colour (khaki"),
    ("model", "stool_1", {}, "accepted", "demo_stool", "whose name says nothing ours does not"),
    ("model", "bowl_stack_1", {}, "refused", "demo_bowl", "a stack or set of them is not one"),
    ("model", "plant_pot_1.quarters", {}, "refused", "demo_potted_plant", "plant pot; it is pot plant"),
    ("model", "radiator_1.old_station", {}, "refused", "demo_car_radiator", "a part of a whole"),
    ("model", "kenney_furniture_lamp_round_floor", {}, "refused", "demo_floor", "floor light; it is floor"),
    ("model", "kenney_road_dumpster", {}, "refused", "demo_tilt_truck", "names no dumpster"),
    ("surface", "suit_fabric_white", {}, "refused", "demo_fabric_blanket_white", "another use (blanket)"),
    ("surface", "tarp_cloth", {}, "refused", "demo_fabric_body", "another use (body)"),
    ("surface", "plastic_orange", {"family": ["plastic"]}, "accepted", "demo_m_opaque__plastic__orange_a01_0",
     "the same maps in all 2"),
    ("surface", "plastic_grey", {"family": ["plastic"]}, "refused", None, "tied"),
]


def test_older_entries_pair_with_the_same_kind_and_apply_withdraws_them_naming_it(library, tmp_path, monkeypatch):
    monkeypatch.setenv("SCORE_LIB_CACHE", str(tmp_path / "cache"))
    files = {"crate_2": sized(tmp_path, [0.4, 0.3, 0.3]), "box_blue_1": sized(tmp_path, [5.0, 1.0, 1.0]),
             "crate_4": held(tmp_path, "crate_4.gltf", json.dumps(GLTF).encode(), "gltf")}
    for kind, entry_id, tags, *_ in ROWS:
        put(library, kind, entry_id, tags=tags, files=files.get(entry_id))
    put(library, "model", "wrench_9", state="withdrawn", replaced_by="model/demo_handtool_wrench_a01@1")
    index.rebuild(library)
    sizes = adopted.Sizes(library, LocalBlobStore(tmp_path / "blobs"), tmp_path / "cache" / "adopted")
    report = adopted.match(library, source_ids=[SOURCE], sizes=sizes)
    found = {row["old"]: row for row in report["proposals"]}
    assert report["sizes"]["model/crate_4@1"] == [1.0, 0.4, 0.2] and report["sizes"]["model/demo_box_gray_a01@1"] == [
        0.45, 0.45, 0.45] and not list((tmp_path / "cache" / "adopted").iterdir()), report["sizes"]
    assert sorted(found) == sorted(entries.address_of(kind, entry_id, 1) for kind, entry_id, *_ in ROWS)
    wrong = [found[entries.address_of(kind, entry_id, 1)] for kind, entry_id, _, status, new, word in ROWS
             if (found[entries.address_of(kind, entry_id, 1)]["status"], found[entries.address_of(kind, entry_id, 1)][
                 "new"]) != (status, new and entries.address_of(kind, new, 1))
             or word not in found[entries.address_of(kind, entry_id, 1)]["reason"]]
    assert not wrong, wrong
    proposals = tmp_path / "proposals.json"
    cli.main(["--root", str(library), "adopted", "match", "--source", SOURCE, "--proposals", str(proposals),
              "--apply"])
    report = json.loads(proposals.read_text())
    accepted = {row["old"]: row["new"] for row in report["proposals"] if row["status"] == "accepted"}
    assert sorted(report["withdrawn"]) == sorted(accepted) and len(accepted) == 9
    for old, new in accepted.items():
        record = entries.load_address(library, old)
        assert (record["state"], record["replaced_by"]) == ("withdrawn", new)
    rows = {row["address"]: row for row in json.loads((library / "index/model.json").read_text())["entries"]}
    assert rows["model/stool_1@1"]["replaced_by"] == "model/demo_stool@1"
    again = adopted.match(library, source_ids=[SOURCE], sizes=adopted.Sizes(library, LocalBlobStore(tmp_path / "blobs"),
                                                                            tmp_path / "cache" / "again"))
    assert again["counts"]["accepted"] == 0 and not set(accepted) & {row["old"] for row in again["proposals"]}
