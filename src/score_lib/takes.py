"""A sound's taken-in takes (score #46): one sound is one entry, its generated, recorded and made takes in it. A take
taken in from loose made files (score-lib take-in) is no store output, so the export cannot make it again: an entry
written again keeps the takes its previous version held that way (a file whose `files_from` names only the path it
was taken from), with the made_with rows that name them."""


def taken_in(record):
    """{file name: the path it was taken in from} of an entry's taken-in files."""
    return {name: source for name, source in record.get("provenance", {}).get("files_from", {}).items()
            if isinstance(source, str) and name in record.get("files", {})}


def carried(record, previous):
    """The record with the previous version's taken-in takes it lacks carried over; else the record as it is."""
    if not previous or record["kind"] != "sound":
        return record
    takes = {name: source for name, source in taken_in(previous).items() if name not in record["files"]}
    if not takes:
        return record
    provenance = dict(record["provenance"])
    provenance["files_from"] = {**provenance.get("files_from", {}), **takes}
    rows = list(provenance.get("made_with", []))
    rows += [row for row in previous["provenance"].get("made_with", [])
             if row not in rows and any(row.get("what", "").startswith(source) for source in takes.values())]
    provenance["made_with"] = rows
    files = {**record["files"], **{name: previous["files"][name] for name in takes}}
    return {**record, "files": files, "provenance": provenance}
