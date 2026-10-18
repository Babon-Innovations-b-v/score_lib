"""ground entries: each planet's terrain (`terrain` node: heights, skin, zones, shading and plan pictures,
ground.json, rocks.json, measures.json, the diorama pictures and the depth the relief was read from) as
ground/<planet>, and each place ground cut from it (places/<place>.usdc) as ground/<planet>.<place> (spec 4.8)."""
from score_lib.export import places as place_reads
from score_lib.export.run import Candidate
from score_lib.admission import SCORE, made_by
from score_lib.preview import mesh
from score_lib.rewrite import asset_paths, export, open_copy, rewrite

NODE = "terrain"
PLANET_FILES = ("ground.json", "heights.png", "shading.png", "skin.png", "zones.png", "plan.png", "rocks.json",
                "measures.json", "diorama/c0.png", "diorama/c1.png", "diorama/c2.png", "diorama/c3.png",
                "diorama/chosen-depth.npy")
# The models the terrain stage runs (score paper/source/score.md Table C1; algorithms.md section 11).
MADE_WITH = (
    ("ground relief", "depth_anything_v2"),
    ("ground skins", "nano_banana_pro"),
    ("diorama pictures the relief is read from", "qwen_image_edit"),
)


def plan(context):
    """The planets and the place grounds of the accepted terrain."""
    output = context.store.accepted(NODE)
    if not output:
        return []
    listing = context.store.listing(output)
    planets = sorted({name.split("/", 1)[0] for name in listing if "/" in name})
    found = [planet_candidate(context, output, listing, planet) for planet in planets]
    for name in sorted(listing):
        parts = name.split("/")
        if len(parts) == 3 and parts[1] == "places" and name.endswith(".usdc"):
            found.append(place_candidate(context, output, listing, parts[0], parts[2][:-len(".usdc")]))
    return found


def made_with():
    return [dict(SCORE)] + [made_by(what, source) for what, source in MADE_WITH]


def provenance(context, output, planet, place=None):
    found = place_reads.node_provenance(context, NODE, output)
    found.update({"planet": planet, "place": place})
    return found


def requires(context):
    algorithm = context.by_id.get(("algorithm", "terrain_landforms"))
    return [algorithm] if algorithm else []


def planet_candidate(context, output, listing, planet):
    """ground/<planet>: the planet's pictures and records."""
    tree = context.tree(output)
    files, known, roles = {}, {}, {}
    for name in PLANET_FILES:
        source = f"{planet}/{name}"
        if source in listing:
            files[name] = tree / source
            known[name] = listing[source]
            roles[name] = "picture" if name.endswith(".png") else "record"
    picture = {"file": "skin.png", "made_by": "source picture", "of": f"{planet}/skin.png"} if "skin.png" in files \
        else {"reason": "the terrain output holds no skin.png for this planet"}
    made = Candidate(kind="ground", base_id=planet, identity=f"{output}/{planet}",
                     title=f"{planet.capitalize()} ground", places=[], files=files, roles=roles,
                     made_with=made_with(), provenance=provenance(context, output, planet),
                     requires=requires(context), tags={"place_kind": [planet, "outside"]}, picture=picture,
                     known=known)
    made.entry_id = planet
    return made


def with_neighbours(context, source, destination):
    """(the ground file, neighbour addresses): a place ground that references neighbouring places' roots is copied
    with those references pointed at the place entries; one that references none is kept as stored."""
    layer = open_copy(source)
    mapping, neighbours = {}, []
    for path in asset_paths(layer):
        found = place_reads.neighbour_path(context, path)
        if found:
            mapping[path] = found[0]
            neighbours.append(found[1])
    if not mapping:
        return source, []
    rewrite(layer, mapping)
    return export(layer, destination), sorted(set(neighbours))


def shown_in(context, sha):
    """Places whose assembly's ground layer is these bytes."""
    return [place for place in context.places if context.assembly(place)[1].get("layers/ground.usdc") == sha]


def place_candidate(context, output, listing, planet, place):
    """ground/<planet>.<place>: one place's ground cut from the planet."""
    source = f"{planet}/places/{place}.usdc"
    sha = listing[source]
    work = context.work("ground", planet, place)
    ground, neighbours = with_neighbours(context, context.tree(output) / source, work / "ground.usdc")
    files = {"ground.usdc": ground}
    mesh.render(files["ground.usdc"], work / "preview.png")
    files["preview.png"] = work / "preview.png"
    seen = shown_in(context, sha)
    made = Candidate(kind="ground", base_id=f"{planet}_{place}", identity=sha,
                     title=f"{place.replace('_', ' ').capitalize()} ground ({planet})", places=seen,
                     files=files, roles={"ground.usdc": "usd", "preview.png": "picture"}, made_with=made_with(),
                     usd={"root": "ground.usdc", "layer": "ground", "use": "payload", "neighbours": neighbours},
                     provenance=provenance(context, output, planet, place) | {"source_name": source},
                     requires=requires(context),
                     tags={"place_kind": sorted(set(place_reads.place_kind(context, place)) | {planet}),
                           "engine": ["usd"]},
                     picture={"file": "preview.png", "made_by": "preview/mesh.py", "of": "ground.usdc"},
                     known={} if neighbours else {"ground.usdc": sha},
                     files_from={"ground.usdc": {"path": source, "sha256": sha}} if neighbours else {})
    made.entry_id = f"{planet}.{place_reads.sanitised(place)}"
    return made

