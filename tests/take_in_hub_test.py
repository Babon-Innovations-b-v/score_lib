"""score-lib take-in hub: an adopted set's assets at one pinned revision taken in as model and surface entries under
their own licence, their bytes in the blob store and none left on disk, and the pacing of an anonymous reader."""
import hashlib
import json
import pathlib
import shutil

import pytest
from huggingface_hub.hf_api import RepoFile, RepoFolder

from conftest import png, usd_text
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


PHYSICS_ONLY = '\ndef Material "physics_mat_plastic"\n{\n}'  # a physics material: empty, its API in another layer
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
        material("m_logo", {"albedo": "crate_logo_alb.png"}), material("m_paint", constant="(0.8, 0.1, 0.1)")]))
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
    return "assets_usd/sm_bowl_b01_01.usda"


def broken(folder):
    """An asset holding a file whose ending no role knows."""
    usd = folder / "assets_usd"
    usd.mkdir(parents=True)
    (usd / "sm_broken_c01_01.usda").write_text('#usda 1.0\n(\n    defaultPrim = "RootNode"\n)\n\n'
                                               'def Xform "RootNode"\n{\n}\n')
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
    """A dataset repository at one revision read from a folder, as huggingface_hub's HfApi reads one."""

    def __init__(self, folder):
        self.folder = pathlib.Path(folder)
        self.revisions, self.downloads = set(), []

    def list_repo_tree(self, repo_id, path_in_repo=None, *, recursive=False, revision=None, repo_type=None):
        self.revisions.add(revision)
        for path in sorted(self.folder.iterdir()):
            yield (RepoFolder(path=path.name, oid="0") if path.is_dir()
                   else RepoFile(path=path.name, size=path.stat().st_size, oid="0"))

    def hf_hub_download(self, repo_id, filename, *, repo_type=None, revision=None, local_dir=None):
        self.revisions.add(revision)
        self.downloads.append(filename)
        target = pathlib.Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.folder / filename, target)
        return str(target)


@pytest.fixture
def repository(tmp_path):
    folder = tmp_path / "repository"
    for name, made in (("crate_A01", crate), ("bowl_b01", bowl), ("broken_c01", broken)):
        (folder / name).mkdir(parents=True)
        billed(folder / name, made(folder / name))
    (folder / "README.md").write_text("license: cc-by-4.0\n")
    return folder


def test_an_adopted_set_is_taken_in_whole_under_its_licence(repository, tmp_path):
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    (tmp_path / "sources.toml").write_text(SOURCES)
    sources = Sources.load(tmp_path / "sources.toml")
    blobs = from_environment()
    cache = tmp_path / "caches"
    cache.mkdir()
    api = FolderApi(repository)
    report = take_in_hub.take_in_hub(root, blobs, take_in_hub.HubSource(api, REPO, REVISION), sources=sources,
                                     cache_bytes=1, cache_root=cache)
    assert api.revisions == {REVISION} and not list(cache.iterdir())
    assert [gap["asset"] for gap in report["gaps"]] == ["broken_c01"] and "notes.xyz" in report["gaps"][0]["why"]
    models = sorted(address for address in report["new"] if address.startswith("model/"))
    assert models == ["model/demo_bowl_b01@1", "model/demo_crate_a01@1"], report
    surfaces = [entries.load_address(root, address) for address in report["new"] if address.startswith("surface/")]
    assert len(surfaces) == 3, report  # the shared tile set once, the logo painted for the crate, the constant paint
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
    assert sorted(model["requires"]) == sorted(record["address"] for record in surfaces)
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
    look = Sdf.Layer.FindOrOpen(str(blob(blobs, by_material["m_plastic_black"], "material.usda", tmp_path)))
    normal = look.GetPrimAtPath(f"/{by_material['m_plastic_black']['id']}/normal")
    assert normal.attributes["inputs:scale"].default[1] < 0 < normal.attributes["inputs:bias"].default[1]
    assert "licence" not in by_material["m_paint"] and "licence" in by_material["m_plastic_black"]
    assert sorted(by_material["m_plastic_black"]["files"]) == ["albedo.png", "material.usda", "normal.png", "orm.png",
                                                              "surface.json"]
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
    again = take_in_hub.take_in_hub(root, blobs, take_in_hub.HubSource(FolderApi(repository), REPO, REVISION),
                                    sources=sources, only=["bowl_b01", "crate_A01"])
    assert sorted(again["unchanged"]) == models and not again["new"] and not again["gaps"]


def blob(blobs, record, name, folder):
    """One file of an entry got from the blob store."""
    path = pathlib.Path(folder) / "got" / record["id"] / name
    blobs.get(record["files"][name]["sha256"], path)
    return path


def test_an_anonymous_reader_waits_for_the_window():
    now, waits = [0.0], []
    pacer = take_in_hub.Pacer(limit=2, window=300.0, clock=lambda: now[0], sleep=lambda seconds: waits.append(seconds)
                              or now.__setitem__(0, now[0] + seconds))
    for _ in range(3):
        pacer.tick()
    assert waits == [300.0]
    assert take_in_hub.pacer_for_token("hf_x") is None and take_in_hub.pacer_for_token(None) is not None
