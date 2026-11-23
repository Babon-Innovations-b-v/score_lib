"""A model entry's texture layout against its own detail: the share of its textured surface whose texels a metre
fall under DENSITY_FALL of the model's own area-weighted median, score's `sparse` measure
(score/stages/surfaces/uv_layout.py, which judges each model the bake makes, #92), read here from the entry's USD so
every entry is held to it, old ones too; a model kept as glTF is read from its .gltf or .glb (node transforms left
out, as score-lib review draws one: the measure is against the model's own median). The October 2026 code-built
models' bake gave each face toward the kit's back a twentieth of the density, and those faces show the picture's
unpainted black (score, 2026-11-23: 1,137 entries, a median 37% of each one's surface).

    score-lib layout [--address a] [--withdrawn] [--report FILE]

`score-lib verify` holds every live model entry to it (verify.check_layout); this command lists any set of entries,
withdrawn ones too, with each one's measure in the report."""
import concurrent.futures
import io
import json
import pathlib
import shutil

import numpy as np
from PIL import Image

from score_lib import entry as entries
from score_lib.preview import mesh

# score's uv_layout.DENSITY_FALL and LEAST: a face given under a quarter of its model's median texels a metre, failing
# a model past 1% of its surface (the share score's bake check judges a part at).
DENSITY_FALL = 0.25
LEAST = 0.01
# A triangle under a ten-thousandth of a texel (uv_layout.DEGENERATE_TEXELS), or with no surface, measures nothing.
DEGENERATE_TEXELS = 1e-4
DEGENERATE_M2 = 1e-14


# The roles a measure reads whole; every other file is fetched as an empty stand-in.
READ_ROLES = ("usd", "gltf")


def drawn_file(record):
    """The file a model entry is drawn from: its usd root, else its model.usdc, else its one .gltf or .glb that is
    not a collision hull (`-hulls`); None when it has none of them."""
    named = (record.get("usd") or {}).get("root") or "model.usdc"
    if named in record["files"]:
        return named
    kept = [name for name, described in record["files"].items() if described["role"] == "gltf"
            and name.endswith((".gltf", ".glb")) and not pathlib.Path(name).stem.endswith("-hulls")]
    return kept[0] if len(kept) == 1 else None


def gltf_parts(path):
    """(document, [each buffer's bytes]) of a .gltf (its buffers in files beside it) or a .glb (its binary chunk)."""
    from score_lib import review
    path = pathlib.Path(path)
    if path.suffix == ".glb":
        document, binary = review.glb_parts(path)
        return document, [binary]
    document = json.loads(path.read_text())
    return document, [(path.parent / buffer["uri"]).read_bytes() for buffer in document.get("buffers", [])]


# glTF accessor component types and element widths.
COMPONENTS = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}


def gltf_values(document, buffers, number):
    """One accessor's values as rows, from the buffer its view names, its view's stride kept (no sparse accessors)."""
    found = document["accessors"][number]
    view = document["bufferViews"][found["bufferView"]]
    kind, width = np.dtype(COMPONENTS[found["componentType"]]), WIDTHS[found["type"]]
    stride = view.get("byteStride") or kind.itemsize * width
    start = view.get("byteOffset", 0) + found.get("byteOffset", 0)
    rows = np.lib.stride_tricks.as_strided(
        np.frombuffer(buffers[view.get("buffer", 0)], dtype=np.uint8, offset=start),
        shape=(found["count"], kind.itemsize * width), strides=(stride, 1), writeable=False)
    return np.ascontiguousarray(rows).view(kind).reshape(-1, width)


def gltf_picture(document, buffers, image, folder):
    """The path of a glTF image: its file beside the model, or its bytes inside a buffer written into `folder`."""
    found = document["images"][image]
    if "uri" in found:
        return str(pathlib.Path(folder) / found["uri"])
    view = document["bufferViews"][found["bufferView"]]
    start = view.get("byteOffset", 0)
    target = pathlib.Path(folder) / f"embedded_{image}.png"
    Image.open(io.BytesIO(buffers[view.get("buffer", 0)][start:start + view["byteLength"]])).save(target)
    return str(target)


def gltf_collect(path):
    """(triangle corners (n, 3, 3), Skin) of every triangle primitive of a .gltf or .glb, each wearing its material's
    base colour texture."""
    document, buffers = gltf_parts(path)
    corners, uvs, numbers, images = [], [], [], []
    for primitive in (each for drawn in document.get("meshes", []) for each in drawn["primitives"]):
        if primitive.get("mode", 4) != 4:
            continue
        points = gltf_values(document, buffers, primitive["attributes"]["POSITION"]).astype(np.float64)
        order = (gltf_values(document, buffers, primitive["indices"]).ravel() if "indices" in primitive
                 else np.arange(len(points)))
        order = order[: len(order) // 3 * 3].reshape(-1, 3)
        corners.append(points[order])
        material = document["materials"][primitive["material"]] if "material" in primitive else {}
        texture = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if texture is None or "TEXCOORD_0" not in primitive["attributes"]:
            uvs.append(np.zeros((len(order), 3, 2)))
            numbers.append(np.full(len(order), -1))
            continue
        image = document["textures"][texture["index"]]["source"]
        if image not in images:
            images.append(image)
        uvs.append(gltf_values(document, buffers, primitive["attributes"]["TEXCOORD_0"]).astype(np.float64)[order])
        numbers.append(np.full(len(order), images.index(image)))
    if not corners:
        return np.zeros((0, 3, 3)), mesh.Skin(np.zeros((0, 3, 2)), np.zeros(0, dtype=np.int64), [])
    folder = pathlib.Path(path).parent
    paths = [gltf_picture(document, buffers, image, folder) for image in images] if len(images) > 1 else \
        [str(folder / document["images"][image].get("uri", "embedded")) for image in images]
    return np.concatenate(corners), mesh.Skin(np.concatenate(uvs), np.concatenate(numbers), paths)


def picture_sizes(skin):
    """Each texture's (width, height); every one (1, 1) when the model reads one texture or none, since the measure
    is against the model's own median and one picture's size cancels out."""
    if len(set(skin.numbers[skin.numbers >= 0].tolist())) <= 1:
        return [(1, 1)] * len(skin.paths)
    sizes = []
    for path in skin.paths:
        with Image.open(path) as picture:
            sizes.append(tuple(int(value) for value in picture.size))
    return sizes


def sparse_share(triangles, skin, sizes):
    """(share of the textured surface under DENSITY_FALL of the median texels a metre, that median), each triangle's
    density its texels (UV area times its picture's size) over its area, square-rooted; (0, 0) with no textured
    surface."""
    textured = skin.numbers >= 0
    if not textured.any():
        return 0.0, 0.0
    corners, uvs = triangles[textured], skin.uvs[textured]
    areas = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    first, second = uvs[:, 1] - uvs[:, 0], uvs[:, 2] - uvs[:, 0]
    pixels = np.asarray(sizes, dtype=np.float64)[skin.numbers[textured]]
    texels = 0.5 * np.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) * pixels[:, 0] * pixels[:, 1]
    density = np.sqrt(texels / np.maximum(areas, DEGENERATE_M2))
    sized = len(set(map(tuple, sizes))) > 1 or sizes[0] != (1, 1)
    valid = (areas > DEGENERATE_M2) & ((texels > DEGENERATE_TEXELS) if sized else (texels > 0))
    total = float(areas.sum())
    if not valid.any() or total <= 0:
        return 0.0, 0.0
    median = weighted_median(density[valid], areas[valid])
    return float(areas[valid & (density < median * DENSITY_FALL)].sum() / total), median


def weighted_median(values, weights):
    order = np.argsort(values)
    reached = np.cumsum(weights[order])
    return float(values[order][min(np.searchsorted(reached, 0.5 * reached[-1]), len(values) - 1)])


def real_textures(blobs, fetched, folder, skin):
    """The texture files a measure reads fetched over their stand-ins (verify fetches a USD entry's layers only)."""
    shas = {}
    for record in fetched:
        kind, entry_id, version = entries.parse_address(record["address"])
        base = pathlib.Path(folder) / entries.library_path(kind, entry_id, version, "")
        for name, described in record["files"].items():
            shas[str((base / name).resolve())] = described["sha256"]
    for path in skin.paths:
        target = pathlib.Path(path)
        sha = shas.get(str(target.resolve()))
        if target.stat().st_size == 0 and sha is not None:
            target.unlink()
            blobs.get(sha, target)


def measured(blobs, folder, fetched, record):
    """A fetched model entry's layout: {"sparse": share, "median": texels a metre (per texel of UV when its size
    cancels out)}, or None when it is not drawn from a USD file."""
    named = drawn_file(record)
    if named is None:
        return None
    kind, entry_id, version = entries.parse_address(record["address"])
    path = pathlib.Path(folder) / entries.library_path(kind, entry_id, version, named)
    if path.suffix in (".gltf", ".glb"):
        triangles, skin = gltf_collect(path)
    else:
        triangles, _, _, skin = mesh.collect(path)
    if len(set(skin.numbers[skin.numbers >= 0].tolist())) > 1:
        real_textures(blobs, fetched, folder, skin)
    share, median = sparse_share(triangles, skin, picture_sizes(skin))
    return {"sparse": round(share, 4), "median": round(median, 4)}


def faults(record, found):
    """A model's layout faults as (address, message)."""
    if found is None or found["sparse"] <= LEAST:
        return []
    return [(record["address"], f"layout: {found['sparse']:.1%} of its textured surface at under "
                                f"{DENSITY_FALL:.0%} of its own median texture detail, over {LEAST:.0%}")]


def model_records(root, addresses, withdrawn):
    """The model entries to measure: the addresses asked for, else every one (withdrawn ones only when asked); never
    one whose bytes are recorded as lost."""
    records = [record for record in entries.all_entries(root) if record["kind"] == "model" and "lost" not in record]
    if addresses:
        return [record for record in records if record["address"] in set(addresses)]
    return [record for record in records if withdrawn or record["state"] != "withdrawn"]


def measure_one(root, blobs, record):
    """(record, its measure or None, why it could not be fetched or read or None), its temp root removed after."""
    from pxr import Tf
    from score_lib import verify
    folder, fetched, why = verify.usd_fetched(root, blobs, record, READ_ROLES)
    try:
        return record, (None if why else measured(blobs, folder, fetched, record)), why
    except (Tf.ErrorException, OSError, ValueError, KeyError) as error:  # a file that does not open or parse
        return record, None, f"layout: {drawn_file(record)} does not open ({str(error).strip()[:200]})"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check(root, blobs, records):
    """Each record's layout measured side by side (verify.READERS at once): (failures as (address, message), {address:
    its measure, its state and why it could not be fetched, if so})."""
    from score_lib import verify
    failures, measures = [], {}
    with concurrent.futures.ThreadPoolExecutor(verify.READERS) as pool:
        for record, found, why in pool.map(lambda each: measure_one(root, blobs, each), records):
            measures[record["address"]] = {"state": record["state"], **(found or {}), **({"error": why} if why else {})}
            failures += [(record["address"], why)] if why else faults(record, found)
    return failures, measures


def run(root, blobs, addresses=None, withdrawn=False, report=None):
    """`score-lib layout`: the model entries' failures (check), each measure written to `report` when given."""
    failures, measures = check(root, blobs, model_records(root, addresses, withdrawn))
    if report:
        pathlib.Path(report).write_text(json.dumps(measures, indent=1, sort_keys=True) + "\n")
    return failures
