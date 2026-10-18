"""An entry's record: its address, loading and writing `entry.json`, and its content digest."""
import hashlib
import json
import pathlib
import re

SCHEMA = "score-lib-entry/1"
KINDS = ("algorithm", "surface", "model", "light", "sound", "effect", "character", "ground", "kit", "layer", "place")
ID_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)?$")
ADDRESS_PATTERN = re.compile(r"^(?P<kind>[a-z]+)/(?P<id>[a-z0-9_]+(\.[a-z0-9_]+)?)@(?P<version>[1-9][0-9]*)$")
UNSTAMPED = ("state", "locked")


def parse_address(address):
    """(kind, id, version) of an address `<kind>/<id>@<version>`; ValueError when it is not one."""
    found = ADDRESS_PATTERN.match(address)
    if not found or found["kind"] not in KINDS:
        raise ValueError(f"not an entry address: {address!r} (expected <kind>/<id>@<version>)")
    return found["kind"], found["id"], int(found["version"])


def address_of(kind, entry_id, version):
    """The address of one entry."""
    return f"{kind}/{entry_id}@{version}"


def folder_of(root, kind, entry_id, version):
    """The git folder of one entry: entries/<kind>/<id>/<version>."""
    return pathlib.Path(root) / "entries" / kind / entry_id / str(version)


def library_path(kind, entry_id, version, name):
    """The search path a world uses for one file of an entry: score_lib/<kind>/<id>/<version>/<file>."""
    return f"score_lib/{kind}/{entry_id}/{version}/{name}"


def dumps(record):
    """The canonical text of a JSON record: sorted keys, two-space indent, a closing newline."""
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write(root, record):
    """Write an entry's `entry.json` into its folder; the path written."""
    kind, entry_id, version = parse_address(record["address"])
    path = folder_of(root, kind, entry_id, version) / "entry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(record))
    return path


def load(path):
    """One `entry.json`."""
    return json.loads(pathlib.Path(path).read_text())


def load_address(root, address):
    """The entry at an address, or None when there is none."""
    kind, entry_id, version = parse_address(address)
    path = folder_of(root, kind, entry_id, version) / "entry.json"
    return load(path) if path.is_file() else None


def all_paths(root, kind=None):
    """Every `entry.json` under entries/, sorted by kind, id, then version number."""
    base = pathlib.Path(root) / "entries"
    kinds = [kind] if kind else KINDS
    found = []
    for each in kinds:
        found += [path for path in (base / each).glob("*/*/entry.json")]
    return sorted(found, key=lambda path: (path.parts[-4], path.parts[-3], int(path.parts[-2])))


def all_entries(root, kind=None):
    """Every entry record under entries/, in `all_paths` order."""
    return [load(path) for path in all_paths(root, kind)]


def versions(root, kind, entry_id):
    """Every entry of one id, oldest version first."""
    folder = pathlib.Path(root) / "entries" / kind / entry_id
    paths = sorted(folder.glob("*/entry.json"), key=lambda path: int(path.parts[-2]))
    return [load(path) for path in paths]


def content(record):
    """The entry without its state and lock date: what its identity is made of."""
    return {key: value for key, value in record.items() if key not in UNSTAMPED}


def digest(record):
    """The content digest: the sha256 of the canonical text of the entry minus `state` and `locked`."""
    return hashlib.sha256(dumps(content(record)).encode()).hexdigest()
