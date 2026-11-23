"""score-lib take-in hub: an adopted set's assets at one pinned revision taken in, side by side, as model and surface
entries under their own licence (a UDIM map's every tile in its surface), their bytes in the blob store and none left
on disk; a save from another branch pushed to the library's main, a run without --save no cause for the save to see
them unchanged, and --retake settling them again; and the pacing of a reader, its back-off after a refusal for rate.

Breaks that turn it red: a constant-only surface left without its licence block and carried source, a UDIM material gapped, a save pushed to its own branch, a save after a run without it
committing nothing (or not the surfaces its models require), assets side by side settled out of the run's order (the
shared tile set's size then the crate's, which has no mesh), a glass's MDL transmission left out of its record or
its look left opaque, a material with no UsdPreviewSurface gapped though its OmniPBR shader gives its look (or its look
not read from that shader's maps, scale and constants), a stand-in material whose outputs no shader feeds gapped, an
OmniPBR cutout opacity (constant, threshold or map) left out of the look or record, the source's own check report
(no layer names it, no role) gapping its asset or kept in its entry, --retake leaving a changed asset unchanged, a 429
not tried again or not counted, the shared back-off not held."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import time

import pytest
from huggingface_hub.errors import HfHubHTTPError
from huggingface_hub.hf_api import RepoFile, RepoFolder
from huggingface_hub.utils import httpx

from conftest import git, png, usd_text
from score_lib import entry as entries
from score_lib import take_in_hub, verify
from score_lib.admission import Sources
from score_lib.blobs import from_environment

REPO = "example/fake-assets"
REVISION = "a" * 40
SOURCES = """
[[source]]
id = "score"
name = "made by score"
output = true
copy = true
why = "our own work"
from = "a test"

[[source]]
id = "example_assets"
name = "Example assets"
output = true
copy = true
licence = "CC-BY-4.0"
why = "a test's CC BY 4.0 set"
from = "a test"

[source.take_in]
repo = "example/fake-assets"
url = "example.org/datasets/{repo}"
bom = ".metadata/bom.json"
roots = ".metadata/roots.json"
prefix = "demo"
creator = "Example Corp"
licence_name = "CC BY 4.0"
licence_url = "https://creativecommons.org/licenses/by/4.0/"
"""
TILE = "t_plastic_black_a01_tile"


def texture(name, role, directx=False):
    """A UsdUVTexture shader spec over one map (a normal map's green flipped when it is a DirectX one)."""
    channel = "float3 outputs:rgb" if role != "orm" else "float outputs:g\n        float outputs:b"
    flip = "float4 inputs:scale = (2, -2, 2, 1)\n        " if directx and role == "normal" else ""
    return f"""
    def Shader "{role}"
    {{
        uniform token info:id = "UsdUVTexture"
        asset inputs:file = @../Textures/{name}@
        {flip}{channel}
    }}"""


def material(name, maps=None, constant=None, mdl=None, directx=False):
    """A Material spec: a UsdPreviewSurface over maps {role: file} (albedo, normal, orm) or a constant colour."""
    base = f"/RootNode/Materials/{name}"
    wires = {"albedo": "color3f inputs:diffuseColor.connect = <{0}/albedo.outputs:rgb>",
             "normal": "normal3f inputs:normal.connect = <{0}/normal.outputs:rgb>",
             "orm": "float inputs:roughness.connect = <{0}/orm.outputs:g>\n"
                    "            float inputs:metallic.connect = <{0}/orm.outputs:b>"}
    inputs = [wires[role].format(base) for role in maps or {}]
    if constant:
        inputs.append(f"color3f inputs:diffuseColor = {constant}")
    shaders = "".join(texture(file, role, directx) for role, file in (maps or {}).items())
    shader_lines = "\n            ".join(inputs)
    look = (f'\n    def Shader "look"\n    {{\n        uniform token info:id = "mdl:look"\n'
            f'        uniform asset info:mdl:sourceAsset = @{mdl}@\n    }}' if mdl else "")
    return f"""
def Material "{name}"
{{
    custom string acme:nonvisual:base = "plastic"
    custom float acme:physics_density = 950
    token outputs:surface.connect = <{base}/preview.outputs:surface>

    def Shader "preview"
    {{
        uniform token info:id = "UsdPreviewSurface"
            {shader_lines}
        token outputs:surface
    }}

    def Shader "mapping"
    {{
        uniform token info:id = "UsdTransform2d"
        float2 inputs:scale = (3, 3)
    }}{shaders}{look}
}}"""


GLASS = """
def Material "m_glass"
{
    token outputs:surface.connect = </RootNode/Materials/m_glass/preview.outputs:surface>

    def Shader "preview"
    {
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor = (0.06, 0.06, 0.06)
        float inputs:opacity = 1
        float inputs:roughness = 0.25
        token outputs:surface
    }

    def Shader "glass"
    {
        uniform token info:id = "mdl:OmniGlass"
        float inputs:cutout_opacity = 0.4
        color3f inputs:glass_color = (0.06, 0.06, 0.06)
        float inputs:glass_ior = 1.3
    }
}"""  # a glass whose UsdPreviewSurface says opaque; its MDL glass shader is see-through, its frosting the default
PHYSICS_ONLY = '\ndef Material "physics_mat_plastic"\n{\n}'  # a physics material: empty, its API in another layer
STAND_IN = '\ndef Material "m_stand_in_1"\n{\n    token outputs:surface\n    token outputs:displacement\n}'
# a stand-in: its outputs declared, none wired, no shader (its look, if any, another layer's)
PBR_ONLY = f"""
def Material "m_pbr"
{{
    token outputs:mdl:surface.connect = </RootNode/Materials/m_pbr/pbr.outputs:out>
    token outputs:surface

    def Shader "pbr"
    {{
        uniform token info:implementationSource = "sourceAsset"
        uniform asset info:mdl:sourceAsset = @../source_assets/look.mdl@
        uniform token info:mdl:sourceAsset:subIdentifier = "OmniPBR"
        asset inputs:diffuse_texture = @../Textures/{TILE}_alb.png@
        bool inputs:enable_ORM_texture = 1
        asset inputs:ORM_texture = @../Textures/{TILE}_orm.png@
        float2 inputs:texture_scale = (2, 2)
        bool inputs:enable_opacity = 1
        float inputs:opacity_constant = 0.5
        float inputs:opacity_threshold = 0.25
        token outputs:out
    }}
}}"""  # an MDL OmniPBR alone (named by its sub-identifier, no UsdPreviewSurface), its cutout opacity a constant
CUTOUT = """
def Material "m_cutout"
{
    token outputs:surface.connect = </RootNode/Materials/m_cutout/preview.outputs:surface>

    def Shader "preview"
    {
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor = (0.5, 0.5, 0.5)
        float inputs:opacity = 1
        token outputs:surface
    }

    def Shader "pbr"
    {
        uniform token info:id = "mdl:OmniPBR"
        bool inputs:enable_opacity = 1
        bool inputs:enable_opacity_texture = 1
        asset inputs:opacity_texture = @../Textures/crate_logo_alb.png@
        int inputs:opacity_mode = 0
    }
}"""  # a UsdPreviewSurface saying opaque beside an OmniPBR whose opacity map cuts it out (mode 0: its alpha)
QUAD = """    def Mesh "seat" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        point3f[] points = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
        texCoord2f[] primvars:st = [(0, 0), (1, 0), (1, 1), (0, 1)] (
            interpolation = "faceVarying"
        )
        rel material:binding = </RootNode/Materials/m_plastic_black>
    }
"""  # 2 by 2 units over the whole UV square once: at 1 m per unit and a UV scale of 3, one repeat is 2/3 m


def layer(materials, documentation=None, sub=None, mesh=""):
    """A payload layer: RootNode with its Wikidata tags and description, its Materials scope and a mesh."""
    head = f"(\n    defaultPrim = \"RootNode\"\n    subLayers = [@./{sub}@]\n)" if sub else "(\n    defaultPrim = \"RootNode\"\n)"
    tags = (f'    custom string acme:documentation = "{documentation}"\n'
            '    custom string semantics:wikidata_class = "Crates"\n'
            '    custom string semantics:wikidata_qcode = "Q1"\n'
            '    custom string semantic:wikidata_qcode:params:semanticType = "wikidata_qcode"\n') if documentation else ""
    body = "".join(materials).replace("\n", "\n        ")
    return f"""#usda 1.0
{head}

{"def Xform" if documentation else "over"} "RootNode"
{{
{tags}{mesh}    def Scope "Materials"
    {{{body}
    }}
}}
"""


def crate(folder):
    """A layout-A asset: a crate root, base and materials payloads, shared tile maps and a logo painted for it, an
    MDL beside a copy of a map, a Newton payload and thumbnails."""
    usd = folder / "assets_usd"
    (usd / "payloads").mkdir(parents=True)
    root = '#usda 1.0\n(\n    defaultPrim = "RootNode"\n    upAxis = "Z"\n)\n\n' \
           'def Xform "RootNode" (\n    prepend references = @./payloads/base.usda@\n)\n{\n}\n'
    (usd / "sm_crate_A01_01.usd").write_bytes(usd_text(root, ".usd", folder))
    (folder / "made.usd").unlink(missing_ok=True)
    (usd / "payloads/base.usda").write_text(layer([PHYSICS_ONLY], documentation="A crate",
                                                          sub="materials.usda"))
    tile = {"albedo": f"{TILE}_alb.png", "normal": f"{TILE}_nor.png", "orm": f"{TILE}_orm.png"}
    (usd / "payloads/materials.usda").write_text(layer([
        material("m_plastic_black", tile, mdl="../source_assets/look.mdl"),
        material("m_logo", {"albedo": "crate_logo_alb.png"}), material("m_paint", constant="(0.8, 0.1, 0.1)"),
        GLASS, STAND_IN, PBR_ONLY, CUTOUT]))
    tiles(usd / "Textures")
    (usd / "Textures/crate_logo_alb.png").write_bytes(png((200, 30, 30), 4))
    (usd / "source_assets").mkdir()
    (usd / "source_assets/look.mdl").write_text("mdl 1.6;\n")
    shutil.copy(usd / f"Textures/{TILE}_alb.png", usd / f"source_assets/{TILE}_alb.png")
    (usd / "Newton").mkdir()
    (usd / "Newton/enabled.usda").write_text('#usda 1.0\n\ndef "RootNode"\n{\n}\n')
    (usd / ".thumbs/256x256").mkdir(parents=True)
    (usd / ".thumbs/256x256/sm_crate_A01_01.png").write_bytes(png((9, 9, 9), 4))
    return "assets_usd/sm_crate_A01_01.usd"


def tiles(folder):
    """The shared tile set's three maps."""
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, colour in (("alb", (20, 20, 20)), ("nor", (128, 128, 255)), ("orm", (255, 120, 0))):
        (folder / f"{TILE}_{suffix}.png").write_bytes(png(colour, 4))


def bowl(folder):
    """A layout-B asset: an ASCII root with a Newton variant payload, the same tile set as the crate."""
    usd = folder / "assets_usd"
    (usd / "payloads").mkdir(parents=True)
    (usd / "runnables/physics").mkdir(parents=True)
    (usd / "sm_bowl_b01_01.usda").write_text(
        '#usda 1.0\n(\n    defaultPrim = "RootNode"\n    metersPerUnit = 1\n)\n\ndef Xform "RootNode" (\n'
        '    prepend references = @./payloads/base.usda@\n    variants = {\n        string Newton = "on"\n    }\n'
        '    prepend variantSets = "Newton"\n)\n{\n    variantSet "Newton" = {\n        "on" (\n'
        '            prepend payload = @./runnables/physics/newton.usda@\n        ) {\n        }\n    }\n}\n')
    (usd / "runnables/physics/newton.usda").write_text('#usda 1.0\n(\n    defaultPrim = "RootNode"\n)\n\n'
                                                       'def "RootNode"\n{\n}\n')
    tile = {"albedo": f"{TILE}_alb.png", "normal": f"{TILE}_nor.png", "orm": f"{TILE}_orm.png"}
    (usd / "payloads/base.usda").write_text(layer([material("m_plastic_black", tile, directx=True)],
                                                  documentation="A bowl", mesh=QUAD))
    tiles(usd / "Textures")
    (usd / "validator_sm_bowl_b01_01_issues.csv").write_text("Asset,Rule,Message\n")  # the source's check report
    return "assets_usd/sm_bowl_b01_01.usda"


def tiled(folder):
    """A layout-B asset whose albedo names UDIM tiles (two of them)."""
    usd = folder / "assets_usd"
    (usd / "payloads").mkdir(parents=True)
    (usd / "sm_panel_d01_01.usda").write_text('#usda 1.0\n(\n    defaultPrim = "RootNode"\n)\n\n'
                                              'def Xform "RootNode" (\n'
                                              '    prepend references = @./payloads/base.usda@\n)\n{\n}\n')
    (usd / "payloads/base.usda").write_text(layer([material("m_panel", {"albedo": "panel_alb.<UDIM>.png"})],
                                                  documentation="A panel"))
    (usd / "Textures").mkdir()
    for tile, colour in (("1001", (10, 200, 10)), ("1002", (10, 10, 200))):
        (usd / f"Textures/panel_alb.{tile}.png").write_bytes(png(colour, 4))
    return "assets_usd/sm_panel_d01_01.usda"


def broken(folder):
    """An asset whose layer names a file whose ending no role knows."""
    usd = folder / "assets_usd"
    usd.mkdir(parents=True)
    (usd / "sm_broken_c01_01.usda").write_text('#usda 1.0\n(\n    defaultPrim = "RootNode"\n)\n\n'
                                               'def Xform "RootNode"\n{\n    asset notes = @./notes.xyz@\n}\n')
    (usd / "notes.xyz").write_text("?")
    return "assets_usd/sm_broken_c01_01.usda"


def billed(folder, root):
    """The asset's root list and bill of materials (every file but the metadata itself)."""
    items = [{"relative_path": path.relative_to(folder).as_posix(), "size": path.stat().st_size,
              "hash": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
             for path in sorted(folder.rglob("*")) if path.is_file()]
    (folder / ".metadata").mkdir()
    (folder / ".metadata/bom.json").write_text(json.dumps({"format_version": 1, "items": items}))
    (folder / ".metadata/roots.json").write_text(json.dumps({"entries": [root]}))


class FolderApi:
    """A dataset repository at one revision read from a folder, as huggingface_hub's HfApi reads one, the `slow`
    assets' files a moment late each."""

    def __init__(self, folder, slow=()):
        self.folder = pathlib.Path(folder)
        self.revisions, self.downloads, self.slow = set(), [], set(slow)

    def list_repo_tree(self, repo_id, path_in_repo=None, *, recursive=False, revision=None, repo_type=None):
        self.revisions.add(revision)
        for path in sorted(self.folder.iterdir()):
            yield (RepoFolder(path=path.name, oid="0") if path.is_dir()
                   else RepoFile(path=path.name, size=path.stat().st_size, oid="0"))

    def hf_hub_download(self, repo_id, filename, *, repo_type=None, revision=None, local_dir=None):
        self.revisions.add(revision)
        self.downloads.append(filename)
        if filename.split("/")[0] in self.slow:
            time.sleep(0.05)
        target = pathlib.Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.folder / filename, target)
        return str(target)


@pytest.fixture
def repository(tmp_path):
    folder = tmp_path / "repository"
    for name, made in (("crate_A01", crate), ("bowl_b01", bowl), ("broken_c01", broken), ("tiles_d01", tiled)):
        (folder / name).mkdir(parents=True)
        billed(folder / name, made(folder / name))
    (folder / "README.md").write_text("license: cc-by-4.0\n")
    return folder


def on_a_branch_beside_main(root, origin):
    """The library root a git repository whose origin holds main, checked out on another branch (a worktree's)."""
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.org")
    (root / "entries/.keep").write_text("")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "library")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "origin", "main")
    git(root, "checkout", "-q", "-b", "x155-work")


def test_an_adopted_set_is_taken_in_whole_under_its_licence(repository, tmp_path):
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    on_a_branch_beside_main(root, tmp_path / "origin.git")
    (tmp_path / "sources.toml").write_text(SOURCES)
    sources = Sources.load(tmp_path / "sources.toml")
    blobs = from_environment()
    cache = tmp_path / "caches"
    cache.mkdir()
    api = FolderApi(repository, slow={"bowl_b01"})  # the crate, side by side, done first: settled after the bowl
    report = take_in_hub.take_in_hub(root, blobs, take_in_hub.HubSource(api, REPO, REVISION), sources=sources,
                                     cache_bytes=1, cache_root=cache, jobs=2)
    assert api.revisions == {REVISION} and not list(cache.iterdir())
    assert [gap["asset"] for gap in report["gaps"]] == ["broken_c01"] and "notes.xyz" in report["gaps"][0]["why"]
    models = sorted(address for address in report["new"] if address.startswith("model/"))
    assert models == ["model/demo_bowl_b01@1", "model/demo_crate_a01@1", "model/demo_tiles_d01@1"], report
    surfaces = [entries.load_address(root, address) for address in report["new"] if address.startswith("surface/")]
    assert len(surfaces) == 7, report  # the shared tile set once, the crate's logo, paint, glass, OmniPBR and cutout,
    # the UDIM tiles; the crate's stand-in material none
    assert report["left out"] == [{"asset": "bowl_b01", "file": "validator_sm_bowl_b01_01_issues.csv",
                                   "why": take_in_hub.LEFT_OUT}], report["left out"]
    model = entries.load_address(root, "model/demo_crate_a01@1")
    assert model["usd"]["root"] == "sm_crate_A01_01.usd" and model["usd"]["default_prim"] == "/RootNode"
    assert model["usd"]["path"] == "score_lib/model/demo_crate_a01/1/sm_crate_A01_01.usd"
    assert "source_assets/look.mdl" in model["files"] and "Newton/enabled.usda" in model["files"]
    assert not [name for name in model["files"] if ".thumbs" in name or name == f"source_assets/{TILE}_alb.png"]
    assert model["licence"] == {"id": "CC-BY-4.0", "source": "example_assets", "url":
                                "https://creativecommons.org/licenses/by/4.0/", "credit":
                                f"crate_A01 by Example Corp, from example.org/datasets/{REPO} at revision "
                                f"{REVISION}, CC BY 4.0; {take_in_hub.CHANGED}"}
    assert model["tags"]["wikidata_qcode"] == ["Q1"] and model["tags"]["description"] == ["A crate"]
    assert model["tags"]["simulators"] == ["newton"]
    assert "changed" in model["provenance"]["files_from"]["payloads/materials.usda"]
    assert sorted(model["requires"]) == sorted(record["address"] for record in surfaces if record["title"] != "m_panel")
    from pxr import Sdf
    by_material = {json.loads((blob(blobs, record, "surface.json", tmp_path)).read_text())["material"]: record
                   for record in surfaces}
    assert json.loads(blob(blobs, by_material["m_logo"], "surface.json", tmp_path).read_text())["recipe_gap"]
    tiled = json.loads(blob(blobs, by_material["m_plastic_black"], "surface.json", tmp_path).read_text())
    assert not tiled["recipe_gap"] and tiled["tile_metres"] == pytest.approx(2 / 3, abs=1e-4), tiled
    assert (tiled["normal_convention"], tiled["normal_convention_assumed"], tiled["tile_metres_reason"]) == (
        "directx", False, None)
    assert tiled["maps"] == {"albedo": "albedo.png", "normal": "normal.png", "orm": "orm.png"}
    for name, reason in (("m_logo", take_in_hub.ONE_MODEL_TILE), ("m_paint", take_in_hub.NO_MAPS)):
        record = json.loads(blob(blobs, by_material[name], "surface.json", tmp_path).read_text())
        assert (record["tile_metres"], record["tile_metres_reason"], record["normal_convention_assumed"]) == (
            None, reason, True)
    glass = json.loads(blob(blobs, by_material["m_glass"], "surface.json", tmp_path).read_text())
    assert glass["transmission"] == {"shader": "OmniGlass", "weight": 1.0, "colour": [0.06, 0.06, 0.06], "ior": 1.3,
                                     "roughness": 0.0, "thin_walled": False, "presence": 1.0}, glass
    assert "transmission" not in tiled and glass["constants"]["opacity"] == 1.0  # the source's own settings kept
    see_through = Sdf.Layer.FindOrOpen(str(blob(blobs, by_material["m_glass"], "material.usda", tmp_path)))
    preview = see_through.GetPrimAtPath(f"/{by_material['m_glass']['id']}/surface").attributes
    assert (preview["inputs:opacity"].default, preview["inputs:roughness"].default) == (0.0, 0.0)
    assert preview["inputs:ior"].default == pytest.approx(1.3)
    pbr = json.loads(blob(blobs, by_material["m_pbr"], "surface.json", tmp_path).read_text())
    assert (pbr["maps"], pbr["tile_scale"], pbr["look_from"]["shader"]) == (
        {"albedo": "albedo.png", "orm": "orm.png"}, [2.0, 2.0], "OmniPBR"), pbr
    assert pbr["opacity"] == {"shader": "OmniPBR", "constant": 0.5, "map": None, "mode": "mono_average",
                              "threshold": 0.25}, pbr
    pbr_look = Sdf.Layer.FindOrOpen(str(blob(blobs, by_material["m_pbr"], "material.usda", tmp_path)))
    pbr_inputs = pbr_look.GetPrimAtPath(f"/{by_material['m_pbr']['id']}/surface").attributes
    assert (pbr_inputs["inputs:opacity"].default, pbr_inputs["inputs:opacityThreshold"].default) == (0.5, 0.25)
    assert pbr_inputs["inputs:diffuseColor"].connectionPathList.GetAddedOrExplicitItems()
    cutout = json.loads(blob(blobs, by_material["m_cutout"], "surface.json", tmp_path).read_text())
    assert cutout["maps"]["opacity"] == "opacity.png" and cutout["opacity"]["map"] and "look_from" not in cutout
    cut_look = Sdf.Layer.FindOrOpen(str(blob(blobs, by_material["m_cutout"], "material.usda", tmp_path)))
    wired = cut_look.GetPrimAtPath(f"/{by_material['m_cutout']['id']}/surface").attributes["inputs:opacity"]
    assert [str(path) for path in wired.connectionPathList.GetAddedOrExplicitItems()] == [
        f"/{by_material['m_cutout']['id']}/opacity.outputs:a"]
    assert "opacity" not in tiled and "look_from" not in tiled
    assert not [name for name in entries.load_address(root, "model/demo_bowl_b01@1")["files"] if name.endswith(".csv")]
    look = Sdf.Layer.FindOrOpen(str(blob(blobs, by_material["m_plastic_black"], "material.usda", tmp_path)))
    normal = look.GetPrimAtPath(f"/{by_material['m_plastic_black']['id']}/normal")
    assert normal.attributes["inputs:scale"].default[1] < 0 < normal.attributes["inputs:bias"].default[1]
    assert by_material["m_paint"]["licence"]["source"] == "example_assets" and "licence" in by_material["m_plastic_black"]
    assert [made for made in by_material["m_paint"]["provenance"]["made_with"] if made.get("carried")]
    assert sorted(by_material["m_plastic_black"]["files"]) == ["albedo.png", "material.usda", "normal.png", "orm.png",
                                                              "surface.json"]
    udim = by_material["m_panel"]
    assert sorted(udim["files"]) == ["albedo.1001.png", "albedo.1002.png", "material.usda", "surface.json"], udim
    udim_record = json.loads(blob(blobs, udim, "surface.json", tmp_path).read_text())
    assert udim_record["maps"] == {"albedo": "albedo.<UDIM>.png"}
    tiles = Sdf.Layer.FindOrOpen(str(blob(blobs, udim, "material.usda", tmp_path)))
    assert tiles.GetPrimAtPath(f"/{udim['id']}/albedo").attributes["inputs:file"].default.path == "./albedo.<UDIM>.png"
    bowl = entries.load_address(root, "model/demo_bowl_b01@1")
    assert by_material["m_plastic_black"]["address"] in bowl["requires"]
    named = Sdf.Layer.FindOrOpen(str(blob(blobs, model, "payloads/materials.usda", tmp_path)))
    assert named.GetPrimAtPath("/RootNode/Materials/m_logo").attributes["score:surface"].default == \
        by_material["m_logo"]["address"]
    records = [entries.load_address(root, address) for address in report["new"]]
    faults = (verify.check_schemas(root, records) + verify.check_blobs(records, blobs, True)
              + verify.check_admission(root, records, sources) + verify.check_taken_in(blobs, records)
              + verify.check_usd(root, blobs, records))
    assert faults == []
    hub = take_in_hub.HubSource(FolderApi(repository), REPO, REVISION)
    saved = take_in_hub.take_in_hub(root, blobs, hub, sources=sources, save=True, jobs=2)
    assert saved["saved"] and all(each["pushed"] for each in saved["saved"]), saved["saved"]
    on_main = git(tmp_path / "origin.git", "ls-tree", "-r", "--name-only", "main", "entries").split()
    assert sorted({"entries/{0}/{1}/{2}/entry.json".format(*entries.parse_address(address))
                   for address in report["new"]} - set(on_main)) == []
    (repository / "bowl_b01/assets_usd/payloads/base.usda").write_text(
        (repository / "bowl_b01/assets_usd/payloads/base.usda").read_text().replace("A bowl", "A deep bowl"))
    shutil.rmtree(repository / "bowl_b01/.metadata")
    billed(repository / "bowl_b01", "assets_usd/sm_bowl_b01_01.usda")
    again = take_in_hub.take_in_hub(root, blobs, take_in_hub.HubSource(FolderApi(repository), REPO, REVISION),
                                    sources=sources, only=["bowl_b01", "crate_A01"], save=True)
    assert again["unchanged"] == models[:2] and not again["new"] and not again["gaps"]
    retaken = take_in_hub.take_in_hub(root, blobs, take_in_hub.HubSource(FolderApi(repository), REPO, REVISION),
                                      sources=sources, only=["bowl_b01"], retake=True)
    assert retaken["changed draft"] == ["model/demo_bowl_b01@1"], retaken
    assert entries.load_address(root, "model/demo_bowl_b01@1")["tags"]["description"] == ["A deep bowl"]


def blob(blobs, record, name, folder):
    """One file of an entry got from the blob store."""
    path = pathlib.Path(folder) / "got" / record["id"] / name
    blobs.get(record["files"][name]["sha256"], path)
    return path


class RefusingApi:
    """An HfApi refusing each download first with the refusals given ((status, named wait), or a network fault to
    raise), then answering."""

    def __init__(self, refusals):
        self.refusals = list(refusals)

    def hf_hub_download(self, repo_id, filename, *, repo_type=None, revision=None, local_dir=None):
        if self.refusals and isinstance(self.refusals[0], Exception):
            raise self.refusals.pop(0)
        if self.refusals:
            status, named = self.refusals.pop(0)
            response = httpx.Response(status, headers={"Retry-After": named} if named else {},
                                      request=httpx.Request("GET", "https://example.org/x"))
            raise HfHubHTTPError("refused", response=response)
        target = pathlib.Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"four")
        return str(target)


def test_a_reader_waits_for_the_window_and_after_a_refusal(tmp_path):
    """Red when: the pacer lets a full window through; a 429 or 5xx is not tried again after its named wait or the
    doubled back-off; a passing network fault with no status (a read timeout, a dropped connection) is raised at once
    instead of tried again (2026-11-22: one ReadTimeout ended a 173-minute take-in); a 404 is tried again."""
    now, waits = [0.0], []

    def pacer(limit):
        return take_in_hub.Pacer(limit=limit, window=300.0, clock=lambda: now[0],
                                 sleep=lambda seconds: waits.append(seconds) or now.__setitem__(0, now[0] + seconds))
    counting = pacer(2)
    for _ in range(3):
        counting.tick()
    assert waits == [300.0]
    assert take_in_hub.pacer_for_token("hf_x").limit is None
    assert take_in_hub.pacer_for_token(None).limit == take_in_hub.PACE_LIMIT
    waits.clear()
    source = take_in_hub.HubSource(RefusingApi([(429, "7"), (503, None)]), REPO, REVISION, pacer(None))
    source.download("a/b.bin", tmp_path / "got/b.bin")
    assert (tmp_path / "got/b.bin").read_bytes() == b"four"
    assert waits == [7.0, take_in_hub.BACK_OFF * 2], waits  # the named wait, then the doubled back-off
    assert source.measure == {"requests": 3, "throttled": 1, "retries": 1, "bytes": 4}, source.measure
    waits.clear()
    request = httpx.Request("GET", "https://example.org/x")
    faults = [httpx.ReadTimeout("The read operation timed out", request=request),
              httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)]
    faulted = take_in_hub.HubSource(RefusingApi(faults), REPO, REVISION, pacer(None))
    faulted.download("a/d.bin", tmp_path / "got/d.bin")
    assert waits == [take_in_hub.BACK_OFF, take_in_hub.BACK_OFF * 2], waits  # no status: the doubled back-off
    assert faulted.measure["retries"] == 2 and faulted.measure["requests"] == 3, faulted.measure
    with pytest.raises(HfHubHTTPError):
        take_in_hub.HubSource(RefusingApi([(404, None)]), REPO, REVISION, pacer(None)).download("a/c.bin",
                                                                                           tmp_path / "c.bin")


def test_a_save_that_does_not_reach_origin_stops_the_run(tmp_path, monkeypatch):
    """Red when: a batch whose save stayed local (its push refused) is only noted in the report and the run goes on
    (2026-11-22: 8 of 19 batch commits never reached origin while the run reported them saved), or the stop does not
    name the commit left behind."""
    from score_lib import handover
    blocks = iter([{"committed": "a" * 40, "pushed": True, "why": ""},
                   {"committed": "b" * 40, "pushed": False, "why": "! [rejected] HEAD -> main (fetch first)"}])
    monkeypatch.setattr(handover, "saved", lambda root, message, paths, target: next(blocks))
    monkeypatch.setattr(take_in_hub.index, "rebuild", lambda root: None)
    report = {}
    save = take_in_hub.saver(tmp_path, REPO, REVISION, report)
    save(["model/one@1"])
    with pytest.raises(handover.HandoverError, match="b" * 40 + r".*rejected"):
        save(["model/two@1"])
    assert [block["pushed"] for block in report["saved"]] == [True, False]


def finish(name, colour, roughness):
    """A Material spec: a UsdPreviewSurface of one grey (linear) and a roughness."""
    return f"""
def Material "{name}"
{{
    token outputs:surface.connect = </RootNode/Materials/{name}/preview.outputs:surface>

    def Shader "preview"
    {{
        uniform token info:id = "UsdPreviewSurface"
        color3f inputs:diffuseColor = ({colour}, {colour}, {colour})
        float inputs:roughness = {roughness}
        token outputs:surface
    }}
}}"""


def finished(folder, asset, name, colour, roughness):
    """An asset of one root layer holding one finish, billed."""
    usd = folder / asset / "assets_usd"
    usd.mkdir(parents=True)
    (usd / f"sm_{asset}.usda").write_text(layer([finish(name, colour, roughness)], documentation=f"A {asset}"))
    billed(folder / asset, f"assets_usd/sm_{asset}.usda")


@pytest.mark.parametrize("taken_apart, name, colour, roughness, takes", [
    (False, "m_finish", 0.513, 0.5, "b"),  # within delta E 1 of both a and b: b, required twice, a once
    (False, "m_finish", 0.505, 0.51, "a"),  # within delta E 1 of a alone, its roughness 0.01 apart
    (True, "m_finish", 0.505, 0.5, "a"),  # taken apart before, retaken: the alike surface takes its place
    (False, "m_finish", 0.8, 0.5, None),  # a colour a person sees apart: a surface of its own
    (False, "m_finish", 0.5, 0.6, None),  # a roughness 0.1 apart: a surface of its own
    (False, "m_other", 0.505, 0.5, None),  # alike, but another material's name: a surface of its own
])
def test_a_material_alike_to_the_eye_takes_the_most_required_surface(tmp_path, monkeypatch, taken_apart, name, colour,
                                                                      roughness, takes):
    """Red when: a material of the same name whose constants differ from a live surface's by less than a person sees
    (delta E under 1, roughness within 0.02) gets a surface of its own (score #155: one NVIDIA glass became 37
    surfaces), a retake leaves the one it was taken apart into, the one taken is not the most required of those
    alike, or a colour or roughness a person sees apart, or another name, is merged."""
    repository = tmp_path / "repository"
    for asset, colour_of in (("a_01", 0.5), ("b_01", 0.526), ("d_01", 0.526)):  # a and b delta E 1.57 apart
        finished(repository, asset, "m_finish", colour_of, 0.5)
    finished(repository, "c_01", name, colour, roughness)
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    (tmp_path / "sources.toml").write_text(SOURCES)
    sources, blobs = Sources.load(tmp_path / "sources.toml"), from_environment()

    def taken(only, retake=False):
        hub = take_in_hub.HubSource(FolderApi(repository), REPO, REVISION)
        return take_in_hub.take_in_hub(root, blobs, hub, sources=sources, only=only, retake=retake)
    with monkeypatch.context() as patched:
        patched.setattr(take_in_hub, "COLOUR_DELTA_E", 0.0, raising=False)
        patched.setattr(take_in_hub, "LOOK_TOLERANCES", {}, raising=False)
        first = taken(["a_01", "b_01", "d_01", *(["c_01"] if taken_apart else [])])
    assert not first["gaps"], first
    required = {asset: entries.load_address(root, f"model/demo_{asset}_01@1")["requires"] for asset in ("a", "b", "d")}
    assert required["b"] == required["d"] != required["a"], required
    before = entries.load_address(root, "model/demo_c_01@1")["requires"] if taken_apart else None
    report = taken(["c_01"], retake=taken_apart)
    assert not report["gaps"], report
    now = entries.load_address(root, "model/demo_c_01@1")["requires"]
    if takes:
        assert now == required[takes] and not [address for address in report["new"] if address.startswith("surface/")]
        assert before is None or before != now
    else:
        assert len(now) == 1 and now[0] not in (required["a"] + required["b"]), (now, required)
