"""The export run: the order of kinds, each kind's candidates resolved against the entries already written (new,
unchanged, changed draft, new version, refused), the sha -> entry map the layers are rewritten with, the dry-run
report, and the writes (blobs put, `entry.json` written as draft, the index rebuilt).

Output is a pure function of the store, the repo commit and the world toml: no wall-clock fields, sorted keys, and
generated files made the same way each time. Locked entries are never written; changed content for a locked id
becomes the next version as a draft.
"""
import dataclasses
import functools
import hashlib
import importlib
import json
import os
import pathlib
import shutil
import tempfile
import tomllib

from score_lib import entry as entries
from score_lib import index
from score_lib.blobs import file_sha256
from score_lib.admission import NotAdmitted, Sources
from score_lib.rewrite import info
from score_lib.source.release import Release
from score_lib.source.repo import Repo
from score_lib.source.store import Store

ORDER = ("algorithm", "surface", "model", "light", "sound", "effect", "character", "ground", "kit", "layer", "place")
ATOMS = ORDER[:-2]
STATUSES = ("new", "unchanged", "changed draft", "new version", "refused")
UNVERSIONED = ("address", "version", "state", "locked")


@dataclasses.dataclass
class Candidate:
    """One entry a kind proposes, before its id, version and state are settled."""
    kind: str
    base_id: str
    identity: str
    title: str
    places: list
    files: dict  # entry file name -> local path
    roles: dict  # entry file name -> role
    made_with: list = dataclasses.field(default_factory=list)  # {what, source, carried?}: admission.made_by/carried
    usd: dict | None = None  # {root, layer, use}
    requires: list = dataclasses.field(default_factory=list)
    provenance: dict = dataclasses.field(default_factory=dict)
    tags: dict = dataclasses.field(default_factory=dict)
    picture: dict | None = None  # {file, made_by, of} or {reason}
    known: dict = dataclasses.field(default_factory=dict)  # entry file name -> sha256 already known (store blobs)
    files_from: dict = dataclasses.field(default_factory=dict)  # name -> {path, sha256} when the bytes were rewritten
    aliases: list = dataclasses.field(default_factory=list)  # [source sha256, entry file name]: other store bytes
    refused: str | None = None
    entry_id: str | None = None


@dataclasses.dataclass
class Planned:
    candidate: Candidate
    status: str
    address: str | None
    record: dict | None
    reason: str = ""


class Context:
    """What every kind's plan reads: the store, the repo at the commit, the world, the sources an entry may be made
    from, the map of store bytes to the entries holding them and the bytes of refused entries."""

    def __init__(self, root, world, store, repo, sources, places=None, scratch=None, cache=None):
        self.root = pathlib.Path(root)
        self.world = world
        self.store = store
        self.repo = repo
        self.sources = sources
        self.places = sorted(places or world["places"])
        self.scratch = pathlib.Path(scratch or tempfile.mkdtemp(prefix="score-lib-"))
        self.cache = pathlib.Path(cache) if cache else None
        self.release = Release.from_repo(repo, world["name"])
        self.sha_map = {}  # store sha256 -> (address, entry file name)
        self.addresses = {}  # (kind, identity) -> address
        self.by_id = {}  # (kind, id) -> address planned in this run
        self.memo = {}  # values the kinds share (a place's object prims, its layer paths), made once a run
        self.refused = {}  # sha256 of a refused entry's bytes -> why, so no other entry carries them in
        self.seed_map_from_entries()

    def remembered(self, key, make):
        """A value made once a run and shared by the kinds."""
        if key not in self.memo:
            self.memo[key] = make()
        return self.memo[key]

    def seed_map_from_entries(self):
        """Map the bytes of entries already written (their newest, not withdrawn version) so a run limited to some
        kinds still rewrites layers against them."""
        newest = {}
        for record in entries.all_entries(self.root):
            if record["state"] != "withdrawn":
                newest[(record["kind"], record["id"])] = record
        for record in newest.values():
            self.map_record(record)

    def map_record(self, record):
        """Put one atom entry's files (their bytes, the store bytes they came from, and aliases) into the sha map;
        layer and place entries are never mapped (a layer's own files stay its own)."""
        self.by_id[(record["kind"], record["id"])] = record["address"]
        if record["kind"] not in ATOMS:
            return
        for name, described in sorted(record["files"].items()):
            self.sha_map.setdefault(described["sha256"], (record["address"], name))
        provenance = record.get("provenance", {})
        for name, source in sorted(provenance.get("files_from", {}).items()):
            self.sha_map.setdefault(source["sha256"], (record["address"], name))
        for sha, name in provenance.get("aliases", []):
            self.sha_map.setdefault(sha, (record["address"], name))

    @functools.cache
    def assembly(self, place):
        """(output, listing) of a place's accepted assembly, or (None, {})."""
        output = self.store.accepted(f"assembly@{place}")
        return (output, self.store.listing(output)) if output else (None, {})

    @functools.cache
    def tree(self, output):
        """A folder of links naming an output's files."""
        return self.store.link_tree(output, self.scratch / "trees" / output)

    def work(self, *parts):
        """A fresh folder in the scratch space for generated files."""
        folder = self.scratch.joinpath("work", *parts)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        return folder

    def blob(self, sha):
        return self.store.blob(sha)


def write_json(path, record):
    """Write a generated JSON file (sorted keys, two-space indent); the path."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    return path


def load_world(root, name):
    """A world's toml (worlds/<name>.toml)."""
    with open(pathlib.Path(root) / "worlds" / f"{name}.toml", "rb") as source:
        world = tomllib.load(source)
    world.setdefault("name", name)
    return world


def context_from_environment(root, world_name, places=None, cache=None):
    """A context from SCORE_STORE (the store's objects folder) and SCORE_REPO plus the world toml's commit."""
    for variable in ("SCORE_STORE", "SCORE_REPO"):
        if not os.environ.get(variable):
            raise SystemExit(f"{variable} is not set: SCORE_STORE names a SCORE store's objects folder, SCORE_REPO "
                             f"the score repository (read only)")
    world = load_world(root, world_name)
    store = Store(pathlib.Path(os.environ["SCORE_STORE"]).expanduser())
    repo = Repo(pathlib.Path(os.environ["SCORE_REPO"]).expanduser(), world["commit"])
    return Context(root, world, store, repo, Sources.load(), places, cache=cache or os.environ.get("SCORE_LIB_CACHE"))


def kind_module(kind):
    return importlib.import_module(f"score_lib.export.{kind}")


def settle_ids(candidates):
    """Give each candidate its id: the base id, or `<base>.<place>` when one base id holds different content.
    Returns the collisions resolved as {base id: [ids]}."""
    groups = {}
    for candidate in candidates:
        if candidate.entry_id is None:  # a kind may name the id itself (a dress made for one place)
            groups.setdefault(candidate.base_id, []).append(candidate)
    collisions = {}
    for base, group in sorted(groups.items()):
        if len(group) == 1:
            group[0].entry_id = base
            continue
        taken = set()
        for candidate in sorted(group, key=lambda each: (each.places or [""])[0]):
            choices = [f"{base}.{place}" for place in candidate.places] or [f"{base}.{candidate.identity[:8]}"]
            free = [choice for choice in choices if choice not in taken]
            if not free:
                raise RuntimeError(f"{candidate.kind}/{base}: no place qualifier left for {candidate.identity}")
            candidate.entry_id = free[0]
            taken.add(free[0])
        collisions[base] = sorted(taken)
    return collisions


def file_table(candidate):
    """{name: {sha256, bytes, role}} of a candidate's files."""
    table = {}
    for name, path in sorted(candidate.files.items()):
        sha = candidate.known.get(name) or file_sha256(path)
        table[name] = {"sha256": sha, "bytes": os.path.getsize(path), "role": candidate.roles[name]}
    return table


def usd_block(candidate, address):
    """The entry's usd block: the root file's metadata, its layer, how a world uses it and its search path."""
    if not candidate.usd:
        return None
    kind, entry_id, version = entries.parse_address(address)
    root = candidate.usd["root"]
    block = {"root": root, "layer": candidate.usd.get("layer"), "use": candidate.usd["use"],
             "path": entries.library_path(kind, entry_id, version, root)}
    block.update(info(candidate.files[root]))
    for key in ("neighbours",):
        if candidate.usd.get(key):
            block[key] = candidate.usd[key]
    return block


def picture_block(candidate):
    """The picture field: {file, made_by, of}, or None when the entry has none (its reason kept beside it)."""
    if candidate.picture and candidate.picture.get("file"):
        return {key: candidate.picture[key] for key in ("file", "made_by", "of")}
    return None


def build_record(context, candidate, address, state):
    """The full entry record of a candidate at an address."""
    kind, entry_id, version = entries.parse_address(address)
    provenance = dict(candidate.provenance)
    provenance["made_with"] = context.sources.made_with(candidate.made_with)
    provenance["world"] = context.world["name"]
    provenance["commit"] = context.world["commit"]
    if candidate.files_from:
        provenance["files_from"] = candidate.files_from
    if candidate.aliases:
        provenance["aliases"] = sorted(candidate.aliases)
    record = {"schema": entries.SCHEMA, "address": address, "kind": kind, "id": entry_id, "version": version,
              "title": candidate.title, "state": state, "picture": picture_block(candidate),
              "files": file_table(candidate), "usd": usd_block(candidate, address),
              "requires": sorted(set(candidate.requires)),
              "provenance": provenance, "tags": candidate.tags, "seen_in": sorted(set(candidate.places))}
    if record["picture"] is None:
        record["picture_reason"] = (candidate.picture or {}).get("reason", "no picture made")
    return record


def comparable(record):
    """An entry without what differs between versions of the same content (address, version, state, lock date,
    the usd search path)."""
    found = {key: value for key, value in record.items() if key not in UNVERSIONED}
    if found.get("usd"):
        found["usd"] = {key: value for key, value in found["usd"].items() if key != "path"}
    return json.dumps(found, sort_keys=True)


TEXT_RECORDS = (".json", ".txt")


def home_free(context, candidate):
    """Replace the exporting account's home folder with `~` in a candidate's text records (absolute local paths
    must not reach a public blob store); a changed file keeps the sha of the source bytes in files_from."""
    home = str(pathlib.Path.home()).rstrip("/") + "/"
    for name, path in sorted(candidate.files.items()):
        if candidate.roles.get(name) != "record" or not name.endswith(TEXT_RECORDS):
            continue
        text = pathlib.Path(path).read_text(errors="surrogateescape")
        if home not in text:
            continue
        source = candidate.known.pop(name, None) or file_sha256(path)
        folder = hashlib.sha256(f"{candidate.kind}/{candidate.identity}".encode()).hexdigest()[:16]
        target = context.scratch / "home_free" / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(home, "~/"), errors="surrogateescape")
        candidate.files[name] = target
        candidate.files_from.setdefault(name, {"path": name, "sha256": source})


def candidate_bytes(candidate):
    """The sha256 of every file a candidate would carry (the store's sha where known)."""
    return {candidate.known.get(name) or file_sha256(path) for name, path in sorted(candidate.files.items())}


def refused_carried(context, record):
    """Why an entry may not go out because it carries a refused entry's bytes, or ''."""
    shas = [described["sha256"] for described in record["files"].values()]
    shas += [source["sha256"] for source in record["provenance"].get("files_from", {}).values()]
    for sha in shas:
        if sha in context.refused:
            return f"not MIT: carries the bytes of refused {context.refused[sha]}"
    return ""


def resolve(context, candidate):
    """Settle one candidate against the entries already written: its status, address and record."""
    if candidate.refused:
        return Planned(candidate, "refused", None, None, candidate.refused)
    home_free(context, candidate)
    existing = entries.versions(context.root, candidate.kind, candidate.entry_id)
    latest = existing[-1] if existing else None
    version = latest["version"] if latest else 1
    try:
        record = build_record(context, candidate, entries.address_of(candidate.kind, candidate.entry_id, version),
                              "draft")
    except NotAdmitted as error:
        return Planned(candidate, "refused", None, None, f"not MIT: {error}")
    carried = refused_carried(context, record)
    if carried:
        return Planned(candidate, "refused", None, None, carried)
    if latest is None:
        return Planned(candidate, "new", record["address"], record)
    if comparable(latest) == comparable(record):
        return Planned(candidate, "unchanged", latest["address"], latest)
    if latest["state"] == "draft":
        return Planned(candidate, "changed draft", record["address"], record)
    address = entries.address_of(candidate.kind, candidate.entry_id, latest["version"] + 1)
    return Planned(candidate, "new version", address, build_record(context, candidate, address, "draft"))


def remember(context, planned):
    """Put a planned entry's bytes and identity into the maps later kinds read."""
    if planned.record is None:
        return
    record = planned.record
    context.addresses[(record["kind"], planned.candidate.identity)] = record["address"]
    context.by_id[(record["kind"], record["id"])] = record["address"]
    if record["kind"] not in ATOMS:
        return
    for name, described in sorted(record["files"].items()):
        context.sha_map[described["sha256"]] = (record["address"], name)
    for name, source in sorted(record["provenance"].get("files_from", {}).items()):
        context.sha_map.setdefault(source["sha256"], (record["address"], name))
    for sha, name in record["provenance"].get("aliases", []):
        context.sha_map.setdefault(sha, (record["address"], name))


def plan_kind(context, kind):
    """Every planned entry of one kind and the id collisions resolved."""
    candidates = kind_module(kind).plan(context)
    collisions = settle_ids([candidate for candidate in candidates])
    planned = [resolve(context, candidate) for candidate in sorted(candidates, key=lambda each: each.entry_id)]
    for each in planned:
        remember(context, each)
        if each.status == "refused" and not each.candidate.refused:
            for sha in candidate_bytes(each.candidate):
                context.refused.setdefault(sha, f"{kind}/{each.candidate.entry_id}")
    return planned, collisions


def write_planned(context, blobs, planned):
    """Put a planned entry's blobs (skipped when present) and write its entry.json unless it is unchanged."""
    if planned.status == "refused":
        return
    for name, described in sorted(planned.record["files"].items()):
        if not blobs.has(described["sha256"]):
            blobs.put(described["sha256"], planned.candidate.files[name])
    if planned.status != "unchanged":
        entries.write(context.root, planned.record)


def kind_report(planned, collisions):
    """{counts, entries by status, refused with reasons, collisions} of one kind."""
    report = {"counts": {status: 0 for status in STATUSES}, "refused": [], "collisions": collisions,
              "addresses": {status: [] for status in STATUSES if status != "refused"}}
    for each in planned:
        report["counts"][each.status] += 1
        if each.status == "refused":
            report["refused"].append({"id": each.candidate.entry_id, "reason": each.reason,
                                      "places": each.candidate.places})
        else:
            report["addresses"][each.status].append(each.address)
    return report


def export(context, kinds=None, dry_run=False, blobs=None):
    """Run the export over the kinds (in ORDER); the report. Writes nothing when dry_run."""
    asked = [kind for kind in ORDER if kinds is None or kind in kinds]
    report = {"world": context.world["name"], "commit": context.world["commit"], "dry_run": dry_run, "kinds": {}}
    for kind in asked:
        planned, collisions = plan_kind(context, kind)
        if not dry_run:
            for each in planned:
                write_planned(context, blobs, each)
        report["kinds"][kind] = kind_report(planned, collisions)
    if not dry_run:
        index.rebuild(context.root)
    return report


def report_text(report):
    """The dry-run listing: per kind the counts, then each refused entry and collision."""
    lines = [f"world {report['world']} at {report['commit']}{' (dry run: nothing written)' if report['dry_run'] else ''}"]
    for kind, found in report["kinds"].items():
        counts = ", ".join(f"{status} {found['counts'][status]}" for status in STATUSES)
        lines.append(f"{kind}: {counts}")
        for status in ("new", "changed draft", "new version"):
            for address in found["addresses"][status]:
                lines.append(f"  {status}: {address}")
        for refused in found["refused"]:
            lines.append(f"  refused: {kind}/{refused['id']} ({', '.join(refused['places'])}): {refused['reason']}")
        for base, ids in found["collisions"].items():
            lines.append(f"  id collision {kind}/{base} resolved as {', '.join(ids)}")
    return "\n".join(lines) + "\n"
