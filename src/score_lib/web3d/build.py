"""What each kind's web copy is made of, from the entry's own OpenUSD fetched (linked, read only) with what it requires
into a work folder and opened with that folder as the search path:

    model      model.usdc, and its glow half (model_glow.usdc) beside it
    character  character.usdc in its first pose; its parts' library surfaces from its dress.json, or from the dress of
               the same look when it is a body
    kit        the place's base layer entry, only the objects whose model the kit's pieces name
    place      the place's root with every sublayer it requires (the places it only shows are not fetched)
"""
import json
import pathlib
import posixpath
import shutil
import tempfile

from score_lib import entry as entries
from score_lib.fetch import fetch
from score_lib.web3d import finish, gltf, pack
from score_lib.web3d.record import SKIPPED_ROLES


def fetched(root, blobs, addresses, folder):
    """Entries and what they require linked into `folder`, but their pictures and web copies: the records."""
    return fetch(root, blobs, addresses, folder, with_requires=True, link=True, left_out_roles=SKIPPED_ROLES)


def entry_folder(folder, address):
    kind, entry_id, version = entries.parse_address(address)
    return pathlib.Path(folder) / entries.library_path(kind, entry_id, version, "")


def model_view(record, folder, work):
    """A small root showing a model and its glow half: its path."""
    from pxr import Sdf
    base = entry_folder(folder, record["address"])
    layer = Sdf.Layer.CreateNew(str(work / "view.usda"))
    root = Sdf.CreatePrimInLayer(layer, "/model")
    root.specifier, root.typeName = Sdf.SpecifierDef, "Xform"
    layer.defaultPrim = "model"
    for name, child in (("model.usdc", "body"), ("model_glow.usdc", "glow")):
        if name in record["files"]:
            prim = Sdf.CreatePrimInLayer(layer, f"/model/{child}")
            prim.specifier = Sdf.SpecifierDef
            prim.referenceList.Prepend(Sdf.Reference(str(base / name)))
    layer.Save()
    return work / "view.usda"


def kit_names(kit):
    """The model names a kit's pieces lay."""
    names = {piece.get("model") for piece in kit.get("pieces") or [] if piece.get("model")}
    return names | set((kit.get("models") or {}).keys())


def read_json_file(blobs, described, work, name):
    blobs.get(described["sha256"], work / name)
    return json.loads((work / name).read_text())


def kit_piece_test(names):
    """A test of a prim: whether it is, or sits under, an object whose model one of the kit's names is."""
    def model_name(prim):
        attribute = prim.GetAttribute("score:model")
        value = attribute.Get() if attribute and attribute.HasAuthoredValue() else None
        return posixpath.splitext(posixpath.basename(value))[0] if value else None

    def keep(prim):
        while prim and not prim.IsPseudoRoot():
            name = model_name(prim)
            if name is not None:
                return name in names
            prim = prim.GetParent()
        return False
    return keep


def base_layer(root, place):
    """The newest base layer entry of a place, or None."""
    found = entries.versions(root, "layer", f"base.{place}")
    found = [record for record in found if record["state"] != "withdrawn"]
    return found[-1] if found else None


def look_dress(root, record):
    """The dress entry of a body's look (dress_<look>), or None."""
    look = record["id"][len("body_"):] if record["id"].startswith("body_") else None
    found = entries.versions(root, "character", f"dress_{look}") if look else []
    return found[-1] if found else None


class Plan:
    """What one copy opens: the stage file, the entries it reads besides the entry itself, which prims it keeps and
    the library surfaces a character's parts carry."""

    def __init__(self, path, read=(), keep=None, parts=None):
        self.path, self.read, self.keep, self.parts = path, list(read), keep, parts or {}


def plan(root, blobs, record, folder, work):
    """The Plan of one entry's copy, its entries fetched into `folder`."""
    kind = record["kind"]
    if kind == "model":
        fetched(root, blobs, [record["address"]], folder)
        return Plan(model_view(record, folder, work))
    if kind == "character":
        fetched(root, blobs, [record["address"]], folder)
        dress = record if "dress.json" in record["files"] else look_dress(root, record)
        parts = finish.dress_parts(dress, blobs) if dress else {}
        read = [dress["address"]] if dress and dress is not record else []
        return Plan(entry_folder(folder, record["address"]) / record["usd"]["root"], read, parts=parts)
    if kind == "kit":
        layer = base_layer(root, record["id"])
        if layer is None:
            raise LookupError(f"{record['address']}: no base layer entry of its place to lay its pieces from")
        records = fetched(root, blobs, [layer["address"]], folder)
        kit = read_json_file(blobs, record["files"]["kit.json"], work, "kit.json")
        return Plan(entry_folder(folder, layer["address"]) / "layer.usda", [each["address"] for each in records],
                    keep=kit_piece_test(kit_names(kit)))
    if kind == "place":
        records = fetched(root, blobs, [record["address"]], folder)
        return Plan(entry_folder(folder, record["address"]) / record["usd"]["root"],
                    [each["address"] for each in records if each["address"] != record["address"]])
    raise ValueError(f"no web copy for a {kind} entry")


FAR = "far"  # a character's far stand-in (score's people bodies), hidden up close


def shown_outfit(stage, moment):
    """A kit body stored with every outfit hidden (a cast person's clothes chosen per place) shows its outfits, in
    the stage's session layer only; a body that shows one is left as it is."""
    from pxr import UsdGeom
    groups = [child for prim in stage.Traverse() if prim.GetName() == "Body"
              for child in prim.GetChildren() if child.GetName() != FAR]
    if any(UsdGeom.Imageable(group).ComputeVisibility(moment) != UsdGeom.Tokens.invisible for group in groups):
        return
    stage.SetEditTarget(stage.GetSessionLayer())
    for group in groups:
        UsdGeom.Imageable(group).MakeVisible(moment)


def build(root, blobs, library, record, cache):
    """Make one entry's copies in a work folder: (the work folder, {"light", "full"} paths, facts)."""
    from pxr import Ar, Usd
    work = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-web3d-"))
    folder = work / "fetched"
    try:
        chosen = plan(root, blobs, record, folder, work)
        context = Ar.ResolverContext(Ar.DefaultResolverContext([str(folder)]))
        with Ar.ResolverContextBinder(context):
            stage = Usd.Stage.Open(str(chosen.path), context)
            moment = Usd.TimeCode(stage.GetStartTimeCode())
            if record["kind"] == "character":
                shown_outfit(stage, moment)
            resolver = finish.Resolver(library, chosen.parts)
            document = gltf.Gltf(work / "document")
            document.folder.mkdir()
            boxes, triangles, left_out = gltf.write_meshes(stage, document, moment, resolver, chosen.keep)
            if not triangles:
                raise ValueError(f"{record['address']}: its stage draws nothing to copy")
            written = document.write()
        copies = pack.packed(written, work, cache)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    facts = {"triangles": triangles, "objects": len(boxes), "left_out": left_out, **resolver.summary(),
             "read": chosen.read}
    return work, copies, facts
