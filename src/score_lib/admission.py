"""The admission rule: the library and every entry are MIT (LICENSE), so an entry is let in only when every source of
its content may be released under MIT (`config/sources.toml`). An entry keeps a short provenance of what made it
(provenance.made_with), never a licence of its own."""
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
