"""Running an algorithm entry: its snapshot, preset and tests materialised into one folder at their repository paths,
then its tests run there, or its entry function called with the preset (refused when it needs Blender or a GPU)."""
import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

from score_lib import entry as entries
from score_lib.fetch import fetch


class Refused(RuntimeError):
    """The algorithm cannot be run here (it needs Blender, a GPU or other paid parts)."""


def materialise(root, blobs, address, folder, preset="world1"):
    """The entry's code/, presets/<preset>/ and test/ laid into one folder at their repository paths; the record."""
    record = entries.load_address(root, address)
    if record is None or record["kind"] != "algorithm":
        raise KeyError(f"{address}: no algorithm entry")
    fetched = pathlib.Path(folder) / ".entry"
    fetch(root, blobs, [address], fetched)
    kind, entry_id, version = entries.parse_address(address)
    base = fetched / entries.library_path(kind, entry_id, version, "")
    for prefix in ("code/", f"presets/{preset}/", "test/"):
        for name in sorted(record["files"]):
            if name.startswith(prefix):
                target = entries.inside(folder, name[len(prefix):])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(base / name, target)
    return record, base


def search_folders(folder, base):
    """The import folders: the snapshot's root and the definition's search folders."""
    definition = json.loads((base / "definition.json").read_text())
    return [str(folder)] + [str(pathlib.Path(folder) / each) for each in definition.get("search", [])], definition


def test(root, blobs, address, folder):
    """Run the entry's tests with pytest in the materialised folder; pytest's exit code."""
    record, base = materialise(root, blobs, address, folder)
    folders, definition = search_folders(folder, base)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(folders))
    tests = [each for each in definition.get("tests", []) if (pathlib.Path(folder) / each).is_file()]
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=folder, env=environment)
    return done.returncode


def arguments(pairs, folder, out):
    """{name: value} from name=value pairs, {preset} and {out} filled in."""
    found = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        found[name] = value.replace("{preset}", str(folder)).replace("{out}", str(out))
    return found


def run(root, blobs, address, out, preset="world1", pairs=()):
    """Call the entry function with the materialised preset; its return value. Refused when the algorithm needs
    Blender, a GPU or other parts this machine does not run."""
    record = entries.load_address(root, address)
    if record is None:
        raise KeyError(f"{address}: no such entry")
    needs = record.get("tags", {}).get("needs", [])
    if needs:
        raise Refused(f"{address} needs {', '.join(needs)}: snapshot and schema only, it is not run here")
    folder = pathlib.Path(out) / "work"
    record, base = materialise(root, blobs, address, folder, preset)
    folders, definition = search_folders(folder, base)
    sys.path[:0] = folders
    module_name, function_name = definition["entry"].split(":")
    function = getattr(importlib.import_module(module_name), function_name)
    return function(**arguments(pairs, folder, out))
