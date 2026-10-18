"""score-lib verify (spec section 8): every check over the entries and the index; each failure named with its
address. Exit code 0 only when all pass."""
import pathlib
import shutil
import tempfile

from score_lib import entry as entries
from score_lib import index, locks, schema
from score_lib.blobs import BadBlob, check_sha
from score_lib.fetch import fetch
from score_lib.licences import Licences

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
    """3. a locked entry's digest against index/locks.json; a locks row with no entry."""
    rows = locks.read(root)
    newest = {}
    for row in rows:
        newest[row["address"]] = row
    by_address = {record["address"]: record for record in records}
    failures = []
    for record in records:
        if record["state"] in ("locked", "withdrawn"):
            row = newest.get(record["address"])
            if row is None:
                failures.append((record["address"], "locked but not in index/locks.json"))
            elif row["digest"] != entries.digest(record):
                failures.append((record["address"], "content digest differs from index/locks.json"))
    failures += [(address, "locks row with no entry") for address in sorted(newest) if address not in by_address]
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


def check_licences(records, licences):
    """5. licence: spdx present and known, commercial_ok matching its components."""
    return [(record["address"], f"licence: {fault}") for record in records
            for fault in licences.check(record.get("licence", {}))]


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


def allowed_unresolved(records):
    """Paths an entry may leave unresolved: its neighbours' roots (optional views of other places) and paths the
    source assembly already left unresolved."""
    allowed = set()
    for record in records:
        for address in (record.get("usd") or {}).get("neighbours", []):
            kind, entry_id, version = entries.parse_address(address)
            allowed.add(entries.library_path(kind, entry_id, version, f"{entry_id}.usda"))
        allowed.update(record.get("provenance", {}).get("unresolved", []))
    return allowed


def check_usd_entry(root, blobs, record):
    """7. one entry fetched with its requires into a temp root; each usd file opened with that root as the search
    path, every dependency resolved, the default prim present."""
    from pxr import Ar, Usd, UsdUtils
    failures = []
    folder = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-verify-"))
    try:
        fetched = fetch(root, blobs, [record["address"]], folder, with_requires=True, link=True)
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


def verify(root, blobs, addresses=None, deep=False, usd=True):
    """Every failure as (address or file, message)."""
    records = selected(root, addresses)
    licences = Licences.load()
    failures = check_schemas(root, records) + check_index(root) + check_locks(root, records)
    failures += check_blobs(records, blobs, deep) + check_licences(records, licences)
    failures += check_requires(root, records)
    if usd:
        failures += check_usd(root, blobs, records)
    return failures
