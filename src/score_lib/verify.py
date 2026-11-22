"""score-lib verify (spec section 8): every check over the entries and the index; each failure named with its
address. Exit code 0 only when all pass."""
import json
import pathlib
import shutil
import tempfile

from score_lib import contribution
from score_lib import entry as entries
from score_lib import index, locks, schema
from score_lib.blobs import BadBlob, check_sha
from score_lib.export import algorithm as algorithm_export
from score_lib.fetch import fetch
from score_lib.admission import Sources

USD_ROLES = ("usd",)


def check_schemas(root, records):
    """1. entry.json and index files against schemas/."""
    failures = []
    for record in records:
        failures += [(record.get("address", "?"), f"schema: {message}") for message in schema.faults("entry", record)]
    folder = pathlib.Path(root) / "index"
    for kind in entries.KINDS:
        path = folder / f"{kind}.json"
        if path.is_file():
            failures += [(f"index/{kind}.json", f"schema: {message}")
                         for message in schema.faults("index", entries.load(path))]
    catalogue = folder / "catalogue.json"
    if catalogue.is_file():
        failures += [("index/catalogue.json", f"schema: {message}")
                     for message in schema.faults("catalogue", entries.load(catalogue))]
    return failures


def check_index(root):
    """2. the index in step with entries/."""
    return [(name, "index out of step with entries/ (run score-lib index)") for name in index.stale(root)]


def check_locks(root, records):
    """3. a locked entry's digest against its one row in index/locks.json; an address with more than one row; a
    row whose entry is no longer locked or withdrawn; a row with no entry."""
    rows = locks.read(root)
    by_row = {}
    for row in rows:
        by_row.setdefault(row["address"], []).append(row)
    by_address = {record["address"]: record for record in records}
    failures = [(address, f"{len(found)} rows in index/locks.json, a lock is written once")
                for address, found in sorted(by_row.items()) if len(found) > 1]
    for record in records:
        found = by_row.get(record["address"])
        if record["state"] in ("locked", "withdrawn"):
            if found is None:
                failures.append((record["address"], "locked but not in index/locks.json"))
            elif found[0]["digest"] != entries.digest(record):
                failures.append((record["address"], "content digest differs from index/locks.json"))
        elif found is not None:
            failures.append((record["address"], f"in index/locks.json but its state is {record['state']}"))
    on_disk = {record["address"] for record in entries.all_entries(root)}  # every entry, not only those asked for
    failures += [(address, "locks row with no entry") for address in sorted(by_row) if address not in on_disk]
    return failures


def check_blobs(records, blobs, deep):
    """4. every blob present; with deep (always for a local store) its bytes hash to its name."""
    failures = []
    checked = set()
    for record in records:
        for name, described in sorted(record["files"].items()):
            sha = described["sha256"]
            if not blobs.has(sha):
                failures.append((record["address"], f"blob missing: {name} {sha}"))
                continue
            if (deep or getattr(blobs, "deep_always", False)) and sha not in checked:
                checked.add(sha)
                try:
                    check_blob(blobs, sha)
                except BadBlob as error:
                    failures.append((record["address"], f"blob corrupt: {name}: {error}"))
    return failures


def check_blob(blobs, sha):
    """Hash one blob's bytes (on disk for a local store, downloaded for another)."""
    if hasattr(blobs, "path"):
        check_sha(blobs.path(sha), sha)
        return
    with tempfile.TemporaryDirectory() as folder:
        blobs.get(sha, pathlib.Path(folder) / sha)


MIT_FIRST_LINE = "MIT License"


def check_licence_file(root):
    """5. the one licence: LICENSE at the root is the MIT licence."""
    path = pathlib.Path(root) / "LICENSE"
    if not path.is_file() or path.read_text().splitlines()[:1] != [MIT_FIRST_LINE]:
        return [("LICENSE", "the library's one licence file is missing or not the MIT licence")]
    return []


def check_admission(root, records, sources):
    """5. admission: every source in an entry's made_with may go out under MIT (or under its row's own licence for
    the bytes it carries); an entry names a licence block exactly when it carries such a source's bytes; an algorithm
    carries no file of its outside requirements."""
    failures = []
    for record in records:
        made_with = record.get("provenance", {}).get("made_with")
        failures += [(record["address"], fault) for fault in sources.licence_faults(made_with, record.get("licence"))]
        failures += [(record["address"], f"not MIT: {fault}") for fault in sources.check(made_with)]
        if record["kind"] == "algorithm":
            failures += [(record["address"], f"not MIT: carries {name}, which belongs to an outside requirement")
                         for name in outside_files(root, record)]
    return failures


def outside_files(root, record):
    """An algorithm entry's preset and code files that its definition's outside requirements keep out."""
    path = pathlib.Path(root) / algorithm_export.DEFINITIONS / f"{record['id']}.toml"
    if not path.is_file():
        return []
    definition = algorithm_export.load(path)
    prefixes = ("presets/world1/", "code/")
    return [name for name in sorted(record["files"]) for prefix in prefixes
            if name.startswith(prefix) and algorithm_export.kept_out(name[len(prefix):], definition)]


def check_requires(root, records):
    """6. a locked entry requiring a draft or a missing entry."""
    failures = []
    for record in records:
        if record["state"] != "locked":
            continue
        for address in record.get("requires", []):
            needed = entries.load_address(root, address)
            if needed is None:
                failures.append((record["address"], f"requires missing {address}"))
            elif needed["state"] == "draft":
                failures.append((record["address"], f"locked but requires draft {address}"))
    return failures


def taken_in_faults(blobs, record):
    """A taken-in entry's faults: every file named where it came from and back, its role by its ending, and every
    buffer and picture a .gltf file names held by the entry (the .gltf read from the blob store)."""
    from score_lib.take_in import Refused, role_of
    files, came_from = record["files"], record["provenance"].get("files_from", {})
    faults = [f"{name}: no files_from row" for name in sorted(set(files) - set(came_from))]
    faults += [f"files_from names {name}, which the entry does not hold" for name in sorted(set(came_from) - set(files))]
    for name, described in sorted(files.items()):
        try:
            if role_of(name, record["kind"]) != described["role"]:
                faults.append(f"{name}: role {described['role']}, its ending says {role_of(name, record['kind'])}")
        except Refused as why:
            faults.append(str(why))
        if name.endswith(".gltf") and blobs.has(described["sha256"]):  # a missing blob is check_blobs' fault
            faults += gltf_faults(blobs, name, described["sha256"], files)
    return faults


def gltf_faults(blobs, name, sha, files):
    """Every buffer or picture a .gltf names that its entry does not hold beside it."""
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "model.gltf"
        blobs.get(sha, path)
        found = json.loads(path.read_text())
    base = pathlib.PurePosixPath(name).parent
    named = [each["uri"] for each in found.get("buffers", []) + found.get("images", []) if "uri" in each]
    return [f"{name} names {uri}, which the entry does not hold" for uri in named
            if not uri.startswith("data:") and str(base / uri) not in files]


def check_taken_in(blobs, records):
    """8. every taken-in entry (provenance.taken_from: loose made files, score-lib take-in) whole and named."""
    return [(record["address"], f"taken in: {fault}") for record in records
            if "taken_from" in record.get("provenance", {}) and record["state"] != "withdrawn"
            for fault in taken_in_faults(blobs, record)]


def check_contributed(records, pending=False):
    """9. every outside entry traced to its contributor, pull request, signed-off commit, claim and bundle
    (contribution.py); `pending` checks only the contributor's half, before the pull request is merged."""
    return [(record["address"], f"contribution: {fault}") for record in records if record["state"] != "withdrawn"
            for fault in contribution.faults(record, pending)]


def tail(path):
    """A relative path without its leading ./ and ../ parts (how it ends once anchored to any folder)."""
    parts = path.split("/")
    while parts and parts[0] in (".", ".."):
        parts.pop(0)
    return "/".join(parts)


def allowed_unresolved(records):
    """Paths an entry may leave unresolved: its neighbours' roots (optional views of other places) and paths the
    source assembly already left unresolved."""
    allowed = set()
    for record in records:
        for address in (record.get("usd") or {}).get("neighbours", []):
            kind, entry_id, version = entries.parse_address(address)
            allowed.add(entries.library_path(kind, entry_id, version, f"{entry_id}.usda"))
        allowed.update(tail(path) for path in record.get("provenance", {}).get("unresolved", []))
    return allowed


def check_usd_entry(root, blobs, record):
    """7. one entry fetched with its requires into a temp root; each usd file opened with that root as the search
    path, every dependency resolved, the default prim present."""
    from pxr import Ar, Usd, UsdUtils
    failures = []
    folder = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-verify-"))
    try:
        try:
            fetched = fetch(root, blobs, [record["address"]], folder, with_requires=True, link=True)
        except entries.UnsafeName as why:
            return [(record["address"], f"files: {why}")]
        allowed = allowed_unresolved(fetched)
        context = Ar.ResolverContext(Ar.DefaultResolverContext([str(folder)]))
        kind, entry_id, version = entries.parse_address(record["address"])
        base = folder / entries.library_path(kind, entry_id, version, "")
        usd_files = [name for name, described in sorted(record["files"].items()) if described["role"] in USD_ROLES]
        for name in usd_files:
            path = base / name
            with Ar.ResolverContextBinder(context):
                _, _, unresolved = UsdUtils.ComputeAllDependencies(str(path))
            for missing in unresolved:
                if not any(missing.endswith(each) for each in allowed):
                    failures.append((record["address"], f"usd: {name} names {missing}, which does not resolve"))
        root_file = (record.get("usd") or {}).get("root")
        default = (record.get("usd") or {}).get("default_prim")
        if root_file:
            stage = Usd.Stage.Open(str(base / root_file), context)
            if stage is None:
                failures.append((record["address"], f"usd: {root_file} does not open"))
            elif default and not stage.GetPrimAtPath(default):
                failures.append((record["address"], f"usd: default prim {default} missing"))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return failures


def check_usd(root, blobs, records):
    failures = []
    for record in records:
        if record.get("usd") and record["state"] != "withdrawn":
            failures += check_usd_entry(root, blobs, record)
    return failures


def selected(root, addresses):
    """The records to verify: every entry, or the addresses asked for."""
    records = entries.all_entries(root)
    if addresses:
        records = [record for record in records if record["address"] in set(addresses)]
    return records


def verify(root, blobs, addresses=None, deep=False, usd=True, pending=False):
    """Every failure as (address or file, message); `pending` for an outside contribution not yet merged."""
    records = selected(root, addresses)
    failures = check_schemas(root, records) + check_index(root) + check_locks(root, records)
    failures += check_blobs(records, blobs, deep) + check_licence_file(root)
    failures += check_admission(root, records, Sources.load())
    failures += check_requires(root, records)
    failures += check_taken_in(blobs, records)
    failures += check_contributed(records, pending)
    if usd:
        failures += check_usd(root, blobs, records)
    return failures
