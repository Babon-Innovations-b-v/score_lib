"""light entries: each sky picture of a place's assembly (assets/light/*.png) with the prims that draw it and the
place's Environment dome light (spec 4.5). Lamp rigs stay in the layer entries."""
import posixpath

from pxr import Sdf, Usd

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate
from score_lib.admission import SCORE, carried
from score_lib.rewrite import asset_paths, export, gathered, rewrite

EARTH = "nasa_visible_earth"


def plan(context):
    """One candidate per distinct sky picture, the first place in sorted order giving its prims."""
    groups = {}
    for place in context.places:
        output, listing = context.assembly(place)
        for name, sha in sorted(listing.items()):
            if name.startswith("assets/light/") and name.endswith(".png"):
                groups.setdefault(sha, []).append((place, name))
    return [candidate(context, sha, uses) for sha, uses in sorted(groups.items(), key=lambda item: item[1][0])]


def picture_users(layer, picture):
    """Paths of the materials whose shaders read a picture of this file name."""
    found = set()

    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.AttributeSpec) and spec.name == "inputs:file" and spec.default is not None:
            if posixpath.basename(spec.default.path) == picture:
                found.add(path.GetPrimPath().GetParentPath())
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def bound_to(layer, materials):
    """Paths of the prims whose material:binding targets one of the materials."""
    found = set()

    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.RelationshipSpec) and spec.name == "material:binding":
            if any(target in materials for target in spec.targetPathList.GetAddedOrExplicitItems()):
                found.add(path.GetPrimPath())
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def sky_layer(context, place, picture, root_name):
    """The sky's prims (the materials reading the picture, the prims bound to them, the Environment dome light)
    composed from the place's light and base layers and gathered under /<root_name>."""
    output, listing = context.assembly(place)
    tree = context.tree(output)
    names = [name for name in ("layers/light.usda", "layers/base.usda") if name in listing]
    layers = [Sdf.Layer.OpenAsAnonymous(str(tree / name)) for name in names]
    materials = set().union(*(picture_users(layer, picture) for layer in layers))
    prims = set(materials).union(*(bound_to(layer, materials) for layer in layers))
    environment = Sdf.Path(f"/{place}/Environment")
    prims.add(environment)
    root = Sdf.Layer.CreateAnonymous(".usda")
    root.subLayerPaths = [str(tree / name) for name in names]
    mask = Usd.StagePopulationMask()
    for path in prims:
        mask.Add(path)
    stage = Usd.Stage.OpenMasked(root, mask)
    flat = stage.Flatten()
    present = [path for path in prims if flat.GetPrimAtPath(path)]
    layer = gathered(flat, present, root_name)
    rewrite(layer, {path: f"./{picture}" for path in asset_paths(layer) if posixpath.basename(path) == picture})
    return layer, sorted(str(path) for path in present)


def made_with(picture):
    """score's own work, and NASA's pictures the Earth ball is baked from (tools/usd/earth.py)."""
    found = [dict(SCORE)]
    if picture.startswith("earth"):
        found.append(carried("Earth picture baked from NASA Blue Marble and cloud cover", EARTH))
    return found


def candidate(context, sha, uses):
    """One sky picture candidate."""
    place, name = uses[0]
    picture = posixpath.basename(name)
    stem = picture[:-len(".png")]
    root_name = place_reads.sanitised(stem)
    work = context.work("light", sha[:16])
    layer, prims = sky_layer(context, place, picture, root_name)
    output, _ = context.assembly(place)
    files = {picture: context.tree(output) / name, "sky.usda": export(layer, work / "sky.usda")}
    provenance = place_reads.assembly_provenance(context, place)
    provenance.update({"place": place, "source_name": name, "prims": prims})
    places = sorted({each for each, _ in uses})
    return Candidate(kind="light", base_id=root_name, identity=sha, title=stem.replace("_", " ").capitalize(),
                     places=places, files=files, roles={picture: "picture", "sky.usda": "usd"},
                     made_with=made_with(picture), usd={"root": "sky.usda", "layer": "light", "use": "reference"},
                     provenance=provenance,
                     tags={"place_kind": sorted({tag for each in places for tag in place_reads.place_kind(context, each)}),
                           "engine": ["usd"]},
                     picture={"file": picture, "made_by": "source picture", "of": name}, known={picture: sha})
