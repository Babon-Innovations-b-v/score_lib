"""`score-lib adopted glow [--apply] [--save]`: the adopted surfaces already in, their emission put in the recipes'
units (score #197).

A surface taken in before take_in_hub.glow_of carried its source's emissiveColor as given: its colour times its
intensity in nits (the mirror's LED strip 5000), which our look drew and score's look-alike check fitted as a recipe
glow of 5000, so a part finished in it was drawn blown out white. Each adopted surface's newest version whose
surface.json constants carry an emissiveColor gets, as a new take-in writes them, its `emission` (glow_of) in
surface.json and its look's emissiveColor in material.usda in the recipes' units; its recipe is left for score's
look-alike check to fit again from them (its glow is the emission's strongest channel).

The rewritten files go into the blob store and the entry's files rows take their sha256 and bytes: a draft in place, a
locked entry as its next version, a draft (adopted_recipes.next_version). An entry whose files would not change is
left as it is. Without `apply` nothing is written: the report names each surface and its emission before and after.
Each surface.json is read through a scratch folder emptied after; with `save` the entries and the index are committed
and pushed under the handover's lock (adopted_recipes.saved_batch's way).
"""
import concurrent.futures
import copy
import json
import os
import pathlib
import re
import shutil
import tempfile

from score_lib import entry as entries
from score_lib import handover, index
from score_lib.adopted import adopted_sources, is_adopted
from score_lib.adopted_recipes import LIBRARY_BRANCH, next_version
from score_lib.admission import Sources
from score_lib.blobs import file_sha256, put_all
from score_lib.take_in_hub import glow_of

READERS = 8  # surface.json files read side by side
WHY = "its emission in the recipes' units (score-lib adopted glow, score #197)"
EMISSIVE_LINE = re.compile(r"(color3f inputs:emissiveColor = )\(([^)]*)\)")


class Refused(ValueError):
    """A run that cannot be made as asked."""


def candidates(root, source_ids):
    """The newest version of every live adopted surface holding a surface.json and a material.usda."""
    found = []
    for record in entries.all_entries(root):
        if record["kind"] != "surface" or record["state"] not in ("draft", "locked"):
            continue
        newest = entries.versions(root, "surface", record["id"])[-1]
        if newest["address"] != record["address"] or not is_adopted(record, source_ids):
            continue
        if {"surface.json", "material.usda"} <= set(record["files"]):
            found.append(record)
    return found


def read_json(blobs, sha256, scratch):
    """A blob read as JSON through `scratch`, the copy removed."""
    path = pathlib.Path(tempfile.mkstemp(dir=scratch, suffix=".json")[1])
    try:
        blobs.get(sha256, path)
        return json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)


def glowing_look(text, emission):
    """A material.usda's text with its constant emissiveColor set to `emission`; None when it has none."""
    shown = "(" + ", ".join(f"{value:g}" for value in emission) + ")"
    made, count = EMISSIVE_LINE.subn(lambda found: found.group(1) + shown, text)
    return made if count else None


def planned(root, blobs, scratch, sources=None):
    """[{address, status, record, surface, emission}] of every adopted surface whose constants carry an emissiveColor
    that gives off light."""
    source_ids = adopted_sources(sources or Sources.load())
    records = candidates(root, source_ids)
    with concurrent.futures.ThreadPoolExecutor(READERS) as pool:
        surfaces = list(pool.map(lambda record: read_json(blobs, record["files"]["surface.json"]["sha256"], scratch),
                                 records))
    rows = []
    for record, surface in zip(records, surfaces):
        emissive = (surface.get("constants") or {}).get("emissiveColor")
        if not emissive or max(emissive) <= 0:
            continue
        rows.append({"address": record["address"], "record": record, "surface": surface, "emission": glow_of(emissive),
                     "status": "in place" if record["state"] == "draft" else "new version"})
    return rows


def new_files(blobs, row, scratch):
    """{file: path} of the row's rewritten surface.json and material.usda in `scratch`, those that change."""
    record, made = row["record"], {}
    surface = {**row["surface"], "emission": row["emission"]}
    if surface != row["surface"]:
        made["surface.json"] = scratch / f"{record['id']}.surface.json"
        made["surface.json"].write_text(entries.dumps(surface))
    old = scratch / f"{record['id']}.old.usda"
    blobs.get(record["files"]["material.usda"]["sha256"], old)
    try:
        text = old.read_text()
    finally:
        old.unlink(missing_ok=True)
    look = glowing_look(text, row["emission"])
    if look is not None and look != text:
        made["material.usda"] = scratch / f"{record['id']}.material.usda"
        made["material.usda"].write_text(look)
    return made


def rewritten(row, files):
    """The record a row writes: its changed files' sha256 and bytes and their files_from; the next version for a
    locked one."""
    record = next_version(row["record"]) if row["status"] == "new version" else copy.deepcopy(row["record"])
    for name, path in files.items():
        record["files"][name].update(sha256=file_sha256(path), bytes=path.stat().st_size)
        written = record["provenance"].setdefault("files_from", {}).get(name, "")
        if WHY not in written:
            record["provenance"]["files_from"][name] = f"{written}; {WHY}" if written else WHY
    return record


def saved(root, written, report):
    """The entries and the index committed and pushed under the handover's lock; HandoverError when the commit does
    not reach origin."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        index.rebuild(root)
        folders = sorted({"entries/{0}/{1}".format(*entries.parse_address(address)[:2]) for address in written})
        message = f"adopted glow: {len(written)} surfaces' emission in the recipes' units (score #197)"
        block = handover.saved(root, message, (*folders, "index"), LIBRARY_BRANCH)
        report["saved"].append(block)
        if not block["pushed"]:
            raise handover.HandoverError(f"adopted glow: the save {block['committed'] or '(no commit)'} did not "
                                         f"reach origin {LIBRARY_BRANCH}: {block['why']}")


def write_glows(root, blobs, apply=False, save=False, sources=None):
    """The report of the adopted surfaces' emission against the recipes' units: {planned: [{address, status, from,
    to}]}, with `apply` also {in place, new version, unchanged: [address], saved: [block]}, the index rebuilt."""
    if save and not apply:
        raise Refused("--save saves what --apply writes: give both")
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-adopted-glow-"))
    try:
        rows = planned(root, blobs, scratch, sources)
        report = {"planned": [{"address": row["address"], "status": row["status"],
                               "from": row["surface"]["constants"]["emissiveColor"], "to": row["emission"]}
                              for row in rows]}
        if not apply:
            return report
        report.update({"in place": [], "new version": [], "unchanged": [], "saved": []})
        pairs, records = [], []
        for row in rows:
            files = new_files(blobs, row, scratch)
            if not files:
                report["unchanged"].append(row["address"])
                continue
            pairs += [(file_sha256(path), path) for path in files.values()]
            records.append((row["status"], rewritten(row, files)))
        put_all(blobs, pairs)
        for status, record in records:
            entries.write(root, record)
            report[status].append(record["address"])
        written = [record["address"] for _, record in records]
        if save and written:
            saved(root, written, report)
        index.rebuild(root)
        return report
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def write_report(path, report):
    """The report as JSON at `path`."""
    pathlib.Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
