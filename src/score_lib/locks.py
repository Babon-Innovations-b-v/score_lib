"""index/locks.json: the append-only list of locked entries, {address, digest, date}; locking and withdrawing."""
import datetime
import json
import pathlib

from score_lib import entry as entries
from score_lib import index


class LockRefused(Exception):
    """An entry cannot be locked or withdrawn as asked."""


def path_of(root):
    return pathlib.Path(root) / "index/locks.json"


def read(root):
    """Every lock row, oldest first."""
    path = path_of(root)
    return json.loads(path.read_text()) if path.is_file() else []


def append(root, row):
    """Add one row to the end of locks.json."""
    rows = read(root)
    rows.append(row)
    path_of(root).parent.mkdir(parents=True, exist_ok=True)
    path_of(root).write_text(entries.dumps(rows))


def today():
    return datetime.date.today().isoformat()


def lock(root, address, date=None):
    """Lock one draft: state locked, the date, and a locks row with its content digest."""
    record = entries.load_address(root, address)
    if record is None:
        raise LockRefused(f"{address}: no such entry")
    if record["state"] != "draft":
        raise LockRefused(f"{address}: is {record['state']}, only a draft can be locked")
    for required in record.get("requires", []):
        needed = entries.load_address(root, required)
        if needed is None or needed["state"] == "draft":
            raise LockRefused(f"{address}: requires {required}, which is {'missing' if needed is None else 'a draft'}")
    record["state"] = "locked"
    record["locked"] = date or today()
    entries.write(root, record)
    append(root, {"address": address, "digest": entries.digest(record), "date": record["locked"]})
    return record


def drafts(root, kind):
    """Addresses of every draft of one kind."""
    return [record["address"] for record in entries.all_entries(root, kind) if record["state"] == "draft"]


def withdraw(root, address, date=None):
    """A locked entry or a draft becomes withdrawn: kept with its files, hidden from the catalogue. A draft is frozen
    as it stood, its locks row written now (a retired kind's drafts, which nothing will make again)."""
    record = entries.load_address(root, address)
    if record is None or record["state"] not in ("locked", "draft"):
        raise LockRefused(f"{address}: only a locked entry or a draft can be withdrawn")
    if record["state"] == "draft":
        record["locked"] = date or today()
        append(root, {"address": address, "digest": entries.digest(record), "date": record["locked"]})
    record["state"] = "withdrawn"
    entries.write(root, record)
    return record


def lock_many(root, addresses, date=None):
    """Lock several drafts in an order that locks what each requires first; the index rebuilt."""
    pending = list(addresses)
    while pending:
        progressed = False
        for address in list(pending):
            record = entries.load_address(root, address)
            waiting = [each for each in (record or {}).get("requires", []) if each in pending and each != address]
            if waiting:
                continue
            lock(root, address, date)
            pending.remove(address)
            progressed = True
        if not progressed:
            raise LockRefused(f"requires form a cycle among: {', '.join(sorted(pending))}")
    index.rebuild(root)
