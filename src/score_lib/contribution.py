"""An outside contribution's trace: who added an entry, from which pull request, on what claim.

An entry a contributor takes in names its contributor (`provenance.contributor`, the public GitHub handle) and keeps a
`contribution` block: the contributor's `handle` and `claim` (their own work, or the source and its terms), written by
`score-lib take-in`, and, once a maintainer takes the pull request's bundle in (`score-lib take-bundle`), the `pr`
number, the `signed_off` commit, the `date` and the `bundle_sha256`. Nothing but the public handle is kept about a
person. `score-lib verify` fails an outside entry whose block is not whole (`faults`); `score-lib trace` answers, for
an entry, a blob, a handle or a source, every entry it reaches (`traced`), so a takedown finds them all at once.
"""
import re

from score_lib import entry as entries

HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Written by the maintainer's take-bundle; the rest of the block by the contributor's take-in.
MERGED_FIELDS = ("pr", "signed_off", "date", "bundle_sha256")


def block_of(handle, claim):
    """The contributor's half of a contribution block, as take-in writes it."""
    if not HANDLE.match(handle or ""):
        raise ValueError(f"{handle!r}: not a GitHub handle")
    if not (claim or "").strip():
        raise ValueError("a contribution needs its claim of rights (own work, or the source and its terms)")
    return {"handle": handle, "claim": claim.strip()}


def is_outside(record):
    """Whether an entry came from an outside contributor."""
    return "contributor" in record.get("provenance", {})


def merged_faults(block):
    """What the maintainer's half of a contribution block lacks."""
    faults = []
    if not isinstance(block.get("pr"), int) or block["pr"] < 1:
        faults.append("no pull request number")
    if not SHA1.match(str(block.get("signed_off", ""))):
        faults.append("no signed-off commit")
    if not DATE.match(str(block.get("date", ""))):
        faults.append("no date")
    if not SHA256.match(str(block.get("bundle_sha256", ""))):
        faults.append("no bundle hash")
    return faults


def faults(record, pending=False):
    """Why an outside entry's trace is not whole (empty for an entry of ours); `pending` leaves out the maintainer's
    half, for a contribution not yet merged."""
    if not is_outside(record):
        return []
    block = record.get("contribution")
    if not isinstance(block, dict):
        return ["an outside entry without its contribution record"]
    found = []
    if block.get("handle") != record["provenance"]["contributor"]:
        found.append("its contribution's handle is not its contributor's")
    if not str(block.get("claim", "")).strip():
        found.append("no claim of rights")
    return found + ([] if pending else merged_faults(block))


def row(record):
    """One traced entry: its address, its contribution block and its sources."""
    return {"address": record["address"], "state": record["state"], "contribution": record.get("contribution"),
            "sources": sorted({part["by"] for part in record.get("provenance", {}).get("made_with", [])})}


def matches(record, query):
    """Whether an entry is reached by a query: an address, a blob's sha256, `handle:<h>`, `source:<id>` or
    `pr:<n>`."""
    block = record.get("contribution") or {}
    if query.startswith("handle:"):
        return block.get("handle", "").lower() == query[len("handle:"):].lower()
    if query.startswith("source:"):
        return query[len("source:"):] in row(record)["sources"]
    if query.startswith("pr:"):
        return str(block.get("pr")) == query[len("pr:"):]
    if SHA256.match(query):
        return any(about["sha256"] == query for about in record["files"].values())
    return record["address"] == query


def traced(root, query):
    """Every entry a query reaches, as rows, in address order."""
    return [row(record) for record in entries.all_entries(root) if matches(record, query)]
