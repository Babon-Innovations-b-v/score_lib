"""What every kind's quality measures read, made once a run: the library's entries and blob store, the SCORE store
(read only) with its newest judgement of each output, the score repository's data and the checks its stages already
measured per object (the likeness gate's outlines, the surfaces check's bake colours)."""
import functools
import json
import pathlib
import sys

from score_lib import entry as entries


class Context:
    def __init__(self, root, blobs, store, repo, work):
        self.root = pathlib.Path(root)
        self.blobs = blobs
        self.store = store
        self.repo = pathlib.Path(repo)
        self.work = pathlib.Path(work)
        self.work.mkdir(parents=True, exist_ok=True)

    def repo_json(self, relative):
        """A JSON file of the score repository, or None when it has none."""
        path = self.repo / relative
        return json.loads(path.read_text()) if path.is_file() else None

    def repo_tool(self, folder):
        """Put a folder of the score repository's tools on the import path (read only)."""
        path = str(self.repo / folder)
        if path not in sys.path:
            sys.path.insert(0, path)

    @functools.cached_property
    def records(self):
        """{address: record} of every entry, withdrawn ones left out."""
        return {record["address"]: record for record in entries.all_entries(self.root)
                if record["state"] != "withdrawn"}

    @functools.cached_property
    def verdicts_by_output(self):
        """{output hash: (node, the newest judgement's verdicts)} over every judgement in the store."""
        found = {}
        for path in sorted((self.store.objects / "judgements").glob("*/*/*.json")):
            judgement = json.loads(path.read_text())
            output = judgement.get("output")
            if not output:
                continue
            if output not in found or judgement.get("at", "") >= found[output][2]:
                found[output] = (judgement.get("node"), judgement.get("verdicts", {}), judgement.get("at", ""))
        return {output: (node, verdicts) for output, (node, verdicts, _) in found.items()}

    def accepted_verdicts(self, node):
        """(output, verdicts) of a node's accepted output, or (None, {})."""
        output = self.store.accepted(node)
        if not output:
            return None, {}
        return output, self.verdicts_by_output.get(output, (node, {}))[1]

    @functools.cached_property
    def library(self):
        """The surface library (data/library/materials.json)."""
        return self.repo_json("data/library/materials.json")

    @functools.cached_property
    def variant_families(self):
        """{library variant: its family}."""
        return {variant: family for family, about in self.library["families"].items()
                for variant in about["variants"]}

    @functools.cache
    def kit(self, place):
        """A place's kit layout (data/kit/<place>.json), or None."""
        return self.repo_json(f"data/kit/{place}.json")

    @functools.cache
    def palette(self, place):
        """Every colour the place's library surfaces bake to (L*a*b*), by the room sweep's own rule, or None when the
        place has no palette."""
        self.repo_tool("tools/props/library")
        import sweep
        kit = self.kit(place) or {}
        try:
            return sweep.palette(kit.get("place", place))
        except KeyError:
            return None

    @functools.cached_property
    def likeness(self):
        """{(place, model stem): the likeness stage's measures of it} from every accepted likeness output."""
        found = {}
        for node in self.store.nodes_of("likeness"):
            output = self.store.accepted(node)
            measures = self.store.read_json(output, "likeness.json") or {}
            place = node.split("@", 1)[1]
            for name, measured in measures.get("objects", {}).items():
                stem = pathlib.Path(measured.get("model") or name).stem
                found[(place, stem)] = dict(measured, node=node, output=output)
        return found

    @functools.cached_property
    def surface_checks(self):
        """{(place, model stem): the surfaces check's measures of it} from every accepted surfaces-check output."""
        found = {}
        for node in self.store.nodes_of("surfaces-check"):
            output = self.store.accepted(node)
            measures = self.store.read_json(output, "checks.json") or {}
            place = node.split("@", 1)[1]
            for stem, measured in measures.items():
                found[(place, stem)] = dict(measured, node=node, output=output)
        return found
