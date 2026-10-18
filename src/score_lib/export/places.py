"""What several kinds read of one place's accepted assembly: its object prims, the asset paths each layer names, the
kind of place it is, and the provenance of the assembly node."""
import posixpath
import re

from pxr import Sdf, Usd

from score_lib import entry as entries
from score_lib.export.run import world_places
from score_lib.source.store import SUB, stored_output
from score_lib.rewrite import asset_paths, joined, open_copy

OBJECT_FIELDS = ("model", "model_sha256", "row", "kind", "name")
NEIGHBOUR = re.compile(r"^\.\./\.\./(?P<place>[a-z0-9_]+)/(?P=place)\.usda$")


def object_prims(context, place):
    """Every prim of the place's base layer carrying score:model: [{path, asset, model, model_sha256, row, kind,
    name}], `asset` the assembly-relative file its reference names."""
    return context.remembered(("objects", place), lambda: read_object_prims(context, place))


def read_object_prims(context, place):
    output, listing = context.assembly(place)
    if "layers/base.usda" not in listing:
        return []
    layer = open_copy(context.tree(output) / "layers/base.usda")
    found = []

    def walk(spec):
        for child in spec.nameChildren:
            attributes = child.attributes
            if "score:model" in attributes:
                references = [item.assetPath for item in child.referenceList.GetAddedOrExplicitItems()]
                row = {"path": str(child.path),
                       "asset": joined("layers", references[0]) if references else None}
                for field in OBJECT_FIELDS:
                    key = f"score:{field}"
                    row[field] = attributes[key].default if key in attributes else None
                found.append(row)
            walk(child)

    walk(layer.pseudoRoot)
    return found


def layer_paths(context, place, name):
    """Every asset path one layer file of the assembly names, as written."""
    return context.remembered(("paths", place, name), lambda: read_layer_paths(context, place, name))


def read_layer_paths(context, place, name):
    output, _ = context.assembly(place)
    return asset_paths(open_copy(context.tree(output) / name))


def used_assets(context, place, name):
    """Assembly-relative names of the files one layer names that the assembly holds."""
    _, listing = context.assembly(place)
    folder = posixpath.dirname(name)
    return sorted({joined(folder, path) for path in layer_paths(context, place, name)
                   if joined(folder, path) in listing})


def layer_files(context, place):
    """The assembly's layer files (layers/*.usda|usdc), sorted."""
    _, listing = context.assembly(place)
    return sorted(name for name in listing if name.startswith("layers/") and name.endswith((".usda", ".usdc")))


def environment(context, place):
    """The attributes of the place's Environment dome light: {inside, from} (None when it has none)."""
    return context.remembered(("environment", place), lambda: read_environment(context, place))


def read_environment(context, place):
    output, listing = context.assembly(place)
    if "layers/base.usda" not in listing:
        return None
    layer = Sdf.Layer.OpenAsAnonymous(str(context.tree(output) / "layers/base.usda"))
    for root in layer.rootPrims:
        spec = layer.GetPrimAtPath(root.path.AppendChild("Environment"))
        if spec and spec.typeName == "DomeLight":
            attributes = spec.attributes
            return {"inside": attributes["score:inside"].default if "score:inside" in attributes else None,
                    "from": attributes["score:from"].default if "score:from" in attributes else ""}
    return None


def place_kind(context, place):
    """The place_kind tags of a place, read from its Environment dome light: inside or outside, and the world it
    stands on when its sky record names one."""
    found = environment(context, place)
    if not found:
        return []
    tags = []
    if found["inside"] is not None:
        tags.append("inside" if found["inside"] else "outside")
    tags += [world for world in ("moon", "mars", "earth") if world in (found["from"] or "").split()]
    return tags


def assembly_provenance(context, place):
    """The provenance block of the node holding a place's assembled stage: node, output, record key, accept, code,
    and the folder of the output it is read from when the place is one only other places show."""
    node, output = context.assembly_source(place)
    found = node_provenance(context, node or f"assembly@{place}", stored_output(output) if output else None)
    if output and SUB in output:
        found["folder"] = output.partition(SUB)[2]
    return found


def node_provenance(context, node, output=None):
    """{node, output, record_key, accept{by, at}, code, models} of a node's accepted output (or the output given)."""
    accept = context.store.current_accept(node) or {}
    output = output or accept.get("output")
    record = context.store.record(node, accept["key"]) if accept.get("output") == output and accept else {}
    return {"node": node, "output": output, "record_key": record.get("key", accept.get("key")) if record else None,
            "accept": {"by": accept.get("by"), "at": accept.get("at")} if accept.get("output") == output else None,
            "code": record.get("code"), "models": pinned_models(record)}


def pinned_models(record):
    """The finish record's pinned models without the registry host: {name, revision, sha256, digest, source} (what
    made the output; the library names no licence per entry)."""
    found = []
    for pinned in (record.get("models") or {}).values():
        for model in pinned.get("models", []):
            found.append({"name": model.get("name"), "revision": model.get("revision"), "sha256": model.get("sha256"),
                          "digest": pinned.get("digest"), "source": model.get("source")})
    return sorted(found, key=lambda model: model["name"] or "")


def sanitised(name):
    """An id part from a file or record name: lower case, anything outside [a-z0-9_] made an underscore."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", name.lower())).strip("_") or "unnamed"


def place_address(context, place):
    """The address a neighbour reference points at: the place entry planned this run, else its newest version, else
    its first."""
    entry_id = sanitised(place)
    newest = entries.versions(context.root, "place", entry_id)
    return context.by_id.get(("place", entry_id)) or (newest[-1]["address"] if newest else
                                                       entries.address_of("place", entry_id, 1))


def neighbour_path(context, path):
    """(library path, place address) of a neighbouring place's root named `../../<place>/<place>.usda`, or None when
    the path names none of the world's places."""
    found = NEIGHBOUR.match(path)
    if not found or found["place"] not in world_places(context.world):
        return None
    address = place_address(context, found["place"])
    kind, entry_id, version = entries.parse_address(address)
    return entries.library_path(kind, entry_id, version, f"{found['place']}.usda"), address


def stage_output(context, place, stage):
    """The output of a stage the place's assembly took (origin.json `layers`), else the stage's accepted output."""
    output, _ = context.assembly(place)
    origin = (context.store.read_json(output, "origin.json") if output else None) or {}
    return (origin.get("layers") or {}).get(stage) or context.store.accepted(f"{stage}@{place}")


def look(context, place):
    """(path, sha256, name) of the first look render of the place in the light output its assembly took, or None."""
    output = stage_output(context, place, "light")
    if not output:
        return None
    listing = context.store.listing(output)
    folder = f"looks/{place}/"
    names = sorted(name for name in listing if name.startswith(folder) and name.endswith("-look.png"))
    return (context.tree(output) / names[0], listing[names[0]], names[0]) if names else None


def composed(context, place):
    """The place's composed stage (payloads unloaded), opened once a run."""
    return context.remembered(("stage", place), lambda: open_stage(context, place))


def open_stage(context, place):
    output, _ = context.assembly(place)
    return Usd.Stage.Open(str(context.tree(output) / f"{place}.usda"), Usd.Stage.LoadNone)


def kit(context, place):
    """The place's kit (data/kit/<place>.json at the commit), or {}."""
    source = f"data/kit/{place}.json"
    return context.repo.read_json(source) if context.repo.exists(source) else {}
