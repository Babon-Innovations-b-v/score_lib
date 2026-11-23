"""`score-lib adopted recipes --from FOLDER [--apply] [--save]`: score's look-alike check's results written into the
adopted surface entries they judged (score #155).

The folder is one run of score's check (tools/props/library/likeness/check.py): `summary.json` ({passed, failed,
unread}: addresses) and, per surface, `<id>@<version>/likeness.json` ({address, recipe: {recipe, settings,
wear_levels}, likeness: {measures, pass, algorithm}, rounds, ...}, or {address, error} for one it could not read).

Each result goes to the newest version of its surface's id, which must be an adopted set's (adopted.is_adopted) and
tile (no `painted_for` tag; a set painted for one model keeps its own gap). Its surface.json gets:

- a pass: `recipe` the fitted {recipe, settings, wear_levels}, `likeness` {measures, pass, algorithm}, `recipe_gap`
  null; the entry's made_with gains the recipe as our own work (RECIPE_WHAT, by score, never carried) and its
  requires the recipe algorithm that draws it;
- a fail: `recipe` null, `likeness` the measures, `recipe_gap` naming every measure outside its bound (BOUNDS);
- an unread one: `recipe` null, `likeness` null, `recipe_gap` naming why the check could not read it.

The new surface.json goes into the blob store and the entry's files row takes its sha256 and bytes: a draft in place,
a locked entry as its next version, a draft (the library rule). An entry whose surface.json would not change is left
as it is. Without `apply` nothing is read or written: the report says what each result would do. With `apply` the
entries are written BATCH at a time (their surface.json read through a scratch folder emptied after), the index
rebuilt; with `save` each batch is committed and pushed under the handover's lock (handover.saved: rebased, the index
rebuilt on a conflict), and a batch that does not reach origin stops the run.
"""
import copy
import json
import os
import pathlib
import shutil
import tempfile

from score_lib import entry as entries
from score_lib import handover, index
from score_lib.adopted import adopted_sources, is_adopted
from score_lib.admission import Sources
from score_lib.blobs import file_sha256, put_all

BATCH = 200  # surfaces written (and saved) together: one blob store commit and one library commit each
LIBRARY_BRANCH = "main"
RECIPE_WHAT = "its recipe, fitted to its maps by score's look-alike check"
RECIPE_ALGORITHM = "algorithm/surface_recipes@"
FILES_FROM = ("written by score-lib take-in hub (the material's settings as the source gives them); its recipe and "
              "likeness by score-lib adopted recipes (score's look-alike check)")
# The look-alike check's bounds, as score sets them (tools/props/library/likeness/measure.py, #155, 2026-11-22): the
# measures a failed result's gap names. A measure is outside when it is past its bound (highlight by its size).
BOUNDS = {"delta_e": (None, 10.0), "lightness": (None, 8.0), "highlight": (-12.0, 12.0), "detail": (0.8, 1.25)}


class Refused(ValueError):
    """A result that cannot be written into the library as given."""


def results_in(folder):
    """[(address, result or None)] of a check run's folder, in its summary's order (passed, failed, unread); None
    for an address whose likeness.json is not there."""
    folder = pathlib.Path(folder)
    summary = json.loads((folder / "summary.json").read_text())
    addresses = list(dict.fromkeys([*summary.get("passed", []), *summary.get("failed", []),
                                    *summary.get("unread", [])]))
    found = []
    for address in addresses:
        path = folder / address.split("/", 1)[1] / "likeness.json"
        found.append((address, json.loads(path.read_text()) if path.is_file() else None))
    return found


def outcome_of(result):
    """pass, fail or unread."""
    if "error" in result or not result.get("likeness"):
        return "unread"
    return "pass" if result["likeness"].get("pass") else "fail"


def outside_bounds(likeness):
    """Every measure of every view past its bound, in words ("close detail 0.495, bound 0.8 to 1.25")."""
    found = []
    for view, measured in sorted(likeness.get("measures", {}).items()):
        for name, (low, high) in BOUNDS.items():
            value = measured.get(name)
            if value is None:
                continue
            if (low is not None and value < low) or value > high:
                bound = f"at most {high}" if low is None else f"{low} to {high}"
                found.append(f"{view} {name} {value}, bound {bound}")
    return found


def gap_of(result):
    """The recipe_gap of a result that did not pass."""
    if outcome_of(result) == "unread":
        return f"score's look-alike check could not read it: {result.get('error', 'no likeness measured')}"
    likeness = result["likeness"]
    failed = outside_bounds(likeness) or ["no view measured"]
    return (f"its fitted recipe did not look like its maps in score's look-alike check ({likeness['algorithm']}, "
            f"{result.get('rounds', 0)} renders): {'; '.join(failed)}")


def surface_fields(result):
    """The fields a result sets in a surface.json: recipe, likeness and recipe_gap."""
    outcome = outcome_of(result)
    if outcome == "pass":
        recipe = result["recipe"]
        likeness = result["likeness"]
        return {"recipe": {key: recipe[key] for key in ("recipe", "settings", "wear_levels")},
                "likeness": {key: likeness[key] for key in ("measures", "pass", "algorithm")}, "recipe_gap": None}
    likeness = result.get("likeness")
    shown = {key: likeness[key] for key in ("measures", "pass", "algorithm")} if outcome == "fail" else None
    return {"recipe": None, "likeness": shown, "recipe_gap": gap_of(result)}


def target_of(root, address, source_ids):
    """(status, newest record) a result is written to: `in place` for a draft, `new version` for a locked entry;
    Refused naming why it cannot be."""
    kind, entry_id, _ = entries.parse_address(address)
    if kind != "surface":
        raise Refused(f"{address}: the check judges surfaces only")
    found = entries.versions(root, kind, entry_id)
    if not found:
        raise Refused(f"{address}: the library holds no surface {entry_id}")
    newest = found[-1]
    if not is_adopted(newest, source_ids):
        raise Refused(f"{newest['address']}: no adopted set's surface")
    if newest["state"] not in ("draft", "locked"):
        raise Refused(f"{newest['address']}: is {newest['state']}")
    if newest.get("tags", {}).get("painted_for"):
        raise Refused(f"{newest['address']}: painted for one model, its gap is its own")
    if "surface.json" not in newest["files"]:
        raise Refused(f"{newest['address']}: holds no surface.json")
    return ("in place" if newest["state"] == "draft" else "new version"), newest


def planned(root, folder, source_ids):
    """(rows [{address, outcome, status, record, result}], refused [{address, why}]) of a check run's folder."""
    rows, refused = [], []
    for address, result in results_in(folder):
        if result is None:
            refused.append({"address": address, "why": "its likeness.json is not in the folder"})
            continue
        try:
            status, record = target_of(root, address, source_ids)
        except Refused as why:
            refused.append({"address": address, "why": str(why)})
            continue
        rows.append({"address": address, "outcome": outcome_of(result), "status": status, "record": record,
                     "result": result})
    return rows, refused


def made_with_of(made_with, passed):
    """made_with with the recipe row (our own work) when the recipe passed, without it when not."""
    rows = [row for row in made_with if not (row.get("by") == "score" and row.get("what") == RECIPE_WHAT)]
    if passed:
        rows.append({"what": RECIPE_WHAT, "by": "score"})
    return sorted(rows, key=lambda row: (row["by"], row["what"]))


def requires_of(requires, algorithm):
    """requires without any recipe algorithm, with the one that draws a passing recipe (`algorithm`, or None)."""
    kept = [address for address in requires if not address.startswith(RECIPE_ALGORITHM)]
    return sorted(kept + ([algorithm] if algorithm else []))


def next_version(record):
    """A locked record's next version as a draft (its lock, replacement and quality fields left behind)."""
    kind, entry_id, version = entries.parse_address(record["address"])
    made = {key: value for key, value in copy.deepcopy(record).items() if key not in entries.UNSTAMPED}
    made.update(version=version + 1, address=entries.address_of(kind, entry_id, version + 1), state="draft")
    if made.get("usd") and made["usd"].get("path"):
        made["usd"]["path"] = entries.library_path(kind, entry_id, version + 1, made["usd"]["root"])
    return made


def rewritten(row, sha256, size):
    """The record a row writes: its surface.json's new sha256 and bytes, its made_with and requires; the next version
    for a locked one."""
    passed = row["outcome"] == "pass"
    record = next_version(row["record"]) if row["status"] == "new version" else copy.deepcopy(row["record"])
    record["files"]["surface.json"].update(sha256=sha256, bytes=size)
    record["provenance"]["made_with"] = made_with_of(record["provenance"].get("made_with", []), passed)
    record["provenance"].setdefault("files_from", {})["surface.json"] = FILES_FROM
    record["requires"] = requires_of(record.get("requires", []),
                                     row["result"]["likeness"]["algorithm"] if passed else None)
    return record


def new_surface(blobs, row, scratch):
    """The row's new surface.json written into `scratch` (its old one read from the blob store and removed); its
    path, or None when it would not change."""
    record = row["record"]
    old = scratch / f"{record['id']}.old.json"
    blobs.get(record["files"]["surface.json"]["sha256"], old)
    try:
        surface = json.loads(old.read_text())
    finally:
        old.unlink(missing_ok=True)
    made = {**surface, **surface_fields(row["result"])}
    if made == surface:
        return None
    path = scratch / f"{record['id']}.json"
    path.write_text(entries.dumps(made))
    return path


def applied_batch(root, blobs, rows, report):
    """One batch written: every changed surface.json into the blob store together, then the records naming them;
    the addresses written."""
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-adopted-recipes-"))
    try:
        pairs, records = [], []
        for row in rows:
            path = new_surface(blobs, row, scratch)
            if path is None:
                report["unchanged"].append(row["record"]["address"])
                continue
            sha256 = file_sha256(path)
            pairs.append((sha256, path))
            records.append((row["status"], rewritten(row, sha256, path.stat().st_size)))
        put_all(blobs, pairs)
        for status, record in records:
            entries.write(root, record)
            report[status].append(record["address"])
        return [record["address"] for _, record in records]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def saved_batch(root, written, report):
    """One batch's entries and the index committed and pushed under the handover's lock; HandoverError when the
    commit does not reach origin."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        index.rebuild(root)
        folders = sorted({"entries/{0}/{1}".format(*entries.parse_address(address)[:2]) for address in written})
        message = f"adopted recipes: {len(written)} surfaces' look-alike results written (score #155)"
        block = handover.saved(root, message, (*folders, "index"), LIBRARY_BRANCH)
        report["saved"].append(block)
        if not block["pushed"]:
            raise handover.HandoverError(f"adopted recipes: the save {block['committed'] or '(no commit)'} did not "
                                         f"reach origin {LIBRARY_BRANCH}: {block['why']}; the run stops")


def write_recipes(root, blobs, folder, apply=False, save=False, sources=None, batch=BATCH):
    """The report of a check run's results against the library: {outcomes: {pass, fail, unread}, planned: [{address,
    outcome, status}], refused: [{address, why}]}, with `apply` also {in place, new version, unchanged: [address],
    saved: [block]}, the index rebuilt."""
    if save and not apply:
        raise Refused("--save saves what --apply writes: give both")
    sources = sources or Sources.load()
    rows, refused = planned(root, folder, adopted_sources(sources))
    report = {"outcomes": {outcome: sum(row["outcome"] == outcome for row in rows)
                           for outcome in ("pass", "fail", "unread")},
              "planned": [{key: row[key] for key in ("address", "outcome", "status")} for row in rows],
              "refused": refused}
    if not apply:
        return report
    report.update({"in place": [], "new version": [], "unchanged": [], "saved": []})
    for start in range(0, len(rows), batch):
        written = applied_batch(root, blobs, rows[start:start + batch], report)
        if save and written:
            saved_batch(root, written, report)
    index.rebuild(root)
    return report


def write_report(path, report):
    """The report as JSON at `path`."""
    pathlib.Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
