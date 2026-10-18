"""layer entries: each sublayer of each place's accepted assembly, every asset path rewritten from the store path to
the address of the entry holding the same bytes (the sha -> entry map the atoms made), plus its side record from the
source node the assembly's origin.json names (spec 4.8). A file no atom holds stays an own file of the layer entry,
at its path in the assembly, so its own relative paths keep working."""
import hashlib
import json
import posixpath

from pxr import Sdf

from score_lib import entry as entries
from score_lib.export import places as place_reads
from score_lib.export.model import dependencies
from score_lib.export.run import Candidate
from score_lib.licences import SCORE_COMPONENT
from score_lib.rewrite import asset_paths, export, joined, open_copy, rewrite

# sublayer -> (the stage whose output gives its side record, the side record files)
SIDE = {"light": ("light", ("lamps.json",)), "motion": ("motion", ("movers.json",)),
        "settle": ("settle", ("settle.json",)), "effects": ("effects-layer", ("held.json",)),
        "sound": ("sound", ("sound.json",)), "characters": ("characters-layer", ("characters.json",)),
        "crops": ("crops-layer", ("spots.json",)), "machines": ("machines-layer", ("placed.json",))}
# sublayer -> the algorithm entry that writes it (provenance only; a world needs none of them to use the layer)
WRITTEN_BY = {"base": "room_layout", "ground": "terrain_landforms", "settle": "settle", "machines": "machines_lay",
              "crops": "crops_spots", "effects": "particle_effects", "motion": "motion_movers",
              "motion_keys": "motion_movers", "light": "light_rig", "sound": "sound_layer",
              "characters": "cast_spread"}
NOT_ENTRIES = ("layers/edit.usda",)


def plan(context):
    """One candidate per sublayer file of each place's assembly (the edit slot left to the place)."""
    found = []
    for place in context.places:
        for name in place_reads.layer_files(context, place):
            if name not in NOT_ENTRIES:
                found.append(candidate(context, place, name))
    return found


def sublayer_of(name):
    """The sublayer name of an assembly layer file (layers/base.usda -> base)."""
    return posixpath.splitext(posixpath.basename(name))[0]


class Mapping:
    """How one layer's asset paths are rewritten: to library addresses, own files, neighbour places, or left."""

    def __init__(self, context, place, name):
        self.context = context
        self.place = place
        self.output, self.listing = context.assembly(place)
        self.folder = posixpath.dirname(name)
        self.paths = {}
        self.requires = set()
        self.own = set()
        self.neighbours = set()
        self.unresolved = set()

    def library(self, sha):
        """The library search path of the entry file holding these bytes, or None."""
        found = self.context.sha_map.get(sha)
        if not found:
            return None
        address, file_name = found
        kind, entry_id, version = entries.parse_address(address)
        self.requires.add(address)
        return entries.library_path(kind, entry_id, version, file_name)

    def neighbour(self, path):
        """The library path of a neighbouring place's root, or None when the path names none of the world's places."""
        found = place_reads.neighbour_path(self.context, path)
        if not found:
            return None
        self.neighbours.add(found[1])
        return found[0]

    def own_file(self, name):
        """Keep a file the atoms do not hold (and what it names, when it is USD) as an own file of the entry."""
        self.own.add(name)
        if name.endswith((".usd", ".usda", ".usdc")):
            self.own.update(dependencies(self.context, self.output, name))
        return f"./{name}"

    def resolve(self, path):
        """The new text of one asset path."""
        name = joined(self.folder, path)
        if name in self.listing:
            return self.library(self.listing[name]) or self.own_file(name)
        found = self.neighbour(path)
        if found:
            return found
        self.unresolved.add(path)
        return path

    def apply(self, layer):
        """Rewrite every asset path of a layer."""
        self.paths = {path: self.resolve(path) for path in asset_paths(layer)}
        rewrite(layer, self.paths)


def side_records(context, place, sublayer):
    """{entry name: (path, sha)} of the sublayer's side record, from the output origin.json names for its stage
    (the stage's accepted output when origin.json names none), and that output."""
    if sublayer not in SIDE:
        return {}, None
    stage, names = SIDE[sublayer]
    output, _ = context.assembly(place)
    origin = context.store.read_json(output, "origin.json") or {}
    source = (origin.get("layers") or {}).get(stage) or context.store.accepted(f"{stage}@{place}")
    if not source:
        return {}, None
    listing = context.store.listing(source)
    found = {name: (context.tree(source) / name, listing[name]) for name in names if name in listing}
    return found, source


def ground_layer(context, mapping, name, work):
    """The ground sublayer: a layer.usda holding the ground entry's usdc as its one sublayer (or its own copy)."""
    sha = mapping.listing[name]
    target = mapping.library(sha) or mapping.own_file(name)
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.subLayerPaths = [target]
    mapping.paths = {"./" + posixpath.basename(name): target}
    return export(layer, work / "layer.usda")


def rewritten_layer(context, mapping, name, work):
    """A usda sublayer with its asset paths rewritten."""
    layer = open_copy(context.tree(mapping.output) / name)
    mapping.apply(layer)
    return export(layer, work / "layer.usda")


def candidate(context, place, name):
    """One layer candidate."""
    sublayer = sublayer_of(name)
    mapping = Mapping(context, place, name)
    work = context.work("layer", place, sublayer)
    if name.endswith(".usdc"):
        root = ground_layer(context, mapping, name, work)
    else:
        root = rewritten_layer(context, mapping, name, work)
    files = {"layer.usda": root}
    roles = {"layer.usda": "usd"}
    known = {}
    tree = context.tree(mapping.output)
    for own in sorted(mapping.own):
        files[own] = tree / own
        known[own] = mapping.listing[own]
        roles[own] = "usd" if own.endswith((".usd", ".usda", ".usdc")) else "texture"
    sides, side_output = side_records(context, place, sublayer)
    for side, (path, sha) in sides.items():
        files[side] = path
        known[side] = sha
        roles[side] = "record"
    provenance = place_reads.assembly_provenance(context, place)
    algorithm = context.by_id.get(("algorithm", WRITTEN_BY.get(sublayer, "")))
    provenance.update({"place": place, "source_name": name, "side_records_from": side_output, "written_by": algorithm,
                       "unresolved": sorted(mapping.unresolved), "own_files": sorted(mapping.own)})
    identity = hashlib.sha256(json.dumps([place, name, mapping.listing[name]]).encode()).hexdigest()
    usd = {"root": "layer.usda", "layer": sublayer, "use": "sublayer"}
    if mapping.neighbours:
        usd["neighbours"] = sorted(mapping.neighbours)
    made = Candidate(kind="layer", base_id=f"{sublayer}_{place}", identity=identity,
                     title=f"{place.replace('_', ' ').capitalize()} {sublayer.replace('_', ' ')} layer",
                     places=[place], files=files, roles=roles, components=[dict(SCORE_COMPONENT)], usd=usd,
                     requires=sorted(mapping.requires), provenance=provenance,
                     tags={"layer": [sublayer], "place_kind": place_reads.place_kind(context, place)},
                     picture={"reason": "a sublayer is shown only as part of its place; no renderer is run"},
                     known=known, files_from={"layer.usda": {"path": name, "sha256": mapping.listing[name]}})
    made.entry_id = f"{sublayer}.{place_reads.sanitised(place)}"
    return made
