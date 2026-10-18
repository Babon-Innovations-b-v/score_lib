"""A read-only reader of a SCORE store's objects folder (accepts, rejects, outputs, records, judgements, blobs).

The accept recipe follows score/store/__init__.py:333-341 (`current_accept`): a node's accepted output is the newest
accept whose `at` is later than every reject of that output. Nothing here writes to the store; checkouts are folders
of symbolic links to the stored blobs, made outside the store.
"""
import json
import os
import pathlib

# Rows kept in the store for history, not library sources (layers.md section 0). ship_kit stays: the ship places'
# assemblies still read its gltf pieces.
RETIRED_STAGES = {"effects", "lightkit", "late_bake", "models", "room_sound", "sweep", "line-setup",
                  "line-launch-setup", "surface-asks", "surface-judge"}
RETIRED_PREFIXES = ("split-", "fm-", "surf-", "surfaces-hangar-", "person-", "image-pins-", "image-proof-")


def stage_of(node):
    """The stage name of a node: `<stage>@<place>` or a world node's own name."""
    return node.split("@", 1)[0]


def place_of(node):
    """The place of a place-grain node, or None for a world node."""
    return node.split("@", 1)[1] if "@" in node else None


def retired(node):
    """True for a row kept for history only."""
    stage = stage_of(node)
    return stage in RETIRED_STAGES or stage.startswith(RETIRED_PREFIXES)


class Store:
    def __init__(self, objects):
        self.objects = pathlib.Path(objects)

    def timed(self, area, node):
        """Every object of a node in accepts/ or rejects/, oldest first by `at`."""
        folder = self.objects / area / node
        if not folder.is_dir():
            return []
        found = [json.loads(path.read_text()) for path in folder.glob("*.json")]
        return sorted(found, key=lambda item: item["at"])

    def current_accept(self, node):
        """The newest accept that no later reject of the same output undid, or None."""
        rejected = {}
        for reject in self.timed("rejects", node):
            rejected[reject["output"]] = max(rejected.get(reject["output"], ""), reject["at"])
        standing = [accept for accept in self.timed("accepts", node)
                    if accept["at"] > rejected.get(accept["output"], "")]
        return standing[-1] if standing else None

    def accepted(self, node):
        """The node's accepted output hash, or None."""
        return (self.current_accept(node) or {}).get("output")

    def nodes(self):
        """Every node with an accepts folder, retired rows left out, sorted."""
        folder = self.objects / "accepts"
        if not folder.is_dir():
            return []
        return sorted(path.name for path in folder.iterdir() if path.is_dir() and not retired(path.name))

    def nodes_of(self, stage):
        """Every node of one stage that has an accepted output, sorted."""
        return [node for node in self.nodes() if stage_of(node) == stage and self.accepted(node)]

    def listing(self, output):
        """{relative path: sha256} of a stored output."""
        return json.loads((self.objects / "outputs" / f"{output}.json").read_text())

    def blob(self, sha256):
        """The path of one stored blob (read only)."""
        return self.objects / "blobs" / sha256

    def has_blob(self, sha256):
        return self.blob(sha256).is_file()

    def read_json(self, output, name):
        """One JSON file of a stored output, or None when the output lacks it."""
        sha = self.listing(output).get(name)
        return json.loads(self.blob(sha).read_text()) if sha else None

    def read_text(self, output, name):
        """One text file of a stored output, or None when the output lacks it."""
        sha = self.listing(output).get(name)
        return self.blob(sha).read_text() if sha else None

    def record(self, node, key):
        """A node's finish record for a key, or {} when there is none."""
        path = self.objects / "records" / node / f"{key}.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def judgements(self, node, key):
        """Every judgement of a node's key, oldest first by `at` (or file name when undated)."""
        folder = self.objects / "judgements" / node / key
        if not folder.is_dir():
            return []
        found = [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]
        return sorted(found, key=lambda item: item.get("at", ""))

    def link_tree(self, output, folder, names=None):
        """A folder of symbolic links naming an output's files (or only `names`), each pointing at its stored blob;
        the folder. Links, not copies: nothing is written into the store, and readers never write through them."""
        folder = pathlib.Path(folder)
        for name, sha in self.listing(output).items():
            if names is not None and name not in names:
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                continue
            os.symlink(self.blob(sha), target)
        return folder
