"""score-lib adopted glow and take-in's glow_of: an adopted surface's emission in the recipes' units (score #197).

The breaks that turn it red: glow_of carrying a source's nits as a strength (the LED strip's 5000), losing its hue,
letting it past GLOW_MOST or keeping a glow under GLOW_SEEN; a take-in look or record keeping the nits; the rewrite not
setting surface.json's `emission` or the look's emissiveColor, rewriting a locked entry in place, touching a surface
with no emission or one not of an adopted set, writing anything without `apply`, or writing again on a second run."""
import json

from score_lib import adopted_glow, locks
from score_lib import entry as entries
from score_lib.blobs import file_sha256, from_environment
from score_lib.take_in_hub import GLOW_MOST, glow_of, look_constants, surface_record

LICENCE = {"credit": "a test", "id": "CC-BY-4.0", "source": "nvidia_simready",
           "url": "https://creativecommons.org/licenses/by/4.0/"}
ADOPTED_MADE = [{"by": "nvidia_simready", "carried": True, "what": "an asset's settings"}]
LED = [5000.1206, 4955.5586, 2062.693]
LOOK = """#usda 1.0
def Material "m"
{{
    def Shader "surface"
    {{
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor = (1, 0.99, 0.41)
        color3f inputs:emissiveColor = ({0})
    }}
}}
"""


def put(blobs, path, text):
    path.write_text(text)
    sha256 = file_sha256(path)
    blobs.put(sha256, path)
    return {"sha256": sha256, "bytes": path.stat().st_size}


def surface(root, blobs, folder, entry_id, emissive, adopted=True):
    """A draft adopted surface whose constants carry `emissive` (none: no emissiveColor), its two blobs put."""
    constants = {"diffuseColor": [1.0, 0.99, 0.41], **({"emissiveColor": emissive} if emissive else {})}
    record_json = put(blobs, folder / f"{entry_id}.json", entries.dumps({"source": "nvidia_simready",
                                                                          "constants": constants, "recipe": None}))
    look = put(blobs, folder / f"{entry_id}.usda", LOOK.format(", ".join(str(value) for value in emissive or [0, 0, 0])))
    record = {"schema": entries.SCHEMA, "address": f"surface/{entry_id}@1", "kind": "surface", "id": entry_id,
              "version": 1, "title": entry_id, "state": "draft", "picture": None, "picture_reason": "none",
              "files": {"surface.json": {**record_json, "role": "record"}, "material.usda": {**look, "role": "usd"}},
              "usd": None, "requires": [], "tags": {"maps_key": ["k"]}, "seen_in": [],
              "provenance": {"world": "world1", "commit": "0" * 40, "taken_from": "a test",
                             "made_with": ADOPTED_MADE if adopted else [{"by": "score", "what": "a bake"}],
                             "files_from": {"surface.json": "written by a test"}}}
    if adopted:
        record["licence"] = LICENCE
    entries.write(root, record)


def read(blobs, root, address, name, scratch):
    record = entries.load_address(root, address)
    blobs.get(record["files"][name]["sha256"], scratch / "read")
    return record, (scratch / "read").read_text()


def test_an_adopted_glow_is_in_the_recipes_units(tmp_path):
    strip = glow_of(LED)
    assert max(strip) == GLOW_MOST and abs(strip[2] / strip[0] - LED[2] / LED[0]) < 1e-4, strip
    assert glow_of([100.0, 100.0, 100.0]) == [0.1, 0.1, 0.1]
    assert glow_of([2.0, 0.0, 0.0]) == [0.0, 0.0, 0.0] and glow_of([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
    material = {"name": "m", "maps": {}, "constants": {"emissiveColor": LED}, "settings": {}}
    assert look_constants(material["constants"], None)["emissiveColor"] == strip
    assert surface_record({"id": "nvidia_simready"}, material, True)["emission"] == strip

    root, blobs, made = tmp_path / "library", from_environment(), tmp_path / "made"
    made.mkdir()
    surface(root, blobs, made, "strip", LED)
    surface(root, blobs, made, "locked_lamp", [100.0, 100.0, 100.0])
    surface(root, blobs, made, "plain", None)
    surface(root, blobs, made, "dark", [0.0, 0.0, 0.0])
    surface(root, blobs, made, "ours", [3.0, 3.0, 3.0], adopted=False)
    locks.lock(root, "surface/locked_lamp@1", "2026-11-24")
    before = sorted(path.read_text() for path in (root / "entries").rglob("entry.json"))

    looked = adopted_glow.write_glows(root, blobs)
    assert {row["address"]: (row["status"], row["to"]) for row in looked["planned"]} == {
        "surface/strip@1": ("in place", strip), "surface/locked_lamp@1": ("new version", [0.1, 0.1, 0.1])}, looked
    assert sorted(path.read_text() for path in (root / "entries").rglob("entry.json")) == before

    report = adopted_glow.write_glows(root, blobs, apply=True)
    assert report["in place"] == ["surface/strip@1"] and report["new version"] == ["surface/locked_lamp@2"], report
    record, text = read(blobs, root, "surface/strip@1", "surface.json", tmp_path)
    assert json.loads(text)["emission"] == strip and json.loads(text)["constants"]["emissiveColor"] == LED
    assert adopted_glow.WHY in record["provenance"]["files_from"]["material.usda"]
    _, look = read(blobs, root, "surface/strip@1", "material.usda", tmp_path)
    assert "emissiveColor = (0.5, 0.495544, 0.206264)" in look and "5000" not in look, look
    record, look = read(blobs, root, "surface/locked_lamp@2", "material.usda", tmp_path)
    assert record["state"] == "draft" and "emissiveColor = (0.1, 0.1, 0.1)" in look
    assert entries.load_address(root, "surface/locked_lamp@1")["state"] == "locked"
    _, ours = read(blobs, root, "surface/ours@1", "surface.json", tmp_path)
    assert "emission" not in json.loads(ours)

    again = adopted_glow.write_glows(root, blobs, apply=True)
    assert sorted(again["unchanged"]) == ["surface/locked_lamp@2", "surface/strip@1"], again
    assert again["in place"] == again["new version"] == []
