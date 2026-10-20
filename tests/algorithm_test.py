"""Algorithm entries: the snapshot closure, schema defaults from module constants, the tests and run lines, and a
run refused when the algorithm needs a GPU."""
import json
import os
import pathlib

import jsonschema
import pytest

from score_lib import algorithms, schema
from score_lib import entry as entries
from score_lib.blobs.local import LocalBlobStore
from score_lib.export import algorithm


def blob_json(record, name):
    store = LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"])
    return json.loads(store.path(record["files"][name]["sha256"]).read_text())


def test_snapshot_closure(world):
    context = world.context()
    assert algorithm.closure(context, ["tools/demo/algo.py"], ["tools/demo"]) == ["tools/demo/algo.py",
                                                                                 "tools/demo/helper.py"]


def test_a_definition_naming_a_missing_file_is_refused(world):
    context = world.context()
    with pytest.raises(algorithm.Missing, match="tools/demo/gone.py"):
        algorithm.closure(context, ["tools/demo/algo.py", "tools/demo/gone.py"], ["tools/demo"])
    definition = {"name": "hollow", "title": "x", "entry": "algo:run", "roots": ["tools/demo/algo.py"],
                  "search": ["tools/demo"], "tests": ["tools/demo/gone_test.py"]}
    with pytest.raises(algorithm.Missing, match="gone_test.py"):
        algorithm.candidate(context, definition)


def test_entry_files_and_schema_defaults(world):
    world.export(kinds=["algorithm"])
    record = entries.load_address(world.root, "algorithm/demo@1")
    assert {"code/tools/demo/algo.py", "code/tools/demo/helper.py", "code.json", "settings.schema.json",
            "presets/world1/seed.json", "presets/world1/data/demo/preset.json", "test/tools/demo/algo_test.py",
            "run.json"} <= set(record["files"])
    constants = blob_json(record, "settings.schema.json")["properties"]["constants"]["properties"]
    assert constants["RATE"]["default"] == 24.0 and constants["RATE"]["type"] == "number"
    assert constants["STEP"]["default"] == pytest.approx(1 / 30)
    assert "expression" in constants["NAMES"]  # a comprehension is kept as its text, not evaluated
    code = blob_json(record, "code.json")
    assert code["code_hash"] == record["provenance"]["code"] and code["python"] == "3.12"
    assert blob_json(record, "run.json")["how"].startswith("score-lib run algorithm/demo@")


def test_run_refuses_what_needs_a_gpu(world, tmp_path):
    world.export(kinds=["algorithm"])
    with pytest.raises(algorithms.Refused, match="gpu"):
        algorithms.run(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), "algorithm/demo_gpu@1",
                       tmp_path / "out")


def test_run_calls_the_entry(world, tmp_path):
    world.export(kinds=["algorithm"])
    result = algorithms.run(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), "algorithm/demo@1",
                            tmp_path / "out", pairs=["out={out}"])
    assert result == 48.0


def test_entry_tests_pass_in_the_materialised_folder(world, tmp_path):
    world.export(kinds=["algorithm"])
    code = algorithms.test(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), "algorithm/demo@1",
                           tmp_path / "run")
    assert code == 0


def test_the_library_definitions_are_valid():
    folder = pathlib.Path(__file__).resolve().parents[1] / "definitions/algorithms"
    found = sorted(folder.glob("*.toml"))
    assert len(found) == 17
    for path in found:
        assert schema.faults("algorithm", algorithm.load(path)) == [], path.name
    jsonschema.Draft202012Validator.check_schema(schema.load("entry"))
