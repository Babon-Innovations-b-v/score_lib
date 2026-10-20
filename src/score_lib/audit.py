"""score-lib audit: what a world holds, kind by kind, against the library's entries, and why anything is missing.

Read only, like the export: the store and the score repository at the world's commit, and entries/. A thing of the
world is in the library when an entry holds its bytes (its files, files_from or aliases); a lamp or a placement lives
inside its place's layer, so it is in when that layer's entry is. Each missing thing names a reason:

    not exported   the exporter does not read it
    deduplicated   the same content is in under another entry or form
    refused        the admission keeps it out (not MIT)
    not accepted   the store holds no accepted output of it
    other          anything else, said in the note

Nothing renders and nothing is written; the report is a dict (`report_text` prints it).
"""
import collections
import dataclasses
import posixpath

from pxr import Sdf

from score_lib import entry as entries
from score_lib.export import character as character_export
from score_lib.export import effect as effect_export
from score_lib.export import model as model_export
from score_lib.export import places as place_reads
from score_lib.export import sound as sound_export
from score_lib.export.algorithm import DEFINITIONS, load
from score_lib.export.model import MADE_STAGES
from score_lib.export.run import world_places
from score_lib.source.store import place_of, stage_of

REASONS = ("not exported", "deduplicated", "refused", "not accepted", "other")
# Stages that judge, film or plan rather than make something a place shows: not algorithms the library lacks.
NOT_MAKERS = ("complete", "complete-boxes", "likeness", "likeness-calibration", "surfaces-check", "surfaces-turntable",
              "demo-turntables", "people-turntable", "concept-views", "concept_people", "partition-proposals",
              "light-passes", "scene-record", "plan", "closeup", "people-take", "props-inputs", "people-inputs")
LIGHTS = ("SphereLight", "DistantLight", "DomeLight", "RectLight", "DiskLight", "CylinderLight")


@dataclasses.dataclass
class Thing:
    """One thing of the world: whether the library holds it, and why not."""
    kind: str
    key: str
    places: list
    held: bool
    reason: str = ""
    note: str = ""


class Held:
    """What entries/ holds: every sha an entry carries (by kind), and its entries by kind and id."""

    def __init__(self, root):
        self.records = [record for record in entries.all_entries(root) if record["state"] != "withdrawn"]
        self.shas = collections.defaultdict(set)
        self.ids = collections.defaultdict(dict)
        self.addresses = {}  # (kind, sha) -> the first entry holding it
        for record in self.records:
            self.ids[record["kind"]][record["id"]] = record
            self.shas[record["kind"]].update(record_shas(record))
            for sha in record_shas(record):
                self.addresses.setdefault((record["kind"], sha), record["address"])

    def has(self, kind, sha):
        return sha in self.shas[kind]

    def of_kind(self, kind):
        return list(self.ids[kind].values())


def record_shas(record):
    """Every sha an entry holds: its files, the store bytes they came from and its aliases."""
    found = {described["sha256"] for described in record["files"].values()}
    found |= {source["sha256"] for source in record["provenance"].get("files_from", {}).values()}
    found |= {sha for sha, _ in record["provenance"].get("aliases", [])}
    return found


def missing(kind, key, places, reason, note):
    return Thing(kind, key, sorted(places), False, reason, note)


def models(context, held):
    """Every model a place's stage shows (one per identity), and every model a bake made that no place shows."""
    uses = collections.defaultdict(list)
    for place in context.places:
        for use in model_export.place_models(context, place):
            uses[use["identity"]].append(use)
    found = []
    for identity, group in sorted(uses.items()):
        places = sorted({use["place"] for use in group})
        sha = context.store.listing(group[0]["output"])[group[0]["asset"]]
        if held.has("model", sha):
            found.append(Thing("model", group[0]["stem"], places, True))
        else:
            found.append(missing("model", group[0]["stem"], places, "not exported",
                                 "its place is not one the export reads"))
    return found + unshown_models(context)


def unshown_models(context):
    """Models a bake made (made/<name>.gltf) that the bake's own place does not show: no usd form exists."""
    found = []
    for stage in MADE_STAGES:
        for node in context.store.nodes_of(stage):
            place = place_of(node)
            listing = context.assembly(place)[1] if place else {}
            for name in sorted(context.store.listing(context.store.accepted(node))):
                stem = posixpath.basename(name)[:-len(".gltf")]
                if name.startswith("made/") and name.endswith(".gltf") and name.count("/") == 1 \
                        and f"assets/{stem}.usdc" not in listing:
                    found.append(missing("model", stem, [place] if place else [], "other",
                                         f"{node} made it, but no place's stage shows it, so it has no usd form"))
    return found


def gltf_forms(context, held):
    """The bakes' gltf forms of the models places show: the same model as its entry's model.usdc."""
    found = []
    for stage in MADE_STAGES:
        for node in context.store.nodes_of(stage):
            listing = context.store.listing(context.store.accepted(node))
            for name, sha in sorted(listing.items()):
                if name.startswith("made/") and name.endswith(".gltf") and name.count("/") == 1:
                    key = f"{node}:{name}"
                    found.append(Thing("model gltf form", key, [place_of(node)], True) if held.has("model", sha) else
                                 missing("model gltf form", key, [place_of(node)], "deduplicated",
                                         "the bake's own gltf of a model whose usd form (model.usdc, textures with "
                                         "the same pixels) is in"))
    return found


def kits(context, held):
    """Every place kit, and every model its pieces name, linked from the kit entry's requires."""
    found = []
    model_of = context.memo.get("model_of", {})
    for place in context.places:
        kit = place_reads.kit(context, place)
        if not kit:
            continue
        record = held.ids["kit"].get(place_reads.sanitised(place))
        found.append(Thing("kit", place, [place], bool(record)) if record else
                     missing("kit", place, [place], "not exported", "no kit entry for this place"))
        required = set((record or {}).get("requires", []))
        names = {piece.get("model") for piece in kit.get("pieces") or [] if piece.get("model")}
        names |= set((kit.get("models") or {}).keys())
        for name in sorted(names):
            found.append(kit_piece(context, held, place, name, model_of.get((place, name)), required))
    return found


def kit_piece(context, held, place, name, identity, required):
    """One kit piece model: in when the kit entry requires the model entry that holds it."""
    key = f"{place}:{name}"
    if identity is None:
        return missing("kit piece", key, [place], "other",
                       "the kit lays it, but the place's stage holds no model of that name (the assembly left it out)")
    address = model_address(context, held, place, identity)
    if address and address in required:
        return Thing("kit piece", key, [place], True)
    if address:
        return missing("kit piece", key, [place], "other", "a model entry holds it, but the kit entry does not require it "
                       "(the kit names a released model by its file, the stage by its fixture_ asset)")
    return missing("kit piece", key, [place], "not exported", "no model entry holds it")


def model_address(context, held, place, identity):
    """The address of the model entry holding a place's model of this identity, or None."""
    for use in context.remembered(("place_models", place), lambda: model_export.place_models(context, place)):
        if use["identity"] == identity:
            return held.addresses.get(("model", context.store.listing(use["output"])[use["asset"]]))
    return None


def surfaces(context, held):
    """Every variant of the surface library."""
    library = context.repo.read_json("data/library/materials.json")
    variants = sorted(name for family in library.get("families", {}).values() for name in family.get("variants", {}))
    ids = {entry_id.split(".")[0] for entry_id in held.ids["surface"]}
    return [Thing("surface", name, [], True) if place_reads.sanitised(name) in ids else
            missing("surface", name, [], "not exported", "no surface entry for this variant") for name in variants]


def effects(context, held):
    """Every emitter a place's effects layer holds."""
    found = []
    for place in context.places:
        for use in effect_export.place_emitters(context, place):
            key = f"{place}:{use['path']}"
            in_library = held.has("effect", use["clip_sha"]) if use["clip_sha"] else any(
                place in record["seen_in"] and record["provenance"].get("prim") == str(use["path"])
                for record in held.of_kind("effect"))
            found.append(Thing("effect", key, [place], True) if in_library else
                         missing("effect", key, [place], "not exported", "no effect entry holds its clip"))
    return found


def lights(context, held):
    """Every sky picture, and every lamp of a place's layers (in when its layer's entry is)."""
    found = []
    for place in context.places:
        output, listing = context.assembly(place)
        for name, sha in sorted(listing.items()):
            if name.startswith("assets/light/") and name.endswith(".png"):
                found.append(Thing("light", f"{place}:{name}", [place], True) if held.has("light", sha) else
                             missing("light", f"{place}:{name}", [place], "not exported", "no light entry holds it"))
        for layer, path, kind in lamps(context, place):
            entry_id = f"{posixpath.splitext(posixpath.basename(layer))[0]}.{place_reads.sanitised(place)}"
            key = f"{place}:{path} ({kind})"
            found.append(Thing("lamp", key, [place], True, note=f"inside layer/{entry_id}")
                         if entry_id in held.ids["layer"] else
                         missing("lamp", key, [place], "not exported", f"its layer entry layer/{entry_id} is missing"))
    return found


def lamps(context, place):
    """(layer file, prim path, light type) of every UsdLux light the place's layers define."""
    output, _ = context.assembly(place)
    found = []
    for layer_name in place_reads.layer_files(context, place):
        layer = Sdf.Layer.OpenAsAnonymous(str(context.tree(output) / layer_name))

        def visit(path, layer=layer, layer_name=layer_name):
            spec = layer.GetObjectAtPath(path)
            if isinstance(spec, Sdf.PrimSpec) and spec.typeName in LIGHTS:
                found.append((layer_name, str(path), spec.typeName))
        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def sounds(context, held):
    """Every sound source of a place."""
    found = []
    for place in context.places:
        for use in sound_export.place_sounds(context, place):
            key = f"{place}:{use['name']}"
            shas = [sha for _, sha, _ in use["audio"].values()]
            found.append(Thing("sound", key, [place], True) if shas and all(held.has("sound", sha) for sha in shas)
                         else missing("sound", key, [place], "not exported", "no sound entry holds its audio"))
    return found


def characters(context, held):
    """Every accepted body and dress, and every look the store holds only close-ups of."""
    found = []
    bodies = set()
    for node in context.store.nodes():
        if node.startswith((character_export.BODY, character_export.DRESS)) and context.store.accepted(node):
            look = character_export.names_of(node)[0]
            bodies.add(look)
            listing = context.store.listing(context.store.accepted(node))
            usd = [sha for name, sha in listing.items() if name.startswith("usd/") and name.endswith(".usdc")]
            found.append(Thing("character", node, [], True) if usd and held.has("character", usd[0]) else
                         missing("character", node, [], "not exported", "no character entry holds its body"))
    for node in context.store.nodes():
        if node.startswith("people-take-") and context.store.accepted(node):
            look = node[len("people-take-"):].replace("_wide-", "-", 1)
            if look not in bodies:
                found.append(missing("character", node, [], "not accepted",
                                     "the store holds close-up pictures of this look but no accepted body"))
    return found


def layers(context, held):
    """Every sublayer of a place's stage (the edit slot rides in the place) and every accepted physics layer."""
    found = []
    for place in context.places:
        _, listing = context.assembly(place)
        for name in place_reads.layer_files(context, place):
            if name == "layers/edit.usda":
                continue
            key = f"{place}:{name}"
            found.append(Thing("layer", key, [place], True) if held.has("layer", listing[name]) else
                         missing("layer", key, [place], "not exported", "no layer entry holds it"))
        physics = context.store.accepted(f"physics-layer@{place}")
        if physics:
            key = f"{place}:layers/physics.usda"
            sha = context.store.listing(physics).get("layers/physics.usda")
            found.append(Thing("layer", key, [place], True) if held.has("layer", sha) else
                         missing("layer", key, [place], "not exported", "no layer entry holds it"))
    return found


def places(context, held):
    """Every place of the world (its own and the ones it only shows)."""
    found = []
    for place in context.places:
        if not context.assembly(place)[0]:
            found.append(missing("place", place, [place], "not accepted", "no accepted assembly holds its stage"))
        elif place_reads.sanitised(place) in held.ids["place"]:
            found.append(Thing("place", place, [place], True))
        else:
            found.append(missing("place", place, [place], "not exported", "no place entry"))
    return found + unlisted_places(context)


def unlisted_places(context):
    """Places other places' neighbours outputs show that the world toml names nowhere."""
    known = set(world_places(context.world))
    shown = set()
    for node in context.store.nodes_of("neighbours"):
        listing = context.store.listing(context.store.accepted(node))
        shown |= {name.split("/")[1] for name in listing if name.startswith("places/") and name.count("/") >= 2}
    return [missing("place", place, [place], "not exported", "only other places show it, and the world toml does not "
                    "name it in `shown`") for place in sorted(shown - known)]


def algorithms(context, held):
    """Every algorithm definition, and every stage that made accepted content no definition names."""
    definitions = [load(path) for path in sorted((context.root / DEFINITIONS).glob("*.toml"))]
    found = [Thing("algorithm", each["name"], [], True) if each["name"] in held.ids["algorithm"] else
             missing("algorithm", each["name"], [], "not exported", "its definition was never exported")
             for each in definitions]
    covered = {each.get("stage") for each in definitions}
    stages = sorted({stage_of(node) for node in context.store.nodes() if context.store.accepted(node)})
    routes = collections.defaultdict(list)
    for stage in stages:
        if stage not in covered and stage not in NOT_MAKERS and not stage.startswith(NOT_MAKER_PREFIXES):
            routes[route_of(stage)].append(stage)
    for route, members in sorted(routes.items()):
        found.append(missing("algorithm", route, [], "not exported",
                             "a route whose stages made accepted content; no definition in definitions/algorithms "
                             "names it (stages: " + ", ".join(members) + ")"))
    return found


def route_of(stage):
    """The route a stage belongs to: its prefix for the many-step routes, else the stage itself."""
    for prefix, route in ROUTES:
        if stage.startswith(prefix):
            return route
    return stage


NOT_MAKER_PREFIXES = ("people-take-",)
# many-step routes, by stage prefix (first match wins): one algorithm each
ROUTES = (("crops-surfaces-", "surfaces (finish route)"), ("surfaces-", "surfaces (finish route)"),
          ("people-", "people (character maker)"), ("crops-", "crops (crop models)"))
KINDS = (("algorithm", algorithms), ("surface", surfaces), ("model", models), ("model gltf form", gltf_forms),
         ("light", lights), ("sound", sounds), ("effect", effects), ("character", characters),
         ("kit", kits), ("layer", layers), ("place", places))


def things(context):
    """Every thing of the world, kind by kind, with whether the library holds it."""
    held = Held(context.root)
    found = []
    for _, read in KINDS:
        found += read(context, held)
    return found, held


def audit(context):
    """The report: per kind the world's count, the library's, what is in and what is missing by reason."""
    found, held = things(context)
    report = {"world": context.world["name"], "commit": context.world["commit"], "kinds": {}}
    by_kind = collections.defaultdict(list)
    for thing in found:
        by_kind[thing.kind].append(thing)
    for kind, group in by_kind.items():
        absent = [thing for thing in group if not thing.held]
        reasons = {reason: [dataclasses.asdict(thing) for thing in absent if thing.reason == reason]
                   for reason in REASONS}
        report["kinds"][kind] = {"world": len(group), "in": len(group) - len(absent), "missing": len(absent),
                                 "entries": len(held.ids.get(kind, {})),
                                 "missing_by_reason": {reason: len(rows) for reason, rows in reasons.items() if rows},
                                 "rows": {reason: rows for reason, rows in reasons.items() if rows}}
    return report


def report_text(report, examples=5):
    """The audit as lines: per kind the counts, then each reason's count, note and first examples."""
    lines = [f"world {report['world']} at {report['commit']}"]
    for kind, found in report["kinds"].items():
        lines.append(f"{kind}: world {found['world']}, in {found['in']}, missing {found['missing']}, "
                     f"entries {found['entries']}")
        for reason, rows in found["rows"].items():
            notes = collections.Counter(row["note"] for row in rows)
            for note, count in notes.most_common():
                keys = [row["key"] for row in rows if row["note"] == note][:examples]
                lines.append(f"  {reason} ({count}): {note}: {', '.join(keys)}")
    return "\n".join(lines) + "\n"
