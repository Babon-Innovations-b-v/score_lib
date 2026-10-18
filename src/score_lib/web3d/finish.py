"""Every surface's finish from the shared surface library (the owner's hard rule, 2026-10-18: every finish comes from the
library; never a picture's own texture, a model's baked-in colour or a default grey).

A surface of the stage gets, in this order:

    library        a material named for a library variant (a place's Library, a ground's Outside/Looks, a
                   character part the dress laid a library surface on): that variant's finish from its surface entry
    library bake   a material whose every picture is a map the library bake made (score's tools/props/library: the
                   recipe of each part's library variant baked into `<set>_base_color`, `_metal_roughness` and
                   `_normal`): those maps, which are the library's own colour, roughness, metal and relief
    replaced       anything else (a picture's own texture, a model's own colour, no material at all): the library
                   variant nearest the colour the surface showed (CIELAB distance; glowing and still surfaces each
                   matched among their own kind, printed labels never), its finish as the library holds it

Every resolution is counted and every replacement listed (the surface, the variant chosen, the distance), so what
did not trace to the library stays visible.
"""
import collections
import json
import pathlib
import posixpath
import shutil
import tempfile

import numpy as np

from score_lib import entry as entries

BAKE_MAPS = ("_base_color", "_metal_roughness", "_normal")
# A glow part (a model's `_glow` half) reads the same baked set.
BAKE_GLOW = ("_glow",)
FINISH_INPUTS = ("diffuseColor", "roughness", "metallic", "emissiveColor", "opacity", "opacityThreshold")
NEVER_MATCHED = ("print",)  # printed labels and notices: their colour is a token, their content a picture
GREY = (0.18, 0.18, 0.18)  # what an unpainted surface shows


def srgb_to_linear(values):
    values = np.asarray(values, dtype=np.float64)
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def linear_to_lab(rgb):
    """CIELAB (D65) of linear sRGB colours, shape (..., 3)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    matrix = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
    xyz = rgb @ matrix.T / np.array([0.95047, 1.0, 1.08883])
    cubed = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * cubed[..., 1] - 16, 500 * (cubed[..., 0] - cubed[..., 1]),
                     200 * (cubed[..., 1] - cubed[..., 2])], axis=-1)


def material_constants(path):
    """{input: value} of the first UsdPreviewSurface of a material file (values only)."""
    from pxr import Usd, UsdShade
    stage = Usd.Stage.Open(str(path))
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            found = {}
            for name in FINISH_INPUTS:
                value = shader.GetInput(name).Get() if shader.GetInput(name) else None
                if value is not None:
                    found[name] = [float(part) for part in value] if hasattr(value, "__len__") else float(value)
            return found
    return {}


class Library:
    """The surface library as its entries hold it: each variant's finish (from its material.usda) and family."""

    def __init__(self, root, blobs):
        self.finishes, self.families = {}, {}
        folder = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-library-"))
        try:
            for record in newest_surfaces(root):
                name = record["id"].split(".")[0]
                if name in self.finishes or "material.usda" not in record["files"]:
                    continue
                target = folder / f"{record['id']}.usda"
                blobs.get(record["files"]["material.usda"]["sha256"], target)
                self.finishes[name] = material_constants(target)
                self.families[name] = (record.get("tags", {}).get("family") or [""])[0]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.names = sorted(self.finishes)
        self.lab = {name: linear_to_lab(self.finishes[name].get("diffuseColor", GREY)) for name in self.names}

    def inputs(self, name):
        """A variant's finish as glTF material inputs (channel_source form)."""
        return {key: ["value", value] for key, value in self.finishes[name].items()}

    def glows(self, name):
        return max(self.finishes[name].get("emissiveColor", [0.0])) > 0

    def nearest(self, colour, glowing):
        """(variant, CIELAB distance) of the library variant nearest a linear colour, among the glowing or the still
        ones as the surface is, printed labels left out."""
        target = linear_to_lab(colour)
        pool = [name for name in self.names if self.families[name] not in NEVER_MATCHED and self.glows(name) == glowing]
        pool = pool or self.names
        distances = {name: float(np.linalg.norm(self.lab[name] - target)) for name in pool}
        chosen = min(sorted(distances), key=distances.get)
        return chosen, round(distances[chosen], 2)


def newest_surfaces(root):
    """The newest, not withdrawn, version of every surface entry, the place-free id first."""
    newest = {}
    for record in entries.all_entries(root, "surface"):
        if record["state"] != "withdrawn":
            newest[record["id"]] = record
    return sorted(newest.values(), key=lambda record: ("." in record["id"], record["id"]))


def channel_source(shader, name):
    """A UsdPreviewSurface input: ["texture", file, channel] when a picture drives it, else ["value", value]; None
    when unauthored."""
    from pxr import UsdShade
    found = shader.GetInput(name)
    if not found:
        return None
    sources = found.GetConnectedSources()[0]
    if sources:
        asset = UsdShade.Shader(sources[0].source.GetPrim()).GetInput("file").Get()
        path = asset.resolvedPath or asset.path if asset else ""
        return ["texture", path, str(sources[0].sourceName)] if path else None
    value = found.Get()
    if value is None:
        return None
    return ["value", [float(part) for part in value] if hasattr(value, "__len__") else float(value)]


def stage_inputs(material):
    """A material's UsdPreviewSurface inputs that glTF can carry, by name, or {}."""
    from pxr import UsdShade
    shader = material.ComputeSurfaceSource()[0] if material else None
    if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
        return {}
    names = FINISH_INPUTS + ("normal",)
    found = {name: channel_source(UsdShade.Shader(shader.GetPrim()), name) for name in names}
    return {name: source for name, source in found.items() if source is not None}


def baked_map(path):
    """Whether a picture is one of the library bake's maps, by its name's end (score's tools/props/library/stored.py
    kind_of), a glow part's included."""
    stem = posixpath.splitext(posixpath.basename(path))[0]
    return stem.endswith(BAKE_MAPS + BAKE_GLOW) and pathlib.Path(path).is_file()


def library_baked(inputs):
    """Whether a material's pictures are all the library bake's maps (and it has one)."""
    pictures = [source[1] for source in inputs.values() if source[0] == "texture"]
    return bool(pictures) and all(baked_map(path) for path in pictures)


class Resolver:
    """Resolves each surface of a stage to its library finish and keeps the count of how."""

    def __init__(self, library, parts=None):
        self.library = library
        self.parts = parts or {}  # a character's score:part -> the library surface its dress laid on it
        self.counts = collections.Counter()
        self.replaced = {}

    def named(self, material):
        """The library variant a material is named for, or None."""
        if not material:
            return None
        prim = material.GetPrim()
        part = prim.GetAttribute("score:part").Get() if prim.HasAttribute("score:part") else None
        if part and self.parts.get(part) in self.library.finishes:
            return self.parts[part]
        return prim.GetName() if prim.GetName() in self.library.finishes else None

    def resolve(self, surface, material, corners, chosen):
        """(material name, glTF inputs) of one surface of a mesh."""
        variant = self.named(material)
        if variant:
            self.counts["library"] += 1
            return variant, self.library.inputs(variant)
        inputs = stage_inputs(material)
        if library_baked(inputs):
            self.counts["library bake"] += 1
            return surface, {key: value for key, value in inputs.items() if key != "opacity" or value[0] == "value"}
        colour = shown_colour(inputs, corners, chosen)
        glowing = "emissiveColor" in inputs and (inputs["emissiveColor"][0] == "texture"
                                                 or max(inputs["emissiveColor"][1]) > 0)
        variant, distance = self.library.nearest(colour, glowing)
        self.counts["replaced"] += 1
        key = material.GetPrim().GetPath().pathString if material else f"{surface} (unpainted)"
        self.replaced[key] = {"by": variant, "distance": distance,
                              "colour": [round(float(value), 4) for value in colour]}
        return variant, self.library.inputs(variant)

    def without_pictures(self, name):
        """A baked surface on a mesh with no texture coordinates: its library variant cannot be told, so the grey
        nearest variant stands in (counted as replaced)."""
        variant, distance = self.library.nearest(GREY, False)
        self.counts["library bake"] -= 1
        self.counts["replaced"] += 1
        self.replaced[f"{name} (no texture coordinates)"] = {"by": variant, "distance": distance,
                                                            "colour": list(GREY)}
        return self.library.inputs(variant)

    def summary(self):
        """{counts, replaced} for the entry's web3d record."""
        return {"finishes": dict(sorted(self.counts.items())),
                "replaced": dict(sorted(self.replaced.items()))}


def shown_colour(inputs, corners, chosen):
    """The linear colour a surface showed: its picture's mean at its corners' texture coordinates, its constant, its
    display colour, else the grey of an unpainted surface."""
    diffuse = inputs.get("diffuseColor")
    if diffuse and diffuse[0] == "texture" and corners.get("texcoords") is not None:
        return picture_mean(diffuse[1], corners["texcoords"][np.unique(corners["triangles"][chosen])])
    if diffuse and diffuse[0] == "value":
        return np.asarray(diffuse[1][:3], dtype=np.float64)
    if corners.get("colours") is not None:
        return corners["colours"][np.unique(corners["triangles"][chosen])].mean(axis=0)
    return np.asarray(GREY)


PICTURES = {}


def picture_mean(path, uvs):
    """The mean linear colour of a picture read at texture coordinates (glTF's: v from the top)."""
    from PIL import Image
    if path not in PICTURES:
        if len(PICTURES) > 64:
            PICTURES.clear()
        with Image.open(path) as opened:
            PICTURES[path] = srgb_to_linear(np.asarray(opened.convert("RGB"), dtype=np.float64) / 255.0)
    pixels = PICTURES[path]
    height, width = pixels.shape[:2]
    column = np.clip((np.mod(uvs[:, 0], 1.0) * width).astype(np.int64), 0, width - 1)
    row = np.clip((np.mod(uvs[:, 1], 1.0) * height).astype(np.int64), 0, height - 1)
    return pixels[row, column].mean(axis=0)


def dress_parts(record, blobs):
    """A dressed character's {score:part: library surface} from its dress.json, or {}."""
    described = record["files"].get("dress.json")
    if not described:
        return {}
    folder = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-dress-"))
    try:
        blobs.get(described["sha256"], folder / "dress.json")
        dress = json.loads((folder / "dress.json").read_text())
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {part: surface["library"] for part, surface in (dress.get("surfaces") or {}).items()
            if surface.get("library")}
