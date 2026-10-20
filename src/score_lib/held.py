"""What a world and its places hold in the library: the newest version of each entry made from a world, the entries a
place holds, and the store outputs each entry carries (its provenance nodes and the bytes it holds). The handover's
report and the cockpit's invariant check both read these (`score-lib held`)."""
from score_lib import entry as entries

WORLD_KINDS = ("algorithm",)  # the kinds the world's own node hands over


def newest_of_world(root, world):
    """The newest version of every entry made from a world, sorted by address."""
    newest = {}
    for record in entries.all_entries(root):
        if record.get("provenance", {}).get("world") == world:
            newest[(record["kind"], record["id"])] = record
    return sorted(newest.values(), key=lambda record: record["address"])


def holds(record, place):
    """True when a place holds an entry: it is seen there or was made there. A place of None means the world's own
    node, which holds the world-grain kinds."""
    if place is None:
        return record["kind"] in WORLD_KINDS
    return place in record.get("seen_in", []) or record.get("provenance", {}).get("place") == place


def held(root, world, place):
    """The newest version of every entry of a world that a place (None: the world's own node) holds."""
    return [record for record in newest_of_world(root, world) if holds(record, place)]


def node_outputs(provenance):
    """Every (node, output) pair named anywhere in a provenance block (an entry may name several nodes: a place its
    assembly and its complete row, a layer its side records)."""
    found = set()
    if isinstance(provenance, dict):
        if isinstance(provenance.get("node"), str) and isinstance(provenance.get("output"), str):
            found.add((provenance["node"], provenance["output"]))
        for value in provenance.values():
            found |= node_outputs(value)
    elif isinstance(provenance, list):
        for value in provenance:
            found |= node_outputs(value)
    return found


def carried_bytes(record):
    """The sha256 of every byte string an entry holds: its files, the store files they were rewritten from and its
    aliases."""
    provenance = record.get("provenance", {})
    found = {described["sha256"] for described in record["files"].values()}
    found |= {source["sha256"] for source in provenance.get("files_from", {}).values() if isinstance(source, dict)}
    found |= {sha for sha, _ in provenance.get("aliases", [])}
    return found


class Holders:
    """Which entries of a world hold a store output: by the provenance naming its node and output, or by its bytes.
    Withdrawn entries hold nothing."""

    def __init__(self, records):
        self.by_output = {}
        self.by_bytes = {}
        for record in records:
            if record["state"] == "withdrawn":
                continue
            for pair in node_outputs(record.get("provenance", {})):
                self.by_output.setdefault(pair, set()).add(record["address"])
            for sha in carried_bytes(record):
                self.by_bytes.setdefault(sha, set()).add(record["address"])

    def of(self, node, output, listing):
        """The sorted addresses holding one accepted output (its node, its hash and its {path: sha256} listing)."""
        found = set(self.by_output.get((node, output), set()))
        for sha in listing.values():
            found |= self.by_bytes.get(sha, set())
        return sorted(found)
