"""sound entries: each sound source of a place (`sound@<place>` sound.json and its audio files) with its SpatialAudio
prim from the assembly's sound layer (spec 4.6). Sources with the same audio and levels in several places are one
entry."""
import hashlib
import json

from pxr import Sdf

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.preview import audio
from score_lib.rewrite import asset_paths, copy_prim, export, joined, rewrite

LEVELS = ("volume_db", "bus", "reach", "loops", "category")
RECORD = ("volume_db", "bus", "reach", "loops", "category", "prompt", "seed", "clap", "takes", "kept", "credits",
          "made_by", "chosen_by", "ground_thud", "brief_seconds")


def plan(context):
    """One candidate per distinct sound source across the places."""
    groups = {}
    for place in context.places:
        for use in place_sounds(context, place):
            groups.setdefault(use["identity"], []).append(use)
    return [candidate(context, uses) for _, uses in sorted(groups.items(), key=lambda item: item[1][0]["name"])]


def sound_output(context, place):
    """The sound output the place's assembly took (origin.json), else the accepted one."""
    output, _ = context.assembly(place)
    origin = context.store.read_json(output, "origin.json") if output else None
    return ((origin or {}).get("layers") or {}).get("sound") or context.store.accepted(f"sound@{place}")


def spatial_prims(context, place):
    """{score:sound:name: SpatialAudio prim path} of the assembly's sound layer (first path in sorted order)."""
    output, listing = context.assembly(place)
    if "layers/sound.usda" not in listing:
        return {}
    layer = Sdf.Layer.OpenAsAnonymous(str(context.tree(output) / "layers/sound.usda"))
    found = {}

    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.PrimSpec) and spec.typeName == "SpatialAudio":
            name = spec.attributes["score:sound:name"].default if "score:sound:name" in spec.attributes else spec.name
            found.setdefault(name, []).append(path)
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return {name: sorted(paths, key=str)[0] for name, paths in found.items()}


def audio_source(context, place, sound_listing, name):
    """(path, sha, assembly-or-output name) of one audio file the sound record names (files/sound/<rest>): the
    assembly's copy (assets/sound/<rest>) when it holds one, else the sound output's."""
    rest = name[len("files/sound/"):] if name.startswith("files/sound/") else name
    output, listing = context.assembly(place)
    in_assembly = f"assets/sound/{rest}"
    if in_assembly in listing:
        return context.tree(output) / in_assembly, listing[in_assembly], in_assembly, rest
    return None, sound_listing.get(name), name, rest


def place_sounds(context, place):
    """Every sound source of a place: [{place, name, record, audio{rest: (path, sha, source name)}, prim,
    identity, output}]."""
    output = sound_output(context, place)
    if not output:
        return []
    record = context.store.read_json(output, "sound.json") or {}
    sound_listing = context.store.listing(output)
    tree = context.tree(output)
    prims = spatial_prims(context, place)
    found = []
    for name, sound in sorted((record.get("sounds") or {}).items()):
        files = {}
        for each in sound.get("files", []):
            path, sha, source, rest = audio_source(context, place, sound_listing, each)
            files[rest] = (path or tree / each, sha, source)
        levels = {key: sound.get(key) for key in LEVELS}
        identity = hashlib.sha256(json.dumps([name, sorted(sha for _, sha, _ in files.values()), levels],
                                             sort_keys=True).encode()).hexdigest()
        found.append({"place": place, "name": name, "record": sound, "audio": files, "prim": prims.get(name),
                      "identity": identity, "output": output})
    return found


def sound_usda(context, use, work):
    """The source's SpatialAudio prim copied to /<name>, its file paths pointed at ./audio/<rest>; None when the
    place's sound layer has no prim for it."""
    if not use["prim"]:
        return None
    output, _ = context.assembly(use["place"])
    source = Sdf.Layer.OpenAsAnonymous(str(context.tree(output) / "layers/sound.usda"))
    layer = copy_prim(source, use["prim"], "sound.usda", place_reads.sanitised(use["name"]), kind="SpatialAudio")
    mapping = {}
    for path in asset_paths(layer):
        found = joined("layers", path)
        if found.startswith("assets/sound/"):
            mapping[path] = f"./audio/{found[len('assets/sound/'):]}"
    rewrite(layer, mapping)
    return export(layer, work / "sound.usda")


def components(sound):
    """One licence component per credited file."""
    found = []
    for credit in sound.get("credits", []):
        found.append({"what": credit.get("file"), "licence": credit.get("licence"),
                      "source": f"{credit.get('source')}; author: {credit.get('author')}"})
    return found


def candidate(context, uses):
    """One sound candidate."""
    uses = sorted(uses, key=lambda use: use["place"])
    first = uses[0]
    work = context.work("sound", first["identity"][:16])
    files, roles, known = {}, {}, {}
    for rest, (path, sha, _) in sorted(first["audio"].items()):
        files[f"audio/{rest}"] = path
        roles[f"audio/{rest}"] = "audio"
        known[f"audio/{rest}"] = sha
    record = {key: first["record"].get(key) for key in RECORD}
    record["name"] = first["name"]
    record["files"] = sorted(files)
    files["sound.json"] = write_json(work / "sound.json", record)
    roles["sound.json"] = "record"
    usda = sound_usda(context, first, work)
    if usda:
        files["sound.usda"] = usda
        roles["sound.usda"] = "usd"
    picture = {"reason": "no audio file"}
    if first["audio"]:
        kept = sorted(first["audio"])[0]
        audio.render(files[f"audio/{kept}"], work / "preview.png")
        files["preview.png"] = work / "preview.png"
        roles["preview.png"] = "picture"
        picture = {"file": "preview.png", "made_by": "preview/audio.py", "of": f"audio/{kept}"}
    provenance = place_reads.node_provenance(context, f"sound@{first['place']}", first["output"])
    provenance.update({"place": first["place"], "prim": str(first["prim"]) if first["prim"] else None,
                       "files_source": {f"audio/{rest}": source for rest, (_, _, source) in first["audio"].items()}})
    places = [use["place"] for use in uses]
    return Candidate(kind="sound", base_id=place_reads.sanitised(first["name"]), identity=first["identity"],
                     title=first["name"].replace("_", " ").capitalize(), places=places, files=files, roles=roles,
                     components=components(first["record"]),
                     usd={"root": "sound.usda", "layer": "sound", "use": "reference"} if usda else None,
                     provenance=provenance,
                     tags={"category": [first["record"].get("category")] if first["record"].get("category") else [],
                           "made_by": [first["record"].get("made_by")] if first["record"].get("made_by") else [],
                           "engine": ["usd"],
                           "place_kind": sorted({tag for each in places for tag in place_reads.place_kind(context, each)})},
                     picture=picture, known=known)

