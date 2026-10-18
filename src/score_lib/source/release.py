"""A read-only reader of a world's release manifest (`data/assets/<world>.json` in the score repository): the file
rules that name each made model's licence, source and author. The longest matching rule names a file's terms
(tools/assets/world.py)."""


class Release:
    def __init__(self, manifest):
        self.rules = manifest.get("licences", [])
        self.files = manifest.get("files", {})

    @classmethod
    def from_repo(cls, repo, world):
        path = f"data/assets/{world}.json"
        return cls(repo.read_json(path) if repo.exists(path) else {})

    def rule_for(self, name):
        """The longest rule whose `match` the name starts with (or equals), or None."""
        matching = [rule for rule in self.rules if name == rule["match"] or
                    (rule["match"].endswith("/") and name.startswith(rule["match"]))]
        return max(matching, key=lambda rule: len(rule["match"])) if matching else None
