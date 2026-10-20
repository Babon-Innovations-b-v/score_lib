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
from score_lib.admission import SCORE
from score_lib.preview import audio, layer_map, mesh
from score_lib.rewrite import asset_paths, export, joined, open_copy, rewrite

# sublayer -> (the stage whose output gives its side record, the side record files)
SIDE = {"light": ("light", ("lamps.json", "measures.json")), "motion": ("motion", ("movers.json",)),
        "settle": ("settle", ("settle.json",)), "effects": ("effects-layer", ("held.json",)),
        "sound": ("sound", ("sound.json",)), "characters": ("characters-layer", ("characters.json",)),
        "crops": ("crops-layer", ("spots.json",)), "machines": ("machines-layer", ("placed.json",))}
# sublayer -> the algorithm entry that writes it (provenance only; a world needs none of them to use the layer)
WRITTEN_BY = {"ground": "terrain_landforms", "settle": "settle", "machines": "motion", "motion-models": "motion",
              "crops": "crops_spots", "effects": "particle_effects", "motion": "motion", "motion_keys": "motion", "light": "light_rig", "sound": "sound_layer",
              "characters": "cast_spread", "physics": "physics_layer"}
NOT_ENTRIES = ("layers/edit.usda",)
# the physics sublayer of a place is its own stage's output (`physics-layer@<place>`), not part of the assembly; its
# trial (`physics-trial@<place>`) rides along as records
PHYSICS = "layers/physics.usda"
PHYSICS_FILES = {"physics-layer": ("physics.json", "layers/trial.usda"), "physics-trial": ("physics_motion.usda",
                                                                                         "trial.json")}


def plan(context):
    """One candidate per sublayer file of each place's assembly (the edit slot left to the place), and one per
    place with an accepted physics layer."""
    found = []
    for place in context.places:
        for name in place_reads.layer_files(context, place):
            if name not in NOT_ENTRIES:
                found.append(candidate(context, place, name))
        if context.store.accepted(f"physics-layer@{place}"):
            found.append(physics_candidate(context, place))
    return found


def sublayer_of(name):
    """The sublayer name of an assembly layer file (layers/base.usda -> base)."""
    return posixpath.splitext(posixpath.basename(name))[0]


class Mapping:
    """How one layer's asset paths are rewritten: to library addresses, own files, neighbour places, or left."""

    def __init__(self, context, place, name, output=None):
        self.context = context
        self.place = place
        self.output, self.listing = (output, context.store.listing(output)) if output else context.assembly(place)
        self.name = name
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
        if name == self.name:  # the layer naming itself (a prim referencing another prim of the same layer)
            return "./layer.usda"
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
    source = place_reads.stage_output(context, place, stage)
    if not source:
        return {}, None
    listing = context.store.listing(source)
    found = {name: (context.tree(source) / name, listing[name]) for name in names if name in listing}
    return found, source


def haze_records(context, place):
    """{entry name: (path, sha)} of the records of the place's accepted haze pass (`light-haze@<place>`): its
    passes and views JSON. The pass arrays and renders stay out (light pass npz, spec section 12)."""
    output = context.store.accepted(f"light-haze@{place}")
    if not output:
        return {}
    listing = context.store.listing(output)
    return {f"haze/{name}": (context.tree(output) / name, sha) for name, sha in sorted(listing.items())
            if name.endswith(".json") and name != "contract.json"}


def stage_records(context, place, stage, names):
    """{entry name: (path, sha)} of named files of a stage's accepted output for the place, under `<stage>/`."""
    output = context.store.accepted(f"{stage}@{place}")
    if not output:
        return {}
    listing = context.store.listing(output)
    return {f"{stage}/{name}": (context.tree(output) / name, listing[name]) for name in names if name in listing}


def map_picture(context, place, layer_path, work, files, roles):
    """A top-down map of where the layer's prims stand over the place's kit, or the reason there is none."""
    points = layer_map.positions(place_reads.composed(context, place), layer_path)
    if not len(points):
        return {"reason": "the layer positions no prim of its own in the place, so there is nothing to map"}
    drawn = layer_map.render(place_reads.kit(context, place), points, work / "preview.png")
    files["preview.png"] = work / "preview.png"
    roles["preview.png"] = "picture"
    return {"file": "preview.png", "made_by": "preview/layer_map.py",
            "of": f"{drawn} prim positions from above over data/kit/{place}.json"}


AUDIO = (".wav", ".ogg", ".flac", ".mp3")


def waveform_picture(mapping, work, files, roles):
    """A waveform of the first audio file a layer names, or None when it names none."""
    tree = mapping.context.tree(mapping.output)
    sounds = sorted({joined(mapping.folder, path) for path in mapping.paths} & set(mapping.listing))
    sounds = [name for name in sounds if name.endswith(AUDIO)]
    if not sounds:
        return None
    audio.render(tree / sounds[0], work / "preview.png")
    files["preview.png"] = work / "preview.png"
    roles["preview.png"] = "picture"
    return {"file": "preview.png", "made_by": "preview/audio.py", "of": f"{sounds[0]} (the layer's first sound)"}


def layer_picture(context, place, sublayer, mapping, layer_path, work, files, roles, known):
    """The layer's picture: the light pass's look render for a light layer, the ground mesh for a ground layer, a
    waveform for a sound layer whose sources all stand at one point, a map of its prims for any other."""
    if sublayer == "sound" and len(layer_map.positions(place_reads.composed(context, place), layer_path)) < 2:
        shown = waveform_picture(mapping, work, files, roles)
        if shown:
            return shown
    if sublayer == "light":
        found = place_reads.look(context, place)
        if found:
            path, sha, name = found
            files["picture.png"], known["picture.png"], roles["picture.png"] = path, sha, "picture"
            return {"file": "picture.png", "made_by": "source picture", "of": f"light@{place}: {name}"}
    if sublayer == "ground":
        mesh.render(layer_path, work / "preview.png")
        files["preview.png"] = work / "preview.png"
        roles["preview.png"] = "picture"
        return {"file": "preview.png", "made_by": "preview/mesh.py", "of": "the place's ground.usdc"}
    return map_picture(context, place, layer_path, work, files, roles)


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
    if sublayer == "light":
        sides.update(haze_records(context, place))
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
                     places=[place], files=files, roles=roles, made_with=[dict(SCORE)], usd=usd,
                     requires=sorted(mapping.requires), provenance=provenance,
                     tags={"layer": [sublayer], "place_kind": place_reads.place_kind(context, place)},
                     picture=layer_picture(context, place, sublayer, mapping, tree / name, work, files, roles, known),
                     known=known, files_from={"layer.usda": {"path": name, "sha256": mapping.listing[name]}})
    made.entry_id = f"{sublayer}.{place_reads.sanitised(place)}"
    return made


def physics_candidate(context, place):
    """physics.<place>: the place's physics sublayer (collision, mass, material overs) from its own stage, with the
    stage's record and its trial (the trial sublayer, the settled motion and its record)."""
    output = context.store.accepted(f"physics-layer@{place}")
    mapping = Mapping(context, place, PHYSICS, output)
    work = context.work("layer", place, "physics")
    root = rewritten_layer(context, mapping, PHYSICS, work)
    files, roles, known = {"layer.usda": root}, {"layer.usda": "usd"}, {}
    tree = context.tree(output)
    for own in sorted(mapping.own):
        files[own], known[own] = tree / own, mapping.listing[own]
        roles[own] = "usd" if own.endswith((".usd", ".usda", ".usdc")) else "texture"
    for stage, names in PHYSICS_FILES.items():
        for side, (path, sha) in stage_records(context, place, stage, names).items():
            files[side], known[side] = path, sha
            roles[side] = "usd" if side.endswith(".usda") else "record"
    provenance = place_reads.node_provenance(context, f"physics-layer@{place}")
    provenance.update({"place": place, "source_name": PHYSICS, "written_by": context.by_id.get(("algorithm",
                                                                                                  "physics_layer")),
                       "trial": place_reads.node_provenance(context, f"physics-trial@{place}"),
                       "unresolved": sorted(mapping.unresolved), "own_files": sorted(mapping.own)})
    identity = hashlib.sha256(json.dumps([place, PHYSICS, mapping.listing[PHYSICS], sorted(known.values())])
                              .encode()).hexdigest()
    made = Candidate(kind="layer", base_id=f"physics_{place}", identity=identity,
                     title=f"{place.replace('_', ' ').capitalize()} physics layer", places=[place], files=files,
                     roles=roles, made_with=[dict(SCORE)],
                     usd={"root": "layer.usda", "layer": "physics", "use": "sublayer"},
                     requires=sorted(mapping.requires), provenance=provenance,
                     tags={"layer": ["physics"], "place_kind": place_reads.place_kind(context, place)},
                     picture=map_picture(context, place, tree / PHYSICS, work, files, roles), known=known,
                     files_from={"layer.usda": {"path": PHYSICS, "sha256": mapping.listing[PHYSICS]}})
    made.entry_id = f"physics.{place_reads.sanitised(place)}"
    return made
