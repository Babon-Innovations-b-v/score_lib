"""An entry's preview clip (owner, 2026-10-20): a short film of the item that score renders in cloud Blender with the
films' renderer and light rig as a step of the item's handover, so what the cockpit shows is what ships. This library
says what each clip opens and keeps the clip; it renders nothing.

    "clip": {"file": "clip/clip.mp4", "sha256", "bytes", "source": <what it was made from: web3d.record.source_of>,
             "shelf", "frames", "seconds", "size": [wide, tall], "made_by"}

with the file in `files` (role `clip`) and its bytes in the blob store. A clip stands while the entry's content is
what it was made from; an export that writes the entry again from the same content carries it (`carried`). Only a
draft takes a clip: a locked entry is never rewritten.

    score-lib clip-plan ADDRESS... --into DIR       DIR/clip-plan.json: what each clip opens, its entries fetched
    score-lib clip-attach --clips FILE --out FILE   each clip into its draft entry, the index rebuilt, saved; a report

A plan row: {address, kind, shelf, open (the stage to open, absolute), search (the search path root), focus (prim
paths the clip frames, or null: the whole stage), points (a lamp's spots, or null), material (a surface's material
prim), start, end, per_second (its time codes; an effect's one loop from where it shows most, `busiest_loop`), why
("" when it has a clip to make). The entries are fetched with their pictures (a sky's dome picture is one).
"""
import json
import pathlib

from score_lib import entry as entries
from score_lib import index
from score_lib.blobs import file_sha256
from score_lib.fetch import fetch
from score_lib.web3d.record import SKIPPED_ROLES, source_of

FILE = "clip/clip.mp4"
ROLE = "clip"
KEYS = ("sha256", "bytes", "shelf", "frames", "seconds", "size", "made_by")
# Every shelf with a clip; a sound, an algorithm (its demo), a place (its films) and the other layers have none.
SHELVES = ("model", "character", "kit", "effect", "surface", "sky", "lamp", "motion")
PLAN = "clip-plan.json"


def shelf_of(record):
    """The clip's shelf: the kind, a light's sky, a place's lamps (layer light.<place>) or its motion (layer
    motion.<place>); None where no clip is made."""
    kind = record["kind"]
    if kind == "light":
        return "sky"
    if kind == "layer":
        return {"light": "lamp", "motion": "motion"}.get(record["id"].split(".", 1)[0])
    return kind if kind in SHELVES else None


def stands(record):
    """Whether the entry holds a clip made from its content as it is."""
    made = record.get("clip")
    return bool(made) and FILE in record["files"] and made.get("source") == source_of(record)


def carried(record, previous):
    """The record with the previous version's clip carried over when it was made from the same content; else the
    record as it is."""
    if not previous or not stands(previous) or previous["clip"]["source"] != source_of(record):
        return record
    return {**record, "files": {**record["files"], FILE: previous["files"][FILE]}, "clip": previous["clip"]}


def sampled_codes(stage, focus):
    """(first, last) of the time samples on the prims under `focus` (every prim when None), or None when none is
    sampled: an entry whose own layer names no time range (an effect's value clips answer through its points)."""
    from pxr import Usd
    roots = [stage.GetPrimAtPath(path) for path in focus] if focus else [stage.GetPseudoRoot()]
    codes = [code for root in roots if root for prim in Usd.PrimRange(root) for attribute in prim.GetAttributes()
             for code in attribute.GetTimeSamples()]
    return (min(codes), max(codes)) if codes else None


def time_range(own, stage, focus, per_second):
    """{start, end, per_second} the clip plays: the entry's own layer's time codes where it names them, else the span
    of its samples (`sampled_codes`), else none; its rate the layer's, else its record's."""
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(own))
    span = (layer.startTimeCode, layer.endTimeCode) if layer.HasStartTimeCode() and layer.HasEndTimeCode() \
        else sampled_codes(stage, focus) or (0.0, 0.0)
    rate = layer.timeCodesPerSecond if layer.HasTimeCodesPerSecond() else per_second or 24.0
    return {"start": float(span[0]), "end": float(span[1]), "per_second": float(rate)}


def busiest_loop(stage, played):
    """`played` as one of the effect's loops (its emitters' longest `score:effect:loop` seconds) from the time code in
    its first loop where its points show most (their `primvars:displayOpacity` summed), so the clip opens on the
    effect rather than an empty frame (a breath let out every 20 s over a 600 s stage, squeezed into the clip, opened
    blank); `played` as it is when no emitter names a loop or no opacity is sampled in it."""
    from pxr import Gf, UsdGeom
    loops = [prim.GetAttribute("score:effect:loop").Get() for prim in stage.Traverse()
             if prim.HasAttribute("score:effect:loop")]
    loop = max((float(seconds) for seconds in loops if seconds), default=0.0) * played["per_second"]
    opacities = [prim.GetAttribute("primvars:displayOpacity") for prim in stage.Traverse() if prim.IsA(UsdGeom.Points)]
    window = Gf.Interval(played["start"], played["start"] + loop)
    codes = sorted({code for attribute in opacities if attribute for code in attribute.GetTimeSamplesInInterval(window)})
    if not loop or not codes:
        return played
    peak = max(codes, key=lambda code: sum(sum(attribute.Get(code) or ()) for attribute in opacities if attribute))
    return {**played, "start": float(peak), "end": float(peak + loop)}


def timed(opened, played):
    """The clip's own root layer given the time it plays (a root layer's time codes are the stage's)."""
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(opened))
    layer.startTimeCode, layer.endTimeCode = played["start"], played["end"]
    layer.timeCodesPerSecond = layer.framesPerSecond = played["per_second"]
    layer.Save()


def hidden_paths(stage, keep):
    """The topmost prims under which `keep` keeps nothing: what a kit's clip leaves out of its place's base layer."""
    def kept_under(prim):
        return keep(prim) or any(kept_under(child) for child in prim.GetChildren())

    found, pending = [], list(stage.GetPseudoRoot().GetChildren())
    while pending:
        prim = pending.pop()
        if not kept_under(prim):
            found.append(str(prim.GetPath()))
        elif not keep(prim):
            pending += prim.GetChildren()
    return sorted(found)


def moving_paths(layer_path):
    """The prims a motion layer moves: those with an attribute sampled over time."""
    from pxr import Sdf
    layer, found = Sdf.Layer.FindOrOpen(str(layer_path)), set()

    def visit(path):
        if path.IsPropertyPath() and layer.GetNumTimeSamplesForPath(path):
            found.add(str(path.GetPrimPath()))
    layer.Traverse(layer.pseudoRoot.path, visit)
    return sorted(found)


def light_points(stage):
    """Each UsdLux light's spot in the stage, as [x, y, z]."""
    from pxr import Usd, UsdGeom, UsdLux
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    return [list(cache.GetLocalToWorldTransform(prim).ExtractTranslation()) for prim in stage.Traverse()
            if prim.HasAPI(UsdLux.LightAPI)]


def first_material(stage):
    """The path of the stage's first UsdShade Material, or None."""
    from pxr import UsdShade
    return next((str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(UsdShade.Material)), None)


def write_open(folder, sublayers, hidden):
    """clip.usda in `folder`: the given layers, strongest first, with the hidden prims made inactive; its path."""
    from pxr import Sdf
    path = pathlib.Path(folder) / "clip.usda"
    layer = Sdf.Layer.CreateNew(str(path))
    layer.subLayerPaths[:] = [str(each) for each in sublayers]
    for hidden_path in hidden:
        spec = Sdf.CreatePrimInLayer(layer, hidden_path)
        spec.specifier, spec.active = Sdf.SpecifierOver, False
    layer.Save()
    return path


def newest_place(root, place):
    """The newest place entry of a place that is not withdrawn, or None."""
    found = [record for record in entries.versions(root, "place", place) if record["state"] != "withdrawn"]
    return found[-1] if found else None


def what_opens(root, blobs, record, shelf, into, work):
    """(the layers the clip opens, strongest first; the prims hidden; the prims it frames) of one entry, its entries
    fetched into `into`."""
    from score_lib.web3d import build  # the export imports this module's `carried`: build stays out of its imports
    if shelf in ("model", "character", "kit"):
        planned = build.plan(root, blobs, record, into, work)
        hidden = []
        if planned.keep is not None:
            from pxr import Usd
            hidden = hidden_paths(Usd.Stage.Open(str(planned.path)), planned.keep)
        return [planned.path], hidden, None
    # its pictures too: a sky's dome picture is a `picture` file its sky.usda opens
    fetch(root, blobs, [record["address"]], into, with_requires=True, link=True,
          left_out_roles=tuple(role for role in SKIPPED_ROLES if role != "picture"))
    own = build.entry_folder(into, record["address"]) / record["usd"]["root"]
    if shelf != "motion":
        return [own], [], None
    place = newest_place(root, record["id"].split(".", 1)[1])
    if place is None:
        raise LookupError(f"{record['address']}: no place entry of its place to play its move in")
    build.fetched(root, blobs, [place["address"]], into)
    return [own, build.entry_folder(into, place["address"]) / place["usd"]["root"]], [], moving_paths(own)


def plan_row(root, blobs, address, into):
    """One entry's plan row (module docstring); `why` says why no clip is made for it."""
    from pxr import Usd
    record = entries.load_address(root, address)
    row = {"address": address, "kind": record["kind"] if record else None, "shelf": None, "open": None,
           "search": str(into), "focus": None, "points": None, "material": None, "start": None, "end": None,
           "per_second": None, "why": ""}
    if record is None:
        return {**row, "why": "not in the library"}
    shelf = row["shelf"] = shelf_of(record)
    if shelf is None:
        return {**row, "why": f"no clip for this {record['kind']}"}
    if record["state"] != "draft":
        return {**row, "why": f"{record['state']}: only a draft takes a clip"}
    if stands(record):
        return {**row, "why": "its clip stands"}
    folder = pathlib.Path(into) / "items" / address.replace("/", "-").replace("@", "-")
    (folder / "work").mkdir(parents=True, exist_ok=True)
    sublayers, hidden, focus = what_opens(root, blobs, record, shelf, into, folder / "work")
    opened = write_open(folder, sublayers, hidden)
    stage = Usd.Stage.Open(str(opened))
    played = time_range(sublayers[0], stage, focus, (record["usd"] or {}).get("time_codes_per_second")) \
        if shelf in ("effect", "motion") else {"start": 0.0, "end": 0.0, "per_second": 24.0}
    if shelf == "effect":
        played = busiest_loop(stage, played)
    timed(opened, played)
    return {**row, "open": str(opened), "focus": focus, **played,
            "points": light_points(stage) if shelf == "lamp" else None,
            "material": first_material(stage) if shelf == "surface" else None}


def plan(root, blobs, addresses, into):
    """Every address's plan row, written to `<into>/clip-plan.json`; the rows. Entries are opened with `into` as the
    search path (their score_lib/... paths)."""
    from pxr import Ar
    into = pathlib.Path(into).resolve()
    into.mkdir(parents=True, exist_ok=True)
    Ar.DefaultResolver.SetDefaultSearchPath([str(into)])
    rows = []
    for address in addresses:
        try:
            rows.append(plan_row(root, blobs, address, into))
        except (LookupError, ValueError, RuntimeError) as failed:  # one entry's plan fails; the others go on
            rows.append({"address": address, "why": f"could not plan: {failed}"})
    (into / PLAN).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    return rows


def attached(root, blobs, row):
    """Put one clip ({address, file, shelf, frames, seconds, size, made_by}) into its draft entry; '' or why not."""
    record = entries.load_address(root, row["address"])
    if record is None:
        return "not in the library"
    if record["state"] != "draft":
        return f"{record['state']}: only a draft takes a clip"
    path = pathlib.Path(row["file"])
    sha = file_sha256(path)
    blobs.put(sha, path)
    files = {**record["files"], FILE: {"sha256": sha, "bytes": path.stat().st_size, "role": ROLE}}
    made = {"file": FILE, "sha256": sha, "bytes": path.stat().st_size, "source": source_of(record),
            **{key: row[key] for key in KEYS if key not in ("sha256", "bytes")}}
    entries.write(root, {**record, "files": files, "clip": made})
    return ""


def attach(root, blobs, rows, message):
    """Each clip into its entry, the index rebuilt and the library saved; {attached, refused, saved}."""
    from score_lib.handover import locked, saved, LOCK_WAIT
    done, refused = [], []
    with locked(root, LOCK_WAIT):
        for row in rows:
            why = attached(root, blobs, row)
            (refused.append({"address": row["address"], "why": why}) if why else done.append(row["address"]))
        index.rebuild(root)
        kept = saved(root, message)
    return {"attached": done, "refused": refused, "saved": kept}
