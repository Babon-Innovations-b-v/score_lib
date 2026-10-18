"""A kit's quality score:

    pieces           the mean quality score of the model entries its pieces lay
    weak pieces      share of them under 50
    partition        where its record holds one: each group's coverage, gaps and overlap
"""
from score_lib.quality import files, parts

WEAK = (0.0, 0.1, 0.3)
COVERAGE = (1.0, 0.98, 0.9)
GAPS = (0.0, 0.02, 0.1)
OVERLAP = (0.0, 0.05, 0.2)


def run(record, context):
    kit = files.record_json(record, context.blobs, "kit.json") or {}
    held = {address: context.scores.get(address) for address in record["requires"] if address.startswith("model/")}
    found = [parts.held("pieces", held, weight=2.0, what="model entries")]
    known = [value for value in held.values() if value is not None]
    if known:
        weak = sum(value < 50 for value in known) / len(known)
        found.append(parts.ramped("weak pieces", round(weak, 3), WEAK, f"share of its {len(known)} model entries "
                                  f"under 50", unit="share"))
    found += partition_parts(kit.get("partition") or {})
    return found


def partition_parts(partition):
    groups = {name: group for name, group in partition.items() if name != "all" and isinstance(group, dict)}
    if not groups:
        return []
    worst = min(groups.items(), key=lambda item: item[1].get("coverage", 1.0))
    gaps = max(groups.items(), key=lambda item: item[1].get("gaps", 0.0))
    overlap = max(groups.items(), key=lambda item: item[1].get("overlap", 0.0))
    return [parts.ramped("coverage", worst[1].get("coverage", 1.0), COVERAGE, f"worst group's coverage of its "
                         f"surface ({worst[0]})", unit="share"),
            parts.ramped("gaps", gaps[1].get("gaps", 0.0), GAPS, f"worst group's gaps ({gaps[0]})", unit="share"),
            parts.ramped("overlap", overlap[1].get("overlap", 0.0), OVERLAP, f"worst group's overlap "
                         f"({overlap[0]})", weight=0.5, unit="share")]
