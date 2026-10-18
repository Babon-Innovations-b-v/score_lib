"""place entries: one place's root with its sublayers pointing at layer entries, the empty creator slot (edit.usda)
as its own file, the place's JSON files, its named gaps and its completion state (spec 4.8)."""
import hashlib
import json
import posixpath

from score_lib import entry as entries
from score_lib.export import places as place_reads
from score_lib.export.run import Candidate
from score_lib.licences import SCORE_COMPONENT
from score_lib.rewrite import asset_paths, export, joined, open_copy, rewrite

JSON_FILES = ("scene.json", "origin.json", "inputs.json", "contract.json")
EDIT = "layers/edit.usda"


def plan(context):
    """One candidate per place with an accepted assembly."""
    return [candidate(context, place) for place in context.places if context.assembly(place)[0]]


def completion(context, place):
    """`pass` or `named_gaps` (or `fail`) from the latest judgement of the accepted complete@<place> key, with the
    faults it names; (None, []) when the place has no accepted completion."""
    node = f"complete@{place}"
    accept = context.store.current_accept(node)
    if not accept:
        return None, []
    judged = context.store.judgements(node, accept["key"])
    if not judged:
        return None, []
    verdicts = judged[-1].get("verdicts", {})
    failing = sorted(name for name, verdict in verdicts.items() if verdict.get("answer") != "pass")
    if not failing:
        return "pass", []
    return ("named_gaps" if failing == ["named gaps"] else "fail"), failing


def root_layer(context, place, output, work):
    """The place's root with each sublayer pointed at its layer entry and the edit slot at ./edit.usda."""
    root = f"{place}.usda"
    layer = open_copy(context.tree(output) / root)
    mapping, requires, missing = {}, [], []
    for path in asset_paths(layer):
        name = joined("", path)
        if name == EDIT:
            mapping[path] = "./edit.usda"
            continue
        sublayer = posixpath.splitext(posixpath.basename(name))[0]
        address = context.by_id.get(("layer", f"{sublayer}.{place_reads.sanitised(place)}"))
        if address:
            kind, entry_id, version = entries.parse_address(address)
            mapping[path] = entries.library_path(kind, entry_id, version, "layer.usda")
            requires.append(address)
        else:
            missing.append(path)
    rewrite(layer, mapping)
    return export(layer, work / root), requires, missing


def candidate(context, place):
    """One place candidate."""
    output, listing = context.assembly(place)
    work = context.work("place", place)
    root, requires, missing = root_layer(context, place, output, work)
    files = {root.name: root}
    roles = {root.name: "usd"}
    known = {}
    tree = context.tree(output)
    if EDIT in listing:
        files["edit.usda"] = tree / EDIT
        known["edit.usda"] = listing[EDIT]
        roles["edit.usda"] = "usd"
    for name in JSON_FILES:
        if name in listing:
            files[name] = tree / name
            known[name] = listing[name]
            roles[name] = "record"
    gaps = f"data/checks/gaps/{place}.json"
    if context.repo.exists(gaps):
        files["gaps.json"] = context.repo.export(gaps, work / "gaps.json")
        roles["gaps.json"] = "record"
    state, failing = completion(context, place)
    provenance = place_reads.assembly_provenance(context, place)
    provenance.update({"place": place, "complete": place_reads.node_provenance(context, f"complete@{place}"),
                       "complete_failing": failing, "unresolved_sublayers": missing,
                       "gaps": gaps if "gaps.json" in files else None})
    identity = hashlib.sha256(json.dumps([place, output]).encode()).hexdigest()
    made = Candidate(kind="place", base_id=place_reads.sanitised(place), identity=identity,
                     title=place.replace("_", " ").capitalize(), places=[place], files=files, roles=roles,
                     components=[dict(SCORE_COMPONENT)], usd={"root": root.name, "layer": "root", "use": "open"},
                     requires=sorted(set(requires)), provenance=provenance,
                     tags={"complete": [state] if state else [], "place_kind": place_reads.place_kind(context, place)},
                     picture={"reason": "a place is drawn only by a renderer, which v1 does not run"},
                     known=known)
    made.entry_id = place_reads.sanitised(place)
    return made
