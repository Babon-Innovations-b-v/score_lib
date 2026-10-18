"""A model's base colour picture judged where its UVs lay it: the room sweep's palette rule (the share of its texels
within reach of the colours its places' library surfaces bake to, and how blotchy it is) and its seams (the colour
step across a UV cut where the surface itself runs on smoothly)."""
import functools

import numpy as np
from PIL import Image

from score_lib.quality import mesh as meshes

# A seam is judged only where the surface runs on: the two faces' normals within this many degrees.
SMOOTH_DEGREES = 20.0
# A seam shows where the colours either side differ by more than this (CIE76).
SEAM_DELTA_E = 10.0
# Where each side's colour is read: this share of the way from the cut's middle towards its face's far corner.
INSIDE = 0.15


@functools.lru_cache(maxsize=4)
def rgb(path):
    """A picture's pixels as (h, w, 3) uint8."""
    with Image.open(path) as picture:
        return np.asarray(picture.convert("RGB"))


def sweep_module(context):
    context.repo_tool("tools/props/library")
    import sweep
    return sweep


def on_palette(model, palette, context):
    """The sweep's judgement of the texels under the model's UVs ({on_palette, lightness, blotchy}) and the off
    palette texels as a mask over the MASK_SIDE picture, with that picture; None without a colour map or UVs."""
    path = model.maps.get("diffuseColor")
    if path is None or model.uvs is None or palette is None:
        return None
    sweep = sweep_module(context)
    corner_uvs = np.nan_to_num(model.uvs.reshape(-1, 2))
    mask = sweep.islands_mask(corner_uvs, np.arange(len(corner_uvs)).reshape(-1, 3))
    shown = np.asarray(Image.fromarray(rgb(path)).resize((sweep.MASK_SIDE, sweep.MASK_SIDE), Image.BILINEAR))
    colours = sweep.linear_of(shown[mask])
    grey = shown.astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    spreads = tile_spreads(grey, mask, sweep.TILE)
    judged = sweep.judged((colours, spreads), palette)
    off = np.zeros(mask.shape, dtype=bool)
    if mask.any():
        off[mask] = nearest_distance(sweep.lab(colours), palette) >= sweep.NEAR
    return judged, off, shown


def nearest_distance(points, palette, chunk=2048):
    """Each L*a*b* point's distance to its nearest palette colour, a chunk at a time."""
    found = np.empty(len(points))
    for start in range(0, len(points), chunk):
        part = points[start:start + chunk]
        found[start:start + chunk] = np.min(np.linalg.norm(part[:, None, :] - palette[None, :, :], axis=2), axis=1)
    return found


def tile_spreads(grey, mask, tile):
    """The lightness spread (0..255) of every tile x tile window wholly under the UV islands."""
    rows, columns = grey.shape[0] // tile, grey.shape[1] // tile
    blocks = grey[:rows * tile, :columns * tile].reshape(rows, tile, columns, tile)
    inside = mask[:rows * tile, :columns * tile].reshape(rows, tile, columns, tile).all(axis=(1, 3))
    return list(blocks.std(axis=(1, 3))[inside])


def seams(model, context):
    """(metres of showing seam, metres of smooth UV cut, the step a seam must pass, the triangles on a showing seam
    as a mask), or None without a colour map or UVs. A cut shows where the colour either side steps more than
    SEAM_DELTA_E and more than the 90th percentile step across the model's uncut smooth edges read the same way (its
    own texture's grain)."""
    path = model.maps.get("diffuseColor")
    if path is None or model.uvs is None:
        return None
    sweep = sweep_module(context)
    edges, uses, sides = meshes.edges_of(model)
    pairs = paired_sides(sides, uses)
    if not len(pairs):
        return 0.0, 0.0, SEAM_DELTA_E, np.zeros(len(model.triangles), dtype=bool)
    (first_triangle, first_side), (second_triangle, second_side) = pairs[:, 0].T, pairs[:, 1].T
    cut = uv_cut(model, first_triangle, first_side, second_triangle, second_side)
    smooth = facing_angle(model, first_triangle, second_triangle) <= SMOOTH_DEGREES
    picture = rgb(path)

    def steps_of(chosen):
        first = sweep.lab(sweep.linear_of(read_inside(model, picture, first_triangle[chosen], first_side[chosen])))
        second = sweep.lab(sweep.linear_of(read_inside(model, picture, second_triangle[chosen], second_side[chosen])))
        return np.linalg.norm(first - second, axis=1)

    grain = steps_of(~cut & smooth)
    least = max(SEAM_DELTA_E, float(np.percentile(grain, 90)) if len(grain) else 0.0)
    chosen = cut & smooth
    steps = steps_of(chosen)
    lengths = meshes.edge_lengths(model, edges[sides[first_triangle[chosen], first_side[chosen]]])
    showing = steps > least
    flagged = np.zeros(len(model.triangles), dtype=bool)
    flagged[first_triangle[chosen][showing]] = True
    flagged[second_triangle[chosen][showing]] = True
    return float(lengths[showing].sum()), float(lengths.sum()), least, flagged


def paired_sides(sides, uses):
    """(p, 2, 2) the two (triangle, side) of every edge exactly two triangles use."""
    flat = sides.reshape(-1)
    order = np.argsort(flat, kind="stable")
    shared = uses[flat[order]] == 2
    order = order[shared]
    pairs = order.reshape(-1, 2)
    return np.stack([pairs // 3, pairs % 3], axis=2)


def uv_cut(model, first_triangle, first_side, second_triangle, second_side):
    """Whether each shared edge has different UVs on its two triangles (matched by the welded points)."""
    first_ends = side_corners(first_side)
    second_ends = side_corners(second_side)
    first_points = model.triangles[first_triangle[:, None], first_ends]
    second_points = model.triangles[second_triangle[:, None], second_ends]
    swapped = first_points[:, 0] != second_points[:, 0]
    second_ends = np.where(swapped[:, None], second_ends[:, ::-1], second_ends)
    first_uv = model.uvs[first_triangle[:, None], first_ends]
    second_uv = model.uvs[second_triangle[:, None], second_ends]
    return np.abs(first_uv - second_uv).max(axis=(1, 2)) > 1e-5


def side_corners(side):
    """(n, 2) the corners of each triangle side: side 0 is corners 0-1, 1 is 1-2, 2 is 2-0."""
    return np.stack([side, (side + 1) % 3], axis=1)


def facing_angle(model, first_triangle, second_triangle):
    """Degrees between the two triangles' facings."""
    corners = model.points[model.triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    cosine = np.einsum("ij,ij->i", normals[first_triangle], normals[second_triangle])
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))


def read_inside(model, picture, triangle, side):
    """The colour (n, 3 in 0..255) a little inside each triangle from the middle of one of its sides."""
    ends = side_corners(side)
    far = (side + 2) % 3
    middle = model.uvs[triangle[:, None], ends].mean(axis=1)
    spot = middle + INSIDE * (model.uvs[triangle, far] - middle)
    height, width = picture.shape[:2]
    columns = (np.mod(spot[:, 0], 1.0) * (width - 1)).astype(int)
    rows = ((1.0 - np.mod(spot[:, 1], 1.0)) * (height - 1)).astype(int)
    return picture[rows, columns].astype(np.float64)
