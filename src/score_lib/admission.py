"""The admission rule: the library and every entry are MIT (LICENSE), except parts from a source whose row names
another licence (owner, 2026-11-22, score #155). An entry is let in only when every source of its content may go out
under MIT, or, for the bytes of a source whose row names a licence, under that licence (`config/sources.toml`). An
entry keeps a short provenance of what made it (provenance.made_with); it names a licence of its own (a top-level
`licence` block {id, source, credit, url}) exactly when it carries such a source's bytes."""
import pathlib
import tomllib

DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "config/sources.toml"
SCORE = {"what": "made by score", "source": "score"}


class NotAdmitted(Exception):
    """A part of an entry may not go out under MIT; the entry is refused."""

    def __init__(self, what, reason):
        super().__init__(f"{what}: {reason}")
        self.what = what
        self.reason = reason


def made_by(what, source):
    """A part of an entry that a source made (its output)."""
    return {"what": what, "source": source}


def carried(what, source):
    """A part of an entry that is the source's own bytes, copied in."""
    return {"what": what, "source": source, "carried": True}


class Sources:
    def __init__(self, rows):
        self.rows = rows
        self.by_id = {row["id"]: row for row in rows}
        self.by_text = {text: row for row in rows for text in row.get("match", [])}

    @classmethod
    def load(cls, path=DEFAULT):
        with open(path, "rb") as source:
            return cls(tomllib.load(source).get("source", []))

    def lookup(self, name):
        """The row a source id or a credit text names, or None."""
        if not name:
            return None
        return self.by_id.get(name) or self.by_text.get(name)

    def fault(self, source, is_carried):
        """Why a part from this source may not go out under MIT, or None when it may."""
        row = self.lookup(source)
        if row is None:
            return f"source not in sources.toml: {source!r}"
        if is_carried and not row["copy"]:
            return f"carries {row['name']}'s own bytes ({row['why']})"
        if not is_carried and not row["output"]:
            return f"made by {row['name']} ({row['why']})"
        return None

    def made_with(self, parts):
        """provenance.made_with of an entry's parts: [{what, by, carried when copied in}], sorted. NotAdmitted at the
        first part that may not go out under MIT."""
        if not parts:
            raise NotAdmitted("the entry", "names no source of its content")
        found = []
        for part in parts:
            fault = self.fault(part["source"], part.get("carried", False))
            if fault:
                raise NotAdmitted(part["what"], fault)
            item = {"what": part["what"], "by": self.lookup(part["source"])["id"]}
            if part.get("carried"):
                item["carried"] = True
            found.append(item)
        return sorted(found, key=lambda item: (item["by"], item["what"]))

    def licensed(self, made_with):
        """The rows of the sources whose own bytes an entry carries and whose row names a licence other than MIT."""
        found = {}
        for item in made_with or []:
            row = self.lookup(item.get("by")) if item.get("carried") else None
            if row and row.get("licence"):
                found[row["id"]] = row
        return [found[name] for name in sorted(found)]

    def licence_faults(self, made_with, licence):
        """Faults of an entry's top-level `licence` block against the sources it carries: the block is there exactly
        when the entry carries the bytes of one source whose row names a licence, and it names that row and that
        licence. An entry with no block and no such source is MIT."""
        rows = self.licensed(made_with)
        if len(rows) > 1:
            return [f"carries the bytes of {len(rows)} sources under licences of their own "
                    f"({', '.join(row['id'] for row in rows)}); an entry names one"]
        if not rows:
            return [] if licence is None else ["names a licence of its own, and carries no bytes of a source whose row "
                                               "names one; the library is MIT as a whole"]
        row = rows[0]
        if not isinstance(licence, dict):
            return [f"carries {row['name']}'s own bytes ({row['licence']}) and names no licence block with its credit"]
        faults = []
        if licence.get("source") != row["id"]:
            faults.append(f"its licence block names source {licence.get('source')!r}, but its bytes are {row['id']}'s")
        if licence.get("id") != row["licence"]:
            faults.append(f"its licence block names {licence.get('id')!r}, but {row['id']}'s row names "
                          f"{row['licence']}")
        return faults

    def check(self, made_with):
        """Faults of a stored provenance.made_with against the rows of today (verify)."""
        if not made_with:
            return ["provenance.made_with names no source"]
        faults = []
        for item in made_with:
            fault = self.fault(item.get("by"), item.get("carried", False))
            if fault:
                faults.append(f"{item.get('what')}: {fault}")
        return faults
