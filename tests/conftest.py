"""Test setup: PROPS_HOME, SCORE_LIB_BLOBS_DIR and SCORE_STORE point at temp folders; a small SCORE world (a store,
a score repository with one commit and a library root) is built per test by `world`."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest
import soundfile
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).parent))

LIBRARY = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_TEXT = ("made for the first world (the game 2099) by this framework's routes; released with the repository "
                 "under MIT, though whether a model made with AI tools can carry copyright at all is an open question")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test's store, blob store and PROPS_HOME in temp folders."""
    monkeypatch.setenv("PROPS_HOME", str(tmp_path / "props_home"))
    monkeypatch.setenv("SCORE_LIB_BLOBS_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("SCORE_STORE", str(tmp_path / "store" / "objects"))
    for name in ("SCORE_LIB_S3_ENDPOINT", "SCORE_LIB_S3_BUCKET", "SCORE_LIB_CACHE"):
        monkeypatch.delenv(name, raising=False)
    yield


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class FixtureStore:
    """A SCORE store's objects folder written by the test: outputs, accepts, records, judgements."""

    def __init__(self, objects):
        self.objects = pathlib.Path(objects)
        for area in ("blobs", "outputs", "accepts", "rejects", "records", "judgements"):
            (self.objects / area).mkdir(parents=True, exist_ok=True)

    def put_output(self, files):
        """Store {name: bytes} as an output; its hash."""
        listing = {}
        for name, data in sorted(files.items()):
            digest = sha256(data)
            (self.objects / "blobs" / digest).write_bytes(data)
            listing[name] = digest
        output = sha256(json.dumps(listing, sort_keys=True).encode())
        (self.objects / "outputs" / f"{output}.json").write_text(json.dumps(listing, indent=1))
        return output

    def timed(self, area, node, output, at, key="k" * 64, by="runner"):
        folder = self.objects / area / node
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{at}-{output[:12]}.json").write_text(json.dumps(
            {"node": node, "output": output, "key": key, "by": by, "at": at}))

    def accept(self, node, output, at, key=None, record=None):
        key = key or sha256(f"{node}/{output}".encode())
        self.timed("accepts", node, output, at, key)
        folder = self.objects / "records" / node
        folder.mkdir(parents=True, exist_ok=True)
        base = {"node": node, "key": key, "output": output, "code": "c" * 64, "seed": 7, "models": {}}
        (folder / f"{key}.json").write_text(json.dumps(base | (record or {})))
        return key

    def reject(self, node, output, at):
        self.timed("rejects", node, output, at)

    def judge(self, node, key, verdicts, at):
        folder = self.objects / "judgements" / node / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "evidence.json").write_text(json.dumps({"at": at, "answer": "fail", "verdicts": verdicts}))


def usd_text(layer_text, extension, folder):
    """The bytes of a USD file of a format made from usda text."""
    from pxr import Sdf
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.ImportFromString(layer_text)
    path = pathlib.Path(folder) / f"made{extension}"
    layer.Export(str(path))
    return path.read_bytes()


def png(colour, size=8):
    path = pathlib.Path(os.environ["PROPS_HOME"]) / "png.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), colour).save(path)
    return path.read_bytes()


def wav():
    path = pathlib.Path(os.environ["PROPS_HOME"]) / "hum.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = 0.2 * np.sin(np.linspace(0, 200 * np.pi, 4800)).astype("float32")
    soundfile.write(str(path), samples, 48000)
    return path.read_bytes()


CRATE = """#usda 1.0
(
    defaultPrim = "crate_1"
    metersPerUnit = 1
    upAxis = "Y"
)

def Xform "crate_1" (
    kind = "component"
)
{
    def Scope "Looks"
    {
        def Material "look_0"
        {
            token outputs:surface.connect = </crate_1/Looks/look_0/surface.outputs:surface>

            def Shader "surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor.connect = </crate_1/Looks/look_0/base_color.outputs:rgb>
                token outputs:surface
            }

            def Shader "uv"
            {
                uniform token info:id = "UsdPrimvarReader_float2"
                string inputs:varname = "st"
                float2 outputs:result
            }

            def Shader "base_color"
            {
                uniform token info:id = "UsdUVTexture"
                asset inputs:file = @./textures/crate_1_base_color.png@
                float2 inputs:st.connect = </crate_1/Looks/look_0/uv.outputs:result>
                float3 outputs:rgb
            }
        }
    }

    def Mesh "geo" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        int[] faceVertexCounts = [4, 4, 4]
        int[] faceVertexIndices = [0, 1, 2, 3, 4, 5, 6, 7, 0, 4, 7, 3]
        rel material:binding = </crate_1/Looks/look_0>
        point3f[] points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
        texCoord2f[] primvars:st = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0), (1, 0), (1, 1), (0, 1)] (
            interpolation = "vertex"
        )
    }
}
"""

BASE = """#usda 1.0

def Xform "room" (
    kind = "assembly"
)
{
    def Xform "Objects"
    {
        def Xform "crate_1" (
            prepend references = @../assets/crate_1.usdc@
        )
        {
            custom string score:model = "crate_1"
            custom string score:model_sha256 = "%s"
            custom string score:row = "crate"
            custom string score:kind = "room_crate"
            custom string score:name = "a crate"
        }
    }

    def Scope "Library"
    {
        def Material "panel_paint"
        {
            token outputs:surface.connect = </room/Library/panel_paint/surface.outputs:surface>
            custom string score:family = "painted"

            def Shader "surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor = (0.3, 0.4, 0.5)
                float inputs:roughness = 0.4
                token outputs:surface
            }
        }
    }

    def DomeLight "Environment"
    {
        custom bool score:inside = 1
        custom string score:from = "sky record (data/definitions/sky.json) moon indoor"
    }
}
"""

EFFECTS = """#usda 1.0
(
    framesPerSecond = 24
    timeCodesPerSecond = 24
)

def "room"
{
    def Scope "Effects"
    {
        def Xform "steam"
        {
            custom token score:effect:kind = "steam_jet"
            custom int score:effect:count = 40
            custom float score:effect:lifetime = 1.3
            custom color3f score:effect:colour = (0.9, 0.9, 0.9)
            custom string score:effect:from = "a measured source"

            def Points "steam_points" (
                clips = {
                    dictionary default = {
                        double2[] active = [(0, 0)]
                        asset[] assetPaths = [@../assets/effects/steam.usdc@]
                        string primPath = "/Effect"
                        double2[] times = [(0, 0), (24, 24)]
                    }
                }
            )
            {
            }
        }
    }
}
"""

CLIP = """#usda 1.0

def Points "Effect"
{
    point3f[] points.timeSamples = {
        0: [(0, 0, 0), (0, 1, 0)],
        24: [(0, 0.5, 0), (0.2, 1.5, 0)],
    }
    float[] primvars:displayOpacity.timeSamples = {
        0: [0.5, 0.5],
        24: [0.25, 0.25],
    }
}
"""

SOUND = """#usda 1.0

over "room"
{
    def "Sound"
    {
        def Scope "Room"
        {
            def SpatialAudio "hum"
            {
                uniform asset filePath = @../assets/sound/generated/places/hum.wav@
                custom string score:sound:name = "hum"
                custom float score:sound:volumeDb = -21
            }
        }
    }
}
"""

ROOT = """#usda 1.0
(
    defaultPrim = "room"
    metersPerUnit = 1
    upAxis = "Y"
    subLayers = [
        @./layers/edit.usda@,
        @./layers/sound.usda@,
        @./layers/effects.usda@,
        @./layers/base.usda@
    ]
    timeCodesPerSecond = 24
)
"""

EDIT = """#usda 1.0
(
    doc = "The creator's changes to this place."
)
"""

ALGORITHM = '''"""A demo algorithm."""
import json

import helper

RATE = 24.0
STEP = 1.0 / 30.0
NAMES = tuple(str(step) for step in range(3))


def write(out, preset=None):
    return helper.double(RATE)
'''

HELPER = '''def double(value):
    return value * 2
'''

ALGORITHM_TEST = '''import algo


def test_write():
    assert algo.write("x") == 48.0
'''


def git(folder, *arguments):
    return subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True,
                          check=True).stdout.strip()


def write_files(folder, files):
    for name, data in files.items():
        path = pathlib.Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())


def make_repo(folder, manifest_rules):
    """A score repository with the data files the exporters read, committed once; its commit."""
    kit = {"room": "room", "models": {"crate_1": {"route": "code", "glows": False}},
           "kinds": {"room_crate": {"group": "floors"}},
           "pieces": [{"kind": "room_crate", "at": [0, 0, 0], "x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1],
                       "size": [1, 1, 1], "model": "crate_1"}]}
    library = {"families": {"painted": {"variants": {"panel_paint": {"recipe": "painted_metal", "token": "hull",
                                                                    "roughness": 0.4,
                                                                    "reference": {"picture": "WORK/x.jpg"}},
                                                    "unused_paint": {"recipe": "painted_metal", "token": "deck"}}}}}
    files = {"data/assets/world1.json": json.dumps({"licences": manifest_rules, "files": {}}),
             "data/kit/room.json": json.dumps(kit), "data/library/materials.json": json.dumps(library),
             "data/checks/gaps/room.json": json.dumps([{"name": "a gap"}]),
             "data/demo/preset.json": json.dumps({"rate": 24}),
             "data/demo/outside.json": json.dumps({"not": "ours"}),
             "tools/demo/algo.py": ALGORITHM, "tools/demo/helper.py": HELPER, "tools/demo/algo_test.py": ALGORITHM_TEST}
    folder.mkdir(parents=True)
    git(folder, "init", "-q", "-b", "main")
    write_files(folder, files)
    git(folder, "add", ".")
    git(folder, "-c", "user.name=test", "-c", "user.email=test@example.org", "commit", "-q", "-m", "fixture")
    return git(folder, "rev-parse", "HEAD")


def make_root(folder, commit):
    """A library root: the world toml, the MIT LICENSE, the schemas and two algorithm definitions (the first with an
    outside requirement keeping one preset file out)."""
    (folder / "worlds").mkdir(parents=True)
    (folder / "worlds/world1.toml").write_text(
        f'name = "world1"\nseed = 3\ncommit = "{commit}"\nplaces = ["room"]\n')
    definitions = folder / "definitions/algorithms"
    definitions.mkdir(parents=True)
    (definitions / "demo.toml").write_text(
        'name = "demo"\ntitle = "Demo"\nentry = "algo:write"\nroots = ["tools/demo/algo.py"]\n'
        'search = ["tools/demo"]\nconstants = ["tools/demo/algo.py:RATE", "tools/demo/algo.py:STEP", '
        '"tools/demo/algo.py:NAMES"]\npreset = ["data/demo/*.json"]\nneeds = []\n'
        'tests = ["tools/demo/algo_test.py"]\n\n[[outside]]\nname = "an outside library"\nversion = "1.0"\n'
        'where = "https://example.org/outside"\nwhy = "not MIT"\npaths = ["data/demo/outside.json"]\n')
    (folder / "LICENSE").write_text((LIBRARY / "LICENSE").read_text())
    (definitions / "demo_gpu.toml").write_text(
        'name = "demo_gpu"\ntitle = "Demo on a GPU"\nentry = "algo:write"\nroots = ["tools/demo/algo.py"]\n'
        'search = ["tools/demo"]\nconstants = []\npreset = []\nneeds = ["gpu"]\ntests = []\n')
    (folder / "entries").mkdir()


def assembly_files(folder, model_sha, crate_colour=(200, 120, 40)):
    """The files of a small place stage: a model with a texture, an emitter with a clip, a SpatialAudio and a
    sublayer stack."""
    return {"room.usda": usd_text(ROOT, ".usda", folder), "layers/edit.usda": usd_text(EDIT, ".usda", folder),
            "layers/base.usda": usd_text(BASE % model_sha, ".usda", folder),
            "layers/effects.usda": usd_text(EFFECTS, ".usda", folder),
            "layers/sound.usda": usd_text(SOUND, ".usda", folder),
            "assets/crate_1.usdc": usd_text(CRATE, ".usdc", folder),
            "assets/textures/crate_1_base_color.png": png(crate_colour),
            "assets/effects/steam.usdc": usd_text(CLIP, ".usdc", folder),
            "assets/sound/generated/places/hum.wav": wav(),
            "scene.json": b"{}", "inputs.json": json.dumps({"models": {"crate_1": {"sha256": model_sha}}}).encode(),
            "contract.json": json.dumps({"schema": "openusd-stage", "version": 1}).encode()}


class World:
    """The fixture world: store, repo, library root, and helpers to export and change it."""

    def __init__(self, base, manifest_rules=None):
        self.base = pathlib.Path(base)
        self.store = FixtureStore(os.environ["SCORE_STORE"])
        rules = manifest_rules or [{"match": "models/", "licence": MANIFEST_TEXT, "source": "made by the fixture",
                                    "author": "the tests"}]
        self.repo = self.base / "score"
        self.commit = make_repo(self.repo, rules)
        self.root = self.base / "library"
        make_root(self.root, self.commit)
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.publish_place("2026-01-01T00:00:00+00:00")

    def publish_place(self, at, crate_colour=(200, 120, 40)):
        """Accept a new assembly (and its sound, effects and completion rows) at a time."""
        model_sha = sha256(b"crate gltf")
        files = assembly_files(self.scratch, model_sha, crate_colour)
        sound = self.store.put_output({
            "sound.json": json.dumps({"place": "room", "sounds": {"hum": {
                "files": ["files/sound/generated/places/hum.wav"], "volume_db": -21.0, "bus": "ambience",
                "reach": 0.0, "loops": True, "category": "room", "made_by": "recording",
                "credits": [{"file": "sound/generated/places/hum.wav", "source": "a recording",
                             "author": "someone", "licence": "CC0 1.0"}]}}}).encode(),
            "files/sound/generated/places/hum.wav": files["assets/sound/generated/places/hum.wav"]})
        effects = self.store.put_output({"layers/effects.usda": files["layers/effects.usda"],
                                         "assets/effects/steam.usdc": files["assets/effects/steam.usdc"],
                                         "held.json": b'{"particles": {"steam": 40}}'})
        files["origin.json"] = json.dumps({"layers": {"sound": sound, "effects-layer": effects}}).encode()
        assembly = self.store.put_output(files)
        self.store.accept("sound@room", sound, at)
        self.store.accept("effects-layer@room", effects, at)
        self.assembly = assembly
        self.store.accept("assembly@room", assembly, at)
        complete = self.store.put_output({"manifest.json": json.dumps({"stages": {"assembly": assembly}}).encode()})
        key = self.store.accept("complete@room", complete, at)
        self.store.judge("complete@room", key, {"named gaps": {"answer": "fail"}}, at)

    def context(self):
        from score_lib.admission import Sources
        from score_lib.export import run
        from score_lib.source.repo import Repo
        from score_lib.source.store import Store
        world = run.load_world(self.root, "world1")
        return run.Context(self.root, world, Store(os.environ["SCORE_STORE"]), Repo(self.repo, self.commit),
                           Sources.load(), scratch=self.scratch / "work")

    def export(self, dry_run=False, kinds=None):
        from score_lib.blobs import from_environment
        from score_lib.admission import Sources
        from score_lib.export import run
        context = self.context()
        try:
            return run.export(context, kinds, dry_run, None if dry_run else from_environment())
        finally:
            shutil.rmtree(context.scratch, ignore_errors=True)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)
