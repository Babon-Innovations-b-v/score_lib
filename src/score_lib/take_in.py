"""score-lib take-in: loose made files (a file that is no output of a SCORE store, such as a world's old release of
made models and sounds) taken in as draft entries, their bytes put in the blob store.

The list is one JSON file:

    {"world": "world1", "commit": "<the score commit that named the files>", "taken_from": "<where the bytes came
     from, in words>",
     "entries": [{"kind": "sound" | "model" | "kit" | "character", "id": "<entry id>", "title": "<title>",
                  "files": {"<name in the entry>": {"path": "<local file>", "from": "<its name where it came from>"}},
                  "made_with": [{"what": "<part>", "source": "<a sources.toml id or credit text>",
                                 "carried": true when the source's own bytes are copied in}],
                  "tags": {<optional tags, {name: [values]}>}}]}

Each entry is admitted by the MIT rule (config/sources.toml) or refused. Its record has no OpenUSD form and no
picture (`usd` null, `picture_reason` says why) and keeps `provenance.taken_from` and `files_from`, which
`score-lib verify` checks (verify.check_taken_in). An entry whose newest version holds the same content is left as it
is; a changed draft is written over; a changed locked entry becomes the next version as a draft.

A `kit` entry is one building kit piece (its geometry, `.usdc`, and its record); a `character` entry may hold motion
clips (`.npz`) and their records. A `.json` file of any kind is a record.

A `take` entry is one Pixal3D take whole (score's work/pixal, 2026-10-28: its only copy lay on one disk): its models
and record (`<take>.glb`, `<take>-final.glb`, `<take>-<faces>.glb`, `<take>.json`) and its camera folder
(`<take>.svviews/input.png` the close-up, `transforms.json` the camera), each under its own name, so a reader puts
them back where they lay. `--save` (taken_in_saved) takes the list in under the handover's lock and commits and
pushes the entries it wrote and the index only.
"""
import json
import os
import pathlib

from score_lib import entry as entries
from score_lib import handover, index
from score_lib.admission import NotAdmitted, Sources
from score_lib.blobs import file_sha256

KINDS = ("sound", "model", "take", "kit", "character")
ROLES = {".json": "record", ".npz": "motion", ".usdc": "usd", ".usda": "usd", ".ogg": "audio", ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".gltf": "gltf", ".glb": "gltf",
         ".bin": "gltf", ".png": "texture", ".jpg": "texture", ".jpeg": "texture", ".webp": "texture"}
# A take's files: its models and its record, and in its `.svviews` folder the close-up and its camera.
TAKE_ROLES = {".glb": "gltf", ".json": "record"}
CAMERA_ROLES = {".png": "closeup", ".json": "record"}
NO_PICTURE = "a loose made file taken in (score-lib take-in): no picture made yet"
COMPARED_OUT = ("address", "version", "state", "locked") + entries.QUALITY


class Refused(ValueError):
    """An entry of the list that cannot be taken in as given."""


def role_of(name, kind=None):
    """A file's role by its ending (a take's by TAKE_ROLES, and CAMERA_ROLES inside its `.svviews` folder); Refused
    for an ending the take-in does not know."""
    path = pathlib.PurePosixPath(name)
    roles = ROLES if kind != "take" else CAMERA_ROLES if path.parent.suffix == ".svviews" else TAKE_ROLES
    role = roles.get(path.suffix.lower())
    if role is None:
        raise Refused(f"{name}: no role for its ending (take-in knows {', '.join(sorted(roles))})")
    return role


def file_table(files, kind):
    """{name: {sha256, bytes, role}} of an entry's files."""
    table = {}
    for name, about in sorted(files.items()):
        path = pathlib.Path(about["path"])
        table[name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size, "role": role_of(name, kind)}
    return table


def record_of(listed, about, sources, version):
    """The draft record of one listed entry at a version; Refused or NotAdmitted when it cannot go in."""
    address = entries.address_of(listed["kind"], listed["id"], version)
    provenance = {"world": about["world"], "commit": about["commit"], "taken_from": about["taken_from"],
                  "made_with": sources.made_with(listed["made_with"]),
                  "files_from": {name: file["from"] for name, file in sorted(listed["files"].items())}}
    return {"schema": entries.SCHEMA, "address": address, "kind": listed["kind"], "id": listed["id"],
            "version": version, "title": listed["title"], "state": "draft", "picture": None,
            "picture_reason": NO_PICTURE, "files": file_table(listed["files"], listed["kind"]), "usd": None, "requires": [],
            "provenance": provenance, "tags": listed.get("tags", {}), "seen_in": []}


def comparable(record):
    """A record without what a version, a lock or a score changes."""
    return {key: value for key, value in record.items() if key not in COMPARED_OUT}


def checked(listed):
    """Refused when a listed entry's kind or id cannot be taken in."""
    if listed["kind"] not in KINDS:
        raise Refused(f"{listed['kind']}: take-in takes {' and '.join(KINDS)} entries only")
    if not entries.ID_PATTERN.match(listed["id"]):
        raise Refused(f"{listed['id']}: not an entry id")
    for name in listed["files"]:
        if pathlib.PurePosixPath(name).is_absolute() or ".." in pathlib.PurePosixPath(name).parts:
            raise Refused(f"{name}: a file of an entry is named inside it")


def settle(root, listed, about, sources):
    """(status, record) of one listed entry: new, unchanged, changed draft or new version."""
    checked(listed)
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


def put_blobs(blobs, listed, record):
    """Every file of an entry into the blob store (checked against its sha256 on the way in)."""
    for name, about in sorted(listed["files"].items()):
        blobs.put(record["files"][name]["sha256"], pathlib.Path(about["path"]))


def take_in(root, blobs, listing):
    """Take in every entry of a list; the report {status: [address]} and {refused: [{id, why}]}; the index rebuilt."""
    sources = Sources.load()
    report = {"new": [], "unchanged": [], "changed draft": [], "new version": [], "refused": []}
    for listed in listing["entries"]:
        try:
            status, record = settle(root, listed, listing, sources)
        except (Refused, NotAdmitted) as why:
            report["refused"].append({"id": f"{listed.get('kind')}/{listed.get('id')}", "why": str(why)})
            continue
        if status != "unchanged":
            put_blobs(blobs, listed, record)
            entries.write(root, record)
        report[status].append(record["address"])
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
        return {**report, "saved": handover.saved(root, message, (*folders, "index"))}


def read_listing(path):
    """The list file, checked for the fields every entry needs."""
    listing = json.loads(pathlib.Path(path).read_text())
    for key in ("world", "commit", "taken_from", "entries"):
        if key not in listing:
            raise SystemExit(f"{path}: the list has no {key!r}")
    return listing
