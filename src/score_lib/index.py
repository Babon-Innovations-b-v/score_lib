"""The index: index/<kind>.json and index/catalogue.json, built from entries/ alone (index/locks.json is kept by
locks.py)."""
import hashlib
import json
import pathlib

from score_lib import entry as entries

INDEX_SCHEMA = "score-lib-index/1"
CATALOGUE_SCHEMA = "score-lib-catalogue/1"


def row(record):
    """One entry's row in its kind's index."""
    picture = record.get("picture") or {}
    picture_file = record["files"].get(picture.get("file") or "", {})
    return {"address": record["address"], "title": record["title"], "state": record["state"],
            "version": record["version"], "picture_sha256": picture_file.get("sha256"),
            "tags": record.get("tags", {}), "digest": entries.digest(record)}


def kind_index(root, kind):
    """index/<kind>.json's content: every entry of the kind, sorted by id then version."""
    return {"schema": INDEX_SCHEMA, "kind": kind, "entries": [row(record) for record in entries.all_entries(root, kind)]}


def git_blob_hash(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).digest()


def git_tree_hash(folder):
    """Git's tree hash of a folder, computed from its files (what `git write-tree` names it), as hex."""
    return tree_digest(pathlib.Path(folder)).hex()


def tree_digest(folder):
    """The raw sha1 of a folder as a git tree object."""
    items = []
    for path in folder.iterdir() if folder.is_dir() else []:
        if path.is_dir():
            if any(path.rglob("*")):
                items.append((path.name, b"40000", tree_digest(path), True))
        else:
            items.append((path.name, b"100644", git_blob_hash(path.read_bytes()), False))
    items.sort(key=lambda item: item[0] + ("/" if item[3] else ""))
    body = b"".join(mode + b" " + name.encode() + b"\0" + digest for name, mode, digest, _ in items)
    return hashlib.sha1(b"tree %d\0" % len(body) + body).digest()


def worlds(root):
    return sorted(path.stem for path in (pathlib.Path(root) / "worlds").glob("*.toml"))


def catalogue(root, kind_indexes):
    """index/catalogue.json's content."""
    kinds = {}
    for kind, built in kind_indexes.items():
        kinds[kind] = {"count": len(built["entries"]),
                       "locked": sum(1 for each in built["entries"] if each["state"] == "locked"),
                       "file": f"index/{kind}.json"}
    return {"schema": CATALOGUE_SCHEMA, "kinds": kinds, "worlds": worlds(root),
            "built_from": git_tree_hash(pathlib.Path(root) / "entries")}


def built(root):
    """{relative path: content} of every index file except locks.json."""
    kind_indexes = {kind: kind_index(root, kind) for kind in entries.KINDS}
    files = {f"index/{kind}.json": content for kind, content in kind_indexes.items()}
    files["index/catalogue.json"] = catalogue(root, kind_indexes)
    return files


def rebuild(root):
    """Write the index files from entries/ (locks.json made empty when missing)."""
    for name, content in built(root).items():
        path = pathlib.Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entries.dumps(content))
    locks = pathlib.Path(root) / "index/locks.json"
    if not locks.exists():
        locks.write_text(entries.dumps([]))


def stale(root):
    """Index files that differ from a rebuild: [relative path]."""
    found = []
    for name, content in built(root).items():
        path = pathlib.Path(root) / name
        if not path.is_file() or json.loads(path.read_text()) != content:
            found.append(name)
    return found
