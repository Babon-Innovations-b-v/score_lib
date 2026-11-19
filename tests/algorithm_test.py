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
    assert record["files"]["code/tools/__init__.py"]["bytes"] == 0  # a package it only sits in ships as a marker
    code = blob_json(record, "code.json")
    assert code["code_hash"] == record["provenance"]["code"] and code["python"] == "3.12"
    assert blob_json(record, "run.json")["how"].startswith("score-lib run algorithm/demo@")
    assert blob_json(record, "run.json")["runs_beside"] == []  # its outside part is preset data, not code it runs
    gpu = entries.load_address(world.root, "algorithm/demo_gpu@1")
    assert [each["name"] for each in blob_json(gpu, "run.json")["runs_beside"]] == ["an outside helper", "tools/demo/ported.py"]


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
    """Break: test_support or conftests not shipped, named_files not following `REPO / "data/..."` or the folder a
    test puts on sys.path (inserted_folders), test_command always pytest (a plain-script test: 'no tests ran', exit 5),
    refuse_missing passing a requirement this interpreter lacks, or fill_left_out not laying a left-out file in."""
    world.export(kinds=["algorithm"])
    record = entries.load_address(world.root, "algorithm/demo@1")
    assert {"code/tools/checks/checker.py", "presets/world1/data/demo/expected.json", "test/conftest.py"} <= set(
        record["files"])
    context = world.context()
    hashed = algorithm.closure(context, ["tools/demo/algo.py"], ["tools/demo"])
    assert record["provenance"]["code"] == algorithm.code_hash(context, hashed)  # the tests' imports are not hashed
    code = algorithms.test(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), "algorithm/demo@1",
                           tmp_path / "run")
    assert code == 0
    script = tmp_path / "test_script.py"
    script.write_text("def test_x():\n    pass\n\n\nif __name__ == '__main__':\n    test_x()\n")
    fixtures = tmp_path / "test_fixtures.py"
    fixtures.write_text("def test_x(tmp_path):\n    pass\n")
    assert algorithms.test_command(script, ["py"]) == ["py"]
    assert algorithms.test_command(fixtures, ["py"])[1:3] == ["-m", "pytest"]
    (tmp_path / "run.json").write_text(json.dumps({"requires": ["pytest"], "test_requires": ["no-such-dist==1"]}))
    algorithms.refuse_missing("algorithm/x@1", tmp_path)  # running needs only its requires
    with pytest.raises(algorithms.Refused, match="requires no-such-dist==1, not installed"):
        algorithms.refuse_missing("algorithm/x@1", tmp_path, testing=True)
    beside = {"name": "a port", "version": "1", "where": "score: tools/demo/ported.py", "why": "Apache-2.0"}
    (tmp_path / "run.json").write_text(json.dumps({"requires": [], "runs_beside": [beside]}))
    with pytest.raises(algorithms.Refused, match="only with code the library may not carry: a port"):
        algorithms.refuse_beside("algorithm/x@1", tmp_path)
    gpu = entries.load_address(world.root, "algorithm/demo_gpu@1")
    (tmp_path / "gpu").mkdir()
    (tmp_path / "gpu/code.json").write_text(json.dumps(blob_json(gpu, "code.json")))
    laid = algorithms.fill_left_out(tmp_path / "laid", tmp_path / "gpu", world.repo)
    assert laid == ["tools/demo/helper.py", "tools/demo/ported.py"]
    assert (tmp_path / "laid/tools/demo/ported.py").read_text().startswith("# SPDX-License-Identifier: Apache-2.0")


def live_algorithms(folder):
    """The ids of the algorithm entries whose newest version is not withdrawn."""
    newest = (max(entry.iterdir(), key=lambda version: int(version.name)) for entry in folder.iterdir())
    return {version.parent.name for version in newest
            if json.loads((version / "entry.json").read_text())["state"] != "withdrawn"}


def test_the_library_definitions_are_valid():
    folder = pathlib.Path(__file__).resolve().parents[1] / "definitions/algorithms"
    found = sorted(folder.glob("*.toml"))
    assert {path.stem for path in found} == live_algorithms(folder.parents[1] / "entries/algorithm")
    for path in found:
        assert schema.faults("algorithm", algorithm.load(path)) == [], path.name
    jsonschema.Draft202012Validator.check_schema(schema.load("entry"))
