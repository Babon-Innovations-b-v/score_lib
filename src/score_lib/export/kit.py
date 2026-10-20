"""kit entries: one place kit (`data/kit/<place>.json` at the source commit) and, for ship places, the ship_kit
layout (layout.json, plan.json, job JSON) (spec 4.8). The pieces' models are its requires."""
import hashlib
import json

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate
from score_lib.admission import SCORE
from score_lib.preview import plan as plan_picture

SHIP_FILES = ("layout.json", "layout-full.json", "plan.json")


def plan(context):
    """One kit candidate per place whose kit file the repo holds at the commit."""
    return [candidate(context, place) for place in context.places if context.repo.exists(f"data/kit/{place}.json")]


def ship_files(context, place):
    """{entry name: (path, sha)} of the place's accepted ship_kit layout files, or {}."""
    node = f"ship_kit@{place}"
    output = context.store.accepted(node)
    if not output:
        return {}, None
    listing = context.store.listing(output)
    found = {}
    for name in sorted(listing):
        if name in SHIP_FILES or (name.startswith("job-kit-") and name.endswith(".json")):
            found[f"ship_kit/{name}"] = (context.tree(output) / name, listing[name])
    return found, output


def piece_models(context, place, kit):
    """Addresses of the model entries holding the kit's pieces (by the model each piece names)."""
    names = {piece.get("model") for piece in kit.get("pieces") or [] if piece.get("model")}
    names |= set((kit.get("models") or {}).keys())
    model_of = context.memo.get("model_of", {})
    found = set()
    for name in sorted(names):
        identity = model_of.get((place, name))
        address = context.addresses.get(("model", identity)) if identity else None
        if address:
            found.add(address)
    return sorted(found)


def candidate(context, place):
    """One kit candidate."""
    source = f"data/kit/{place}.json"
    kit = context.repo.read_json(source)
    work = context.work("kit", place)
    files = {"kit.json": context.repo.export(source, work / "kit.json")}
    roles = {"kit.json": "record"}
    known = {}
    ships, ship_output = ship_files(context, place)
    for name, (path, sha) in ships.items():
        files[name] = path
        known[name] = sha
        roles[name] = "record"
    drawn = plan_picture.render(kit, work / "preview.png")
    files["preview.png"] = work / "preview.png"
    roles["preview.png"] = "picture"
    requires = piece_models(context, place, kit)
    identity = hashlib.sha256(json.dumps([hashlib.sha256(context.repo.read_bytes(source)).hexdigest(),
                                          sorted(known.values())]).encode()).hexdigest()
    provenance = {"source": f"{source} at the world's commit", "place": place, "pieces": len(kit.get("pieces") or []),
                  "ship_kit": place_reads.node_provenance(context, f"ship_kit@{place}", ship_output)
                  if ship_output else None}
    return Candidate(kind="kit", base_id=place_reads.sanitised(place), identity=identity,
                     title=f"{place.replace('_', ' ').capitalize()} kit", places=[place], files=files, roles=roles,
                     made_with=[dict(SCORE)], requires=requires, provenance=provenance,
                     tags={"place_kind": place_reads.place_kind(context, place)},
                     picture={"file": "preview.png", "made_by": "preview/plan.py",
                              "of": f"kit.json pieces ({drawn}) from above"},
                     known=known)

