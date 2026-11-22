"""effect entries: one entry a distinct effect, its look, however many spots and places show it (score #46). An
emitter of a place's effects-layer output (the one its assembly took, else the accepted one) carries 35
score:effect:* attributes and a baked value clip (spec 4.4); its placement settings (PLACEMENT: where and how much,
fitted to its spot, door or room) stay in the place's layer, as does every placement's own clip, and the entry keeps
one placement's settings and clip as its sample and names every placement (provenance `placements`). Its effect.usda
and clip.usdc state the units their values are in, the place stage's (metres, Y up): a clip SCORE wrote saying none
read as centimetres, the default, and shrank the effect a hundred times (score #156)."""
import hashlib
import json
import os

from pxr import Sdf, UsdGeom

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.admission import SCORE
from score_lib.preview import points
from score_lib.rewrite import asset_paths, copy_prim, export, joined, rewrite

PREFIX = "score:effect:"
LAYER = "layers/effects.usda"
# The settings fitted to one placement (its spot's seed and wind, its door's or room's size, the place's pump cycle
# and the density over that size, the source lines it was read from): the place's, never the effect's.
PLACEMENT = ("from", "seed", "wind", "emitter_size", "direction", "count", "rate", "loop", "trigger_every",
             "explosiveness", "opacity", "size")
# A SCORE place stage's units (score/contracts: metres, Y up), which its layers and clips are written in.
STAGE_UNITS = {UsdGeom.Tokens.metersPerUnit: 1.0, UsdGeom.Tokens.upAxis: UsdGeom.Tokens.y}


def plan(context):
    """One candidate per distinct effect (its look: the settings without PLACEMENT) across the places."""
    groups = {}
    for place in context.places:
        for use in place_emitters(context, place):
            groups.setdefault(use["identity"], []).append(use)
    return [candidate(context, uses) for _, uses in sorted(groups.items(), key=lambda item: item[1][0]["name"])]


def plain(value):
    """A USD value as plain JSON (vectors and arrays as lists)."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Sdf.AssetPath):
        return value.path
    return [plain(item) for item in value]


def emitter_specs(layer):
    """[(path, weather variant or None)] of every prim spec carrying score:effect:kind, in path order."""
    found = []

    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.PrimSpec) and f"{PREFIX}kind" in spec.attributes:
            selection = dict(path.GetVariantSelections() if hasattr(path, "GetVariantSelections") else [])
            weather = selection.get("weather") or variant_of(path)
            found.append((path, weather))
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return sorted(found, key=lambda item: str(item[0]))


def variant_of(path):
    """The weather variant named in a path ({weather=storm}), or None."""
    text = str(path)
    if "{weather=" not in text:
        return None
    return text.split("{weather=", 1)[1].split("}", 1)[0]


def settings_of(spec):
    """{field: value} of an emitter's score:effect:* attributes."""
    return {name[len(PREFIX):]: plain(attribute.default) for name, attribute in sorted(spec.attributes.items())
            if name.startswith(PREFIX)}


def clip_name(layer, path):
    """The assembly name of the emitter's value clip (its points child's clip asset), or None."""
    for child in layer.GetPrimAtPath(path).nameChildren:
        clips = child.GetInfo("clips") if child.HasInfo("clips") else {}
        for set_values in clips.values():
            for asset in set_values.get("assetPaths", []):
                return joined("layers", asset.path)
    return None


def effects_output(context, place):
    """The effects-layer output the place's assembly took (origin.json), else the node's accepted output."""
    assembly, _ = context.assembly(place)
    origin = (context.store.read_json(assembly, "origin.json") or {}) if assembly else {}
    return (origin.get("layers") or {}).get("effects-layer") or context.store.accepted(f"effects-layer@{place}")


def place_emitters(context, place):
    """Every emitter of a place's effects-layer output: [{place, path, name, weather, settings, clip, clip_sha,
    identity, output}]."""
    output = effects_output(context, place)
    listing = context.store.listing(output) if output else {}
    if LAYER not in listing:
        return []
    layer = Sdf.Layer.OpenAsAnonymous(str(context.tree(output) / LAYER))
    found = []
    for path, weather in emitter_specs(layer):
        spec = layer.GetPrimAtPath(path)
        settings = settings_of(spec)
        clip = clip_name(layer, path)
        clip_sha = listing.get(clip)
        look = {key: value for key, value in settings.items() if key not in PLACEMENT}
        identity = hashlib.sha256(json.dumps(look, sort_keys=True).encode()).hexdigest()
        found.append({"place": place, "path": path, "name": spec.name, "weather": weather, "settings": settings,
                      "clip": clip, "clip_sha": clip_sha, "identity": identity, "output": output})
    return found


def units_of(layer):
    """{metersPerUnit, upAxis} a place's layer states, SCORE's stage units where it states none."""
    root = layer.pseudoRoot
    return {key: root.GetInfo(key) if root.HasInfo(key) else value for key, value in STAGE_UNITS.items()}


def stated(layer, units):
    """The layer with the units written on its root; the layer back."""
    for key, value in units.items():
        layer.pseudoRoot.SetInfo(key, value)
    return layer


def effect_usda(context, use, root_name, work):
    """The emitter Xform copied to /<root_name> with its clip asset pointed at ./clip.usdc, in its place's units."""
    source = Sdf.Layer.OpenAsAnonymous(str(context.tree(use["output"]) / LAYER))
    layer = copy_prim(source, use["path"], "effect.usda", root_name)
    layer.timeCodesPerSecond = source.timeCodesPerSecond
    layer.framesPerSecond = source.framesPerSecond
    stated(layer, units_of(source))
    rewrite(layer, {path: "./clip.usdc" for path in asset_paths(layer) if joined("layers", path) == use["clip"]})
    return export(layer, work / "effect.usda")


def stated_clip(context, use, work):
    """The emitter's clip copied into `work` as clip.usdc stating its place's units (its values are the place stage's,
    so a clip lifted out of it says so); the copy's path."""
    source = Sdf.Layer.OpenAsAnonymous(str(context.tree(use["output"]) / LAYER), metadataOnly=True)
    clip = Sdf.Layer.OpenAsAnonymous(str(context.tree(use["output"]) / use["clip"]))
    destination = work / "clip.usdc"
    if not stated(clip, units_of(source)).Export(str(destination)):
        raise RuntimeError(f"{use['clip']}: the clip could not be written to {destination}")
    return destination


def stem(names):
    """The name the emitters of one effect share: their common start up to a whole word (drift_0_0 .. drift_4_4 ->
    drift, devil_east and devil_west -> devil), or the one name they all have."""
    if len(set(names)) == 1:
        return names[0]
    common = os.path.commonprefix(list(names))
    return common.rsplit("_", 1)[0] if "_" in common else names[0]


def candidate(context, uses):
    """One effect candidate: the first placement (by place, then prim) its sample, every placement named."""
    uses = sorted(uses, key=lambda use: (use["place"], str(use["path"])))
    first = uses[0]
    root_name = place_reads.sanitised(stem([use["name"] for use in uses]))
    work = context.work("effect", first["identity"][:16])
    files = {"settings.json": write_json(work / "settings.json", first["settings"]),
             "effect.usda": effect_usda(context, first, root_name, work)}
    roles = {"settings.json": "record", "effect.usda": "usd"}
    files_from = {}
    picture = {"reason": "the emitter has no baked clip"}
    if first["clip"]:
        files["clip.usdc"] = stated_clip(context, first, work)
        roles["clip.usdc"] = "usd"
        files_from["clip.usdc"] = {"path": first["clip"], "sha256": first["clip_sha"]}
        clip_time, _ = points.render(files["clip.usdc"], work / "preview.png", first["settings"].get("colour"))
        files["preview.png"] = work / "preview.png"
        roles["preview.png"] = "picture"
        picture = {"file": "preview.png", "made_by": "preview/points.py", "of": f"clip.usdc at clip time {clip_time}"}
    provenance = place_reads.node_provenance(context, f"effects-layer@{first['place']}", first["output"])
    provenance.update({"place": first["place"], "prim": str(first["path"]), "from": first["settings"].get("from"),
                       "placements": [{"place": use["place"], "prim": str(use["path"])} for use in uses]})
    weathers = sorted({use["weather"] for use in uses if use["weather"]})
    places = [use["place"] for use in uses]
    return Candidate(kind="effect", base_id=root_name, identity=first["identity"],
                     title=root_name.replace("_", " ").capitalize(), places=places, files=files, roles=roles,
                     made_with=[dict(SCORE)],
                     usd={"root": "effect.usda", "layer": "effects", "use": "reference"},
                     requires=[], provenance=provenance,
                     tags={"effect": [first["settings"].get("kind")], "weather": weathers, "engine": ["usd"],
                           "place_kind": sorted({tag for each in places for tag in place_reads.place_kind(context, each)})},
                     picture=picture, files_from=files_from)
