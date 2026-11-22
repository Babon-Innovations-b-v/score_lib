"""score-lib take-in --place: a place built by score's placing method kept as a draft place entry whose scene points
at the library, and fetched back as a stage that opens."""
import json
import pathlib
import shutil
import subprocess
import sys

from conftest import CRATE, LIBRARY, png, usd_text
from score_lib import entry as entries
from score_lib import take_in, take_in_place, verify
from score_lib.blobs import from_environment
from score_lib.cli import main
from score_lib.fetch import fetch

CC0 = "CC0 1.0"
STEEL = """#usda 1.0
(
    defaultPrim = "steel"
)

def Material "steel"
{
    token outputs:surface.connect = </steel/surface.outputs:surface>

    def Shader "surface"
    {
        uniform token info:id = "UsdPreviewSurface"
        float inputs:metallic = 1
        token outputs:surface
    }
}
"""
# A glTF model's USD as the placing method converts it beside the glTF (gltf.py), its map the entry's own picture.
LAMP = CRATE.replace("crate_1", "lamp").replace("./textures/lamp_base_color.png", "./lamp.png")
DOME = """#usda 1.0
(
    defaultPrim = "dome"
    upAxis = "Y"
)

def Mesh "dome"
{
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    point3f[] points = [(0, 0, 0), (3, 0, 0), (0, 3, 0)]
}
"""
WALKERS = """#usda 1.0

def "yard"
{
    def Scope "Characters"
    {
        def "walker" (
            prepend references = @../assets/characters/walker.usda@
        )
        {
        }
    }
}
"""
SCENE = """#usda 1.0
(
    defaultPrim = "yard"
    metersPerUnit = 1
    subLayers = [
        @layers/walkers/layers/characters.usda@
    ]
    upAxis = "Y"
)

def Xform "yard"
{
    def Xform "Objects"
    {
        def "crate" (
            prepend references = @./assets/kit/crate/1/piece.usdc@
        )
        {
            custom string score:library = "kit/crate@1"
            custom string score:surface = "surface/steel@1"
        }

        def "lamp" (
            prepend references = @./assets/model/lamp/1/gltf/lamp.usdc@
        )
        {
            custom string score:library = "model/lamp@1"
        }

        def "dome" (
            prepend references = @./assets/algorithm/dome_shell/dome_r3.usda@
        )
        {
            custom string score:library = "algorithm/dome_shell"
        }
    }

    def Scope "Looks"
    {
        def Material "crate" (
            prepend references = @./assets/surface/steel/1/material.usda@
        )
        {
            custom string score:surface = "surface/steel@1"
        }
    }
}
"""
OPENS = """import sys
from pxr import Usd, UsdUtils
stage = Usd.Stage.Open(sys.argv[1])
_, _, unresolved = UsdUtils.ComputeAllDependencies(sys.argv[1])
print(bool(stage) and bool(stage.GetPrimAtPath('/yard/Objects/crate/geo'))
      and bool(stage.GetPrimAtPath('/yard/Characters/walker')), unresolved)
"""


def write(folder, files):
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)


def library_entries(root, made):
    """A kit piece with its map, a surface and a glTF model with its map, taken in."""
    write(made, {"piece.usdc": usd_text(CRATE, ".usdc", made), "crate.png": png((200, 120, 40)),
                 "material.usda": STEEL.encode(), "surface.json": b'{"settings": {"roughness": 0.4}}',
                 "lamp.glb": b"glTF a lamp", "lamp.png": png((250, 240, 200))})

    def listed(kind, entry_id, files):
        return {"kind": kind, "id": entry_id, "title": entry_id,
                "files": {name: {"path": str(made / local), "from": local} for name, local in files.items()},
                "made_with": [{"what": entry_id, "source": CC0, "carried": True}]}

    report = take_in.take_in(root, from_environment(), {"world": "world1", "commit": "0" * 40, "taken_from": "a test", "entries": [
        listed("kit", "crate", {"piece.usdc": "piece.usdc", "textures/crate_1_base_color.png": "crate.png"}),
        listed("surface", "steel", {"material.usda": "material.usda", "surface.json": "surface.json"}),
        listed("model", "lamp", {"gltf/lamp.glb": "lamp.glb", "gltf/lamp.png": "lamp.png"})]})
    assert not report["refused"], report


def place_folder(root, folder):
    """A place_build folder: every laid entry's files copied under assets/ (one copy changed since), its own dome and
    a glTF's converted USD, its walkers' layer (sublayered, its own character beside it), its records, render, review
    sheet, viewer GLB and scene package."""
    fetch(root, from_environment(), ["kit/crate@1", "surface/steel@1", "model/lamp@1"], folder / "copied")
    shutil.copytree(folder / "copied/score_lib", folder / "assets")
    shutil.rmtree(folder / "copied")
    write(folder, {"scene.usda": SCENE.encode(), "place.json": json.dumps({"place": "yard"}).encode(),
                   "palette.json": b'{"tokens": {}}', "concept.png": png((10, 20, 30)), "render.png": png((30, 20, 10)),
                   "review/sheet.jpg": b"\xff\xd8 a sheet", "viewer.glb": b"glTF the viewer",
                   "package/scene.json": b'{"format": "score.scene"}', "package/objects/crate.bin": b"\0" * 12,
                   "package/objects/crate.gltf": json.dumps({"buffers": [{"uri": "crate.bin"}]}).encode(),
                   "assets/surface/steel/1/surface.json": b'{"settings": {"roughness": 0.9}}',
                   "assets/model/lamp/1/gltf/lamp.usdc": usd_text(LAMP, ".usdc", folder),
                   "assets/algorithm/dome_shell/dome_r3.usda": DOME.encode(),
                   "layers/walkers/layers/characters.usda": WALKERS.encode(),
                   "layers/walkers/assets/characters/walker.usda": DOME.encode()})
    (folder / "made.usdc").unlink()
    return folder


def test_a_place_keeps_its_own_files_points_at_the_library_and_opens_fetched(tmp_path):
    """Break: take_in_place.library_copy: drop 'or record["files"][held]["sha256"] != file_sha256(path)' (the changed
    surface.json copy is left out as the library's); mapping_of: drop 'mapping[path] = entries.library_path(...)'
    (the scene names ./assets/kit/crate/1/piece.usdc, which the entry does not hold, so verify and the fetched stage
    fail); take_in.role_of: drop the PLACE_ROLES branch (concept.png gets role texture, the package's scene.json
    record); take_in_place.settle: write a changed draft over its own version (the changed yard is taken in as
    place/yard@1 again, not @2, and @1's bytes change under whatever reads it); take_in_place.undrafted: never called
    (the yard with a draft package is taken in as place/yard@3); take_in_place.OWN_PARTS: drop "layers" (the scene's
    walkers sublayer is not kept, so verify and the fetched stage fail)."""
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    shutil.copyfile(LIBRARY / "LICENSE", root / "LICENSE")
    library_entries(root, tmp_path / "made")
    blobs = from_environment()
    folder = place_folder(root, tmp_path / "yard")
    report = take_in_place.taken_in_place(root, blobs, folder, "a" * 40, "world1")
    assert report["new"] == ["place/yard@1"], report
    record = entries.load_address(root, "place/yard@1")
    roles = {name: described["role"] for name, described in record["files"].items()}
    assert {name for name in roles if name.startswith("assets/")} == {
        "assets/algorithm/dome_shell/dome_r3.usda", "assets/model/lamp/1/gltf/lamp.usdc",
        "assets/surface/steel/1/surface.json"}, roles
    assert {name for name in roles if name.startswith("layers/")} == {
        "layers/walkers/layers/characters.usda", "layers/walkers/assets/characters/walker.usda"}, roles
    assert {name: roles[name] for name in ("scene.usda", "concept.png", "render.png", "review/sheet.jpg", "viewer.glb",
                                           "package/scene.json", "package/objects/crate.gltf", "palette.json")} == {
        "scene.usda": "usd", "concept.png": "concept", "render.png": "render", "review/sheet.jpg": "review",
        "viewer.glb": "viewer", "package/scene.json": "package", "package/objects/crate.gltf": "package",
        "palette.json": "record"}, roles
    assert record["requires"] == ["kit/crate@1", "model/lamp@1", "surface/steel@1"], record["requires"]
    assert record["provenance"]["commit"] == "a" * 40 and "algorithm/dome_shell" in record["provenance"]["lays"]
    assert record["usd"]["default_prim"] == "/yard" and record["picture"]["file"] == "render.png"
    assert verify.verify(root, blobs) == []
    assert take_in_place.taken_in_place(root, blobs, folder, "a" * 40, "world1")["unchanged"] == ["place/yard@1"]
    kept = entries.load_address(root, "place/yard@1")
    (folder / "objects.json").write_text(json.dumps([{"word": "crate", "box": [0, 0, 1, 1]}]))
    again = take_in_place.taken_in_place(root, blobs, folder, "a" * 40, "world1")
    assert again["new version"] == ["place/yard@2"] and not again.get("changed draft"), again
    assert entries.load_address(root, "place/yard@1") == kept, "a kept version was written over"
    undrafted = (folder / "package/scene.json").read_bytes()
    (folder / "package/scene.json").write_text(json.dumps({"format": "score.scene", "draft": {"baked": False}}))
    drafted = take_in_place.taken_in_place(root, blobs, folder, "a" * 40, "world1")
    assert [item["id"] for item in drafted["refused"]] == ["place/yard"] and not drafted["new version"], drafted
    (folder / "package/scene.json").write_bytes(undrafted)
    assert verify.verify(root, blobs) == []
    into = tmp_path / "fetched"
    try:
        main(["--root", str(root), "fetch", "place/yard@1", "--into", str(into), "--with-requires"])
    except SystemExit as stopped:
        assert not stopped.code, stopped
    scene = into / "score_lib/place/yard/1/scene.usda"
    assert "@score_lib/kit/crate/1/piece.usdc@" in scene.read_text()
    opened = subprocess.run([sys.executable, "-c", OPENS, str(scene)], capture_output=True, text=True,
                            env={"PXR_AR_DEFAULT_SEARCH_PATH": str(into), "PATH": "/usr/bin:/bin"})
    assert opened.stdout.strip() == "True []", opened.stdout + opened.stderr
