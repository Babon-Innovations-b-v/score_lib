"""Running an algorithm entry: its snapshot, preset and tests materialised into one folder at their repository paths,
then its tests run there, or its entry function called with the preset (refused when it needs Blender or a GPU)."""
import hashlib
import importlib
import importlib.metadata
import json
import os
import pathlib
import re
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


def requirements(base, testing):
    """The Python distributions the entry's run.json requires; testing, with pytest and its test requirements too."""
    run_record = json.loads((base / "run.json").read_text())
    tested = ["pytest", *run_record.get("test_requires", [])] if testing else []
    return [*run_record.get("requires", []), *tested]


def missing_requirements(base, testing=False):
    """The requirements (requirements) this interpreter does not have, in order."""
    missing = []
    for name in requirements(base, testing):
        try:
            importlib.metadata.distribution(re.split(r"[<>=!~\[; ]", name, maxsplit=1)[0])
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


def refuse_beside(address, base):
    """Refused naming what to fetch when the entry's own code runs code the library may not carry (its outside
    requirements)."""
    beside = json.loads((base / "run.json").read_text()).get("runs_beside", [])
    if beside:
        named = "; ".join(f"{each['name']} ({each['where']})" for each in beside)
        raise Refused(f"{address} runs only with code the library may not carry: {named}; give a score checkout "
                      f"(--score, or SCORE_REPO) to lay it in")


def refuse_missing(address, base, testing=False):
    """Refused when the entry requires a distribution this interpreter lacks, naming what to install."""
    missing = missing_requirements(base, testing)
    if missing:
        raise Refused(f"{address} requires {', '.join(missing)}, not installed here: "
                      f"uv pip install {' '.join(missing)} (or pip install)")


def interpreter(base):
    """The Python a test runs in: this one when it has every requirement; else, with uv, a throwaway environment of
    the entry's Python with pytest, its requirements and its test requirements (uv's own cache, nothing in the
    checkout); else this one, which refuse_missing then refuses."""
    uv = shutil.which("uv")
    if not uv or not missing_requirements(base, testing=True):
        return [sys.executable]
    python = json.loads((base / "run.json").read_text()).get("python", "3.12")
    return [uv, "run", "--quiet", "--no-project", "--python", python,
            *(word for name in requirements(base, True) for word in ("--with", name)), "python"]


def test_command(path, python):
    """How one test file runs, as score's own runner does: with pytest when it holds test_ functions and no
    __main__ block (it wants pytest's fixtures), else as a plain script; `python` the interpreter's words."""
    lines = pathlib.Path(path).read_text().splitlines()
    wants_pytest = any(line.startswith("def test_") for line in lines) and not any(
        line.startswith("if __name__") for line in lines)
    return [*python, "-m", "pytest", "-q", "-p", "no:cacheprovider"] if wants_pytest else list(python)


def test_environment(folders, folder):
    """The environment a test runs in: the snapshot's import folders, a sandbox home of its own inside the
    materialised folder (score's conftest refuses any other), and no GPU (a test here runs on the CPU only)."""
    home = pathlib.Path(folder) / ".home"
    home.mkdir(exist_ok=True)
    return dict(os.environ, PYTHONPATH=os.pathsep.join(folders), PROPS_HOME=str(home), CUDA_VISIBLE_DEVICES="",
                PYTHONDONTWRITEBYTECODE="1")


def fill_left_out(folder, base, score):
    """Lay the files code.json leaves out (another licence's code, the cloud runner, framework the tests run) into
    the materialised folder from a score checkout at the snapshot's commit, each checked against its recorded sha256;
    the paths laid. The library never carries them: the person running the tests brings score, as `where` says."""
    code = json.loads((base / "code.json").read_text())
    laid = []
    for path, sha in sorted(code.get("left_out", {}).items()):
        target = entries.inside(folder, path)
        if target.exists():
            continue
        done = subprocess.run(["git", "-C", str(score), "show", f"{code['commit']}:{path}"], capture_output=True)
        if done.returncode != 0 or hashlib.sha256(done.stdout).hexdigest() != sha:
            raise Refused(f"{path}: the score checkout {score} does not hold it at {code['commit']} as recorded")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(done.stdout)
        laid.append(path)
    return laid


def as_checkout(folder):
    """The materialised folder made a git checkout of one commit: score's runner versions code with git (rev-parse
    HEAD, the tree's files), as in the score repository a test is written for."""
    for words in (["init", "-q"], ["add", "-A"],
                  ["-c", "user.name=score-lib", "-c", "user.email=score-lib@localhost", "commit", "-q", "-m", "snapshot"]):
        subprocess.run(["git", "-C", str(folder), *words], check=True, capture_output=True)


def test(root, blobs, address, folder, score=None):
    """Run each of the entry's tests in the materialised folder; 0 when every one passed, else the first failing
    exit code. With `score` (a score checkout) the files the library leaves out are laid in from it first; without it,
    refused when the entry runs code the library may not carry, or (without uv) a requirement is not installed."""
    record, base = materialise(root, blobs, address, folder)
    python = interpreter(base)
    if python == [sys.executable]:
        refuse_missing(address, base, testing=True)
    if score:
        fill_left_out(folder, base, score)
    else:
        refuse_beside(address, base)
    folders, definition = search_folders(folder, base)
    environment = test_environment(folders, folder)
    as_checkout(folder)
    tests = [each for each in definition.get("tests", []) if (pathlib.Path(folder) / each).is_file()]
    codes = [subprocess.run([*test_command(pathlib.Path(folder) / each, python), each], cwd=folder,
                            env=environment).returncode for each in tests]
    return next((code for code in codes if code), 0)


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
    refuse_beside(address, base)
    refuse_missing(address, base)
    folders, definition = search_folders(folder, base)
    sys.path[:0] = folders
    module_name, function_name = definition["entry"].split(":")
    function = getattr(importlib.import_module(module_name), function_name)
    return function(**arguments(pairs, folder, out))
