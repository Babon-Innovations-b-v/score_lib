"""JSON Schema validation of entries and index files against schemas/."""
import json
import pathlib

import jsonschema

SCHEMAS = pathlib.Path(__file__).resolve().parents[2] / "schemas"
FILES = {"entry": "entry.schema.json", "index": "index.schema.json", "catalogue": "catalogue.schema.json",
         "algorithm": "algorithm.schema.json"}


def load(name):
    return json.loads((SCHEMAS / FILES[name]).read_text())


def faults(name, document):
    """Every validation message of a document against one schema (empty when valid)."""
    validator = jsonschema.Draft202012Validator(load(name))
    return [f"{'/'.join(str(part) for part in error.absolute_path) or '(root)'}: {error.message}"
            for error in sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))]
