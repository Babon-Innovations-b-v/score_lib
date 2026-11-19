"""A contribution's bundle: the new entries' records and every byte they name, in one file that travels with the pull
request (a release asset on the contributor's fork, or an attachment on the pull request when it is small).

    score-lib bundle <address>... --out <name>.tgz          (the contributor: pack)
    score-lib take-bundle <url or file>... --pr N --contributor <handle> --commit <signed-off sha> [--save]
                                                            (a maintainer, on merge: check, upload, trace)

A bundle is a gzip tar holding `bundle.json` (schema score-lib-bundle/1: the contributor's handle, each entry's
address and digest, each blob's size), each record at `entries/<kind>/<id>/<version>/entry.json` and each blob at
`blobs/<sha256>`. Nothing else is opened. Taking one in refuses an absolute path, a `..`, a link or any member that is
not a plain file, a member over MEMBER_BYTES, more than MEMBERS members, more than TOTAL_BYTES unpacked or an unpacked
total over RATIO times the packed size, a blob no record names, a blob whose bytes do not hash to its name or size, a
file whose ending its entry's kind does not list (ENDINGS) and a file whose first bytes are not what its ending says
(an executable or a script in a picture's name). Each record must equal the one the merged pull request holds. Only
then are the bytes put in every blob store the environment configures (the S3-compatible store and the Hugging Face
dataset) and each entry's contribution block completed (contribution.py).

A bundle is opened only inside the isolated review environment: the commands that open one refuse to run unless
REVIEW_SETTING names it.
"""
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import tarfile
import tempfile
import urllib.request

from score_lib import contribution, index
from score_lib import entry as entries
from score_lib.blobs import BadBlob, check_sha, file_sha256

SCHEMA = "score-lib-bundle/1"
REVIEW_SETTING = "SCORE_LIB_REVIEW_ENV"
# GitHub's own limits (docs.github.com, read 2026-11-19): a release asset under 2 GiB, a pull request attachment
# (.gz, .tgz or .zip) at most 25 MB.
RELEASE_ASSET_BYTES = 2 * 1024 ** 3
ATTACHMENT_BYTES = 25 * 1000 ** 2
MEMBER_BYTES = 1024 ** 3
TOTAL_BYTES = 4 * 1024 ** 3
MEMBERS = 20000
RATIO = 20
CHUNK = 1 << 20
# The endings each kind an outsider may contribute holds; an algorithm's code (.py) is its own and read by a human.
ENDINGS = {
    "model": {".gltf", ".bin", ".glb", ".png", ".jpg", ".jpeg", ".webp", ".json", ".usda", ".usdc"},
    "surface": {".usda", ".png", ".jpg", ".jpeg", ".webp", ".json"},
    "kit": {".usda", ".usdc", ".json"},
    "character": {".glb", ".usda", ".usdc", ".npz", ".png", ".jpg", ".jpeg", ".webp", ".json"},
    "sound": {".ogg", ".wav", ".flac", ".mp3", ".usda", ".png", ".json"},
    "take": {".glb", ".png", ".json"},
    "place": {".usda", ".usdc", ".gltf", ".bin", ".glb", ".png", ".jpg", ".jpeg", ".webp", ".json"},
    "algorithm": {".py", ".json", ".toml"},
}
# What a file of each binary ending starts with (RIFF files also name their form at bytes 8-12).
MAGIC = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
         ".glb": (b"glTF",), ".ogg": (b"OggS",), ".flac": (b"fLaC",), ".npz": (b"PK\x03\x04",),
         ".usdc": (b"PXR-USDC",), ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")}
RIFF = {".wav": b"WAVE", ".webp": b"WEBP"}
TEXT = {".usda": (b"#usda",), ".json": (b"{", b"["), ".gltf": (b"{",), ".py": (), ".toml": ()}
EXECUTABLE = (b"\x7fELF", b"MZ", b"#!", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
MEMBER = re.compile(r"^(bundle\.json|entries/[a-z]+/[a-z0-9_]+(?:\.[a-z0-9_]+)?/[1-9][0-9]*/entry\.json|blobs/[0-9a-f]{64})$")


class Refused(ValueError):
    """A bundle, or a file in it, that is not taken in, with why."""


def in_review_environment(environ=None):
    """Refused unless the isolated review environment is named (REVIEW_SETTING); there is no default."""
    environ = os.environ if environ is None else environ
    if not environ.get(REVIEW_SETTING):
        raise Refused(f"a bundle is opened only inside the isolated review environment ({REVIEW_SETTING} unset)")


def safe_name(name):
    """Refused for a file name inside an entry that could land outside it."""
    path = pathlib.PurePosixPath(name)
    if (not name or "\\" in name or "//" in name or name.endswith("/") or path.is_absolute() or ".." in path.parts
            or "." in name.split("/")):
        raise Refused(f"{name!r}: a file of an entry is named inside it, with no absolute part, '..' or '\\'")


def allowed_ending(kind, name):
    """Refused for a file whose ending its entry's kind does not list."""
    ending = pathlib.PurePosixPath(name).suffix.lower()
    if kind not in ENDINGS:
        raise Refused(f"{kind}: not a kind an outside contribution may add")
    if ending not in ENDINGS[kind]:
        raise Refused(f"{name}: a {kind} entry holds no {ending or 'file without an ending'} "
                      f"(it holds {', '.join(sorted(ENDINGS[kind]))})")


def first_bytes_fit(name, head):
    """Refused when a file's first bytes are not what its ending says, or are an executable's or a script's."""
    ending = pathlib.PurePosixPath(name).suffix.lower()
    if ending != ".py" and head.startswith(EXECUTABLE):
        raise Refused(f"{name}: an executable or a script")
    if ending in RIFF:
        fits = head[:4] == b"RIFF" and head[8:12] == RIFF[ending]
    elif ending in MAGIC:
        fits = head.startswith(MAGIC[ending])
    elif TEXT.get(ending):
        fits = head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(TEXT[ending])
    else:
        fits = True
    if not fits:
        raise Refused(f"{name}: its bytes are not a {ending} file")


def record_faults(record):
    """Refused for a record whose kind or file names an outside contribution may not hold."""
    for name in record["files"]:
        safe_name(name)
        allowed_ending(record["kind"], name)


def pack(root, blobs, addresses, out):
    """Write a bundle of entries' records and blobs to `out`; {path, sha256, bytes, entries, blobs, fits}."""
    records = [entries.load(entries.folder_of(root, *entries.parse_address(address)) / "entry.json")
               for address in addresses]
    handles = {record.get("provenance", {}).get("contributor") for record in records}
    if len(handles) != 1 or None in handles:
        raise Refused("a bundle holds one contributor's entries, each taken in with its contributor block")
    sizes = {}
    for record in records:
        record_faults(record)
        sizes.update({about["sha256"]: about["bytes"] for about in record["files"].values()})
    manifest = {"schema": SCHEMA, "contributor": handles.pop(), "blobs": dict(sorted(sizes.items())),
                "entries": {record["address"]: entries.digest(record) for record in records}}
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work, tarfile.open(out, "w:gz") as packed:
        add_bytes(packed, "bundle.json", entries.dumps(manifest).encode())
        for record in records:
            kind, entry_id, version = entries.parse_address(record["address"])
            add_bytes(packed, f"entries/{kind}/{entry_id}/{version}/entry.json", entries.dumps(record).encode())
        for sha in sorted(sizes):
            local = pathlib.Path(work) / sha
            blobs.get(sha, local)
            packed.add(str(local), arcname=f"blobs/{sha}", recursive=False, filter=plain)
            local.unlink()
    size = out.stat().st_size
    return {"path": str(out), "sha256": file_sha256(out), "bytes": size, "entries": len(records),
            "blobs": len(sizes), "fits": {"pull request attachment": size <= ATTACHMENT_BYTES,
                                          "release asset": size < RELEASE_ASSET_BYTES}}


def plain(member):
    """A tar member with no owner, mode or time of the machine that packed it."""
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mode = 0o644
    member.mtime = 0
    return member


def add_bytes(packed, name, data):
    """Add one in-memory file to a tar."""
    member = plain(tarfile.TarInfo(name))
    member.size = len(data)
    packed.addfile(member, io.BytesIO(data))


def downloaded(source, folder):
    """A bundle as a local file: a path as it is, an https URL fetched into `folder` and cut off past the release
    asset limit."""
    if not re.match(r"^https?://", source):
        return pathlib.Path(source)
    if not source.startswith("https://"):
        raise Refused(f"{source}: a bundle is fetched over https only")
    target = pathlib.Path(folder) / "bundle.tgz"
    got = 0
    with urllib.request.urlopen(source, timeout=60) as response, open(target, "wb") as written:
        for chunk in iter(lambda: response.read(CHUNK), b""):
            got += len(chunk)
            if got >= RELEASE_ASSET_BYTES:
                raise Refused(f"{source}: larger than a release asset may be")
            written.write(chunk)
    return target


def member_checked(member, seen, total, packed_bytes):
    """Refused for a tar member that is not a plain, small, expected file; the unpacked total with it."""
    if not member.isfile():
        raise Refused(f"{member.name}: not a plain file (a link, folder or device)")
    if not MEMBER.match(member.name):
        raise Refused(f"{member.name}: not a bundle's file (an absolute path, '..' or an unknown name)")
    if member.name in seen:
        raise Refused(f"{member.name}: twice in the bundle")
    if len(seen) >= MEMBERS:
        raise Refused(f"more than {MEMBERS} files")
    if member.size > MEMBER_BYTES:
        raise Refused(f"{member.name}: {member.size} bytes, over {MEMBER_BYTES}")
    total += member.size
    if total > TOTAL_BYTES or total > RATIO * max(packed_bytes, 1):
        raise Refused(f"unpacks to over {TOTAL_BYTES} bytes or {RATIO} times its packed size")
    return total


def unpacked(path, folder):
    """A bundle's files written under `folder` by their checked names, each streamed with a byte cap (no tar
    extraction); {manifest, records, blobs: {sha: path}}."""
    seen, total = set(), 0
    folder = pathlib.Path(folder)
    try:
        packed = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as error:
        raise Refused(f"{path}: not a gzip tar ({error})") from error
    with packed:
        for member in packed:
            total = member_checked(member, seen, total, pathlib.Path(path).stat().st_size)
            seen.add(member.name)
            target = folder / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with packed.extractfile(member) as stream, open(target, "wb") as written:
                shutil.copyfileobj(stream, written, CHUNK)
            if target.stat().st_size != member.size:
                raise Refused(f"{member.name}: its size is not what the tar says")
    if "bundle.json" not in seen:
        raise Refused("no bundle.json")
    manifest = json.loads((folder / "bundle.json").read_text())
    if manifest.get("schema") != SCHEMA:
        raise Refused(f"bundle.json is not {SCHEMA}")
    records = {}
    for name in sorted(seen):
        if name.startswith("entries/"):
            record = json.loads((folder / name).read_text())
            records[record["address"]] = record
    blobs = {name[len("blobs/"):]: folder / name for name in seen if name.startswith("blobs/")}
    return {"manifest": manifest, "records": records, "blobs": blobs}


def comparable(record):
    """A record without the maintainer's half of its contribution block."""
    block = {key: value for key, value in (record.get("contribution") or {}).items()
             if key not in contribution.MERGED_FIELDS}
    return {**record, "contribution": block} if "contribution" in record else record


def checked(root, opened, contributor):
    """Refused unless every record is the pull request's own, the contributor's, of an allowed kind and file names,
    and every blob it names is in the bundle at its size and hash, with nothing more; the records to take."""
    manifest, records, blobs = opened["manifest"], opened["records"], opened["blobs"]
    if manifest.get("contributor") != contributor:
        raise Refused(f"the bundle is {manifest.get('contributor')!r}'s, the pull request {contributor!r}'s")
    if sorted(manifest.get("entries", {})) != sorted(records):
        raise Refused("bundle.json and the records it holds name other entries")
    named = {}
    taken = []
    for address, record in sorted(records.items()):
        path = entries.folder_of(root, *entries.parse_address(address)) / "entry.json"
        if not path.is_file():
            raise Refused(f"{address}: the pull request holds no such entry")
        merged = entries.load(path)
        if comparable(merged) != comparable(record):
            raise Refused(f"{address}: the bundle's record is not the pull request's")
        if contribution.faults(merged, pending=True) or merged["provenance"]["contributor"] != contributor:
            raise Refused(f"{address}: not taken in as {contributor}'s contribution")
        record_faults(merged)
        for name, about in merged["files"].items():
            named[about["sha256"]] = (name, about["bytes"])
        taken.append(merged)
    if set(blobs) != set(named):
        raise Refused(f"the bundle's blobs are not the ones its records name ({len(set(blobs) ^ set(named))} differ)")
    for sha, path in sorted(blobs.items()):
        name, size = named[sha]
        if path.stat().st_size != size:
            raise Refused(f"{name}: {path.stat().st_size} bytes, its record says {size}")
        try:
            check_sha(path, sha)
        except BadBlob as error:
            raise Refused(f"{name}: {error}") from error
        with open(path, "rb") as head:
            first_bytes_fit(name, head.read(64))
    return taken


def bundle_sha256(path):
    """The sha256 of a bundle file."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take(root, stores, sources, pr, contributor, commit, today=None):
    """Take a merged pull request's bundles in: each checked, its blobs put in every store, each entry's
    contribution block completed and the index rebuilt; {entries, blobs, bundles}."""
    in_review_environment()
    if not contribution.SHA1.match(commit or ""):
        raise Refused(f"{commit!r}: not a commit's full sha")
    today = today or datetime.date.today().isoformat()
    report = {"entries": [], "blobs": 0, "bundles": []}
    with tempfile.TemporaryDirectory() as work:
        for number, source in enumerate(sources):
            folder = pathlib.Path(work) / str(number)
            folder.mkdir()
            path = downloaded(source, folder)
            opened = unpacked(path, folder / "files")
            taken = checked(root, opened, contributor)
            for sha, blob in sorted(opened["blobs"].items()):
                for store in stores:
                    store.put(sha, blob)
            stamp = {"pr": int(pr), "signed_off": commit, "date": today, "bundle_sha256": bundle_sha256(path)}
            for record in taken:
                record["contribution"] = {**record["contribution"], **stamp}
                entries.write(root, record)
                report["entries"].append(record["address"])
            report["blobs"] += len(opened["blobs"])
            report["bundles"].append(stamp["bundle_sha256"])
    index.rebuild(root)
    return report


def stores_from_environment(environ=None):
    """Every blob store the environment configures (the S3-compatible store and the Hugging Face dataset, or a
    local folder); at least one."""
    from score_lib.blobs import NoBlobStore, from_environment
    environ = dict(os.environ if environ is None else environ)
    found = []
    for keep in (("SCORE_LIB_BLOBS_DIR",), ("SCORE_LIB_S3_ENDPOINT", "SCORE_LIB_S3_BUCKET"), ("SCORE_LIB_HUB_REPO",)):
        if all(environ.get(name) for name in keep):
            others = {"SCORE_LIB_BLOBS_DIR", "SCORE_LIB_S3_ENDPOINT", "SCORE_LIB_HUB_REPO"} - set(keep)
            found.append(from_environment({key: value for key, value in environ.items() if key not in others}))
    if not found:
        raise NoBlobStore("no blob store configured to take the bundle's bytes into")
    return found
