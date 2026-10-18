"""Known licences (`config/licences.toml`) and an entry's licence block: its components and the commercial decision."""
import hashlib
import pathlib
import tomllib

DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "config/licences.toml"
SCORE_COMPONENT = {"what": "made by score", "licence": "MIT", "source": "score repository"}


class UnknownLicence(Exception):
    """A component names a licence that `licences.toml` does not hold; the entry is refused."""

    def __init__(self, what, text):
        super().__init__(f"{what}: licence not in licences.toml: {text!r}")
        self.what = what
        self.text = text


def text_sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Licences:
    def __init__(self, rows):
        self.rows = rows
        self.by_match = {row["match"]: row for row in rows}
        self.spdx_ids = {row["spdx"] for row in rows}

    @classmethod
    def load(cls, path=DEFAULT):
        with open(path, "rb") as source:
            return cls(tomllib.load(source).get("licence", []))

    def lookup(self, text):
        """The row for a licence text (by the sha256 of its exact text) or an SPDX id, or None."""
        if not text:
            return None
        return self.by_match.get(text_sha256(text)) or self.by_match.get(text)

    def component(self, what, text, source):
        """One licence component; UnknownLicence when the text is missing or not known."""
        row = self.lookup(text)
        if row is None:
            raise UnknownLicence(what, text)
        found = {"what": what, "spdx": row["spdx"], "commercial_ok": bool(row["commercial_ok"]), "source": source}
        if text != row["spdx"]:
            found["text"] = text
        return found

    def block(self, components, note=""):
        """An entry's licence: every component (each a dict of what, licence text, source), the combined SPDX
        expression, commercial_ok only when every component is, and the notes of their rows."""
        built = [self.component(each["what"], each["licence"], each["source"]) for each in components]
        notes = [note] if note else []
        for each in components:
            row_note = self.lookup(each["licence"])["note"]
            if row_note and row_note not in notes:
                notes.append(row_note)
        identifiers = sorted({each["spdx"] for each in built})
        return {"spdx": " AND ".join(identifiers), "commercial_ok": all(each["commercial_ok"] for each in built),
                "note": "; ".join(notes), "components": built}

    def check(self, licence):
        """Faults of an entry's licence block against the known rows (verify, section 8.5)."""
        faults = []
        if not licence.get("spdx"):
            faults.append("licence has no spdx")
        for each in licence.get("components", []):
            faults += self.component_faults(each)
        expected = all(self.row_commercial_ok(each) for each in licence.get("components", [])) \
            and bool(licence.get("components"))
        if licence.get("commercial_ok") != expected:
            faults.append(f"commercial_ok {licence.get('commercial_ok')} does not match its components")
        for identifier in (licence.get("spdx") or "").split(" AND "):
            if identifier and identifier not in self.spdx_ids:
                faults.append(f"spdx {identifier!r} not in licences.toml")
        return faults

    def component_row(self, component):
        """The licences.toml row a stored component came from: by its licence text, else by its SPDX id."""
        return self.lookup(component.get("text")) or self.lookup(component.get("spdx"))

    def row_commercial_ok(self, component):
        """Whether the row behind a stored component allows commercial use today (False with no row)."""
        row = self.component_row(component)
        return bool(row and row["commercial_ok"])

    def component_faults(self, component):
        """Faults of one stored component: its spdx unknown, or its commercial_ok no longer what its row says."""
        what = component.get("what")
        if component.get("spdx") not in self.spdx_ids or self.component_row(component) is None:
            return [f"component {what!r}: spdx {component.get('spdx')!r} not in licences.toml"]
        if component.get("commercial_ok") != self.row_commercial_ok(component):
            return [f"component {what!r}: commercial_ok {component.get('commercial_ok')} differs from licences.toml"]
        return []
