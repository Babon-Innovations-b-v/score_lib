"""character entries: each accepted body look (`people-body-<look>`) and dress (`people-dress-<look>[-in-<place>]`):
the UsdSkel body and its textures, the gltf body, look.json and dress.json (spec 4.7). rig.npz stays out.

Licence components: the finish record's pinned models (none are pinned for these rows in world 1) and the models the
people route names (score/stages/people/__init__.py:1-3: Pixal3D's shape, the surface stage's paint, SOMA-X's
skeleton) with their licences from the paper's Table C1."""
import posixpath

from score_lib.export import places as place_reads
from score_lib.export.model import dependencies
from score_lib.export.run import Candidate
from score_lib.licences import SCORE_COMPONENT

TABLE = "score paper/source/score.md Table C1; route in score/stages/people/__init__.py:1-3"
ROUTE = (
    ("Pixal3D: picture to 3D (the body's shape)", "MIT"),
    ("Pixal3D's encoder (DINOv3)", "DINOv3 licence"),
    ("SegviGen on TRELLIS.2: the finish map laid on the shape (surface stage)", "MIT"),
    ("Nano Banana Pro: finish maps (surface stage)", "paid API"),
    ("Gemini 3.1 Pro: finishes listed and named (surface stage)", "paid API"),
    ("SOMA-X and MHR: the skeleton", "Apache-2.0"),
)
BODY = "people-body-"
DRESS = "people-dress-"


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
    components = [dict(SCORE_COMPONENT)] + [{"what": what, "licence": text, "source": TABLE} for what, text in ROUTE]
    components += [{"what": f"pinned model {model['name']}", "licence": model["licence"], "source": model["source"]}
                   for model in provenance["models"]]
    made = Candidate(kind="character", base_id=base, identity=output, title=look.replace("-", " ").capitalize(),
                     places=[place] if place in context.places else [], files=files, roles=roles,
                     components=components,
                     usd={"root": "character.usdc", "layer": "characters", "use": "payload"} if "character.usdc"
                     in files else None,
                     provenance=provenance, tags={"character": ["dress" if node.startswith(DRESS) else "body"],
                                                  "engine": ["usd"]},
                     picture=picture, known=known)
    made.entry_id = entry_id
    return made
