"""model entries: every made model a place's accepted assembly shows (spec 4.2).

Identity is the gltf the model was built from (score:model_sha256 on its base prim, else inputs.json's models row,
else the usdc's own bytes): one gltf = one entry, the usdc of the first place in sorted order kept, the places showing
it in `seen_in` and their own copies' bytes kept as aliases (the layers are rewritten to the entry through them).
A model's `_glow` part rides in the same entry as `model_glow.usdc`, and the accepted close-up it was made from as
`closeup.png` (owner, 2026-10-20: a locked close-up is handed over to the library, so no work folder holds the only copy).
"""
import concurrent.futures
import hashlib
import multiprocessing
import json
import os
import posixpath
import shutil

from score_lib.admission import made_by
from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.preview import mesh
from score_lib.rewrite import asset_paths, export, joined, open_copy, rewrite
from score_lib.source.store import place_of

MADE_STAGES = ("surfaces-bake", "ship_kit", "crops-surfaces-bake")
PREVIEW_VERSION = "mesh-4"
# a model the ground layer shows is a terrain piece (the terrain adapter's, made through the prop pipeline)
LAYER_TAGS = {"layers/crops.usda": "crop", "layers/machines.usda": "machine", "layers/ground.usdc": "terrain"}
ROUTE_TAGS = {"code": "piece", "model": "prop"}
FIXTURE = "fixture_"
CLOSEUP_STAGE = "closeup"  # score's stages/props closeup@<place>: closeups/<inventory row>.png and closeups.json  # score's tools/usd/scene.py fixture_asset: a released model's asset name


def plan(context):
    """Every model candidate of the world's places."""
    made = made_gltf(context)
    groups = {}
    for place in context.places:
        for use in place_models(context, place):
            groups.setdefault(use["identity"], []).append(use)
    candidates = [candidate(context, identity, uses, made) for identity, uses in sorted(groups.items())]
    draw_previews(context, candidates)
    return candidates


def made_gltf(context):
    """{gltf sha256: {node, output, name}} of every baked gltf in an accepted surfaces-bake, ship_kit or crops bake."""
    found = {}
    for stage in MADE_STAGES:
        for node in context.store.nodes_of(stage):
            output = context.store.accepted(node)
            for name, sha in sorted(context.store.listing(output).items()):
                if name.startswith("made/") and name.endswith(".gltf") and name.count("/") == 1:
                    found.setdefault(sha, {"node": node, "output": output, "name": name})
    return found


def model_assets(listing):
    """The assembly's model files: assets/<name>.usdc directly under assets/, glow parts left to their model."""
    names = [name for name in listing if name.startswith("assets/") and name.count("/") == 1
             and name.endswith(".usdc")]
    return sorted(name for name in names if not (name.endswith("_glow.usdc") and
                                                  name[:-len("_glow.usdc")] + ".usdc" in listing))


def usage(context, place):
    """{asset name: set of layer files naming it} for the place's assembly."""
    found = {}
    for layer in place_reads.layer_files(context, place):
        for name in place_reads.used_assets(context, place, layer):
            found.setdefault(name, set()).add(layer)
    return found


def place_models(context, place):
    """Every model the place's assembly shows: [{place, asset, glow, identity, row, layers, stem}]."""
    output, listing = context.assembly(place)
    if not output:
        return []
    rows = {}
    for row in place_reads.object_prims(context, place):
        rows.setdefault(row["asset"], row)
    inputs = context.store.read_json(output, "inputs.json") or {}
    used = usage(context, place)
    found = []
    for asset in model_assets(listing):
        stem = posixpath.basename(asset)[:-len(".usdc")]
        glow = f"assets/{stem}_glow.usdc"
        row = rows.get(asset, {})
        sha = row.get("model_sha256") or (inputs.get("models", {}).get(stem) or {}).get("sha256")
        identity = sha or f"usdc:{listing[asset]}"
        layers = used.get(asset, set()) | used.get(glow, set())
        found.append({"place": place, "asset": asset, "glow": glow if glow in listing else None, "stem": stem,
                      "identity": identity, "model_sha256": sha, "row": row, "layers": sorted(layers),
                      "output": output})
        model_of = context.memo.setdefault("model_of", {})
        for name in kit_names(stem, row):
            keys = [(place, name)] + ([(place, f"{name}_glow")] if glow in listing else [])
            for key in keys:  # an asset's own stem wins over another asset's alias of the same name
                if name == stem:
                    model_of[key] = identity
                else:
                    model_of.setdefault(key, identity)
    return found


def kit_names(stem, row):
    """The names a place's kit may give a model: its asset stem and, for a released model (a fixture, placed as
    assets/fixture_<its file's stem>.usdc by score's tools/usd/scene.py fixture_asset), that file's own stem."""
    names = [stem]
    if stem.startswith(FIXTURE):
        names.append(stem[len(FIXTURE):])
    model = row.get("model") or ""
    if model.endswith((".gltf", ".glb")):
        names.append(posixpath.splitext(posixpath.basename(model))[0])
    return sorted(set(names))


def dependencies(context, output, name, seen=None):
    """Every file a USD file of an output names, followed through nested USD files: [assembly-relative name]."""
    seen = set() if seen is None else seen
    listing = context.store.listing(output)
    folder = posixpath.dirname(name)
    for path in asset_paths(open_copy(context.tree(output) / name)):
        found = joined(folder, path)
        if found in listing and found not in seen:
            seen.add(found)
            if found.endswith((".usd", ".usda", ".usdc")):
                dependencies(context, output, found, seen)
    return sorted(seen)


def model_files(context, use, work):
    """(files {entry name: path}, known {entry name: sha}, files_from) of one place's model usdc, glow and their
    dependencies, kept at their paths relative to assets/ (a path leaving assets/ is rewritten into deps/)."""
    output = use["output"]
    listing = context.store.listing(output)
    tree = context.tree(output)
    files, known, files_from = {}, {}, {}
    for source, target in ((use["asset"], "model.usdc"), (use["glow"], "model_glow.usdc")):
        if not source:
            continue
        outside = {}
        for dependency in dependencies(context, output, source):
            relative = posixpath.relpath(dependency, "assets")
            if relative.startswith("../"):
                outside[dependency] = f"deps/{posixpath.basename(dependency)}"
                relative = outside[dependency]
            files[relative] = tree / dependency
            known[relative] = listing[dependency]
        if outside:
            files[target] = rewritten_usd(tree, source, outside, work / target)
            files_from[target] = {"path": source, "sha256": listing[source]}
        else:
            files[target] = tree / source
            known[target] = listing[source]
    return files, known, files_from


def rewritten_usd(tree, source, outside, destination):
    """A copy of a USD file with the paths that left its folder pointed at their copies inside the entry."""
    layer = open_copy(tree / source)
    folder = posixpath.dirname(source)
    mapping = {path: "./" + outside[joined(folder, path)] for path in asset_paths(layer)
               if joined(folder, path) in outside}
    rewrite(layer, mapping)
    return export(layer, destination)


def gltf_files(context, made, identity):
    """{entry name: path} and known shas of the gltf form (gltf/<gltf>, its bin and textures), or empty."""
    source = made.get(identity)
    if not source:
        return {}, {}, None
    listing = context.store.listing(source["output"])
    tree = context.tree(source["output"])
    gltf = json.loads(context.store.blob(listing[source["name"]]).read_text())
    folder = posixpath.dirname(source["name"])
    names = [source["name"]] + [joined(folder, item["uri"]) for item in gltf.get("buffers", []) + gltf.get("images", [])
                                if item.get("uri") and not item["uri"].startswith("data:")]
    files, known = {}, {}
    for name in names:
        if name in listing:
            target = "gltf/" + posixpath.relpath(name, folder)
            files[target] = tree / name
            known[target] = listing[name]
    return files, known, source


def made_record(context, source, stem):
    """The bake's report and baked.json row of a model's gltf form, or Nones."""
    if not source:
        return None, None
    model = posixpath.basename(source["name"])[:-len(".gltf")]
    report = context.store.read_json(source["output"], f"made/report-{model}.json")
    baked = (context.store.read_json(source["output"], "baked.json") or {}).get(model)
    return report, baked


def closeup_files(context, source, baked):
    """(files, known, provenance block, made_with) of the close-up a baked model was made from, or empties: the
    accepted closeup@<place> output's closeups/<row>.png, the row the place's prop data (data/props/<place>.json)
    gives the bake's take (baked.json `parts`)."""
    take = (baked or {}).get("parts")
    place = place_of(source["node"]) if source else None
    path = f"data/props/{place}.json"
    if not take or not place or not context.repo.exists(path):
        return {}, {}, None, []
    objects = (context.repo.read_json(path) or {}).get("objects", {})
    row = next((name for name, entry in sorted(objects.items()) if entry.get("take") == take), None)
    node = f"{CLOSEUP_STAGE}@{place}"
    output = context.store.accepted(node)
    name = f"closeups/{row}.png"
    listing = context.store.listing(output) if output and row else {}
    drawn_by = ((context.store.read_json(output, "closeups.json") or {}).get("objects", {}).get(row) or {}).get(
        "drawn_by") if name in listing else None
    # shortcut: a close-up given in the prop data ("given") names no source to admit it by, so only Pro's go in;
    # upgrade when prop data records where a given close-up came from.
    if not drawn_by or drawn_by == "given":
        return {}, {}, None, []
    block = {"node": node, "output": output, "name": name, "row": row, "take": take, "drawn_by": drawn_by}
    return ({"closeup.png": context.tree(output) / name}, {"closeup.png": listing[name]}, block,
            [made_by("close-up: a Nano Banana Pro picture", "nano_banana_pro")])


def kit_route(context, place, stem):
    """The kit's models row of a model ({glows, route}) or None."""
    path = f"data/kit/{place}.json"
    kit = context.remembered(("kit", place), lambda: context.repo.read_json(path) if context.repo.exists(path) else {})
    return (kit.get("models") or {}).get(stem)


def manifest_name(use):
    """The release manifest name a model's rule is matched on: its models/ name, or models/<stem>/."""
    model = use["row"].get("model") or ""
    return model if model.startswith("models/") else f"models/{use['stem']}/"


def made_with(context, use):
    """The model's source, named by its release manifest rule's text (its own route in `what`)."""
    name = manifest_name(use)
    rule = context.release.rule_for(name)
    if not rule:
        return [made_by(f"model {name}", f"no release manifest rule for {name}")]
    return [made_by(f"model: {rule['source']}", rule["licence"])]


def title_of(stem):
    return stem.replace("_", " ").capitalize()


def tags_of(context, uses, first):
    roles = set()
    for use in uses:
        roles |= {LAYER_TAGS[layer] for layer in use["layers"] if layer in LAYER_TAGS}
        route = (kit_route(context, use["place"], use["stem"]) or {}).get("route")
        if route in ROUTE_TAGS:
            roles.add(ROUTE_TAGS[route])
    place_kinds = sorted({tag for use in uses for tag in place_reads.place_kind(context, use["place"])})
    return {"model": sorted(roles), "engine": ["usd"], "place_kind": place_kinds}


def candidate(context, identity, uses, made):
    """One model candidate from every place showing the same gltf."""
    uses = sorted(uses, key=lambda use: (use["place"], use["stem"]))
    first = uses[0]
    work = context.work("model", hashlib.sha256(identity.encode()).hexdigest()[:16])
    files, known, files_from = model_files(context, first, work)
    roles = {name: role_of(name) for name in files}
    gltf, gltf_known, source = gltf_files(context, made, first["model_sha256"])
    files.update(gltf)
    known.update(gltf_known)
    roles.update({name: "gltf" for name in gltf})
    report, baked = made_record(context, source, first["stem"])
    closeup, closeup_known, closeup_block, closeup_made = closeup_files(context, source, baked)
    files.update(closeup)
    known.update(closeup_known)
    roles.update({name: "closeup" for name in closeup})
    record = {"stem": first["stem"], "row": first["row"].get("row"), "kind": first["row"].get("kind"),
              "name": first["row"].get("name"), "model": first["row"].get("model"),
              "kit": kit_route(context, first["place"], first["stem"]), "report": report, "baked": baked,
              "layers": sorted({layer for use in uses for layer in use["layers"]})}
    files["model.json"] = write_json(work / "model.json", record)
    roles["model.json"] = "record"
    files["preview.png"] = work / "preview.png"
    roles["preview.png"] = "picture"
    aliases = alias_rows(context, uses[1:])
    provenance = place_reads.assembly_provenance(context, first["place"])
    provenance.update({"place": first["place"], "model_sha256": first["model_sha256"], "source_name": first["stem"],
                       "gltf": source})
    if closeup_block:
        provenance["closeup"] = closeup_block
    return Candidate(kind="model", base_id=place_reads.sanitised(first["stem"]), identity=identity,
                     title=title_of(first["stem"]), places=[use["place"] for use in uses], files=files, roles=roles,
                     made_with=made_with(context, first) + closeup_made, usd={"root": "model.usdc", "layer": "base",
                                                                          "use": "reference"},
                     provenance=provenance, tags=tags_of(context, uses, first),
                     picture={"file": "preview.png", "made_by": "preview/mesh.py", "of": "model.usdc"},
                     known=known, files_from=files_from, aliases=aliases)


def alias_rows(context, uses):
    """[[store sha, entry file]] of the other places' own copies of the model and its glow part."""
    rows = []
    for use in uses:
        listing = context.store.listing(use["output"])
        rows.append([listing[use["asset"]], "model.usdc"])
        if use["glow"]:
            rows.append([listing[use["glow"]], "model_glow.usdc"])
    return sorted({tuple(row) for row in rows})


def role_of(name):
    if name.endswith((".usdc", ".usda", ".usd")):
        return "usd"
    return "texture"


def preview_key(candidate):
    """The cache key of a model's preview: the preview code's version and the bytes it is drawn from."""
    parts = [PREVIEW_VERSION] + sorted(f"{name} {candidate.known.get(name, '')}" for name in candidate.files
                                       if candidate.roles[name] in ("usd", "texture"))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def draw_previews(context, candidates):
    """Draw every candidate's preview.png (from the cache when it holds one), several at once."""
    jobs = []
    for each in candidates:
        cached = context.cache / "previews" / f"{preview_key(each)}.png" if context.cache else None
        if cached and cached.is_file():
            shutil.copyfile(cached, each.files["preview.png"])
            continue
        source = each.files["model.usdc"]
        jobs.append((str(source), str(each.files["preview.png"]), str(cached) if cached else None))
    workers = max(1, min(8, (os.cpu_count() or 2) - 2))
    if not jobs:
        return
    spawn = multiprocessing.get_context("spawn")  # pxr runs threads; a forked child of it may deadlock
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=spawn) as pool:
        for _ in pool.map(draw_one, jobs, chunksize=4):
            pass


def draw_one(job):
    """Draw one preview, and keep it in the cache when there is one."""
    source, destination, cached = job
    mesh.render(source, destination)
    if cached:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        shutil.copyfile(destination, cached)
