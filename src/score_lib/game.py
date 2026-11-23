"""A game engine's kept form of an entry (2099 #245, step 7 of its plan): the bytes an engine converted the entry into
once, kept beside the entry so no box converts it again. Derived and optional like `web3d` and the clip: the library
works without them, and the block is outside the content digest (entry.UNSTAMPED), so keeping one never rewrites a
locked entry's content or moves the digest a piece list names.

    "unity": {"<path>": {"sha256", "bytes"}}

`<path>` is where the engine's cache holds the file, relative to the folder it fetches the library into (2099's
`unity/Places/`): `pieces/<kind>/<id>@<v>/<digest>/<converter>[-masked]-<Unity version>-<platform>.bundle` for a library
piece, and under the place, `pieces/place/<place>/...` for its made pieces, its texture sets and its light pack. The
path carries the entry's digest or the piece's sha256 and the converter, Unity version and platform, so a stale copy
is never read; a new key is a new path beside the old ones. Bytes live in the blob store under their sha256.

    score-lib game-attach --list FILE [--save]     each row {address, path, file} put into the store and its entry
    score-lib fetch ADDRESS... --into DIR --unity  also lays every fetched entry's unity files at DIR/<path>
"""
import json
import pathlib
import posixpath

from score_lib import entry as entries
from score_lib import index
from score_lib.blobs import file_sha256, put_all

BLOCK = "unity"
ROOT = "pieces/"  # every kept file's path lies under the engine's piece cache


class Refused(ValueError):
    """A row of the list that cannot be kept as given."""


def checked_path(path):
    """The path when it is a plain relative POSIX name under ROOT; Refused otherwise."""
    try:
        entries.checked_name(path)
    except entries.UnsafeName as why:
        raise Refused(str(why)) from why
    if not path.startswith(ROOT) or posixpath.normpath(path) != path:
        raise Refused(f"{path!r}: not a plain path under {ROOT}")
    return path


def read_list(path):
    """The rows of a game-attach list: [{address, path, file}]."""
    rows = json.loads(pathlib.Path(path).read_text())
    if not isinstance(rows, list) or not all(isinstance(row, dict) and {"address", "path", "file"} <= set(row)
                                             for row in rows):
        raise Refused(f"{path}: a game-attach list is a JSON list of {{address, path, file}}")
    return rows


def attached(root, blobs, rows):
    """Every row's file put into the blob store, all in one put (the hub allows a few commits an hour, so one a file
    held a place's 33 bundles for hours, 2099 #161), and named in its entry's unity block; the report {kept: [path],
    unchanged: [path], refused: [{path, why}]}, the index rebuilt when an entry changed."""
    report = {"kept": [], "unchanged": [], "refused": []}
    changed, puts = {}, []
    for row in rows:
        try:
            address, path, file = row["address"], checked_path(row["path"]), pathlib.Path(row["file"])
            record = changed.get(address) or entries.load_address(root, address)
            if record is None:
                raise Refused(f"the library holds no {address}")
            if not file.is_file():
                raise Refused(f"{file}: no such file")
            kept = {"sha256": file_sha256(file), "bytes": file.stat().st_size}
            if record.get(BLOCK, {}).get(path) == kept:
                report["unchanged"].append(path)
                continue
            puts.append((kept["sha256"], file))
            changed[address] = {**record, BLOCK: dict(sorted({**record.get(BLOCK, {}), path: kept}.items()))}
            report["kept"].append(path)
        except (Refused, ValueError) as why:
            report["refused"].append({"path": row.get("path"), "why": str(why)})
    if puts:
        put_all(blobs, list({sha256: (sha256, file) for sha256, file in puts}.values()))
    for record in changed.values():
        entries.write(root, record)
    if changed:
        index.rebuild(root)
    report["entries"] = sorted(changed)
    return report


def attached_saved(root, blobs, rows):
    """attached under the handover's lock, then the entries it wrote and the index committed and pushed; its report
    with the `saved` block (handover.saved), saved only when it wrote something."""
    from score_lib import handover  # handover reads fetch, which reads this module
    with handover.locked(root, handover.LOCK_WAIT):
        report = attached(root, blobs, rows)
        if not report["entries"]:
            return {**report, "saved": None}
        paths = sorted({f"entries/{kind}/{entry_id}" for kind, entry_id, _ in map(entries.parse_address,
                                                                                    report["entries"])})
        message = f"game-attach: {len(report['kept'])} unity files kept beside {len(report['entries'])} entries (2099 #245)"
        return {**report, "saved": handover.saved(root, message, (*paths, "index"))}


def files_of(record):
    """[(path, sha256)] of an entry's unity block."""
    return [(path, kept["sha256"]) for path, kept in sorted(record.get(BLOCK, {}).items())]
