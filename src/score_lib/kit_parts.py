"""score-lib kit-parts: the taken-in kit pieces and models whose parts a plain USD reader cannot split, made again
part by part (score #156).

The CC0 kit pieces and models taken in for the demo's places (score #128) each hold one mesh whose colour regions
are GeomSubsets (familyName materialBind) with no material bound, so Blender's USD reader and other plain readers
show the piece as one part and a finish covers all of it; and a decal part (a name holding "Decal") is a flat quad
whose logo was a picture the library does not keep, so it draws as a blank plate. Each such entry's USD file is made
again:

- every decal part's faces taken off the mesh (its points, primvars and the other parts' face numbers following),
  and the part out of its record's `parts`;
- every other part bound to a placeholder material named after it (`/<default prim>/Looks/<part>`): a plain
  UsdPreviewSurface in a neutral grey (a material with no shader renders black in Blender's Cycles), marked
  `score:placeholder` so score's finish check knows it is no finish and a place still lays a library surface on it
  (place_build's `surface --part`; score's dome_shell.placeholder makes the same one).

    score-lib kit-parts --into <work folder> [--only <address>]

writes the made files and one take-in list per source (`listing-<n>.json`) into the folder and prints one line;
`score-lib take-in <list> --save` then takes them in by the library's own rules (a changed draft written over, a
changed locked entry the next version as a draft). An entry already readable part by part is left out, so a second
run lists nothing.
"""
import json
import pathlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from score_lib import entry as entries
from score_lib.blobs import MissingBlob

KINDS = {"kit": ("piece.usdc", "piece.json"), "model": ("model.usdc", "model.json")}
FETCHES = 16  # entries' files fetched side by side
DECAL = "decal"
LOOKS = "Looks"
PLACEHOLDER = "score:placeholder"  # the mark score's finish check reads (score's dome_shell.PLACEHOLDER)
GREY = (0.8, 0.8, 0.8)  # a plain reader's neutral grey, never a finish
ROUGHNESS = 0.6
STEPS = "its parts each bound to a placeholder material named after the part"  # how this step's words begin
STEP = {"what": f"{STEPS} (a plain grey marked score:placeholder, no finish) so any USD reader splits them and draws "
                "them grey, and its decal faces taken off (their logo a picture the library does not keep) "
                "(score #156)", "source": "score"}


def is_decal(name):
    return DECAL in name.lower()


def part_subsets(mesh):
    """The mesh's face GeomSubsets of the materialBind family."""
    from pxr import UsdGeom, UsdShade
    return [subset for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(mesh))
            if subset.GetFamilyNameAttr().Get() == UsdShade.Tokens.materialBind]


def drawn(material):
    """Whether a material has a surface a plain reader draws (a shader on its surface output)."""
    return bool(material) and material.GetSurfaceOutput().HasConnectedSource()


def needs_parts(stage):
    """Whether the stage holds a decal part, a part with no material bound, or one bound to a material with no
    surface (an earlier run's shaderless placeholder, drawn black)."""
    from pxr import UsdGeom, UsdShade
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        for subset in part_subsets(prim):
            bound = UsdShade.MaterialBindingAPI(subset.GetPrim()).GetDirectBinding().GetMaterial()
            if is_decal(subset.GetPrim().GetName()) or not drawn(bound):
                return True
    return False


def kept_values(values, interpolation, keep, corners, used):
    """A primvar's values for the faces kept: by vertex (`used`), by face corner (`corners`) or by face (`keep`)."""
    from pxr import UsdGeom
    if interpolation in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        return values[used]
    if interpolation == UsdGeom.Tokens.faceVarying:
        return values[corners]
    if interpolation == UsdGeom.Tokens.uniform:
        return values[keep]
    return values


def kept_primvar(primvar, keep, corners, used):
    """One primvar cut to the faces kept (an indexed one by its indices)."""
    if primvar.IsIndexed():
        indices = np.asarray(primvar.GetIndicesAttr().Get())
        primvar.SetIndices(type(primvar.GetIndicesAttr().Get())(
            kept_values(indices, primvar.GetInterpolation(), keep, corners, used).tolist()))
        return
    values = primvar.Get()
    if values is None:
        return
    cut = kept_values(np.arange(len(values)), primvar.GetInterpolation(), keep, corners, used)
    primvar.Set(type(values)([values[int(index)] for index in cut]))


def faces_off(mesh_prim, faces):
    """The faces taken off the mesh: its points, normals, primvars and parts follow; False when none was left."""
    from pxr import UsdGeom
    mesh = UsdGeom.Mesh(mesh_prim)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
    keep = np.ones(len(counts), dtype=bool)
    keep[sorted(faces)] = False
    corners = np.repeat(keep, counts)
    used = np.unique(indices[corners])
    renumbered = np.full(len(mesh.GetPointsAttr().Get()), -1)
    renumbered[used] = np.arange(len(used))
    face_number = np.cumsum(keep) - 1
    for primvar in UsdGeom.PrimvarsAPI(mesh).GetPrimvars():
        kept_primvar(primvar, keep, corners, used)
    normals = mesh.GetNormalsAttr().Get()
    if normals is not None and len(normals):
        cut = kept_values(np.arange(len(normals)), mesh.GetNormalsInterpolation(), keep, corners, used)
        mesh.GetNormalsAttr().Set(type(normals)([normals[int(index)] for index in cut]))
    points = mesh.GetPointsAttr().Get()
    mesh.GetPointsAttr().Set(type(points)([points[int(index)] for index in used]))
    mesh.GetFaceVertexCountsAttr().Set(counts[keep].tolist())
    mesh.GetFaceVertexIndicesAttr().Set(renumbered[indices[corners]].tolist())
    for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(mesh_prim)):
        own = np.asarray(subset.GetIndicesAttr().Get(), dtype=np.int64)
        subset.GetIndicesAttr().Set(face_number[own[keep[own]]].tolist())
    if len(used):
        mesh.GetExtentAttr().Set(UsdGeom.PointBased.ComputeExtent(mesh.GetPointsAttr().Get()))
    return bool(keep.any())


def decals_off(stage, mesh_prim):
    """The mesh's decal parts taken off (their faces and their subsets); (whether any face is left, the decals'
    names)."""
    decals = [subset for subset in part_subsets(mesh_prim) if is_decal(subset.GetPrim().GetName())]
    if not decals:
        return True, []
    faces = {int(face) for subset in decals for face in subset.GetIndicesAttr().Get()}
    left = faces_off(mesh_prim, faces)
    names = [subset.GetPrim().GetName() for subset in decals]
    for subset in decals:
        stage.RemovePrim(subset.GetPath())
    return left, names


def placeholder(stage, path):
    """The placeholder material at `path`: a plain UsdPreviewSurface in GREY, marked PLACEHOLDER."""
    from pxr import Gf, Sdf, UsdShade
    material = UsdShade.Material.Define(stage, path)
    material.GetPrim().CreateAttribute(PLACEHOLDER, Sdf.ValueTypeNames.Bool).Set(True)
    shader = UsdShade.Shader.Define(stage, path.AppendChild("surface"))
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*GREY))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(ROUGHNESS)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def placeholders_bound(stage, mesh_prim, top):
    """Each part of the mesh bound to the placeholder material named after it (made once under the top's Looks)."""
    from pxr import UsdShade
    UsdShade.MaterialBindingAPI.Apply(mesh_prim)
    for subset in part_subsets(mesh_prim):
        path = top.GetPath().AppendChild(LOOKS).AppendChild(subset.GetPrim().GetName())
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(placeholder(stage, path))


def made_again(source, target):
    """The USD file at `source` made part by part into `target`: {"parts": [kept part names], "decals": [the decal
    parts taken off], "empty": [meshes all decal, taken off], "extent": [low, high] of what is left, or None}."""
    from pxr import Usd, UsdGeom
    target = pathlib.Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    Usd.Stage.Open(str(source)).Export(str(target))
    stage = Usd.Stage.Open(str(target))
    top = stage.GetDefaultPrim()
    empty, decals = [], []
    for mesh_prim in [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]:
        left, names = decals_off(stage, mesh_prim)
        decals += names
        if not left:
            empty.append(str(mesh_prim.GetPath()))
            stage.RemovePrim(mesh_prim.GetPath())
            continue
        placeholders_bound(stage, mesh_prim, top)
    parts = sorted({subset.GetPrim().GetName() for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)
                    for subset in part_subsets(prim)})
    stage.GetRootLayer().Save()
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    found = bounds.ComputeWorldBound(top).ComputeAlignedRange()
    extent = None if found.IsEmpty() else [list(found.GetMin()), list(found.GetMax())]
    return {"parts": parts, "decals": sorted(set(decals)), "empty": empty, "extent": extent}


def record_again(source, target, made):
    """The piece's record with its parts as made (decals out) and, when decals came off, its size from what is left,
    written to `target`."""
    record = json.loads(pathlib.Path(source).read_text())
    if isinstance(record.get("parts"), list):
        record["parts"] = [part for part in record["parts"] if part in made["parts"]]
    elif isinstance(record.get("parts"), dict):
        record["parts"] = {part: told for part, told in record["parts"].items() if part in made["parts"]}
    if "size_m" in record and made["extent"] and made["decals"]:
        record["size_m"] = [round(high - low, 3) for low, high in zip(*made["extent"])]
    pathlib.Path(target).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def candidates(root, only=None):
    """The newest taken-in kit and model entries holding a USD file and its record (or only the addresses given)."""
    found = []
    for kind, (usd, own) in KINDS.items():
        for record in entries.all_entries(root, kind):
            newest = entries.versions(root, kind, record["id"])[-1]
            if (newest["version"] != record["version"] or record["state"] == "withdrawn"
                    or "taken_from" not in record["provenance"] or not {usd, own} <= set(record["files"])
                    or (only and record["address"] not in only)):
                continue
            found.append(record)
    return found


def fetched(blobs, record, name, folder):
    """One of the entry's files from the blob store into the folder; its path."""
    path = pathlib.Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    blobs.get(record["files"][name]["sha256"], path)
    return path


def listed(record, files):
    """The take-in list's row of an entry made again (`files`: {name in the entry: made path})."""
    made_with = [{"what": step["what"], "source": step["by"], **({"carried": True} if step.get("carried") else {})}
                 for step in record["provenance"]["made_with"] if not step["what"].startswith(STEPS)]
    made_with.append(dict(STEP))
    return {"kind": record["kind"], "id": record["id"], "title": record["title"], "tags": record.get("tags", {}),
            "requires": record.get("requires", []), "made_with": made_with,
            "files": {name: {"path": str(path), "from": record["provenance"]["files_from"][name]}
                      for name, path in sorted(files.items())}}


def given_files(blobs, record, work):
    """The entry's USD file and record fetched into <work>/given; their paths, or the blob store's fault in words."""
    usd, own = KINDS[record["kind"]]
    given = pathlib.Path(work) / "given" / record["kind"] / record["id"]
    try:
        return fetched(blobs, record, usd, given), fetched(blobs, record, own, given)
    except MissingBlob as why:
        return f"{record['address']}: {why}"


def one_entry(record, given, work):
    """(list row or None, why it was left out or None) of one entry from its fetched files: made again when it
    needs it."""
    from pxr import Usd
    if isinstance(given, str):
        return None, given
    source, own_record = given
    if not needs_parts(Usd.Stage.Open(str(source))):
        return None, None
    usd, own = KINDS[record["kind"]]
    made = pathlib.Path(work) / "made" / record["kind"] / record["id"]
    result = made_again(source, made / usd)
    if result["extent"] is None:
        return None, f"{record['address']}: all of it is decal ({', '.join(result['empty'])})"
    record_again(own_record, made / own, result)
    return listed(record, {usd: made / usd, own: made / own}), None


def listings(root, blobs, work, only=None):
    """The take-in lists (one per world, commit and source) of every entry that needs its parts made again, written
    as <work>/listing-<n>.json; ({path: entries}, [why an entry was left out])."""
    groups, left = {}, []
    records = candidates(root, only)
    with ThreadPoolExecutor(FETCHES) as pool:
        given = list(pool.map(lambda record: given_files(blobs, record, work), records))
    for record, files in zip(records, given):
        row, why = one_entry(record, files, work)
        if why:
            left.append(why)
        if row:
            about = record["provenance"]
            groups.setdefault((about["world"], about["commit"], about["taken_from"]), []).append(row)
    written = {}
    for number, ((world, commit, taken_from), rows) in enumerate(sorted(groups.items()), start=1):
        path = pathlib.Path(work) / f"listing-{number}.json"
        path.write_text(json.dumps({"world": world, "commit": commit, "taken_from": taken_from, "entries": rows},
                                   indent=1) + "\n")
        written[str(path)] = len(rows)
    return written, left
