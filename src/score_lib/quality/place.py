"""A place's quality score:

    completion  its completion gate: complete 100, complete apart from named gaps 60 less 5 a named gap (at least
                50: the gate's line is crossed only by another fault), NOT complete by the share of its
                requirements that pass, under 50
    plan        the plan stage's gates on its accepted plan
    layers      the mean quality score of its layers
"""
from score_lib.quality import files, parts

APART_FROM_GAPS = 60
A_GAP = 5


def run(record, context):
    provenance = record["provenance"]
    found = [completion_part(record, provenance, context)]
    plan = (provenance.get("records_from") or {}).get("plan") or {}
    if plan.get("output"):
        found.append(parts.gates("plan", context.verdicts_by_output.get(plan["output"], (None, {}))[1],
                                 plan.get("node", "its plan")))
    held = {address: context.scores.get(address) for address in record["requires"] if address.startswith("layer/")}
    found.append(parts.held("layers", held, weight=2.0, what="layers"))
    return found


def completion_part(record, provenance, context):
    complete = provenance.get("complete") or {}
    verdicts = context.verdicts_by_output.get(complete.get("output"), (None, {}))[1]
    failing = provenance.get("complete_failing")
    if failing is None and not verdicts:
        return parts.unmeasured("completion", "no completion gate judged in the store")
    failing = failing if failing is not None else [gate for gate, verdict in verdicts.items()
                                                   if verdict.get("answer") != "pass"]
    gaps = files.record_json(record, context.blobs, "gaps.json") or {}
    named = [gap.get("name", "") for gap in gaps.get("gaps", [])] if isinstance(gaps, dict) else []
    if not failing:
        return parts.scored("completion", 100, "complete", "its completion gate passes and it has no named gap",
                            weight=3.0)
    if failing == ["named gaps"]:
        value = max(50, APART_FROM_GAPS - A_GAP * len(named))
        return parts.scored("completion", value, f"complete apart from {len(named)} named gaps",
                            f"its named gaps are its completion gate's only fail ({'; '.join(n[:80] for n in named)}); "
                            f"{APART_FROM_GAPS} less {A_GAP} a named gap, at least 50", weight=3.0)
    passing = (len(verdicts) - len(failing)) / len(verdicts) if verdicts else 0.0
    return parts.scored("completion", round(49 * passing), f"NOT complete: {', '.join(failing)}",
                        f"its completion gate fails on {', '.join(failing)}; 49 times the share of its "
                        f"requirements that pass", weight=3.0)
