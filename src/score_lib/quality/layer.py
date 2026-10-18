"""A layer's quality score:

    gates       the gates of the stage output it was written from (its side records' output, else its place's
                assembly), from the store's judgement of that output
    unresolved  paths in it that point at no library address
    entries     the mean quality score of the entries it points at (algorithms left out)
"""
from score_lib.quality import parts

UNRESOLVED = (0, 1, 5)


def run(record, context):
    provenance = record["provenance"]
    output = provenance.get("side_records_from")
    if output:
        node, verdicts = context.verdicts_by_output.get(output, (None, {}))
        where = node or f"output {output[:12]}"
    else:
        node = provenance.get("node")
        verdicts = context.verdicts_by_output.get(provenance.get("output"), (node, {}))[1]
        where = node or "its assembly"
    found = [parts.gates("gates", verdicts, where, weight=2.0)]
    unresolved = provenance.get("unresolved") or []
    found.append(parts.ramped("unresolved", len(unresolved), UNRESOLVED, f"paths pointing at no library address"
                              f"{': ' + ', '.join(map(str, unresolved[:5])) if unresolved else ''}", unit="paths"))
    held = {address: context.scores.get(address) for address in record["requires"]
            if not address.startswith("algorithm/")}
    if held:
        found.append(parts.held("entries", held, what="entries it points at"))
    return found
