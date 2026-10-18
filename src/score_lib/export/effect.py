"""effect entries: each particle emitter of a place's effects-layer output (the one its assembly took, else the
accepted one), its 35 score:effect:* attributes and its baked value clip (spec 4.4). The record is the reusable
algorithm's settings; the clip is one baked run of it."""
import hashlib
import json

from pxr import Sdf

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.licences import SCORE_COMPONENT
from score_lib.preview import points
from score_lib.rewrite import asset_paths, copy_prim, export, joined, rewrite

PREFIX = "score:effect:"
LAYER = "layers/effects.usda"


def plan(context):
    """One candidate per distinct emitter (settings and clip) across the places."""
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
        identity = hashlib.sha256((json.dumps(settings, sort_keys=True) + (clip_sha or "")).encode()).hexdigest()
        found.append({"place": place, "path": path, "name": spec.name, "weather": weather, "settings": settings,
                      "clip": clip, "clip_sha": clip_sha, "identity": identity, "output": output})
    return found


def effect_usda(context, use, root_name, work):
    """The emitter Xform copied to /<root_name> with its clip asset pointed at ./clip.usdc."""
    source = Sdf.Layer.OpenAsAnonymous(str(context.tree(use["output"]) / LAYER))
    layer = copy_prim(source, use["path"], "effect.usda", root_name)
    layer.timeCodesPerSecond = source.timeCodesPerSecond
    layer.framesPerSecond = source.framesPerSecond
    rewrite(layer, {path: "./clip.usdc" for path in asset_paths(layer) if joined("layers", path) == use["clip"]})
    return export(layer, work / "effect.usda")


def candidate(context, uses):
    """One effect candidate."""
    uses = sorted(uses, key=lambda use: use["place"])
    first = uses[0]
    root_name = place_reads.sanitised(first["name"])
    work = context.work("effect", first["identity"][:16])
    files = {"settings.json": write_json(work / "settings.json", first["settings"]),
             "effect.usda": effect_usda(context, first, root_name, work)}
    roles = {"settings.json": "record", "effect.usda": "usd"}
    known = {}
    picture = {"reason": "the emitter has no baked clip"}
    if first["clip"]:
        files["clip.usdc"] = context.tree(first["output"]) / first["clip"]
        roles["clip.usdc"] = "usd"
        known["clip.usdc"] = first["clip_sha"]
        clip_time, _ = points.render(files["clip.usdc"], work / "preview.png", first["settings"].get("colour"))
        files["preview.png"] = work / "preview.png"
        roles["preview.png"] = "picture"
        picture = {"file": "preview.png", "made_by": "preview/points.py", "of": f"clip.usdc at clip time {clip_time}"}
    provenance = place_reads.node_provenance(context, f"effects-layer@{first['place']}", first["output"])
    provenance.update({"place": first["place"], "prim": str(first["path"]), "from": first["settings"].get("from")})
    algorithm = context.by_id.get(("algorithm", "particle_effects"))
    weathers = sorted({use["weather"] for use in uses if use["weather"]})
    places = [use["place"] for use in uses]
    return Candidate(kind="effect", base_id=root_name, identity=first["identity"],
                     title=first["name"].replace("_", " ").capitalize(), places=places, files=files, roles=roles,
                     components=[dict(SCORE_COMPONENT)],
                     usd={"root": "effect.usda", "layer": "effects", "use": "reference"},
                     requires=[algorithm] if algorithm else [], provenance=provenance,
                     tags={"effect": [first["settings"].get("kind")], "weather": weathers, "engine": ["usd"],
                           "place_kind": sorted({tag for each in places for tag in place_reads.place_kind(context, each)})},
                     picture=picture, known=known)
