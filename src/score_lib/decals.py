"""`score-lib decals cut --from surface/<id>@<v> [--subset NAME] [--apply] [--save] [--report FILE]`: the printed marks
of a surface painted for one model (an adopted set's `painted_for` surface) cut out as draft decal entries (score #164).

A decal is a `surface` entry of family `decal` and recipe `decal`: `decal.png` (colour with transparency), optionally
`normal.png` and `orm.png` crops of the same box, and `surface.json` {variant, family, recipe, size_m (metres), class
(the material class word, score #161 rule 6), opacity, ink (`one`: a single ink kept as transparency only, a scene
palette token gives its colour; `many`: its own colours), ink_lab, words, from {surface, box_px, px_per_m}, maps,
source}. Its licence block and credit come over from the surface it was cut from, its change marked. A decal is
laid over a part's base finish by score's placement record and never changes the part's physics material.

The method (score #164's prototype, measured on five NVIDIA models): the faces the surface is bound to on its model
(the model entry requiring it, its Materials naming their surface by `score:surface`) give its texel density (UV
pixels against world area); each UV island's base colour is its dominant colour (one base for a trim sheet, whose
islands cover under a fifth of the map); a pixel's print is a ramp of its colour difference from its base; marks
closer than 8 mm join into one decal; a region is dropped when under 4 mm, as wide as the sheet (a trim strip), a
second finish (over a quarter of the part, mostly one colour), faint (not solid ink) or soft-edged or of uneven
roughness (wear and shading, not print); repeats (the same picture by its perceptual hash) are kept once.

Without `apply` the cut is read and reported, nothing written; with `apply` the decals' bytes go into the blob store
and their draft entries are written; with `save` they are committed and pushed under the handover's lock. Bytes pass
through a cache folder emptied after each surface (the library's disk rule).
"""
import json
import os
import pathlib
import re
import shutil
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from score_lib import entry as entries
from score_lib import fetch, handover, index
from score_lib.blobs import MissingBlob, file_sha256, put_all

LOW_DE, HIGH_DE = 10.0, 22.0  # print transparency ramps from its base (colour difference 10) to full (22)
WEAR_DE = 4.0                 # a difference of 4 to 10 from its base: wear, dirt and scuffs, never print
MERGE_M = 0.008               # marks closer than 8 mm join into one decal (letters into a word)
LEAST_M = 0.004               # a decal is at least 4 mm across
MOST_SHARE = 0.25             # a region over a quarter of the part, mostly one colour, is a second finish
FLAT_SHARE = 0.7              # ... mostly: this share of it within LOW_DE of its own main colour
SOLID = 0.55                  # mean transparency inside a kept region: print is solid, scuffs are faint
CRISP = 0.15                  # least median transparency step per pixel on a region's edge: print has hard edges
EDGE_PX = 3                   # pixels a region's box grows by to read its edge's step
ROUGH_SPREAD = 0.1            # most spread of roughness inside a print: worn metal's varies, a print's does not
ONE_INK = 0.9                 # share of a print's pixels within ONE_INK_DE of its main colour for a single ink
ONE_INK_DE = 15.0
SAME_BITS = 16                # perceptual hashes (256 bits) this close, sizes within SAME_SIZE: the same picture
SAME_SIZE = 0.1
TRIM_SHARE = 0.2              # islands covering under this share of the map: a trim sheet (one base)
LEAST_ISLAND = 400            # pixels an island needs for a base of its own
CLASSES = ("plastic", "metal", "rubber", "glass", "paper", "cardboard", "wood", "fabric", "ceramic", "leather",
           "stone", "concrete", "organic")  # score #161 rule 6: NVIDIA's material class words
OPACITIES = ("opaque", "trans", "emissive")
SURFACE_ATTRIBUTE = "score:surface"
SRGB_TO_XYZ = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
WHITE = np.array([0.95047, 1.0, 1.08883], np.float32)
CUT_WHAT = "cut out as a decal (score-lib decals cut, score #164)"
LIBRARY_BRANCH = "main"


class Refused(ValueError):
    """A surface that cannot be cut into decals as given."""


def lab_of(rgb):
    """CIE Lab (D65) of sRGB values in 0..1, last axis the channels."""
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4).astype(np.float32)
    xyz = (linear @ SRGB_TO_XYZ.T) / WHITE
    bend = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * bend[..., 1] - 16, 500 * (bend[..., 0] - bend[..., 1]),
                     200 * (bend[..., 1] - bend[..., 2])], axis=-1).astype(np.float32)


def bound_faces(prim, surface_id, subset):
    """The face indices of one mesh bound to a Material naming `surface_id` by score:surface (in the subset named
    `subset` only, when given), and that Material's prim; ([], None) when none is."""
    from pxr import UsdGeom, UsdShade
    binding = UsdShade.MaterialBindingAPI(prim)
    mesh = UsdGeom.Mesh(prim)
    count = len(mesh.GetFaceVertexCountsAttr().Get() or [])
    pieces = [(piece.GetPrim().GetName(), UsdShade.MaterialBindingAPI(piece.GetPrim()).ComputeBoundMaterial()[0],
               [face for face in piece.GetIndicesAttr().Get() or [] if 0 <= face < count])
              for piece in binding.GetMaterialBindSubsets()]
    taken = {face for _, _, faces in pieces for face in faces}
    pieces.append((None, binding.ComputeBoundMaterial()[0], [face for face in range(count) if face not in taken]))
    for name, material, faces in pieces:
        if not material or (subset and name != subset):
            continue
        named = material.GetPrim().GetAttribute(SURFACE_ATTRIBUTE).Get()
        if named and entries.parse_address(named)[1] == surface_id:
            return faces, material.GetPrim()
    return [], None


def mesh_triangles(prim, faces, uv_name, units):
    """(per-triangle UV corners, per-triangle world corners in metres) of a mesh's faces, each fanned."""
    from pxr import Usd, UsdGeom
    mesh = UsdGeom.Mesh(prim)
    counts = np.array(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=np.int64)
    indices = np.array(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
    points = np.array(mesh.GetPointsAttr().Get() or [], dtype=np.float64).reshape(-1, 3)
    xform = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    world = (np.hstack([points, np.ones((len(points), 1))]) @ xform)[:, :3] * units
    primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar(uv_name)
    if not primvar or not primvar.HasValue():
        return np.zeros((0, 3, 2)), np.zeros((0, 3, 3))
    uv = np.array(primvar.ComputeFlattened(), dtype=np.float64).reshape(-1, 2)
    per_corner = primvar.GetInterpolation() == "faceVarying"
    starts = np.cumsum(counts) - counts
    tris_uv, tris_xyz = [], []
    for face in faces:
        corners = range(starts[face], starts[face] + counts[face])
        uvs = [uv[corner] if per_corner else uv[indices[corner]] for corner in corners]
        xyz = [world[indices[corner]] for corner in corners]
        for step in range(1, counts[face] - 1):
            tris_uv.append([uvs[0], uvs[step], uvs[step + 1]])
            tris_xyz.append([xyz[0], xyz[step], xyz[step + 1]])
    return np.array(tris_uv).reshape(-1, 3, 2), np.array(tris_xyz).reshape(-1, 3, 3)


def subset_faces(stage, surface_id, subset=None):
    """(per-triangle UV corners, per-triangle world corners in metres) of every face of a stage bound to the surface
    `surface_id` (in the GeomSubset `subset` only, when given)."""
    from pxr import Usd, UsdGeom
    from score_lib.take_in_hub import uv_name_of
    units = UsdGeom.GetStageMetersPerUnit(stage)
    tris_uv, tris_xyz = [np.zeros((0, 3, 2))], [np.zeros((0, 3, 3))]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        faces, material = bound_faces(prim, surface_id, subset)
        if faces:
            found_uv, found_xyz = mesh_triangles(prim, faces, uv_name_of(material), units)
            tris_uv.append(found_uv)
            tris_xyz.append(found_xyz)
    return np.concatenate(tris_uv), np.concatenate(tris_xyz)


def texel_density(tris_uv, tris_xyz, side):
    """Map pixels per metre on the faces: the square root of their UV pixel area over their world area."""
    edges_xyz = np.cross(tris_xyz[:, 1] - tris_xyz[:, 0], tris_xyz[:, 2] - tris_xyz[:, 0])
    world = np.linalg.norm(edges_xyz, axis=1) / 2
    one, two = (tris_uv[:, 1] - tris_uv[:, 0]) * side, (tris_uv[:, 2] - tris_uv[:, 0]) * side
    pixels = np.abs(one[:, 0] * two[:, 1] - one[:, 1] * two[:, 0]) / 2
    keep = world > 1e-12
    if not keep.any() or pixels[keep].sum() <= 0:
        raise Refused("its faces have no UV area or no world area: no texel density")
    return float(np.sqrt(pixels[keep].sum() / world[keep].sum()))


def uv_coverage(tris_uv, side):
    """The map's pixels the faces cover (a tiling island brought into the unit square)."""
    canvas = Image.new("1", (side, side), 0)
    pen = ImageDraw.Draw(canvas)
    for tri in tris_uv:
        tri = tri - np.floor(tri.min(axis=0))
        pen.polygon([(float(u * side), float((1 - v) * side)) for u, v in tri], fill=1)
    return np.asarray(canvas, dtype=bool)


def base_colour(lab, where):
    """The dominant colour (Lab) of the pixels `where`: the largest of a few k-means groups."""
    from scipy.cluster.vq import kmeans2
    sample = lab[where].astype(np.float64)
    sample = sample[np.random.default_rng(0).choice(len(sample), min(len(sample), 40000), replace=False)]
    groups = min(6, len(sample))
    centres, labels = kmeans2(sample, groups, seed=0, minit="++")
    return centres[np.bincount(labels, minlength=groups).argmax()].astype(np.float32)


def island_bases(lab, where, share):
    """Each pixel's base colour: per UV island its own dominant colour; one base for a trim sheet (islands cover
    under TRIM_SHARE of the map: its marks, not its islands, matter)."""
    from scipy import ndimage
    bases = np.broadcast_to(base_colour(lab, where), lab.shape).copy()
    if share < TRIM_SHARE:
        return bases
    islands, _ = ndimage.label(ndimage.binary_dilation(where, iterations=2))
    for island, box in enumerate(ndimage.find_objects(islands), start=1):
        inside = (islands[box] == island) & where[box]
        if inside.sum() >= LEAST_ISLAND:
            bases[box][inside] = base_colour(lab[box], inside)
    return bases


def print_alpha(lab, bases, where):
    """(transparency of the print per pixel, the share of `where` that is wear: a small difference from its base)."""
    from scipy import ndimage
    delta = np.linalg.norm(ndimage.gaussian_filter(lab, (1, 1, 0)) - bases, axis=2)
    alpha = (np.clip((delta - LOW_DE) / (HIGH_DE - LOW_DE), 0, 1) * where).astype(np.float32)
    wear = float(((delta > WEAR_DE) & (delta <= LOW_DE) & where).sum() / max(1, where.sum()))
    return alpha, wear


def grown(box, by, shape):
    """A box grown by `by` pixels on every side, inside an image of `shape`."""
    return tuple(slice(max(0, span.start - by), min(size, span.stop + by)) for span, size in zip(box, shape))


def crisp_edge(alpha, box, part, roughness=None):
    """Whether a region reads as print, not wear or shading: its transparency steps hard at its edge (median step
    per pixel at least CRISP, read on the box grown by EDGE_PX so the step is inside it) and, given a roughness map,
    its roughness is even inside it (spread under ROUGH_SPREAD)."""
    from scipy import ndimage
    window = grown(box, EDGE_PX, alpha.shape)
    placed = np.zeros(tuple(span.stop - span.start for span in window), bool)
    placed[tuple(slice(inner.start - outer.start, inner.stop - outer.start) for inner, outer in zip(box, window))] = part
    inked = ndimage.binary_dilation(placed, iterations=EDGE_PX - 1) & (alpha[window] > 0.05)
    edge = inked & ~ndimage.binary_erosion(inked)
    if not edge.any():
        return False
    rows, cols = np.gradient(alpha[window])
    if float(np.median(np.hypot(rows, cols)[edge])) < CRISP:
        return False
    return roughness is None or float(roughness[box][part & (alpha[box] > 0.05)].std()) < ROUGH_SPREAD


def mark_regions(alpha, where, density, lab, roughness=None):
    """(kept [{box, part, solid}], dropped {why: count}) of the print's regions: marks joined at MERGE_M, each kept
    region at least LEAST_M across, narrower than the sheet, not a second finish, solid and crisp."""
    from scipy import ndimage
    side = alpha.shape[1]
    merge = max(1, int(round(MERGE_M * density)))
    joined = ndimage.binary_closing(alpha > 0.5, structure=np.ones((merge, merge)))
    labels, _ = ndimage.label(joined)
    kept, dropped = [], {"small": 0, "strip": 0, "second finish": 0, "faint": 0, "soft": 0}
    for region, box in enumerate(ndimage.find_objects(labels), start=1):
        part = labels[box] == region
        tall, wide = part.shape
        if min(tall, wide) < LEAST_M * density:
            dropped["small"] += 1
            continue
        if wide >= 0.95 * side:
            dropped["strip"] += 1
            continue
        if part.sum() > MOST_SHARE * where.sum():
            own = base_colour(lab[box], part)
            if (np.linalg.norm(lab[box][part] - own, axis=1) <= LOW_DE).mean() >= FLAT_SHARE:
                dropped["second finish"] += 1
                continue
        inked = part & (alpha[box] > 0.05)
        solid = float(alpha[box][inked].mean()) if inked.any() else 0.0
        if solid < SOLID:
            dropped["faint"] += 1
            continue
        if not crisp_edge(alpha, box, part, roughness):
            dropped["soft"] += 1
            continue
        kept.append({"box": box, "part": part, "solid": round(solid, 2)})
    return kept, dropped


def ink_of(lab, inked):
    """(`one` or `many`, the main ink's Lab) of a print's inked pixels."""
    main = base_colour(lab, inked)
    near = np.linalg.norm(lab[inked] - main, axis=1) <= ONE_INK_DE
    return ("one" if near.mean() >= ONE_INK else "many"), [round(float(value), 1) for value in main]


def picture_hash(crop):
    """A 256-bit perceptual hash of an RGBA crop: its grey times transparency at 16x16, above its mean."""
    grey = np.asarray(Image.fromarray(crop).convert("LA").resize((16, 16), Image.BILINEAR), dtype=np.float32)
    shade = grey[..., 0] * grey[..., 1] / 255
    return (shade > shade.mean()).ravel()


def deduped(cuts):
    """The cuts with every repeat (a perceptual hash within SAME_BITS, a size within SAME_SIZE) folded into the
    first, its `repeats` counted."""
    kept = []
    for cut in cuts:
        for first in kept:
            same_size = all(abs(mine - theirs) <= SAME_SIZE * max(mine, theirs)
                            for mine, theirs in zip(cut["size_m"], first["size_m"]))
            if same_size and int((cut["hash"] != first["hash"]).sum()) <= SAME_BITS:
                first["repeats"] += 1
                break
        else:
            kept.append({**cut, "repeats": 1})
    return kept


def class_of(surface):
    """(material class, opacity) of a surface: its material name's words (`<opacity>__<class>__<asset>`), else its
    nonvisual base or physics type; Refused when none is a class word."""
    words = re.sub(r"^m_", "", surface.get("material") or "").split("__")
    opacity = words[0] if words and words[0] in OPACITIES else "opaque"
    for found in [*words[1:2], (surface.get("nonvisual") or {}).get("base"), (surface.get("physics") or {}).get("type")]:
        if found in CLASSES:
            return found, opacity
    raise Refused(f"its material {surface.get('material')!r} names no material class of {', '.join(CLASSES)}")


def short_name(surface):
    """The source material's own words as an id part (its opacity and class words left off)."""
    words = re.sub(r"^m_", "", surface.get("material") or "surface").split("__")
    name = words[-1] if len(words) > 1 else words[0]
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", name.lower())).strip("_")[:40] or "surface"


def crop_of(image, box, scale):
    """A box of a map read at another resolution (scale: its pixels per albedo pixel)."""
    rows, cols = box
    return image[int(rows.start * scale):max(int(rows.stop * scale), int(rows.start * scale) + 1),
                 int(cols.start * scale):max(int(cols.stop * scale), int(cols.start * scale) + 1)]


def decal_pictures(rgb, alpha, lab, region, maps, folder, number):
    """One region's pictures written into `folder/<number>/`: decal.png (a single ink as white with its
    transparency), and crops of the normal and orm maps when the surface has them; (ink, ink_lab, its files)."""
    box, part = region["box"], region["part"]
    shown = alpha[box] * part
    ink, ink_lab = ink_of(lab[box], part & (shown > 0.5))
    colour = np.ones_like(rgb[box]) if ink == "one" else rgb[box]
    crop = (np.dstack([colour, shown]) * 255).round().astype(np.uint8)
    out = pathlib.Path(folder) / f"{number:03d}"
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(crop, "RGBA").save(out / "decal.png")
    files = {"decal.png": out / "decal.png"}
    for role, image in maps.items():
        cropped = crop_of(image, box, image.shape[0] / rgb.shape[0])
        Image.fromarray(cropped).save(out / f"{role}.png")
        files[f"{role}.png"] = out / f"{role}.png"
    return ink, ink_lab, files, crop


def decal_record(entry_id, source, surface, cut, words):
    """A decal's surface.json."""
    material_class, opacity = class_of(surface)
    return {"variant": entry_id, "family": "decal", "recipe": "decal", "size_m": cut["size_m"],
            "class": material_class, "opacity": opacity, "ink": cut["ink"], "ink_lab": cut["ink_lab"], "words": words,
            "from": {"surface": source["address"], "box_px": cut["box_px"], "px_per_m": cut["px_per_m"]},
            "maps": {name.removesuffix(".png"): name for name in sorted(cut["files"])},
            "source": surface.get("source")}


def credit_of(licence, mark, source):
    """A decal's credit: its source's credit (less its own change note) naming the mark and the cut as a change."""
    carried = licence["credit"].split("; changed:")[0]
    return f"{mark} cut from {carried}; changed: cut out as a decal from {source}'s map (score-lib decals cut)"


def decal_entry(entry_id, source, cut, files):
    """A decal's draft entry record, its files {name: (sha256, bytes)}; its licence carried from its source."""
    provenance = source["provenance"]
    carried = [row for row in provenance.get("made_with", []) if row.get("carried")]
    record = {
        "schema": entries.SCHEMA, "address": entries.address_of("surface", entry_id, 1), "kind": "surface",
        "id": entry_id, "version": 1, "title": f"{cut['mark']} (decal)", "state": "draft", "picture": None,
        "picture_reason": "a decal cut by score-lib decals cut: no picture made yet",
        "files": {name: {"sha256": sha, "bytes": size, "role": "record" if name.endswith(".json") else "texture"}
                  for name, (sha, size) in sorted(files.items())},
        "usd": None, "requires": [], "seen_in": [],
        "provenance": {"world": provenance.get("world", "world1"), "taken_from": provenance.get("taken_from"),
                       "files_from": {name: ("written by score-lib decals cut" if name.endswith(".json") else
                                             f"cut from {source['address']}'s maps, box {cut['box_px']} px")
                                      for name in sorted(files)},
                       "made_with": sorted(carried + [{"by": "score", "what": CUT_WHAT}],
                                           key=lambda row: (row["by"], row["what"]))},
        "tags": {"family": ["decal"], "class": [cut["class"]], "ink": [cut["ink"]], "cut_from": [source["id"]],
                 "size_m": ["{0:.3f}x{1:.3f}".format(*cut["size_m"])], "words": cut["words"]}}
    if provenance.get("commit"):
        record["provenance"]["commit"] = provenance["commit"]
    if source.get("licence"):
        record["licence"] = {**source["licence"],
                             "credit": credit_of(source["licence"], cut["mark"], source["address"])}
    return record


def model_of(root, address):
    """The newest model entry requiring a surface address; Refused when none does."""
    found = [record for record in entries.all_entries(root, "model") if address in record.get("requires", [])]
    if not found:
        raise Refused(f"{address}: no model entry requires it, so its faces cannot be read")
    return max(found, key=lambda record: (record["id"], record["version"]))


def read_map(path, mode):
    """A map as a float array in 0..1."""
    return np.asarray(Image.open(path).convert(mode), dtype=np.float32) / 255


def cut_maps(rgb, others, tris_uv, tris_xyz, folder):
    """The cut of one surface's maps (rgb the albedo, others {normal, orm}: arrays) on its faces: {px_per_m, base_lab,
    wear_share, uv_covered, dropped, cuts: [{box_px, size_m, ink, ink_lab, solid, files, hash, px_per_m}]}."""
    side = rgb.shape[0]
    density = texel_density(tris_uv, tris_xyz, side)
    covered = uv_coverage(tris_uv, side)
    share = float(covered.mean())
    where = covered if share >= TRIM_SHARE else np.ones_like(covered)
    lab = lab_of(rgb)
    bases = island_bases(lab, where, share)
    alpha, wear = print_alpha(lab, bases, where)
    roughness = None
    if "orm" in others:
        orm = others["orm"]
        roughness = np.asarray(Image.fromarray((orm[..., 1] * 255).astype(np.uint8)).resize(
            (rgb.shape[1], rgb.shape[0])), dtype=np.float32) / 255
    regions, dropped = mark_regions(alpha, where, density, lab, roughness)
    maps = {role: (image * 255).round().astype(np.uint8) for role, image in others.items()}
    cuts = []
    for number, region in enumerate(regions):
        ink, ink_lab, files, crop = decal_pictures(rgb, alpha, lab, region, maps, folder, number)
        rows, cols = region["box"]
        cuts.append({"box_px": [cols.start, rows.start, cols.stop - cols.start, rows.stop - rows.start],
                     "size_m": [round((cols.stop - cols.start) / density, 4), round((rows.stop - rows.start) / density, 4)],
                     "ink": ink, "ink_lab": ink_lab, "solid": region["solid"], "files": files,
                     "hash": picture_hash(crop), "px_per_m": round(density, 1)})
    return {"px_per_m": round(density, 1), "base_lab": [round(float(v), 1) for v in base_colour(lab, where)],
            "wear_share": round(wear, 3), "uv_covered": round(share, 3), "dropped": dropped, "cuts": deduped(cuts)}


def surface_maps(blobs, root, record, folder):
    """(surface.json, albedo as RGB array, {normal, orm} arrays it has) of a surface entry, fetched into `folder`."""
    names = [name for name in ("surface.json", "albedo.png", "normal.png", "orm.png") if name in record["files"]]
    if "albedo.png" not in names:
        raise Refused(f"{record['address']}: holds no albedo.png")
    fetch.fetch(root, blobs, [record["address"]], folder, names=names)
    path = lambda name: fetch.destination(folder, record, name)  # noqa: E731
    surface = json.loads(path("surface.json").read_text()) if "surface.json" in names else {}
    others = {name.removesuffix(".png"): read_map(path(name), "RGB") for name in ("normal.png", "orm.png")
              if name in names}
    return surface, read_map(path("albedo.png"), "RGB"), others


def model_faces(blobs, root, model, surface_id, subset, folder):
    """The surface's faces on its model, the model's USD layers fetched into `folder`."""
    from pxr import Usd
    names = [name for name, described in model["files"].items() if described["role"] == "usd"]
    fetch.fetch(root, blobs, [model["address"]], folder, names=names)
    stage = Usd.Stage.Open(str(fetch.destination(folder, model, model["usd"]["root"])))
    tris_uv, tris_xyz = subset_faces(stage, surface_id, subset)
    if not len(tris_uv):
        raise Refused(f"{model['address']}: no face is bound to {surface_id}" + (f" in {subset}" if subset else ""))
    return tris_uv, tris_xyz


def cut_surface(root, blobs, address, subset, cache):
    """One painted surface cut: (its report row, its drafts [(record, {name: path})]) with the pictures in `cache`."""
    source = entries.load_address(root, address)
    if source is None:
        raise Refused(f"{address}: the library holds no such entry")
    if source["kind"] != "surface" or not source.get("tags", {}).get("painted_for"):
        raise Refused(f"{address}: decals are cut from a surface painted for one model (a painted_for tag)")
    surface, rgb, others = surface_maps(blobs, root, source, cache / "surface")
    tris_uv, tris_xyz = model_faces(blobs, root, model_of(root, address), source["id"], subset, cache / "model")
    material_class, _ = class_of(surface)
    found = cut_maps(rgb, others, tris_uv, tris_xyz, cache / "cut")
    name = short_name(surface)
    drafts, rows = [], []
    for number, cut in enumerate(found["cuts"]):
        sha = file_sha256(cut["files"]["decal.png"])
        entry_id = f"decal_{name}_{sha[:8]}"
        cut.update({"class": material_class, "mark": f"mark {number + 1} of {surface.get('material') or name}"})
        words = sorted(set(word for word in re.split(r"[_\W]+", name) if len(word) > 1))
        cut["words"] = words
        record_path = cut["files"]["decal.png"].with_name("surface.json")
        record_path.write_text(entries.dumps(decal_record(entry_id, source, surface, cut, words)))
        files = {**cut["files"], "surface.json": record_path}
        sized = {file_name: (file_sha256(path), path.stat().st_size) for file_name, path in files.items()}
        drafts.append((decal_entry(entry_id, source, cut, sized), files))
        rows.append({"id": entry_id, "size_m": cut["size_m"], "ink": cut["ink"], "class": material_class,
                     "box_px": cut["box_px"], "repeats": cut["repeats"], "solid": cut["solid"]})
    report = {"source": address, **{key: found[key] for key in ("px_per_m", "base_lab", "wear_share", "uv_covered",
                                                                 "dropped")}, "decals": rows}
    return report, drafts


def written(root, blobs, drafts):
    """The drafts' bytes into the blob store together and their entries written; the addresses (an id the library
    already holds is left as it is)."""
    fresh = [(record, files) for record, files in drafts if not entries.versions(root, "surface", record["id"])]
    put_all(blobs, [(record["files"][name]["sha256"], path) for record, files in fresh for name, path in files.items()])
    for record, _ in fresh:
        entries.write(root, record)
    return [record["address"] for record, _ in fresh]


def saved(root, addresses):
    """The decals written committed and pushed under the handover's lock; the save block."""
    wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
    with handover.locked(root, wait):
        index.rebuild(root)
        folders = sorted({f"entries/surface/{entries.parse_address(address)[1]}" for address in addresses})
        message = f"decals: {len(addresses)} decals cut from painted surfaces as drafts (score #164)"
        return handover.saved(root, message, (*folders, "index"), LIBRARY_BRANCH)


def cache_root():
    """The cache folder pictures and models pass through (emptied after each surface)."""
    base = pathlib.Path(os.environ.get("SCORE_LIB_CACHE") or pathlib.Path.home() / ".cache/score-lib")
    base.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix="decals-", dir=base))


def cut_all(root, blobs, addresses, subset=None, apply=False, save=False):
    """`decals cut`: every surface cut; {cut: [report rows], refused: [{source, why}]}, with `apply` also {written:
    [address]}, with `save` also {saved: block}."""
    if save and not apply:
        raise Refused("--save saves what --apply writes: give both")
    report = {"cut": [], "refused": [], "written": []}
    for address in addresses:
        cache = cache_root()
        try:
            row, drafts = cut_surface(root, blobs, address, subset, cache)
            report["cut"].append(row)
            if apply:
                report["written"] += written(root, blobs, drafts)
        except (Refused, fetch.MissingFile, MissingBlob) as why:
            report["refused"].append({"source": address, "why": str(why)})
        finally:
            shutil.rmtree(cache, ignore_errors=True)
    if apply:
        index.rebuild(root)
    if save and report["written"]:
        report["saved"] = saved(root, report["written"])
    return report


def decal_faults(surface, picture):
    """A decal's faults: its decal.png has transparency, its size is positive metres, its class a class word."""
    faults = []
    if picture.mode not in ("RGBA", "LA") or picture.getchannel("A").getextrema()[0] == 255:
        faults.append("decal.png has no transparency")
    size = surface.get("size_m")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(side, (int, float)) and side > 0
                                                               for side in size)):
        faults.append(f"size_m {size!r} is not two positive sides in metres")
    if surface.get("class") not in CLASSES:
        faults.append(f"class {surface.get('class')!r} is not one of {', '.join(CLASSES)}")
    return faults


def check_decal(blobs, record):
    """One decal entry's faults, its surface.json and decal.png read through a scratch folder."""
    if "surface.json" not in record["files"] or "decal.png" not in record["files"]:
        return ["a decal holds decal.png and surface.json"]
    with tempfile.TemporaryDirectory() as folder:
        found = {}
        for name in ("surface.json", "decal.png"):
            found[name] = pathlib.Path(folder) / name
            blobs.get(record["files"][name]["sha256"], found[name])
        surface = json.loads(found["surface.json"].read_text())
        with Image.open(found["decal.png"]) as picture:
            picture.load()
            return decal_faults(surface, picture)


def is_decal(record):
    """Whether an entry is a decal (a surface tagged family decal)."""
    return record["kind"] == "surface" and "decal" in record.get("tags", {}).get("family", [])
