"""character entries: each accepted body look (`people-body-<look>`) and dress (`people-dress-<look>[-in-<place>]`):
the UsdSkel body and its textures, the gltf body, look.json and dress.json (spec 4.7). rig.npz stays out.

made_with: the finish record's pinned models (none are pinned for these rows in world 1) and the models the people
route names (score/stages/people/__init__.py:1-3: Pixal3D's shape, the surface stage's paint, SOMA-X's skeleton), as
the paper's Table C1 lists them.

A body.glb whose far body (score's crowd level of detail) stands in its default scene is carried with it moved out of
it (`levels_apart`, as score's tools/characters/people/glb.py writes a body since score #156): a reader that opens the
file sees one body, and a reader that switches levels by distance opens the scene LEVELS_SCENE. The source's sha256
is kept in files_from."""
import json
import pathlib
import posixpath
import struct

from score_lib.export import places as place_reads
from score_lib.export.model import dependencies
from score_lib.blobs import file_sha256
from score_lib.export.run import Candidate
from score_lib.admission import SCORE, made_by

ROUTE = (
    ("the body's shape", "pixal3d"),
    ("the finish map laid on the shape (surface stage)", "segvigen"),
    ("finish maps (surface stage)", "nano_banana_pro"),
    ("finishes listed and named (surface stage)", "gemini_31_pro"),
    ("the skeleton fitted inside the shape and its skin weights", "soma_x"),
)
BODY = "people-body-"
DRESS = "people-dress-"
GLTF = "body.glb"
FAR = "far"  # the far body's node
NEAR_SCENE, LEVELS_SCENE = "near", "levels"  # the default scene, the near body alone; every level on one skeleton


def plan(context):
    """One candidate per accepted body and dress row."""
    found = []
    for node in context.store.nodes():
        if node.startswith((BODY, DRESS)) and context.store.accepted(node):
            found.append(candidate(context, node))
    return found


def names_of(node):
    """(look, place or None, base id, entry id) of a body or dress row."""
    dress = node.startswith(DRESS)
    rest = node[len(DRESS if dress else BODY):]
    look, place = rest, None
    if "-in-" in rest:
        look, place = rest.rsplit("-in-", 1)
    base = place_reads.sanitised(("dress_" if dress else "body_") + look)
    return look, place, base, f"{base}.{place_reads.sanitised(place)}" if place else base


def usd_files(context, output, listing):
    """{entry name: path}, known shas: the row's usd/<name>.usdc as character.usdc with its dependencies kept at their
    paths relative to usd/."""
    tree = context.tree(output)
    usdc = sorted(name for name in listing if name.startswith("usd/") and name.count("/") == 1
                  and name.endswith(".usdc"))
    files, known = {}, {}
    if not usdc:
        return files, known
    files["character.usdc"] = tree / usdc[0]
    known["character.usdc"] = listing[usdc[0]]
    for dependency in dependencies(context, output, usdc[0]):
        relative = posixpath.relpath(dependency, "usd")
        files[relative] = tree / dependency
        known[relative] = listing[dependency]
    return files, known


def take_picture(context, look):
    """(path, sha, name) of the matching people-take close-up png, or None."""
    name, _, outfit = look.partition("-")
    for node in context.store.nodes():
        if node == f"people-take-{name}_wide-{outfit}" and context.store.accepted(node):
            output = context.store.accepted(node)
            listing = context.store.listing(output)
            pictures = sorted(each for each in listing if each.startswith("closeup/") and each.endswith(".png"))
            if pictures:
                return context.tree(output) / pictures[0], listing[pictures[0]], f"{node}: {pictures[0]}"
    return None


def candidate(context, node):
    """One character candidate."""
    output = context.store.accepted(node)
    listing = context.store.listing(output)
    look, place, base, entry_id = names_of(node)
    files, known = usd_files(context, output, listing)
    roles = {name: "usd" if name.endswith(".usdc") else "texture" for name in files}
    for name in sorted(listing):
        if name in ("body.glb", "look.json", "dress.json") or (name.startswith("checks/") and name.endswith(".json")):
            files[name] = context.tree(output) / name
            known[name] = listing[name]
            roles[name] = "gltf" if name.endswith(".glb") else "record"
    picture = {"reason": "no accepted people-take close-up of this look"}
    found = take_picture(context, look)
    if found:
        files["picture.png"], known["picture.png"], source = found
        roles["picture.png"] = "picture"
        picture = {"file": "picture.png", "made_by": "source picture", "of": source}
    provenance = place_reads.node_provenance(context, node, output)
    provenance.update({"place": place, "look": look})
    made_with = [dict(SCORE)] + [made_by(what, source) for what, source in ROUTE]
    made_with += [made_by(f"pinned model {model['name']}", model["name"]) for model in provenance["models"]]
    made = Candidate(kind="character", base_id=base, identity=output, title=look.replace("-", " ").capitalize(),
                     places=[place] if place in context.places else [], files=files, roles=roles,
                     made_with=made_with,
                     usd={"root": "character.usdc", "layer": "characters", "use": "payload"} if "character.usdc"
                     in files else None,
                     provenance=provenance, tags={"character": ["dress" if node.startswith(DRESS) else "body"],
                                                  "engine": ["usd"]},
                     picture=picture, known=known)
    made.entry_id = entry_id
    carried_apart(context, made)
    return made


def read_glb(path):
    """(document, the rest of the file after the JSON chunk, its chunk headers included)."""
    data = pathlib.Path(path).read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length]), data[20 + length:]


def write_glb(path, document, rest):
    """The file with its JSON chunk replaced and the rest as it was."""
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I", len(text)) + b"JSON" + text + rest
    pathlib.Path(path).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)


def shown_nodes(document):
    """The nodes the default scene holds, roots first."""
    found, waiting = [], list(document.get("scenes", [{}])[document.get("scene", 0)].get("nodes", []))
    while waiting:
        node = waiting.pop(0)
        found.append(node)
        waiting.extend(document["nodes"][node].get("children", []))
    return found


def far_shown(document):
    """The far body's node when the default scene holds it, else None."""
    return next((node for node in shown_nodes(document) if document["nodes"][node].get("name") == FAR), None)


def levels_apart(document):
    """The document with its far body out of the default scene (NEAR_SCENE, the roots without it) and in LEVELS_SCENE
    as a root of its own (a skinned mesh's node is placed by its joints), every other mesh node of the default scene
    naming it as its lower level (MSFT_lod); the document as it is when the default scene holds no far body."""
    far = far_shown(document)
    if far is None:
        return document
    nodes = [dict(node, children=[child for child in node["children"] if child != far])
             if far in node.get("children", []) else dict(node) for node in document["nodes"]]
    roots = [root for root in document["scenes"][document.get("scene", 0)]["nodes"] if root != far]
    for number in shown_nodes(dict(document, nodes=nodes, scene=0, scenes=[{"nodes": roots}])):
        if "mesh" in nodes[number]:
            nodes[number]["extensions"] = dict(nodes[number].get("extensions", {}), MSFT_lod={"ids": [far]})
    return dict(document, nodes=nodes, scene=0,
                scenes=[{"name": NEAR_SCENE, "nodes": roots}, {"name": LEVELS_SCENE, "nodes": [*roots, far]}],
                extensionsUsed=sorted({*document.get("extensionsUsed", []), "MSFT_lod"}))


def carried_apart(context, candidate):
    """The candidate's body.glb replaced by its levels_apart copy in the scratch space when its far body stands in
    its default scene, the source's sha256 kept in files_from."""
    if GLTF not in candidate.files:
        return
    document, rest = read_glb(candidate.files[GLTF])
    if far_shown(document) is None:
        return
    target = context.work("levels_apart", candidate.kind, candidate.identity) / GLTF
    write_glb(target, levels_apart(document), rest)
    source = candidate.known.pop(GLTF, None) or file_sha256(candidate.files[GLTF])
    candidate.files[GLTF] = target
    candidate.files_from.setdefault(GLTF, {"path": GLTF, "sha256": source})
