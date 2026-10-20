"""How an entry records its web copy, and when that copy still stands.

    "web3d": {"file": "web3d/full.glb", "light": "web3d/light.glb", "full_sha256": ..., "light_sha256": ...,
              "bytes": ..., "light_bytes": ..., "triangles": ..., "objects": ..., "left_out": {...},
              "finishes": {"library": n, "library bake": n, "replaced": n}, "replaced": {...},
              "source": <sha256 of what the copy was made from>, "from": {<address>: <its source>},
              "made_with": ...}

and both files in `files` with role `web3d`, their bytes in the blob store. A copy stands while the entry's own content
(every file but its pictures, its close-up and the copy) and every entry it read are what they were.
"""
import hashlib
import json

VERSION = "web3d-1"
FILES = ("web3d/full.glb", "web3d/light.glb")
SKIPPED_ROLES = ("picture", "web3d", "closeup")


def source_of(record):
    """The digest of what an entry's copy is made from: its files but pictures and copies, its usd block and its
    requires, with the copy method's version."""
    files = {name: described["sha256"] for name, described in sorted(record["files"].items())
             if described["role"] not in SKIPPED_ROLES}
    usd = {key: value for key, value in (record.get("usd") or {}).items() if key != "path"}
    text = json.dumps({"version": VERSION, "files": files, "usd": usd, "requires": sorted(record.get("requires", []))},
                      sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def carried(record, previous):
    """The record with the previous version's web copy carried over when it was made from the same content; else the
    record as it is."""
    if not previous or not previous.get("web3d") or previous["web3d"].get("source") != source_of(record):
        return record
    found = dict(record)
    found["files"] = dict(record["files"])
    for name in FILES:
        if name in previous["files"]:
            found["files"][name] = previous["files"][name]
    found["web3d"] = previous["web3d"]
    return found
