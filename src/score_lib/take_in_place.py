"""score-lib take-in --place: a place built by score's placing method (score/stages/place_build) kept as a draft
`place/<place>@<next>` entry, its bytes put in the blob store.

A place_build folder holds `place.json`, `concept.png`, `scene.usda` and under `assets/` every laid entry's files,
copied from the library at `assets/<kind>/<id>/<version>/<file>` (score's library_files.stage_name), beside the
place's own files there (the code-made parts: dome shells, stairs, blocks, terrain caches, a glTF's converted USD).
The entry keeps:

- `scene.usda`, every asset path that named a library copy pointed at its library search path
  (`score_lib/<kind>/<id>/<version>/<file>`), so the library's bytes are never copied again;
- every own file under `assets/` at its own path (an own USD layer's paths into library copies pointed so too);
- the records `place.json`, `objects.json`, `palette.json` (the place's surface library) and `check.json`, and,
  when present, `concept.png` (role concept), the final render `render.png` (render, the entry's picture), the
  review sheet `review/sheet.jpg` (review), the viewer GLB `viewer.glb` (viewer) and the scene package
  `package/` (scene.json and objects/*.gltf, role package), each by take_in.role_of.

An asset copy whose bytes differ from its entry's is the place's own. `requires` is every entry a path names and
every held entry the scene's `score:library` and `score:surface` name; `provenance` names the place_build commit,
the folder (`taken_from`), every file's path in it (`files_from`) and every address the scene names (`lays`).
`score-lib fetch place/<place>@<v> --into <root> --with-requires` then gives a stage that opens with <root> as its
search path. An unchanged place is left as it is; a changed one, draft or locked, becomes the next version, so a version
a game or a later step already reads is never written over (score #148: closing the flat wrote over place/flat@1).
"""
import json
import os
import pathlib
import posixpath
import re
import shutil
import tempfile

from score_lib import entry as entries
from score_lib import handover, index, take_in
from score_lib.admission import NotAdmitted, Sources, made_by
from score_lib.blobs import file_sha256, put_all
from score_lib.rewrite import asset_paths, export, info, local, open_copy, rewrite

NAMED = ("scene.usda", "place.json", "objects.json", "palette.json", "check.json", "concept.png", "render.png",
         "review/sheet.jpg", "viewer.glb")
SCENE = "scene.usda"
ASSETS = "assets/"
LAYER_ENDINGS = (".usda", ".usdc")
NAMES = ("score:library", "score:surface")  # the scene's attributes that name a laid entry's address
RENDERED = ("render.png", "review/sheet.jpg", "viewer.glb")
PICTURE = {"file": "render.png", "made_by": "score's placing method (Blender)", "of": "the place from its concept's camera"}
NO_RENDER = "a place taken in from score's placing method without its render (render.png)"


def entry_id_of(place):
    """The entry id of a place's name: lower case, every other character an underscore."""
    return re.sub(r"[^a-z0-9_]", "_", place.lower())


def folder_files(folder):
    """{name in the folder: path} of the files a place entry keeps: NAMED that are present, package/ and assets/."""
    folder = pathlib.Path(folder)
    found = {name: folder / name for name in NAMED if (folder / name).is_file()}
    for part in ("package", "assets"):
        found.update({path.relative_to(folder).as_posix(): path for path in sorted((folder / part).rglob("*"))
                      if path.is_file()})
    return found


def library_copy(root, name, path):
    """(address, file) of the library entry an asset is a copy of (`assets/<kind>/<id>/<version>/<file>`, its bytes
    the entry's), or None when it is the place's own."""
    parts = name.split("/")
    if len(parts) < 5 or parts[0] != "assets" or not parts[3].isdigit():
        return None
    try:
        address = entries.address_of(parts[1], parts[2], int(parts[3]))
        record = entries.load_address(root, address)
    except ValueError:
        return None
    held = "/".join(parts[4:])
    if record is None or held not in record["files"] or record["files"][held]["sha256"] != file_sha256(path):
        return None
    return address, held


def anchored(folder, layer_name, path):
    """The name in the folder an asset path written in the layer `layer_name` points at, or None outside it."""
    if path.startswith("/"):
        try:
            return pathlib.Path(path).resolve().relative_to(pathlib.Path(folder).resolve()).as_posix()
        except ValueError:
            return None
    name = posixpath.normpath(posixpath.join(posixpath.dirname(layer_name), path))
    return None if name.startswith("..") else name


def mapping_of(folder, layer_name, layer, copies, files):
    """{asset path: its new path} of one layer: a library copy's its search path, an absolute path to an own file
    its path from the layer; and the addresses of the copies it names."""
    mapping, named = {}, set()
    for path in asset_paths(layer):
        name = anchored(folder, layer_name, path)
        if name in copies:
            address, held = copies[name]
            mapping[path] = entries.library_path(*entries.parse_address(address), held)
            named.add(address)
        elif name in files and path.startswith("/"):
            mapping[path] = local(posixpath.relpath(name, posixpath.dirname(layer_name)))
    return mapping, named


def rewritten(folder, work, name, copies, files):
    """One layer of the place exported under `work` with its paths pointed (mapping_of); its path and the addresses
    it names."""
    layer = open_copy(pathlib.Path(folder) / name)
    mapping, named = mapping_of(folder, name, layer, copies, files)
    rewrite(layer, mapping)
    return export(layer, pathlib.Path(work) / name), named


def scene_names(path):
    """Every address the scene's `score:library` and `score:surface` attributes name, as written."""
    from pxr import Sdf
    layer = open_copy(path)
    found = set()

    def named(spec_path):
        if spec_path.IsPropertyPath() and spec_path.name in NAMES:
            value = layer.GetAttributeAtPath(spec_path).default
            if isinstance(value, str) and value:
                found.add(value)

    layer.Traverse(Sdf.Path.absoluteRootPath, named)
    return found


def held_address(root, address):
    """The held entry an address names (`kind/id@v`, or `kind/id` for its newest version), or None."""
    if "@" in address:
        try:
            return address if entries.load_address(root, address) else None
        except ValueError:
            return None
    kind, _, entry_id = address.partition("/")
    if kind not in entries.KINDS or not entries.ID_PATTERN.match(entry_id):
        return None
    found = entries.versions(root, kind, entry_id)
    return found[-1]["address"] if found else None


def kept_files(root, folder, work):
    """({entry name: path to its bytes} of what the entry keeps, the addresses its layers name): library copies
    left out, every layer (the scene and the own USD) with its paths pointed."""
    files = folder_files(folder)
    copies = {name: found for name, path in files.items() if (found := library_copy(root, name, path))}
    kept, named = {}, set()
    for name, path in files.items():
        if name in copies:
            continue
        if name.endswith(LAYER_ENDINGS) and (name == SCENE or name.startswith(ASSETS)):
            kept[name], names = rewritten(folder, work, name, copies, files)
            named |= names
        else:
            kept[name] = path
    if SCENE not in kept:
        raise take_in.Refused(f"{folder}: no {SCENE} (not a place_build folder)")
    return kept, named


def made_with(kept):
    """provenance.made_with's parts of a place: score's scene and own files, the concept's picture and the renders."""
    parts = [made_by("scene.usda and the place's own files: laid by score's placing method", "score")]
    if "concept.png" in kept:
        parts.append(made_by("concept.png: the locked concept, a Nano Banana Pro picture", "nano_banana_pro"))
    parts += [made_by(f"{name}: rendered by Blender", "blender") for name in RENDERED if name in kept]
    return parts


def record_of(root, folder, about, kept, named, version):
    """The draft place record at a version (about: {place, world, commit}); NotAdmitted when a source is refused."""
    entry_id = entry_id_of(about["place"])
    address = entries.address_of("place", entry_id, version)
    lays = sorted(scene_names(kept[SCENE]))
    requires = sorted(named | {held for each in lays if (held := held_address(root, each))})
    usd = {"root": SCENE, "layer": "root", "use": "open",
           "path": entries.library_path("place", entry_id, version, SCENE), **info(kept[SCENE])}
    provenance = {"world": about["world"], "commit": about["commit"], "place": about["place"],
                  "taken_from": f"score's placing method (place_build) folder {pathlib.Path(folder).resolve().name}",
                  "made_with": Sources.load().made_with(made_with(kept)), "lays": lays,
                  "files_from": {name: name for name in sorted(kept)}}
    shown = "render.png" in kept
    return {"schema": entries.SCHEMA, "address": address, "kind": "place", "id": entry_id, "version": version,
            "title": about["place"].replace("_", " ").capitalize(), "state": "draft",
            "picture": dict(PICTURE) if shown else None, **({} if shown else {"picture_reason": NO_RENDER}),
            "files": take_in.file_table({name: {"path": path} for name, path in kept.items()}, "place"),
            "usd": usd, "requires": requires, "provenance": provenance,
            "tags": {"place": [about["place"]], "laid_by": ["place_build"]}, "seen_in": []}


def settle(root, folder, about, kept, named):
    """(status, record) of the place: new, unchanged or new version (a changed place never writes over a kept one)."""
    found = entries.versions(root, "place", entry_id_of(about["place"]))
    newest = found[-1] if found else None
    if newest is None:
        return "new", record_of(root, folder, about, kept, named, 1)
    same = record_of(root, folder, about, kept, named, newest["version"])
    if take_in.comparable(same) == take_in.comparable(newest):
        return "unchanged", newest
    return "new version", record_of(root, folder, about, kept, named, newest["version"] + 1)


def place_of(folder):
    """The place's name, from its place.json."""
    path = pathlib.Path(folder) / "place.json"
    if not path.is_file():
        raise take_in.Refused(f"{folder}: no place.json (not a place_build folder)")
    return json.loads(path.read_text())["place"]


def taken_in_place(root, blobs, folder, commit, world):
    """The place folder taken in; the report {status: [address]} and {refused: [{id, why}]}; the index rebuilt."""
    report = {"new": [], "unchanged": [], "changed draft": [], "new version": [], "refused": []}
    work = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-place-"))
    try:
        about = {"place": place_of(folder), "world": world, "commit": commit}
        kept, named = kept_files(root, folder, work)
        status, record = settle(root, folder, about, kept, named)
        if status != "unchanged":
            put_all(blobs, [(record["files"][name]["sha256"], pathlib.Path(path))
                            for name, path in sorted(kept.items())])
            entries.write(root, record)
        report[status].append(record["address"])
    except (take_in.Refused, NotAdmitted) as why:
        report["refused"].append({"id": f"place/{pathlib.Path(folder).name}", "why": str(why)})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    index.rebuild(root)
    return report


def taken_in_place_saved(root, blobs, folder, commit, world):
    """taken_in_place under the handover's lock, then the entry it wrote and the index committed and pushed (only
    those paths); its report with the `saved` block (handover.saved), saved only when it wrote something."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        report = taken_in_place(root, blobs, folder, commit, world)
        written = [address for status in ("new", "new version") for address in report[status]]
        if not written:
            return {**report, "saved": None}
        kind, entry_id, _ = entries.parse_address(written[0])
        message = f"take-in: {written[0]} from score's placing method (score {commit[:12]})"
        return {**report, "saved": handover.saved(root, message, (f"entries/{kind}/{entry_id}", "index"))}
