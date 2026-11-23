"""A mesh preview from the entry's own data: every mesh of a USD file drawn three-quarter orthographic with numpy and
a depth buffer (at each pixel the nearest triangle there, by its depth at that pixel; a painter's algorithm by
triangle middles let a big far face cover a small near part, 2026-11-23), each pixel coloured by the base colour
texture at its own UV (the triangle's corner UVs interpolated at that pixel; one sample at a triangle's centre hid
what a model wears, 2026-11-23) or by its material's constant colour, and shaded by its triangle's facing to a fixed
light. No renderer."""
import math
import pathlib

import numpy as np
from PIL import Image, ImageDraw
from pxr import Usd, UsdGeom, UsdShade

SIZE = 320
OVERSAMPLE = 2
BACKGROUND = (238, 236, 230)
GREY = np.array([0.6, 0.6, 0.6])
LIGHT = np.array([0.35, 0.8, 0.5]) / np.linalg.norm([0.35, 0.8, 0.5])
AZIMUTH = math.radians(-35.0)
ELEVATION = math.radians(28.0)
SHOWN_PURPOSES = (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)


def view_matrix():
    """Turn about y by the azimuth, then tilt about x by the elevation (a three-quarter view from above)."""
    turn = np.array([[math.cos(AZIMUTH), 0, math.sin(AZIMUTH)], [0, 1, 0],
                     [-math.sin(AZIMUTH), 0, math.cos(AZIMUTH)]])
    tilt = np.array([[1, 0, 0], [0, math.cos(ELEVATION), -math.sin(ELEVATION)],
                     [0, math.sin(ELEVATION), math.cos(ELEVATION)]])
    return tilt @ turn


def triangulated(counts, indices):
    """(triangle corner indices into `indices`) as an (n, 3) array, each polygon fanned from its first corner."""
    corners = []
    start = 0
    for count in counts:
        corners += [(start, start + step, start + step + 1) for step in range(1, count - 1)]
        start += count
    return np.array(corners, dtype=np.int64).reshape(-1, 3)


def bound_texture(prim):
    """(texture file path, or None) and the constant base colour of the prim's bound material."""
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    if not material:
        return None, GREY
    for shader_prim in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(shader_prim)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            return surface_colour(shader)
    return None, GREY


def surface_colour(surface):
    """(texture path, constant) behind a UsdPreviewSurface's diffuseColor: the texture file its connection reads, or
    its constant colour."""
    colour = surface.GetInput("diffuseColor")
    if not colour:
        return None, GREY
    if colour.HasConnectedSource():
        source = colour.GetConnectedSource()[0]
        texture_input = UsdShade.Shader(source.GetPrim()).GetInput("file")
        asset = texture_input.Get() if texture_input else None
        if asset is not None and asset.resolvedPath and pathlib.Path(asset.resolvedPath).is_file():
            return asset.resolvedPath, GREY
        return None, GREY
    if colour.Get() is not None:
        return None, np.array(colour.Get(), dtype=np.float64) ** (1 / 2.2)
    return None, GREY


def corner_uvs(mesh, corner_count, indices):
    """The st value of each face corner, or None when the mesh has none."""
    primvar = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    if not primvar or not primvar.HasValue():
        return None
    values = np.array(primvar.ComputeFlattened(), dtype=np.float64)
    interpolation = primvar.GetInterpolation()
    if interpolation == UsdGeom.Tokens.faceVarying and len(values) == corner_count:
        return values
    if interpolation in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        return values[indices]
    return None


def sampled(texture, uvs):
    """Colours of a texture at UV points (nearest texel, v up, wrapped)."""
    height, width = texture.shape[:2]
    columns = (np.mod(uvs[:, 0], 1.0) * (width - 1)).astype(int)
    rows = ((1.0 - np.mod(uvs[:, 1], 1.0)) * (height - 1)).astype(int)
    return texture[rows, columns]


def load_texture(path):
    """A texture file as an RGB float array."""
    with Image.open(path) as picture:
        return np.asarray(picture.convert("RGB"), dtype=np.float32) / 255.0


def mesh_triangles(prim, textures):
    """(world triangle corners (n, 3, 3), colours (n, 3), corner UVs (n, 3, 2), texture numbers (n)) of one mesh
    prim: a triangle's texture number indexes `textures` (the texture files' paths, each added as met), -1 for a
    constant colour."""
    mesh = UsdGeom.Mesh(prim)
    points = np.array(mesh.GetPointsAttr().Get() or [], dtype=np.float64)
    counts = np.array(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=np.int64)
    indices = np.array(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
    if not len(points) or not len(counts):
        return np.zeros((0, 3, 3)), np.zeros((0, 3)), np.zeros((0, 3, 2)), np.zeros(0, dtype=np.int64)
    matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    world = np.c_[points, np.ones(len(points))] @ matrix
    corners = triangulated(counts, indices)
    triangles = world[indices[corners]][:, :, :3]
    faces = np.repeat(np.arange(len(counts)), np.maximum(counts - 2, 0))
    uvs = corner_uvs(mesh, len(indices), indices)
    uvs = uvs[corners] if uvs is not None else None
    colours = np.tile(GREY, (len(triangles), 1))
    numbers = np.full(len(triangles), -1, dtype=np.int64)
    for bound, face_indices in material_parts(mesh, len(counts)):
        chosen = np.isin(faces, face_indices)
        path, constant = bound_texture(bound)
        colours[chosen] = constant
        if path is not None and uvs is not None:
            numbers[chosen] = texture_number(textures, path)
    if uvs is None:
        uvs = np.zeros((len(triangles), 3, 2))
    return triangles, colours, uvs, numbers


def texture_number(textures, path):
    """The number of the texture file `path` in the list `textures`, added when new."""
    if path not in textures:
        textures.append(path)
    return textures.index(path)


def material_parts(mesh, face_count):
    """[(prim whose bound material paints them, face indices)]: the mesh's own binding over every face, then each
    material subset (a per-face binding, as the library's models carry) over its own faces."""
    found = [(mesh.GetPrim(), np.arange(face_count))]
    for subset in UsdShade.MaterialBindingAPI(mesh.GetPrim()).GetMaterialBindSubsets():
        found.append((subset.GetPrim(), np.array(subset.GetIndicesAttr().Get() or [], dtype=np.int64)))
    return found


def drawn(prim):
    """Whether a mesh shows in a render: visible, and not a guide or proxy (such as a model's collision hull)."""
    imageable = UsdGeom.Imageable(prim)
    return imageable.ComputeVisibility() != UsdGeom.Tokens.invisible and imageable.ComputePurpose() in SHOWN_PURPOSES


class Skin:
    """What textures a set of triangles wears: each triangle's corner UVs (n, 3, 2), its texture's number (n; -1 for
    none) and the texture files' paths."""

    def __init__(self, uvs, numbers, paths):
        self.uvs, self.numbers, self.paths = uvs, numbers, paths


def collect(path):
    """Every triangle, constant colour and Skin of every mesh a render of a USD file shows (its composed stage)."""
    stage = Usd.Stage.Open(str(path))
    textures = []
    found = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):  # an instanced part's meshes too (SimReady models)
        if prim.IsA(UsdGeom.Mesh) and drawn(prim):
            found.append(mesh_triangles(prim, textures))
    if not found:
        return np.zeros((0, 3, 3)), np.zeros((0, 3)), stage, Skin(np.zeros((0, 3, 2)), np.zeros(0, dtype=int), [])
    triangles, colours, uvs, numbers = (np.concatenate(parts) for parts in zip(*found))
    return triangles, colours, stage, Skin(uvs, numbers, textures)


def up_rotation(stage):
    """A turn that stands a Z-up stage's content with Y up."""
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
        return np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    return np.eye(3)


def lighting(view_triangles):
    """Each triangle's brightness under the fixed light from its facing (both sides lit)."""
    normals = np.cross(view_triangles[:, 1] - view_triangles[:, 0], view_triangles[:, 2] - view_triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    return 0.42 + 0.58 * np.abs(normals @ (view_matrix() @ LIGHT))


def triangle_ids(view_triangles, canvas, middle, scale):
    """Which triangle covers each pixel (its number, -1 for none): the nearest one at that pixel by its own depth
    there (a depth buffer), so a big far face whose middle lies nearer never covers a small near part; a pixel within
    half a pixel of a triangle's edge counts as covered, so thin parts still draw."""
    ids = np.full((canvas, canvas), -1, dtype=np.int64)
    nearest = np.full((canvas, canvas), -np.inf)
    across = canvas / 2 + (view_triangles[:, :, 0] - middle[0]) * scale
    down = canvas / 2 - (view_triangles[:, :, 1] - middle[1]) * scale
    for number in range(len(view_triangles)):
        columns, rows, depths = across[number], down[number], view_triangles[number, :, 2]
        area = ((columns[1] - columns[0]) * (rows[2] - rows[0]) - (columns[2] - columns[0]) * (rows[1] - rows[0]))
        if abs(area) < 1e-9:
            continue  # seen edge-on: it covers no pixel
        left, right = max(int(np.floor(columns.min() - 0.5)), 0), min(int(np.ceil(columns.max() + 0.5)), canvas - 1)
        top, bottom = max(int(np.floor(rows.min() - 0.5)), 0), min(int(np.ceil(rows.max() + 0.5)), canvas - 1)
        if left > right or top > bottom:
            continue
        pixel_rows, pixel_columns = np.mgrid[top:bottom + 1, left:right + 1]
        weights = np.stack([((columns[(at + 1) % 3] - pixel_columns) * (rows[(at + 2) % 3] - pixel_rows)
                             - (columns[(at + 2) % 3] - pixel_columns) * (rows[(at + 1) % 3] - pixel_rows)) / area
                            for at in range(3)])
        edges = np.array([np.hypot(columns[(at + 2) % 3] - columns[(at + 1) % 3],
                                   rows[(at + 2) % 3] - rows[(at + 1) % 3]) for at in range(3)])
        inside = (weights >= -0.5 * edges[:, None, None] / abs(area)).all(axis=0)
        clamped = np.clip(weights, 0.0, None)
        depth = np.einsum("k,kij->ij", depths, clamped / np.maximum(clamped.sum(axis=0), 1e-12))
        window = (slice(top, bottom + 1), slice(left, right + 1))
        nearer = inside & (depth > nearest[window])
        nearest[window][nearer] = depth[nearer]
        ids[window][nearer] = number
    return ids


def pixel_uvs(view_triangles, uvs, rows, columns, covering, canvas, middle, scale):
    """The UV at each covered pixel: its triangle's corner UVs weighted by the pixel's place in the drawn triangle
    (clamped into it at the rim; a triangle drawn edge-on takes its middle)."""
    points = np.c_[(columns - canvas / 2) / scale, (canvas / 2 - rows) / scale] + middle
    corners = view_triangles[covering, :, :2]
    first, second = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    offset = points - corners[:, 0]
    area = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
    flat = np.abs(area) < 1e-12
    safe = np.where(flat, 1.0, area)
    along_first = np.where(flat, 1 / 3, (offset[:, 0] * second[:, 1] - offset[:, 1] * second[:, 0]) / safe)
    along_second = np.where(flat, 1 / 3, (first[:, 0] * offset[:, 1] - first[:, 1] * offset[:, 0]) / safe)
    weights = np.clip(np.c_[1 - along_first - along_second, along_first, along_second], 0, None)
    weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    return np.einsum("pk,pkc->pc", weights, uvs[covering])


def draw(view_triangles, colours, size, skin=None):
    """The triangles drawn through a depth buffer on a square picture fitted to their outline, each pixel in its
    triangle's constant colour or, where `skin` gives it a texture, that texture at the pixel's own UV."""
    canvas = size * OVERSAMPLE
    picture = np.tile(np.array(BACKGROUND, dtype=np.uint8), (canvas, canvas, 1))
    if not len(view_triangles):
        return Image.fromarray(picture).resize((size, size), Image.LANCZOS)
    flat = view_triangles[:, :, :2].reshape(-1, 2)
    low, high = flat.min(axis=0), flat.max(axis=0)
    scale = 0.9 * canvas / max(float((high - low).max()), 1e-9)
    middle = (low + high) / 2
    ids = triangle_ids(view_triangles, canvas, middle, scale)
    rows, columns = np.nonzero(ids >= 0)
    covering = ids[rows, columns]
    painted = colours[covering].astype(np.float64)
    if skin is not None:
        numbers = skin.numbers[covering]
        textured = numbers >= 0
        if textured.any():
            found = pixel_uvs(view_triangles, skin.uvs, rows[textured], columns[textured], covering[textured],
                              canvas, middle, scale)
            for number in np.unique(numbers[textured]):
                chosen = numbers[textured] == number
                where = np.flatnonzero(textured)[chosen]
                painted[where] = sampled(load_texture(skin.paths[number]), found[chosen])
    lit = np.clip(painted * lighting(view_triangles)[covering, None], 0, 1)
    picture[rows, columns] = (lit * 255).astype(np.uint8)
    return Image.fromarray(picture).resize((size, size), Image.LANCZOS)


def render(path, destination, size=SIZE):
    """Draw a USD file's meshes to a png; the number of triangles drawn."""
    triangles, colours, stage, skin = collect(path)
    turned = triangles @ up_rotation(stage).T
    view = turned @ view_matrix().T
    picture = draw(view, colours, size, skin)
    pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
    picture.save(destination, format="PNG", optimize=False)
    return len(triangles)
