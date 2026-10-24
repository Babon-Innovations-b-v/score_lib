"""`score-lib concept`: a place's locked concept as a `concept` entry, so no work folder holds the only copy (score
#40, the owner's rule of 2026-10-20). score's concept review keeps a place's draws and picks in the SCORE store:

    concepts/<place>/draws/<time>.json   {place, sha, at, by, euros, base, marks, note, wording, ...}
    concepts/<place>/picks/<time>.json   {place, sha (None: unlocked again), by, at}
    blobs/<sha>                          each drawn picture

The entry follows the place's standing pick: a pick makes the picked draw `concept/<place>` (new, or the next
version when the newest holds another pick) and locks it, the owner's pick being his lock, and withdraws any older
version still standing, so a place never has two locked concepts (owner, 2026-10-24: "this got duped in the lib");
an unlock withdraws the newest version. Files: `concept.png` (the picture) and `concept.json` (the place, the pick, the prompt, the marks and
notes of every draw that led to it, and every draw of the place by its sha: the draw history, kept outside the
library by sha). Under the handover's lock; commits and pushes this entry's folder and the index only, so a
library checkout with other work not committed keeps that work out of the commit.
"""
import json
import os
import pathlib
import shutil
import tempfile
import types

from score_lib import entry as entries
from score_lib import handover, index, locks
from score_lib.admission import Sources, made_by
from score_lib.export import run

KIND = "concept"
AREA = "concepts"
DRAW_FIELDS = ("sha", "at", "by", "euros", "base", "marks", "note", "wording")


def records(objects, place, kind):
    """The store's records of one place's `kind` (draws or picks), oldest first."""
    folder = pathlib.Path(objects) / AREA / place / kind
    return [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]


def standing_pick(objects, place):
    """The place's newest pick, or None when it was never picked (an unlock is a pick of sha None)."""
    found = records(objects, place, "picks")
    return found[-1] if found else None


def lineage(draws, sha):
    """The draws that led to `sha`, the first draw first: each redraw's base followed back."""
    by_sha = {draw["sha"]: draw for draw in draws}
    chain = []
    while sha and sha in by_sha and sha not in {each["sha"] for each in chain}:
        chain.insert(0, by_sha[sha])
        sha = by_sha[sha].get("base")
    return chain


def concept_record(place, pick, draws):
    """concept.json's content: the place, its pick, the draws that led to it and the place's draw history."""
    return {"place": place, "picked": {key: pick[key] for key in ("sha", "at", "by")},
            "led_to_it": [{key: draw.get(key) for key in DRAW_FIELDS} for draw in lineage(draws, pick["sha"])],
            "history": [{"sha": draw["sha"], "at": draw["at"]} for draw in draws if draw["at"] <= pick["at"]]}


def candidate(objects, place, pick, scratch):
    """The entry the picked draw makes; ValueError when the picture is not in the store."""
    picture = pathlib.Path(objects) / "blobs" / pick["sha"]
    if not picture.is_file():
        raise ValueError(f"{place}: the picked draw {pick['sha'][:12]} is not in the store's blobs")
    written = run.write_json(pathlib.Path(scratch) / place / "concept.json",
                             concept_record(place, pick, records(objects, place, "draws")))
    return run.Candidate(
        kind=KIND, base_id=place, identity=place, title=f"{place.replace('_', ' ')} concept", places=[place],
        files={"concept.png": picture, "concept.json": written},
        roles={"concept.png": "picture", "concept.json": "record"}, known={"concept.png": pick["sha"]},
        made_with=[made_by("concept.png: a Nano Banana Pro picture", "nano_banana_pro"),
                   made_by("concept.json: the concept review's record", "score")],
        provenance={"source": "score's concept review", "place": place, "picked": pick["at"], "by": pick["by"]},
        tags={"place": [place]}, picture={"file": "concept.png", "made_by": "Nano Banana Pro", "of": place},
        entry_id=place)


def withdrawn(root, place):
    """Withdraw the place's newest concept when it is a draft or locked; its address, or None."""
    found = entries.versions(root, KIND, place)
    if not found or found[-1]["state"] not in ("draft", "locked"):
        return None
    locks.withdraw(root, found[-1]["address"])
    return found[-1]["address"]


def older_withdrawn(root, place, address):
    """Withdraw every version of the place's concept but `address` that is still locked or a draft; their addresses."""
    older = [record["address"] for record in entries.versions(root, KIND, place)
             if record["address"] != address and record["state"] in ("draft", "locked")]
    for each in older:
        locks.withdraw(root, each)
    return older


def picked(root, blobs, objects, world, place, pick, commit):
    """Make the picked draw the place's concept entry and lock it; (address, status)."""
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-concept-"))
    try:
        context = types.SimpleNamespace(root=pathlib.Path(root), world={"name": world, "commit": commit},
                                        sources=Sources.load(), scratch=scratch, refused={})
        planned = run.resolve(context, candidate(objects, place, pick, scratch))
        if planned.status == "refused":
            raise ValueError(f"{place}: refused, {planned.reason}")
        run.write_planned(context, blobs, planned)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if planned.record["state"] == "draft":
        locks.lock(root, planned.address)
    return planned.address, planned.status


def hand_over(root, blobs, objects, world, place, commit):
    """Bring `concept/<place>` in line with the place's standing pick under the handover's lock; the report
    {place, did (locked, standing, withdrawn, nothing), address, saved}."""
    pick = standing_pick(objects, place)
    with handover.locked(root, float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))):
        if pick is None or pick["sha"] is None:
            address = withdrawn(root, place)
            did = "withdrawn" if address else "nothing"
        else:
            address, status = picked(root, blobs, objects, world, place, pick, commit)
            did = "standing" if status == "unchanged" else "locked"
            if did == "locked":
                older_withdrawn(root, place, address)
        index.rebuild(root)
        report = {"place": place, "did": did, "address": address}
        if did in ("withdrawn", "locked"):
            report["saved"] = handover.saved(root, f"concept: {did} {address}, the owner's concept review (score "
                                                   f"{commit[:12]})", (f"entries/{KIND}/{place}", "index"))
    return report
