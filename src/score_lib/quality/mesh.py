"""A model's render meshes read from its USD file as one welded triangle mesh, and the free measures of its geometry:
open edges, edges shared by more than two faces, surface noise (how far each point stands off the middle of its
neighbours, against its edge length), size, area and the texels a metre its UVs give each map."""
import pathlib

import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdShade

from score_lib.preview import mesh as preview

# Points closer than this share of the model's diagonal are one point (a generated mesh splits its points at UV
# seams; a welded mesh shows its real open edges).
WELD_SHARE = 1e-6
# Prims that are not drawn: collision shapes and guides.
HIDDEN_PURPOSES = ("guide", "proxy")


class Welded:
    """One model's drawn triangles: welded points (n, 3), triangles (m, 3) into them, each triangle's corner UVs
    (m, 3, 2) or None, the texture files of its maps by role and the names of its bound materials."""

    def __init__(self, points, triangles, uvs, maps, materials):
        self.points = points
        self.triangles = triangles
        self.uvs = uvs
        self.maps = maps
        self.materials = materials


def drawn_meshes(stage):
    """Every mesh prim of a stage that is drawn (not a collision shape, guide or proxy, not invisible)."""
    found = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        imageable = UsdGeom.Imageable(prim)
        if imageable.ComputePurpose() in HIDDEN_PURPOSES or imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        if prim.GetName() == "collision" or prim.HasAttribute("physics:collisionEnabled"):
            continue
        found.append(prim)
    return found


def bound_materials(prim):
    """{name: material} bound to a mesh prim and to each of its GeomSubsets (a generated model binds one library
    surface a subset)."""
    found = {}
    for bound in [prim] + [subset.GetPrim() for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim))]:
        material = UsdShade.MaterialBindingAPI(bound).ComputeBoundMaterial()[0]
        if material:
            found[material.GetPrim().GetName()] = material
    return found


def map_files(materials):
    """{role: texture path} of the UsdPreviewSurface inputs the materials read (diffuseColor, normal, ...); the
    first material naming a role gives it."""
    found = {}
    for material in materials:
        for role, path in material_maps(material).items():
            found.setdefault(role, path)
    return found


def material_maps(material):
    """{role: texture path} of one material's UsdPreviewSurface inputs."""
    found = {}
    for shader_prim in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(shader_prim)
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            continue
        for surface_input in shader.GetInputs():
            if not surface_input.HasConnectedSource():
                continue
            source = UsdShade.Shader(surface_input.GetConnectedSource()[0].GetPrim()).GetInput("file")
            asset = source.Get() if source else None
            if asset is not None and asset.resolvedPath and pathlib.Path(asset.resolvedPath).is_file():
                found[surface_input.GetBaseName()] = pathlib.Path(asset.resolvedPath)
    return found


def prim_triangles(prim, up):
    """(world triangle corners (m, 3, 3), corner UVs (m, 3, 2) or None, {name: bound material}) of one mesh prim,
    Y up."""
    mesh = UsdGeom.Mesh(prim)
    points = np.array(mesh.GetPointsAttr().Get() or [], dtype=np.float64)
    counts = np.array(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=np.int64)
    indices = np.array(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=np.int64)
    if not len(points) or not len(counts):
        return np.zeros((0, 3, 3)), None, {}
    matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    world = (np.c_[points, np.ones(len(points))] @ matrix)[:, :3] @ up.T
    corners = preview.triangulated(counts, indices)
    uvs = preview.corner_uvs(mesh, len(indices), indices)
    materials = bound_materials(prim)
    return world[indices[corners]], (uvs[corners] if uvs is not None else None), materials


def read(path):
    """A USD file's drawn meshes as one Welded mesh (None when it draws no triangle)."""
    stage = Usd.Stage.Open(str(path))
    up = preview.up_rotation(stage)
    corners, uvs, materials = [], [], {}
    for prim in drawn_meshes(stage):
        triangles, prim_uvs, prim_materials = prim_triangles(prim, up)
        if not len(triangles):
            continue
        corners.append(triangles)
        uvs.append(prim_uvs if prim_uvs is not None else np.full((len(triangles), 3, 2), np.nan))
        materials.update(prim_materials)
    if not corners:
        return None
    corners = np.concatenate(corners)
    uvs = np.concatenate(uvs)
    points, triangles = weld(corners.reshape(-1, 3))
    return Welded(points, triangles.reshape(-1, 3), None if np.isnan(uvs).all() else uvs,
                  map_files(materials.values()), sorted(materials))


def weld(corners):
    """(unique points, index of each corner into them), points within WELD_SHARE of the diagonal merged."""
    diagonal = float(np.linalg.norm(corners.max(axis=0) - corners.min(axis=0))) or 1.0
    keys = np.round(corners / (diagonal * WELD_SHARE)).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    return corners[first], inverse.reshape(-1)


def size_of(model):
    """The model's box (wide x, tall y, deep z) in metres."""
    return model.points.max(axis=0) - model.points.min(axis=0)


def face_areas(model):
    corners = model.points[model.triangles]
    return np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1) / 2


def edges_of(model):
    """(unique edges (e, 2), how many triangles use each (e,), the edge of each triangle side (m, 3))."""
    sides = np.stack([model.triangles[:, [0, 1]], model.triangles[:, [1, 2]], model.triangles[:, [2, 0]]], axis=1)
    flat = np.sort(sides.reshape(-1, 2), axis=1)
    unique, inverse, uses = np.unique(flat, axis=0, return_inverse=True, return_counts=True)
    return unique, uses, inverse.reshape(-1, 3)


def edge_lengths(model, edges):
    return np.linalg.norm(model.points[edges[:, 0]] - model.points[edges[:, 1]], axis=1)


def open_edges(model):
    """(share of edge length on edges one triangle uses, the open edges' triangles as a mask)."""
    edges, uses, sides = edges_of(model)
    lengths = edge_lengths(model, edges)
    open_mask = uses == 1
    share = float(lengths[open_mask].sum() / max(lengths.sum(), 1e-12))
    return share, open_mask[sides].any(axis=1)


def crowded_edges(model):
    """(share of edges more than two triangles use, those triangles as a mask)."""
    edges, uses, sides = edges_of(model)
    crowded = uses > 2
    return float(crowded.mean()) if len(uses) else 0.0, crowded[sides].any(axis=1)


def vertex_normals(model, areas):
    """Area-weighted normals at each welded point."""
    corners = model.points[model.triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    found = np.zeros_like(model.points)
    for corner in range(3):
        np.add.at(found, model.triangles[:, corner], normals)
    lengths = np.linalg.norm(found, axis=1, keepdims=True)
    return np.divide(found, lengths, out=np.zeros_like(found), where=lengths > 0)


def surface_noise(model):
    """(area-weighted median of how far each point's bend differs from its neighbours' bends; each triangle's noise
    (m,)). A point's bend is its offset from its neighbours' middle along its normal, over its mean edge length: even
    on a flat or evenly curved surface (a coarse dome bends every point alike), uneven on a melted or blobby one; a
    sharp corner is one point and leaves the median alone."""
    edges, _, _ = edges_of(model)
    count = len(model.points)
    total = np.zeros((count, 3))
    neighbours = np.zeros(count)
    length_sum = np.zeros(count)
    lengths = edge_lengths(model, edges)
    for this, other in ((0, 1), (1, 0)):
        np.add.at(total, edges[:, this], model.points[edges[:, other]])
        np.add.at(neighbours, edges[:, this], 1)
        np.add.at(length_sum, edges[:, this], lengths)
    has = neighbours > 0
    middle = np.divide(total, neighbours[:, None], out=model.points.copy(), where=has[:, None])
    areas = face_areas(model)
    normals = vertex_normals(model, areas)
    offset = np.einsum("ij,ij->i", model.points - middle, normals)
    mean_length = np.divide(length_sum, neighbours, out=np.ones(count), where=has)
    bend = np.divide(offset, mean_length, out=np.zeros(count), where=mean_length > 0)
    bend_total = np.zeros(count)
    for this, other in ((0, 1), (1, 0)):
        np.add.at(bend_total, edges[:, this], bend[edges[:, other]])
    point_noise = np.abs(bend - np.divide(bend_total, neighbours, out=bend.copy(), where=has))
    point_area = np.zeros(count)
    for corner in range(3):
        np.add.at(point_area, model.triangles[:, corner], areas / 3)
    return weighted_median(point_noise, point_area), point_noise[model.triangles].mean(axis=1)


def weighted_median(values, weights):
    if not len(values) or weights.sum() <= 0:
        return 0.0
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cumulative, cumulative[-1] / 2)])


def uv_areas(model):
    """Each triangle's area in UV space (0 where it has no UVs)."""
    if model.uvs is None:
        return np.zeros(len(model.triangles))
    edge_one = model.uvs[:, 1] - model.uvs[:, 0]
    edge_two = model.uvs[:, 2] - model.uvs[:, 0]
    found = np.abs(edge_one[:, 0] * edge_two[:, 1] - edge_one[:, 1] * edge_two[:, 0]) / 2
    return np.nan_to_num(found)


def texel_density(model, role):
    """(texels a metre a map gives over the model's area-weighted median triangle, each triangle's (m,)), or None
    when the model has no such map or no UVs."""
    path = model.maps.get(role)
    if path is None or model.uvs is None:
        return None
    with Image.open(path) as picture:
        width, height = picture.size
    areas = face_areas(model)
    texels = uv_areas(model) * width * height
    each = np.sqrt(np.divide(texels, areas, out=np.zeros_like(areas), where=areas > 1e-12))
    drawn = areas > 1e-12
    return weighted_median(each[drawn], areas[drawn]), each
