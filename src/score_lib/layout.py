"""A model entry's texture layout against its own detail: the share of its textured surface whose texels a metre
fall under DENSITY_FALL of the model's own area-weighted median, score's `sparse` measure
(score/stages/surfaces/uv_layout.py, which judges each model the bake makes, #92), read here from the entry's USD so
every entry is held to it, old ones too. The October 2026 code-built models' bake gave each face toward the kit's
back a twentieth of the density, and those faces show the picture's unpainted black (score, 2026-11-23: 1,137
entries, a median 37% of each one's surface).

    score-lib layout [--address a] [--withdrawn] [--report FILE]

`score-lib verify` holds every live model entry to it (verify.check_layout); this command lists any set of entries,
withdrawn ones too, with each one's measure in the report."""
import concurrent.futures
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


def drawn_file(record):
    """The USD file a model entry is drawn from: its usd root, else its model.usdc; None when it has neither."""
    named = (record.get("usd") or {}).get("root") or "model.usdc"
    return named if named in record["files"] else None


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
    folder, fetched, why = verify.usd_fetched(root, blobs, record)
    try:
        return record, (None if why else measured(blobs, folder, fetched, record)), why
    except Tf.ErrorException as error:  # a USD file that does not open; verify's USD check names it too
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
