"""The handover: one call that takes a place's accepted outputs (or the world's own) into the library as drafts and
writes the report score reads, `handover.json` (contract `library-handover`, version 1).

Under a file lock it records the place and seed in `worlds/<world>.toml`, runs the export at the commit given (not
written into the toml), gives each entry its web 3D copy and quality score through this library's own functions,
stamps each entry made from a store output with the route score made it under (`--route`, in its provenance),
works out which entries hold each accepted output, commits and pushes `entries/ index/ worlds/`, and writes the
report. Locked entries are never rewritten; nothing is locked or withdrawn here. The report has sorted keys and no
clock fields, so a handover of unchanged outputs writes the same bytes.

A place's handover exports the place kinds over every place of the world, not the one place alone: an entry seen in
several places (a model, a surface) carries all of them in `seen_in`, and an export of one place would rewrite it
with that place only, back and forth on every handover.
"""
import contextlib
import fcntl
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import tomllib

from score_lib import entry as entries
from score_lib import held, index
from score_lib.admission import Sources
from score_lib.export import run
from score_lib.source.repo import Repo
from score_lib.source.store import Store, place_of, stage_of

SCHEMA = "library-handover"
VERSION = 1
PLACE_KINDS = tuple(kind for kind in run.ORDER if kind not in held.WORLD_KINDS)
LIBRARY_STAGES = ("library", "library-world")  # the handover nodes themselves: no entry holds their reports
COPY_KINDS = ("model", "layer", "place", "character")  # the kinds the cockpit shows in 3D
COPY_ROLE = "web3d"
LOCK = ".handover.lock"
LOCK_WAIT = 1800.0
SAVED_PATHS = ("entries", "index", "worlds")
PUSH_TRIES = 3  # times a save catches up with origin and pushes, while other writers push between the two
# The hooks the copies and quality work fills in: each a function (root, blobs, record) -> its block, or None.
COPY_HOOK = ("score_lib.copies", "web_copy")
QUALITY_HOOK = ("score_lib.quality", "score_of")


class HandoverError(RuntimeError):
    """The handover could not run at all (no store, the lock held past its wait): no report is written."""


@contextlib.contextmanager
def locked(root, wait):
    """Hold `<root>/.handover.lock` for the block, waiting up to `wait` seconds for another handover to let go."""
    path = pathlib.Path(root) / LOCK
    deadline = time.monotonic() + wait
    with open(path, "w") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise HandoverError(f"another handover held {path} for longer than {wait:.0f} s") from None
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def world_text(name, seed, commit, places):
    """The text of a new world toml."""
    listed = ", ".join(json.dumps(place) for place in places)
    return (f"# {name}: the source the library is exported from (written by the handover).\n"
            f'name = "{name}"\nseed = {seed}\ncommit = "{commit}"  # the commit of its first handover\n'
            f"places = [{listed}]\n")


def with_seed(text, seed):
    """A world toml's text with its seed line set, any comment on the line kept."""
    return re.sub(r"^seed\s*=\s*[^#\n]*?(\s*#.*)?$", lambda found: f"seed = {seed}{found[1] or ''}", text, count=1,
                  flags=re.M)


def with_place(text, place, places):
    """A world toml's text with a place added to its places list (kept sorted)."""
    listed = ", ".join(json.dumps(each) for each in sorted(set(places) | {place}))
    return re.sub(r"^places\s*=\s*\[[^\]]*\]", f"places = [{listed}]", text, count=1, flags=re.M)


def record_world(root, name, place, seed, commit):
    """Make or update `worlds/<name>.toml`: the place added to its places, its seed set. The commit is written only
    into a new file; each handover passes its own commit to the export instead."""
    path = pathlib.Path(root) / "worlds" / f"{name}.toml"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(world_text(name, seed, commit, [place] if place else []))
        return
    text = path.read_text()
    found = tomllib.loads(text)
    text = with_seed(text, seed) if "seed" in found else text.replace("\nplaces", f"\nseed = {seed}\nplaces", 1)
    if place and place not in found.get("places", []):
        text = with_place(text, place, found.get("places", []))
    path.write_text(text)


def context_at(root, name, commit, store, repo, scratch, route=None):
    """An export context for the world at a commit given by the caller (the toml's own commit left as written), its
    entries' provenance stamped with the route score made the outputs under."""
    world = dict(run.load_world(root, name), commit=commit)
    return run.Context(root, world, Store(store), Repo(repo, commit), Sources.load(), scratch=scratch,
                       route=route)


def exported(context, place, blobs):
    """Run the export for a place's kinds (or the world node's kinds when place is None); its report."""
    kinds = list(PLACE_KINDS if place else held.WORLD_KINDS)
    return run.export(context, kinds, dry_run=False, blobs=blobs)


def statuses(report):
    """{address: status} of every entry the export planned (refused ones have no address)."""
    found = {}
    for kind_report in report["kinds"].values():
        for status, addresses in kind_report["addresses"].items():
            for address in addresses:
                found[address] = status
    return found


def planned_records(root, report, place):
    """The records of every entry this export planned that the place (None: the world node) holds, by address."""
    records = [entries.load_address(root, address) for address in sorted(statuses(report))]
    return [record for record in records if record and held.holds(record, place)]


def hook(module_name, function_name):
    """A function this library offers for a hook, or None while it has none."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    return getattr(module, function_name, None)


def own_copy(record):
    """The web 3D copy recorded in the entry ({sha256, light_sha256, role}), or None when it carries none. Read from
    its `web3d` field, never from a file's role: a character's body.glb is a source file, not its web copy."""
    made = record.get("web3d")
    if not made:
        return None
    return {"sha256": made["full_sha256"], "light_sha256": made["light_sha256"], "role": COPY_ROLE}


def web_copy(root, blobs, record):
    """(copy, why) of one entry: its web 3D copy, or None and why there is none."""
    if record["kind"] not in COPY_KINDS:
        return None, f"a {record['kind']} entry is not shown in 3D"
    make = hook(*COPY_HOOK)
    if make is None:
        found = own_copy(record)
        return (found, "") if found else (None, "no copies command in this library yet")
    try:
        found = make(root, blobs, record)
    except (RuntimeError, ValueError, LookupError) as error:  # one entry's copy failing is that row's why
        return None, f"its copy failed: {str(error)[:300]}"
    return (found, "") if found else (None, "the copies command made no copy of this entry")


def quality(root, blobs, record):
    """(quality, why) of one entry: its score and measures, or None and why there is none."""
    score = hook(*QUALITY_HOOK)
    if score is None:
        return None, "no quality command in this library yet"
    found = score(root, blobs, record)
    return (found, "") if found else (None, "the quality command gave this entry no score")


def entry_row(root, blobs, record, status):
    """One entry's row of the report: address, state, status, provenance, copy and quality."""
    provenance = record.get("provenance", {})
    copy, copy_why = web_copy(root, blobs, record)
    score, quality_why = quality(root, blobs, record)
    return {"address": record["address"], "kind": record["kind"], "state": record["state"], "status": status,
            "node": provenance.get("node"), "output": provenance.get("output"),
            "source": "" if provenance.get("node") else provenance.get("source", ""),
            "copy": copy, "copy_why": copy_why, "quality": score, "quality_why": quality_why}


def handed_nodes(store, place):
    """The accepted nodes a handover covers: the place's (None: the world's own), the handover nodes left out."""
    return [node for node in store.nodes() if place_of(node) == place and stage_of(node) not in LIBRARY_STAGES
            and store.accepted(node)]


def refused_rows(report, place):
    """The entries the export refused that came from the place (None: the world node's kinds)."""
    rows = []
    for kind, kind_report in sorted(report["kinds"].items()):
        for refused in kind_report["refused"]:
            node = refused.get("node")
            if place is None or place in refused["places"] or (node and place_of(node) == place):
                rows.append({"kind": kind, "id": refused["id"], "node": node, "output": refused.get("output"),
                             "reason": refused["reason"]})
    return sorted(rows, key=lambda row: (row["kind"], row["id"]))


def not_held_why(node, output, refused):
    """Why no entry holds an accepted output: the export's refusal of it, else that no exporter takes its stage."""
    reasons = sorted({row["reason"] for row in refused if row["node"] == node and row["output"] == output})
    if reasons:
        return "refused: " + "; ".join(reasons)
    return f"no exporter takes {stage_of(node)} outputs"


def covered_rows(store, holders, place, refused):
    """One row per accepted output the handover covers: the entries holding it, or why none does."""
    rows = []
    for node in handed_nodes(store, place):
        output = store.accepted(node)
        addresses = holders.of(node, output, store.listing(output))
        rows.append({"node": node, "output": output, "entries": addresses, "exception": None,
                     "why": "" if addresses else not_held_why(node, output, refused)})
    return rows


def git(root, *arguments):
    """One git command in the library root; the finished process (never raises)."""
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True)


def is_repository(root):
    """True when the library root is the top of a git repository (not a folder inside another one)."""
    done = git(root, "rev-parse", "--show-toplevel")
    return done.returncode == 0 and pathlib.Path(done.stdout.strip()).resolve() == pathlib.Path(root).resolve()


def identity(root):
    """The library's identity: its repository's root commit, else its folder."""
    if is_repository(root):
        done = git(root, "rev-list", "--max-parents=0", "HEAD")
        if done.returncode == 0 and done.stdout.strip():
            return sorted(done.stdout.split())[0]
    return str(pathlib.Path(root).resolve())


def last_line(done):
    """The last line a failed git command printed."""
    lines = (done.stderr or done.stdout).strip().splitlines()
    return lines[-1] if lines else f"git exited {done.returncode}"


def committed(root, message, paths=SAVED_PATHS):
    """(commit, why): commit `paths` (only those; `entries/ index/ worlds/` by default), or name the newest commit
    holding them when nothing changed; None and why when no commit could be made."""
    present = [path for path in paths if (pathlib.Path(root) / path).exists()]
    added = git(root, "add", "-A", "--", *present)
    if added.returncode != 0:
        return None, last_line(added)
    if git(root, "diff", "--cached", "--quiet", "--", *present).returncode != 0:
        done = git(root, "commit", "-q", "-m", message, "--", *present)
        if done.returncode != 0:
            return None, last_line(done)
    newest = git(root, "log", "-1", "--format=%H", "--", *present)
    if newest.returncode != 0 or not newest.stdout.strip():
        return None, last_line(newest)
    return newest.stdout.strip(), ""


def current_branch(root):
    """(branch, why): the branch the library is on, or None and why when it is on none."""
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.returncode != 0 or branch.stdout.strip() == "HEAD":
        return None, "the library is on no branch" if branch.returncode == 0 else last_line(branch)
    return branch.stdout.strip(), ""


def caught_up(root, target=None):
    """(caught up, why): the current branch (or HEAD onto origin's `target` branch, from a worktree on any branch)
    rebased onto origin's, so a push after other writers' commits (take-in, another handover's checkout) is a
    fast-forward. A step stopping only on generated index files (index/catalogue.json and the kind indexes, which both
    sides rewrite) takes the index rebuilt from its merged entries and goes on (2026-11-22: 8 of 19 take-in saves
    stayed local on a catalogue conflict); a rebase that stops on anything else is aborted, the branch left as it was.
    A branch origin does not hold yet has nothing to rebase onto, so its first push goes as it is."""
    branch, why = (target, "") if target else current_branch(root)
    if branch is None:
        return False, why
    remote = git(root, "ls-remote", "--exit-code", "--heads", "origin", branch)
    if remote.returncode == 2:
        return True, ""
    done = git(root, "pull", "-q", "--rebase", "origin", branch)
    while done.returncode != 0 and only_index_conflicts(root):
        index.rebuild(root)
        git(root, "add", "--", *index.built(root))
        done = git(root, "-c", "core.editor=true", "rebase", "--continue")
    if done.returncode != 0:
        git(root, "rebase", "--abort")
        return False, last_line(done)
    return True, ""


def only_index_conflicts(root):
    """True when a stopped rebase's conflicted paths are all generated index files (locks.json is no generated file)."""
    done = git(root, "diff", "--name-only", "--diff-filter=U")
    conflicted = set(done.stdout.split())
    return done.returncode == 0 and bool(conflicted) and conflicted <= set(index.built(root))


def pushed(root, target=None):
    """(pushed, why): rebase the current branch onto origin's, then push it to origin (to origin's `target` branch,
    HEAD:<target>, when one is given); a push refused because origin moved in between caught up and tried again, up to
    PUSH_TRIES."""
    if git(root, "remote", "get-url", "origin").returncode != 0:
        return False, "the library has no remote named origin"
    why = ""
    for _ in range(PUSH_TRIES):
        rebased, why = caught_up(root, target)
        if not rebased:
            return False, why
        done = git(root, "push", "-q", "origin", f"HEAD:{target}" if target else "HEAD")
        if done.returncode == 0:
            return True, ""
        why = last_line(done)
    return False, why


def saved(root, message, paths=SAVED_PATHS, target=None):
    """The report's saved block: the library commit holding `paths` and whether it reached origin (origin's `target`
    branch when one is given)."""
    if not is_repository(root):
        return {"committed": None, "pushed": False, "why": "the library root is not a git repository"}
    commit, why = committed(root, message, paths)
    if commit is None:
        return {"committed": None, "pushed": False, "why": why}
    went, why = pushed(root, target)
    if went:
        commit, why = committed(root, message, paths)
    return {"committed": commit, "pushed": went, "why": why}


def write_report(out, report):
    """Write `<out>/handover.json` (sorted keys, two-space indent); its path."""
    path = pathlib.Path(out) / "handover.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    return path


def checked_paths(store, repo):
    """Refuse a store or repository that is not there, in words."""
    if not (pathlib.Path(store) / "accepts").is_dir():
        raise HandoverError(f"no SCORE store at {store}: --store names a store's objects folder (it holds accepts/)")
    if git(repo, "rev-parse", "--git-dir").returncode != 0:
        raise HandoverError(f"no score repository at {repo}: --repo names the score repository")


def export_place(root, blobs, world, place, seed, commit, store, repo, route=None):
    """Record the place in the world toml and run its export at the commit; the export's report."""
    record_world(root, world, place, seed, commit)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-handover-"))
    try:
        return exported(context_at(root, world, commit, store, repo, scratch, route), place, blobs)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def report_of(root, blobs, world, place, commit, store, export_report):
    """The handover report from an export's report, before the library is saved."""
    found = statuses(export_report)
    rows = [entry_row(root, blobs, record, found[record["address"]])
            for record in planned_records(root, export_report, place)]
    refused = refused_rows(export_report, place)
    holders = held.Holders(held.newest_of_world(root, world))
    return {"schema": SCHEMA, "version": VERSION, "backend": os.environ.get("SCORE_LIB_BACKEND", "command"),
            "library": identity(root), "world": world, "place": place, "commit": commit,
            "covered": covered_rows(Store(store), holders, place, refused), "entries": rows, "refused": refused}


def handover(root, blobs, world, place, seed, commit, store, repo, out, command, route=None):
    """Hand a place (None: the world's own node) to the library under the lock and write the report; the report.
    `route` (score's hash of its route stages' code and gates) goes into each made entry's provenance."""
    checked_paths(store, repo)
    root = pathlib.Path(root)
    with locked(root, float(os.environ.get("SCORE_LIB_LOCK_WAIT", LOCK_WAIT))):
        if is_repository(root) and git(root, "remote", "get-url", "origin").returncode == 0:
            caught_up(root)
        export_report = export_place(root, blobs, world, place, seed, commit, store, repo, route)
        result = report_of(root, blobs, world, place, commit, store, export_report)
        result["command"] = list(command)
        result["saved"] = saved(root, f"handover: {world}@{place or 'world'} at score {commit[:12]}")
        write_report(out, result)
    return result
