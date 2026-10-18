"""A mesh preview from the entry's own data: every mesh of a USD file drawn three-quarter orthographic with numpy and
a painter's algorithm (far triangles first), each triangle filled with the base colour sampled at its UV centre and
shaded by a fixed light. No renderer."""
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
    """(texture image as float array, or None) and the constant base colour of the prim's bound material."""
    material = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
    if not material:
        return None, GREY
    for shader_prim in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(shader_prim)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            return surface_colour(shader)
    return None, GREY


def surface_colour(surface):
    """(texture, constant) behind a UsdPreviewSurface's diffuseColor: the texture its connection reads, or its
    constant colour."""
    colour = surface.GetInput("diffuseColor")
    if not colour:
        return None, GREY
    if colour.HasConnectedSource():
        source = colour.GetConnectedSource()[0]
        texture_input = UsdShade.Shader(source.GetPrim()).GetInput("file")
        asset = texture_input.Get() if texture_input else None
        if asset is not None and asset.resolvedPath and pathlib.Path(asset.resolvedPath).is_file():
            with Image.open(asset.resolvedPath) as picture:
                return np.asarray(picture.convert("RGB"), dtype=np.float32) / 255.0, GREY
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


def mesh_triangles(prim):
    """(world triangle corners (n, 3, 3), colours (n, 3)) of one mesh prim."""
    mesh = UsdGeom.Mesh(prim)
    points = np.array(mesh.GetPointsAttr().Get() or [], dtype=np.float64)
    counts = np.array(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=np.int64)
    indices = np.array(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
    if not len(points) or not len(counts):
        return np.zeros((0, 3, 3)), np.zeros((0, 3))
    matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    world = np.c_[points, np.ones(len(points))] @ matrix
    corners = triangulated(counts, indices)
    triangles = world[indices[corners]][:, :, :3]
    faces = np.repeat(np.arange(len(counts)), np.maximum(counts - 2, 0))
    centres = corner_uvs(mesh, len(indices), indices)
    centres = centres[corners].mean(axis=1) if centres is not None else None
    colours = np.tile(GREY, (len(triangles), 1))
    for bound, face_indices in material_parts(mesh, len(counts)):
        chosen = np.isin(faces, face_indices)
        colours[chosen] = part_colours(bound, centres[chosen] if centres is not None else None, int(chosen.sum()))
    return triangles, colours


def material_parts(mesh, face_count):
    """[(prim whose bound material paints them, face indices)]: the mesh's own binding over every face, then each
    material subset (a per-face binding, as the library's models carry) over its own faces."""
    found = [(mesh.GetPrim(), np.arange(face_count))]
    for subset in UsdShade.MaterialBindingAPI(mesh.GetPrim()).GetMaterialBindSubsets():
        found.append((subset.GetPrim(), np.array(subset.GetIndicesAttr().Get() or [], dtype=np.int64)))
    return found


def part_colours(bound, centres, count):
    """The colours of `count` triangles painted by `bound`'s material: its texture at their UV centres, else its
    constant colour."""
    texture, constant = bound_texture(bound)
    if texture is None or centres is None:
        return np.tile(constant, (count, 1))
    return sampled(texture, centres)


def drawn(prim):
    """Whether a mesh shows in a render: visible, and not a guide or proxy (such as a model's collision hull)."""
    imageable = UsdGeom.Imageable(prim)
    return imageable.ComputeVisibility() != UsdGeom.Tokens.invisible and imageable.ComputePurpose() in SHOWN_PURPOSES


def collect(path):
    """Every triangle and colour of every mesh a render of a USD file shows (its composed stage)."""
    stage = Usd.Stage.Open(str(path))
    found_triangles, found_colours = [], []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and drawn(prim):
            triangles, colours = mesh_triangles(prim)
            found_triangles.append(triangles)
            found_colours.append(colours)
    if not found_triangles:
        return np.zeros((0, 3, 3)), np.zeros((0, 3)), stage
    return np.concatenate(found_triangles), np.concatenate(found_colours), stage


def up_rotation(stage):
    """A turn that stands a Z-up stage's content with Y up."""
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
        return np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    return np.eye(3)


def shade(view_triangles, colours):
    """Colours lit by the fixed light from each triangle's facing (both sides lit)."""
    normals = np.cross(view_triangles[:, 1] - view_triangles[:, 0], view_triangles[:, 2] - view_triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    light = np.abs(normals @ (view_matrix() @ LIGHT))
    return np.clip(colours * (0.42 + 0.58 * light[:, None]), 0, 1)


def draw(view_triangles, colours, size):
    """The triangles painted far to near on a square picture fitted to their outline."""
    canvas = size * OVERSAMPLE
    picture = Image.new("RGB", (canvas, canvas), BACKGROUND)
    if not len(view_triangles):
        return picture.resize((size, size), Image.LANCZOS)
    flat = view_triangles[:, :, :2].reshape(-1, 2)
    low, high = flat.min(axis=0), flat.max(axis=0)
    scale = 0.9 * canvas / max(float((high - low).max()), 1e-9)
    middle = (low + high) / 2
    order = np.argsort(view_triangles[:, :, 2].mean(axis=1), kind="stable")
    pen = ImageDraw.Draw(picture)
    filled = (shade(view_triangles, colours) * 255).astype(np.uint8)
    for number in order:
        corners = (view_triangles[number, :, :2] - middle) * scale
        outline = [(canvas / 2 + x, canvas / 2 - y) for x, y in corners]
        pen.polygon(outline, fill=tuple(int(value) for value in filled[number]))
    return picture.resize((size, size), Image.LANCZOS)


def render(path, destination, size=SIZE):
    """Draw a USD file's meshes to a png; the number of triangles drawn."""
    triangles, colours, stage = collect(path)
    turned = triangles @ up_rotation(stage).T
    view = turned @ view_matrix().T
    picture = draw(view, colours, size)
    pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
    picture.save(destination, format="PNG", optimize=False)
    return len(triangles)
