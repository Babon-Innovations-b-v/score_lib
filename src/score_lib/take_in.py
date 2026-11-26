"""score-lib take-in: loose made files (a file that is no output of a SCORE store, such as a world's old release of
made models and sounds) taken in as draft entries, their bytes put in the blob store.

The list is one JSON file:

    {"world": "world1", "commit": "<the score commit that named the files>", "taken_from": "<where the bytes came
     from, in words>",
     "entries": [{"kind": "sound" | "model" | "kit" | "character" | "surface" | "take" | "effect" | "light",
                  "id": "<entry id>", "title": "<title>",
                  "files": {"<name in the entry>": {"path": "<local file>", "from": "<its name where it came from>"}},
                  "made_with": [{"what": "<part>", "source": "<a sources.toml id or credit text>",
                                 "carried": true when the source's own bytes are copied in}],
                  "tags": {<optional tags, {name: [values]}>},
                  "picture": {"file": "<one of its files, a .png or .jpg>", "made_by": "<the tool that drew it>",
                              "of": "<the file it shows>"} when it brings its own preview picture}],
     "contributor": {"handle": "<GitHub handle>", "claim": "<own work, or the source and its terms>"}}

A route that takes a whole source in (take_in_hub.py) may also give, per entry: a file's `sha256` and `bytes` (the
source's own record of them, so a blob the store already holds need not be on disk), `usd` ({root, use, layer,
default_prim, ...}: its root file and how it is laid), `requires` (addresses), `unresolved` (paths its USD names that
resolve outside the library, verify.allowed_unresolved) and `licence` (the top-level block of a part under its own
licence, admission.Sources.licence_faults; any entry carrying such a source's bytes without it is refused).

`contributor` is given by an outside contributor only (CONTRIBUTING.md): each entry then names its contributor
(`provenance.contributor`) and keeps the contribution block (contribution.py), which `score-lib take-bundle` completes.

Each entry is admitted by the MIT rule (config/sources.toml) or refused. Its record has no OpenUSD form (`usd` null)
and no picture (`picture_reason` says why) unless the list gives its own (`picture`: a preview drawn of it, its file
role `picture`) and keeps `provenance.taken_from` and `files_from`, which
`score-lib verify` checks (verify.check_taken_in). An entry whose newest version holds the same content is left as it
is; a changed draft is written over; a changed locked entry becomes the next version as a draft.

A `kit` entry is one building kit piece (its geometry, `.usdc`, and its record); a `character` entry may hold motion
clips (`.npz`) and their records; a `surface` entry is one image-mapped finish (its look, `material.usda`, its maps
and its record, `surface.json`), such as a CC0 PBR set; an `effect` entry is one emitter score's particle effects
algorithm baked (its schema prim, `effect.usda`, the root its record's `usd` names as the export's effects do, the
clip it plays, `.usdc`, and its settings record), never one from outside (CONTRIBUTING.md). A `.json` file of any kind
is a record. A `light` entry is one photographed sky and setting as an environment light (a CC0 HDRI's
high-range picture, `.exr` or `.hdr`, and its UsdLux dome, `dome.usda`), lighting and backing a picture as the real
place it was taken in (score #223: a person drawn as a photograph).

A `take` entry is one Pixal3D take whole (score's work/pixal, 2026-10-28: its only copy lay on one disk): its models
and record (`<take>.glb`, `<take>-final.glb`, `<take>-<faces>.glb`, `<take>.json`) and its camera folder
(`<take>.svviews/input.png` the close-up, `transforms.json` the camera), each under its own name, so a reader puts
them back where they lay. `--save` (taken_in_saved) takes the list in under the handover's lock and commits and
pushes the entries it wrote and the index only.
"""
import json
import os
import pathlib

from score_lib import contribution
from score_lib import entry as entries
from score_lib import handover, index
from score_lib.admission import NotAdmitted, Sources
from score_lib.blobs import file_sha256, put_all


KINDS = ("sound", "model", "take", "kit", "character", "surface", "effect", "light")
# An effect entry's emitter file, laid by reference as the export's effect entries are (export/effect.py).
EFFECT_USD = {"root": "effect.usda", "layer": "effects", "use": "reference"}
ROLES = {".json": "record", ".npz": "motion", ".usdc": "usd", ".usda": "usd", ".usd": "usd", ".mdl": "code", ".ogg": "audio", ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".gltf": "gltf", ".glb": "gltf",
         ".bin": "gltf", ".png": "texture", ".jpg": "texture", ".jpeg": "texture", ".webp": "texture",
         ".exr": "texture", ".hdr": "texture"}
# A take's files: its models and its record, and in its `.svviews` folder the close-up and its camera.
TAKE_ROLES = {".glb": "gltf", ".json": "record"}
CAMERA_ROLES = {".png": "closeup", ".json": "record"}
# A place built by score's placing method (take_in_place.py): its named files' roles, and its scene package's.
PLACE_ROLES = {"scene.usda": "usd", "concept.png": "concept", "render.png": "render", "review/sheet.jpg": "review",
               "viewer.glb": "viewer"}
PACKAGE = "package/"
PIECES = "pieces/"  # a place's piece list (score.scene version 2, 2099 #245) beside its scene package
NO_PICTURE = "a loose made file taken in (score-lib take-in): no picture made yet"
PICTURE_ENDINGS = (".png", ".jpg")  # an entry's own preview picture (`picture` in the list)
COMPARED_OUT = ("address", "version") + entries.UNSTAMPED


class Refused(ValueError):
    """An entry of the list that cannot be taken in as given."""


def role_of(name, kind=None):
    """A file's role by its ending (a take's by TAKE_ROLES, and CAMERA_ROLES inside its `.svviews` folder; a place's
    named files by PLACE_ROLES, its scene package's as `package` and its piece list's as `pieces`); Refused for an
    ending the take-in does not know."""
    if kind == "place" and name.startswith(PIECES):
        return "pieces"
    if kind == "place" and (name in PLACE_ROLES or name.startswith(PACKAGE)):
        return PLACE_ROLES.get(name, "package")
    path = pathlib.PurePosixPath(name)
    roles = ROLES if kind != "take" else CAMERA_ROLES if path.parent.suffix == ".svviews" else TAKE_ROLES
    role = roles.get(path.suffix.lower())
    if role is None:
        raise Refused(f"{name}: no role for its ending (take-in knows {', '.join(sorted(roles))})")
    return role


def file_table(files, kind, picture=None):
    """{name: {sha256, bytes, role}} of an entry's files: hashed from its local path, or as listed when the list gives
    its sha256 and bytes (a source's own record of them, checked when the bytes go into the blob store); the file
    named `picture` takes the role `picture`."""
    table = {}
    for name, about in sorted(files.items()):
        role = "picture" if name == picture else role_of(name, kind)
        if "sha256" in about:
            table[name] = {"sha256": about["sha256"], "bytes": about["bytes"], "role": role}
            continue
        path = pathlib.Path(about["path"])
        table[name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size, "role": role}
    return table


def picture_of(listed):
    """The entry's own picture block ({file, made_by, of}) when the list gives one, else None; Refused when it names a
    file the entry does not hold or one that is no picture."""
    given = listed.get("picture")
    if not given:
        return None
    name = given.get("file")
    if name not in listed["files"] or pathlib.PurePosixPath(name).suffix.lower() not in PICTURE_ENDINGS:
        raise Refused(f"{listed['id']}: its picture {name!r} is not one of its {'/'.join(PICTURE_ENDINGS)} files")
    if not given.get("made_by") or given.get("of") not in listed["files"]:
        raise Refused(f"{listed['id']}: its picture names no tool that drew it (made_by) or no file of it (of)")
    return {"file": name, "made_by": given["made_by"], "of": given["of"]}


def usd_of(listed, version):
    """An entry's `usd` block: an effect's emitter file laid by reference, or the block the list gives (its `root`
    file and how it is laid), at its path in the library; None when it has neither."""
    if listed["kind"] == "effect":
        found = EFFECT_USD
    elif listed.get("usd"):
        found = listed["usd"]
    else:
        return None
    path = entries.library_path(listed["kind"], listed["id"], version, found["root"])
    return {**found, "path": str(path)}


def record_of(listed, about, sources, version):
    """The draft record of one listed entry at a version; Refused or NotAdmitted when it cannot go in."""
    address = entries.address_of(listed["kind"], listed["id"], version)
    provenance = {"world": about["world"], "commit": about["commit"], "taken_from": about["taken_from"],
                  "made_with": sources.made_with(listed["made_with"]),
                  "files_from": {name: file["from"] for name, file in sorted(listed["files"].items())}}
    picture = picture_of(listed)
    record = {"schema": entries.SCHEMA, "address": address, "kind": listed["kind"], "id": listed["id"],
              "version": version, "title": listed["title"], "state": "draft", "picture": picture,
              **({} if picture else {"picture_reason": NO_PICTURE}),
              "files": file_table(listed["files"], listed["kind"], (picture or {}).get("file")),
              "usd": usd_of(listed, version), "requires": sorted(listed.get("requires", [])),
              "provenance": provenance, "tags": listed.get("tags", {}), "seen_in": []}
    if listed.get("unresolved"):
        provenance["unresolved"] = sorted(listed["unresolved"])
    if listed.get("licence"):
        record["licence"] = listed["licence"]
    faults = sources.licence_faults(provenance["made_with"], listed.get("licence"))
    if faults:
        raise Refused(f"{listed['id']}: {'; '.join(faults)}")
    if about.get("contributor"):
        try:
            block = contribution.block_of(about["contributor"].get("handle"), about["contributor"].get("claim"))
        except ValueError as why:
            raise Refused(str(why)) from why
        provenance["contributor"] = block["handle"]
        record["contribution"] = block
    return record


def comparable(record):
    """A record without what a version, a lock or a score changes."""
    return {key: value for key, value in record.items() if key not in COMPARED_OUT}


def checked(listed):
    """Refused when a listed entry's kind or id cannot be taken in."""
    if listed["kind"] not in KINDS:
        raise Refused(f"{listed['kind']}: take-in takes {' and '.join(KINDS)} entries only")
    if not entries.ID_PATTERN.match(listed["id"]):
        raise Refused(f"{listed['id']}: not an entry id")
    if listed["kind"] == "effect" and EFFECT_USD["root"] not in listed["files"]:
        raise Refused(f"{listed['id']}: an effect entry holds its emitter as {EFFECT_USD['root']}")
    for name in listed["files"]:
        try:
            entries.checked_name(name)
        except entries.UnsafeName as why:
            raise Refused(str(why)) from why


def settle(root, listed, about, sources):
    """(status, record) of one listed entry: new, unchanged, changed draft or new version."""
    checked(listed)
    if listed["kind"] == "effect" and about.get("contributor"):
        raise Refused(f"{listed['id']}: an effect is score's bake, not taken in from outside (CONTRIBUTING.md)")
    found = entries.versions(root, listed["kind"], listed["id"])
    newest = found[-1] if found else None
    if newest is None:
        return "new", record_of(listed, about, sources, 1)
    same = record_of(listed, about, sources, newest["version"])
    if comparable(same) == comparable(newest):
        return "unchanged", newest
    if newest["state"] == "draft":
        return "changed draft", same
    return "new version", record_of(listed, about, sources, newest["version"] + 1)


def blob_pairs(listed, record):
    """[(sha256, path)] of every file of an entry."""
    return [(record["files"][name]["sha256"], pathlib.Path(about["path"]))
            for name, about in sorted(listed["files"].items())]


def take_in(root, blobs, listing):
    """Take in every entry of a list; the report {status: [address]} and {refused: [{id, why}]}; the index rebuilt.
    Every written entry's files go into the blob store together (checked against their sha256 on the way in; the
    hub's commit rate allows a few commits an hour, not one an entry), before any record naming them is written."""
    sources = Sources.load()
    report = {"new": [], "unchanged": [], "changed draft": [], "new version": [], "refused": []}
    written = []
    for listed in listing["entries"]:
        try:
            status, record = settle(root, listed, listing, sources)
        except (Refused, NotAdmitted) as why:
            report["refused"].append({"id": f"{listed.get('kind')}/{listed.get('id')}", "why": str(why)})
            continue
        if status != "unchanged":
            written.append((listed, record))
        report[status].append(record["address"])
    put_all(blobs, [pair for listed, record in written for pair in blob_pairs(listed, record)])
    for _, record in written:
        entries.write(root, record)
    index.rebuild(root)
    return report



def taken_in_saved(root, blobs, listing):
    """take_in under the handover's lock, then the entries it wrote and the index committed and pushed (only those
    paths); its report with the `saved` block (handover.saved), saved only when it wrote something."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        report = take_in(root, blobs, listing)
        written = [address for status in ("new", "changed draft", "new version") for address in report[status]]
        if not written:
            return {**report, "saved": None}
        folders = sorted({"entries/{0}/{1}".format(*entries.parse_address(address)[:2]) for address in written})
        message = (f"take-in: {len(written)} entries from {listing['taken_from']} (score {listing['commit'][:12]})")
        return {**report, "saved": handover.saved(root, message, (*folders, "index"), handover.LIBRARY_BRANCH)}


def read_listing(path):
    """The list file, checked for the fields every entry needs."""
    listing = json.loads(pathlib.Path(path).read_text())
    for key in ("world", "commit", "taken_from", "entries"):
        if key not in listing:
            raise SystemExit(f"{path}: the list has no {key!r}")
    return listing
