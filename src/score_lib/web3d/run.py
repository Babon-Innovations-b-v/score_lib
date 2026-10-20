"""score-lib web3d: give every model, kit, character and place entry its web copy, one entry at a time.

An entry is made again when it has no copy, when its copy's files are not in the blob store, when its own content
changed since (record.source_of), or when an entry its copy read changed or has a newer version. A locked entry is never rewritten: it is listed and left. Each copy's
two files go into the blob store and the entry, then the index is rebuilt once.
"""
import shutil

from score_lib import entry as entries
from score_lib import index
from score_lib.blobs import file_sha256
from score_lib.web3d import build, record as copies
from score_lib.web3d.finish import Library
from score_lib.web3d.pack import GLTFPACK

KINDS = ("model", "character", "kit", "place")
MADE_WITH = f"score_lib web3d ({copies.VERSION}): meshes from the entry's OpenUSD, finishes from the surface " \
            f"library, {GLTFPACK} meshopt (-cc -kn -km -vpf), pictures WebP"


def newest(root, kind):
    """The newest not withdrawn version of every entry of a kind."""
    found = {}
    for record in entries.all_entries(root, kind):
        if record["state"] != "withdrawn":
            found[record["id"]] = record
    return [found[entry_id] for entry_id in sorted(found)]


def stale_reason(root, blobs, record):
    """Why an entry needs a copy made, or None when its copy stands."""
    made = record.get("web3d")
    if not made:
        return "no copy"
    if not all(blobs.has(made[key]) for key in ("full_sha256", "light_sha256")):
        return "its copy is not in the blob store"
    if made.get("source") != copies.source_of(record):
        return "its content changed"
    for address, source in sorted((made.get("from") or {}).items()):
        read = entries.load_address(root, address)
        kind, entry_id, _ = entries.parse_address(address)
        if read is None or copies.source_of(read) != source:
            return f"{address} changed"
        if entries.versions(root, kind, entry_id)[-1]["address"] != address:
            return f"{address} has a newer version"
    return None


def recorded(root, record, work, made, facts):
    """The entry with its copy recorded: both files in `files`, the web3d field."""
    found = dict(record)
    found["files"] = dict(record["files"])
    for name, path in (("web3d/light.glb", made["light"]), ("web3d/full.glb", made["full"])):
        found["files"][name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size, "role": "web3d"}
    read = {}
    for address in facts["read"]:
        other = entries.load_address(root, address)
        read[address] = copies.source_of(other)
    found["web3d"] = {"file": "web3d/full.glb", "light": "web3d/light.glb",
                      "full_sha256": found["files"]["web3d/full.glb"]["sha256"],
                      "light_sha256": found["files"]["web3d/light.glb"]["sha256"],
                      "bytes": found["files"]["web3d/full.glb"]["bytes"],
                      "light_bytes": found["files"]["web3d/light.glb"]["bytes"],
                      "triangles": facts["triangles"], "objects": facts["objects"], "left_out": facts["left_out"],
                      "finishes": facts["finishes"], "replaced": facts["replaced"],
                      "source": copies.source_of(record), "from": read, "made_with": MADE_WITH}
    return found


def make_one(root, blobs, library, record, cache):
    """Make and record one entry's copy: the new record."""
    work, made, facts = build.build(root, blobs, library, record, cache)
    try:
        found = recorded(root, record, work, made, facts)
        for name, path in (("web3d/light.glb", made["light"]), ("web3d/full.glb", made["full"])):
            if not blobs.has(found["files"][name]["sha256"]):
                blobs.put(found["files"][name]["sha256"], path)
        entries.write(root, found)
        return found
    finally:
        shutil.rmtree(work, ignore_errors=True)


def selected(root, kinds, addresses):
    """The entries to look at: the newest of each kind asked for, or the addresses."""
    if addresses:
        return [entries.load_address(root, address) for address in addresses]
    return [record for kind in kinds for record in newest(root, kind)
            if not (kind == "model" and record.get("usd") is None)]  # a taken-in glTF has no OpenUSD to copy from


def run(root, blobs, cache, kinds=KINDS, addresses=None, limit=None, say=print):
    """Make every stale copy (at most `limit`); the report {made, standing, locked, failed}."""
    library = Library(root, blobs)
    report = {"made": [], "standing": 0, "locked": [], "failed": []}
    for record in selected(root, kinds, addresses):
        reason = stale_reason(root, blobs, record)
        if reason is None:
            report["standing"] += 1
            continue
        if record["state"] != "draft":
            report["locked"].append(record["address"])
            continue
        if limit is not None and len(report["made"]) >= limit:
            break
        try:
            found = make_one(root, blobs, library, record, cache)
        except (RuntimeError, ValueError, LookupError) as error:
            report["failed"].append({"address": record["address"], "error": str(error)[:500]})
            say(f"FAIL {record['address']}: {str(error)[:300]}")
            continue
        made = found["web3d"]
        report["made"].append({"address": record["address"], "bytes": made["bytes"], "light_bytes": made["light_bytes"],
                               "triangles": made["triangles"], "finishes": made["finishes"], "reason": reason})
        say(f"{record['address']}: {made['triangles']} triangles, {made['bytes'] / 1e6:.2f} MB "
            f"(light {made['light_bytes'] / 1e6:.2f} MB), finishes {made['finishes']} ({reason})")
    index.rebuild(root)
    return report
