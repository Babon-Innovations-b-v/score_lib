"""An entry's files as a folder of links to the blob store's bytes (read only, nothing copied), and its JSON
records read straight from their blobs."""
import json
import os
import pathlib
import shutil


def linked(record, blobs, folder):
    """A fresh folder naming every file of an entry by its entry path, each a link to its blob; the folder."""
    folder = pathlib.Path(folder)
    if folder.exists():
        shutil.rmtree(folder)
    for name, described in record["files"].items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(blobs.path(described["sha256"]), target)
    return folder


def blob_path(record, blobs, name):
    """The blob path of one of an entry's files, or None when the entry has no such file."""
    described = record["files"].get(name)
    return blobs.path(described["sha256"]) if described else None


def record_json(record, blobs, name):
    """One of an entry's JSON files, or None when it has none."""
    path = blob_path(record, blobs, name)
    return json.loads(path.read_text()) if path else None


def named(record, role=None, ending=""):
    """An entry's file names with a role and ending, sorted."""
    return sorted(name for name, described in record["files"].items()
                  if (role is None or described["role"] == role) and name.endswith(ending))
