"""Materialise entries into a folder: <root>/score_lib/<kind>/<id>/<version>/<file>, optionally with everything they
require. A world then opens its stage with that root as its default search path."""
import os
import pathlib

from score_lib import entry as entries
from score_lib import game


class MissingEntry(KeyError):
    """An address with no entry."""


def closure(root, addresses, with_requires):
    """Every entry record to fetch: the addresses, and what they require (followed) when asked; in address order."""
    found = {}
    pending = list(addresses)
    while pending:
        address = pending.pop()
        if address in found:
            continue
        record = entries.load_address(root, address)
        if record is None:
            raise MissingEntry(address)
        found[address] = record
        if with_requires:
            pending += [each for each in record.get("requires", []) if each not in found]
    return [found[address] for address in sorted(found)]


def destination(into, record, name):
    """Where one file of an entry is fetched to; UnsafeName when its name would land outside the entry's folder (an
    existing link is fetch's own and stays: place_file never writes through it)."""
    kind, entry_id, version = entries.parse_address(record["address"])
    return entries.inside(pathlib.Path(into) / entries.library_path(kind, entry_id, version, ""), name,
                          existing_link_ok=True)


# The sha256 of no bytes: an empty file (a package's __init__.py) is written as it is, never asked of the store.
EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def place_file(blobs, sha, target, link):
    """One file into place: an empty file written, a link to a local blob (verify's read-only use) or a checked
    copy."""
    if target.exists() or target.is_symlink():
        return
    if sha == EMPTY:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return
    if link and hasattr(blobs, "path"):
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(blobs.path(sha), target)
        return
    blobs.get(sha, target)


class MissingFile(KeyError):
    """A file asked for that none of the fetched entries holds."""


def fetch(root, blobs, addresses, into, with_requires=False, link=False, names=None, left_out_roles=(), unity=None):
    """Materialise entries (and their requires), only the files `names` when given and none of a role in
    `left_out_roles`; with `unity` "also" each entry's unity block laid at <into>/<its path> too (score_lib.game), with
    "only" that block alone; the records fetched."""
    records = closure(root, addresses, with_requires)
    held = {name for record in records for name in record["files"]}
    missing = sorted(set(names or ()) - held)
    if missing:
        raise MissingFile(", ".join(missing))
    for record in records:
        for name, described in sorted(record["files"].items()):
            if unity == "only" or (names and name not in names) or described["role"] in left_out_roles:
                continue
            place_file(blobs, described["sha256"], destination(into, record, name), link)
        if unity:
            for path, sha in game.files_of(record):
                place_file(blobs, sha, entries.inside(pathlib.Path(into), game.checked_path(path)), link)
    return records
