"""score-lib review: a maintainer's check of an outside pull request, run on its head inside the isolated review
environment (bundle.REVIEW_SETTING), before any human merges it.

    score-lib review --base <ref> --contributor <handle> [--bundle <url or file>]... [--sheet <picture>]

An outside pull request may change none of our code or config. It may only add files: new entry folders
(`entries/<kind>/<id>/<version>/`, an algorithm's own code and definition inside its own folder), a new
`definitions/algorithms/<name>.toml`, new rows of `config/sources.toml` (every row already there kept as it is) and
the index lines `score-lib index` builds for them (`index/`, never `index/locks.json`). A changed, deleted or renamed
file anywhere else (src/, tests/, schemas/, worlds/, .github/, CLAUDE.md, README.md, LICENSE, ...) fails it, as does a
commit without a `Signed-off-by:` line (the Developer Certificate of Origin), a new entry that is not a draft taken in
as the contributor's, and a file name or ending its kind does not allow. With bundles it also checks them whole
(bundle.checked) and draws a contact sheet of every picture, model view and sound in them, which a human looks at
before merging.
"""
import json
import pathlib
import re
import struct
import subprocess
import tempfile
import tomllib

import numpy as np
from PIL import Image, ImageDraw

from score_lib import bundle, contribution, index
from score_lib import entry as entries

ENTRY = re.compile(r"^entries/(?P<kind>[a-z]+)/(?P<id>[a-z0-9_]+(?:\.[a-z0-9_]+)?)/(?P<version>[1-9][0-9]*)/.+$")
DEFINITION = re.compile(r"^definitions/algorithms/[a-z0-9_]+\.toml$")
INDEX = re.compile(r"^index/(?!locks\.json$)[a-z]+\.json$")
SOURCES = "config/sources.toml"
SIGNED_OFF = re.compile(r"^Signed-off-by: .+ <[^>]+>$", re.MULTILINE)
TILE = 240
COLUMNS = 6
PICTURES = (".png", ".jpg", ".jpeg", ".webp")
SOUNDS = (".ogg", ".wav", ".flac", ".mp3")
MESHES = (".usda", ".usdc")
GLTF = (".glb", ".gltf")
COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTHS = {"SCALAR": 1, "VEC3": 3}
GREY = (0.72, 0.72, 0.7)


def git(root, *arguments):
    """A git command's output in the library checkout."""
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True).stdout


def changes(root, base):
    """[(status, path)] of every file the pull request changes against `base` (renames as a delete and an add)."""
    listed = git(root, "diff", "--name-status", "--no-renames", f"{base}...HEAD")
    return [tuple(line.split("\t", 1)) for line in listed.splitlines() if line.strip()]


def existed(root, base, path):
    """Whether a path is a file or a folder at `base`."""
    found = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"], capture_output=True)
    return found.returncode == 0


def sources_kept(root, base):
    """The fault when the pull request's config/sources.toml changes or drops a row `base` holds, else None."""
    before = tomllib.loads(git(root, "show", f"{base}:{SOURCES}"))
    after = tomllib.loads((pathlib.Path(root) / SOURCES).read_text())
    changed = [key for key, value in before.items() if after.get(key) != value]
    return f"{SOURCES}: changes rows already there ({', '.join(changed)})" if changed else None


def index_built(root, path):
    """The fault when an index file is not what `score-lib index` builds from the entries, else None."""
    built = index.built(root)
    if path not in built or entries.dumps(built[path]) != (pathlib.Path(root) / path).read_text():
        return f"{path}: not the index score-lib index builds"
    return None


def path_fault(root, base, status, path):
    """Why one changed file is not allowed in an outside pull request, or None."""
    if status == "A":
        found = ENTRY.match(path)
        if found:
            folder = f"entries/{found['kind']}/{found['id']}/{found['version']}"
            return f"{path}: adds to {folder}, which is not a new entry" if existed(root, base, folder) else None
        if DEFINITION.match(path):
            return None
        return f"{path}: an outside pull request adds only entries, algorithm definitions, sources and index lines"
    if status == "M" and path == SOURCES:
        return sources_kept(root, base)
    if status == "M" and INDEX.match(path):
        return index_built(root, path)
    return f"{path}: an outside pull request may change none of our code or config ({status})"


def diff_faults(root, base):
    """Every changed file an outside pull request may not change."""
    return [fault for status, path in changes(root, base) if (fault := path_fault(root, base, status, path))]


def unsigned(root, base):
    """The pull request's commits without a Signed-off-by line."""
    commits = git(root, "rev-list", f"{base}..HEAD").split()
    return [commit for commit in commits if not SIGNED_OFF.search(git(root, "log", "-1", "--format=%B", commit))]


def new_entries(root, base):
    """The records of the entries the pull request adds."""
    folders = sorted({"/".join(path.split("/")[:4]) for status, path in changes(root, base)
                      if status == "A" and ENTRY.match(path)})
    return [entries.load(pathlib.Path(root) / folder / "entry.json") for folder in folders
            if (pathlib.Path(root) / folder / "entry.json").is_file()]


def entry_faults(record, contributor):
    """Why a new entry is not a draft taken in as the contributor's with allowed file names."""
    found = [f"{record['address']}: {fault}" for fault in contribution.faults(record, pending=True)]
    if record.get("provenance", {}).get("contributor") != contributor:
        found.append(f"{record['address']}: not taken in as {contributor}'s (take-in's contributor block)")
    if record["state"] != "draft":
        found.append(f"{record['address']}: a new entry comes in as a draft")
    try:
        bundle.record_faults(record)
    except bundle.Refused as why:
        found.append(f"{record['address']}: {why}")
    return found


def glb_parts(path):
    """(document, binary chunk) of a .glb file."""
    data = pathlib.Path(path).read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length])
    rest = 20 + length
    return document, data[rest + 8:] if len(data) > rest + 8 else b""


def accessor(document, binary, number):
    """One accessor's values as an array of rows (no sparse accessors)."""
    found = document["accessors"][number]
    view = document["bufferViews"][found["bufferView"]]
    kind = COMPONENTS[found["componentType"]]
    width = WIDTHS[found["type"]]
    start = view.get("byteOffset", 0) + found.get("byteOffset", 0)
    values = np.frombuffer(binary, dtype=kind, count=found["count"] * width, offset=start)
    return values.reshape(-1, width)


def gltf_view(path, destination):
    """Draw a .glb's triangles as stored (node transforms left out) with the mesh preview's painter; a .gltf's
    buffers lie in other files, so only its name is shown."""
    from score_lib.preview import mesh
    if pathlib.Path(path).read_bytes()[:4] != b"glTF":
        return False
    document, binary = glb_parts(path)
    triangles = []
    for drawn in document.get("meshes", []):
        for primitive in drawn["primitives"]:
            points = accessor(document, binary, primitive["attributes"]["POSITION"]).astype(np.float64)
            order = (accessor(document, binary, primitive["indices"]).ravel() if "indices" in primitive
                     else np.arange(len(points)))
            triangles.append(points[order[: len(order) // 3 * 3]].reshape(-1, 3, 3))
    if not triangles:
        return False
    joined = np.concatenate(triangles) @ mesh.view_matrix().T
    mesh.draw(joined, np.tile(GREY, (len(joined), 1)), TILE).save(destination)
    return True


def drawn_tile(ending, path, folder):
    """A sound's waveform or a model's view drawn into `folder`; the picture, or None when nothing was drawn."""
    from score_lib.preview import audio, mesh
    drawn = pathlib.Path(folder) / "tile.png"
    linked = pathlib.Path(folder) / f"source{ending}"
    linked.symlink_to(pathlib.Path(path).resolve())
    if ending in GLTF:
        if not gltf_view(linked, drawn):
            return None
    else:
        (audio if ending in SOUNDS else mesh).render(linked, drawn)
    return Image.open(drawn).convert("RGB")


def tile_of(name, path):
    """A contact sheet's tile for one file, or None when it is not a picture, a model or a sound."""
    ending = pathlib.PurePosixPath(name).suffix.lower()
    if ending in PICTURES:
        picture = Image.open(path).convert("RGB")
    elif ending in SOUNDS + MESHES + GLTF:
        with tempfile.TemporaryDirectory() as folder:
            picture = drawn_tile(ending, path, folder)
            picture = picture.copy() if picture else None
        if picture is None:
            return None
    else:
        return None
    picture.thumbnail((TILE, TILE - 20))
    tile = Image.new("RGB", (TILE, TILE), (24, 26, 30))
    tile.paste(picture, ((TILE - picture.width) // 2, 0))
    ImageDraw.Draw(tile).text((4, TILE - 16), name[-38:], fill=(230, 232, 235))
    return tile


def sheet(opened, out):
    """Draw every picture, mesh view and sound of an opened bundle on one picture; the number of tiles."""
    named = {about["sha256"]: name for record in opened["records"].values()
             for name, about in record["files"].items()}
    tiles = [tile for sha, path in sorted(opened["blobs"].items())
             if (tile := tile_of(named.get(sha, sha), path)) is not None]
    rows = max(1, -(-len(tiles) // COLUMNS))
    picture = Image.new("RGB", (TILE * COLUMNS, TILE * rows), (12, 13, 15))
    for number, tile in enumerate(tiles):
        picture.paste(tile, (TILE * (number % COLUMNS), TILE * (number // COLUMNS)))
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return len(tiles)


def review(root, base, contributor, sources=(), sheet_path=None):
    """Every fault of an outside pull request (empty when it may go to a human's look), and the sheet's tiles."""
    bundle.in_review_environment()
    faults = diff_faults(root, base)
    faults += [f"{commit}: no Signed-off-by line" for commit in unsigned(root, base)]
    for record in new_entries(root, base):
        faults += entry_faults(record, contributor)
    tiles = 0
    with tempfile.TemporaryDirectory() as work:
        for number, source in enumerate(sources):
            folder = pathlib.Path(work) / str(number)
            folder.mkdir()
            try:
                opened = bundle.unpacked(bundle.downloaded(source, folder), folder / "files")
                bundle.checked(root, opened, contributor)
            except bundle.Refused as why:
                faults.append(f"{source}: {why}")
                continue
            if sheet_path:
                out = pathlib.Path(sheet_path)
                tiles += sheet(opened, out.with_name(f"{out.stem}-{number}{out.suffix}") if number else out)
    return faults, tiles
