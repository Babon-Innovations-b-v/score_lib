"""An algorithm's quality score:

    tests     its own tests, run here from its snapshot (pytest, the interpreter running this): all pass 100, any
              fail or a test that cannot be collected 0
    preset    its world 1 preset is in the entry
    schema    its settings schema is in the entry
"""
import os
import subprocess
import sys
import tempfile

from score_lib import algorithms
from score_lib.quality import parts

TEST_SECONDS = 600


def run(record, context):
    found = [tests_part(record, context)]
    presets = [name for name in record["files"] if name.startswith("presets/world1/")]
    found.append(parts.scored("preset", 100 if presets else 0, len(presets), "files of its world 1 preset in the "
                              "entry", weight=0.5, unit="files"))
    schema = "settings.schema.json" in record["files"]
    found.append(parts.scored("schema", 100 if schema else 0, schema, "its settings schema is in the entry",
                              weight=0.5))
    return found


def tests_part(record, context):
    with tempfile.TemporaryDirectory(prefix="score-lib-quality-test-") as folder:
        _, base = algorithms.materialise(context.root, context.blobs, record["address"], folder)
        folders, definition = algorithms.search_folders(folder, base)
        tests = [each for each in definition.get("tests", []) if os.path.isfile(os.path.join(folder, each))]
        if not tests:
            return parts.unmeasured("tests", "its definition names no test in the snapshot")
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(folders))
        try:
            done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                                  cwd=folder, env=environment, capture_output=True, text=True, timeout=TEST_SECONDS)
        except subprocess.TimeoutExpired:
            return parts.scored("tests", 0, "timed out", f"its {len(tests)} test files did not finish in "
                                f"{TEST_SECONDS} s", weight=3.0)
    summary = (done.stdout.strip().splitlines() or ["no output"])[-1]
    errors = [line for line in done.stdout.splitlines() if line.startswith(("E   ", "ERROR"))][:3]
    how = f"its {len(tests)} test files run from its snapshot with pytest: {summary}"
    if done.returncode != 0 and errors:
        how += f" ({'; '.join(errors)})"
    return parts.scored("tests", 100 if done.returncode == 0 else 0, summary, how, weight=3.0)
