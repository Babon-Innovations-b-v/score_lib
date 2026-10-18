"""A stage's drawn meshes as one glTF document (a .gltf, its .bin and its pictures in a work folder, for gltfpack).

The method is score's tools/review/viewer/export.py (at the world's commit), copied rather than imported: the stage is
read with usd-core at its first moment (a character stands in its first pose, UsdSkel skinning its meshes), every mesh
or sphere a renderer draws is written whole in the stage's frame, one primitive per surface (a GeomSubset with a
material of its own, else the mesh's material), each node named after its object. Two changes: each surface's finish
comes from finish.py (the library), not from the stage's own shader inputs, and a point instancer's prototypes are
written once and each instance is a node of its own pointing at them (score's dashboard web3d/meshes.py's matrices).
What it cannot write (another kind of shape) is counted in `left_out`; the sky is left to the light entries.
"""
import json
import os

import numpy as np

GLTF_FLOAT, GLTF_UINT = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
# A UsdGeom.Sphere drawn as a latitude-longitude mesh this many steps round.
SPHERE_SEGMENTS = 256
# The scope of a place's sky (dome, stars, haze): a light entry's prims, not a finish; left out of the copy.
SKY = "Sky"


def drawn(prim, time):
    """Whether a renderer draws this prim: a mesh or a sphere of purpose default or render, visible at the time."""
    from pxr import UsdGeom
    imageable = UsdGeom.Imageable(prim)
    shown = imageable.ComputePurpose() in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)
    return ((prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Sphere)) and shown
            and imageable.ComputeVisibility(time) != UsdGeom.Tokens.invisible)


def fan(counts):
    """Each polygon as a triangle fan: (triangles, 3) corner numbers into faceVertexIndices, and each one's face."""
    counts = np.asarray(counts, dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    per_face = np.maximum(counts - 2, 0)
    faces = np.repeat(np.arange(len(counts)), per_face)
    step = np.arange(per_face.sum()) - np.repeat(np.cumsum(per_face) - per_face, per_face)
    first = starts[faces]
    return np.column_stack([first, first + step + 1, first + step + 2]), faces


def per_corner(values, interpolation, indices, corner_faces):
    """A primvar's values at every corner of the mesh, whatever its interpolation."""
    values = np.asarray(values, dtype=np.float64)
    if interpolation in ("vertex", "varying"):
        return values[indices]
    if interpolation == "faceVarying":
        return values
    if interpolation == "uniform":
        return values[corner_faces]
    return np.repeat(values.reshape(1, -1), len(indices), axis=0)


def skins(stage):
    """Every skinned mesh of the stage by its path: (its skeleton's query, its skinning query)."""
    from pxr import Usd, UsdSkel
    cache, found = UsdSkel.Cache(), {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdSkel.Root):
            continue
        cache.Populate(UsdSkel.Root(prim), Usd.PrimDefaultPredicate)
        for binding in cache.ComputeSkelBindings(UsdSkel.Root(prim), Usd.PrimDefaultPredicate):
            skeleton = cache.GetSkelQuery(binding.GetSkeleton())
            for target in binding.GetSkinningTargets():
                if target.HasJointInfluences():
                    found[target.GetPrim().GetPath()] = (skeleton, target)
    return found


def skinned_points(prim, skinned, time):
    """A skinned mesh's points at the time, posed by its skeleton, in the stage's frame ((count, 4), w = 1), or None
    for a mesh no skeleton moves."""
    from pxr import UsdGeom
    if prim.GetPath() not in skinned:
        return None
    skeleton, query = skinned[prim.GetPath()]
    points = UsdGeom.Mesh(prim).GetPointsAttr().Get(time)
    if not query.ComputeSkinnedPoints(skeleton.ComputeSkinningTransforms(time), points, time):
        raise RuntimeError(f"{prim.GetPath()}: UsdSkel could not skin its points")
    world = np.array(UsdGeom.Xformable(skeleton.GetPrim()).ComputeLocalToWorldTransform(time))
    return np.column_stack([np.asarray(points, dtype=np.float64), np.ones(len(points))]) @ world


def world_points(prim, skinned, time):
    """A mesh's points in the stage's frame at the time ((count, 4), w = 1) and whether that frame mirrors it. USD's
    matrices act on row vectors."""
    from pxr import UsdGeom
    posed = skinned_points(prim, skinned, time)
    if posed is not None:
        return posed, False
    world = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time))
    points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(time), dtype=np.float64)
    return np.column_stack([points, np.ones(len(points))]) @ world, np.linalg.det(world[:3, :3]) < 0


def mesh_corners(prim, skinned, time):
    """A mesh at every corner of its triangles: positions, normals (or None), texcoords (or None), the triangles'
    corners and each triangle's face; None for a mesh with no faces."""
    from pxr import UsdGeom
    mesh = UsdGeom.Mesh(prim)
    counts = mesh.GetFaceVertexCountsAttr().Get(time)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(time) or [], dtype=np.int64)
    points_attribute = mesh.GetPointsAttr().Get(time)
    if counts is None or not len(indices) or points_attribute is None or not len(points_attribute):
        return None
    points, mirrored = world_points(prim, skinned, time)
    triangles, faces = fan(counts)
    corner_faces = np.repeat(np.arange(len(counts)), np.asarray(counts, dtype=np.int64))
    if (mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded) != mirrored:
        triangles = triangles[:, ::-1]
    return {"positions": points[indices, :3], "normals": world_normals(prim, indices, corner_faces, time),
            "texcoords": texcoords(prim, indices, corner_faces, time), "triangles": triangles, "faces": faces,
            "colours": display_colours(prim, indices, corner_faces, time)}


def sphere_corners(prim, time):
    """A UsdGeom.Sphere as a latitude-longitude mesh in mesh_corners's form, in the stage's frame."""
    from pxr import UsdGeom
    radius = float(UsdGeom.Sphere(prim).GetRadiusAttr().Get(time))
    rows, columns = SPHERE_SEGMENTS // 2 + 1, SPHERE_SEGMENTS + 1
    around, down = np.meshgrid(np.linspace(0, 2 * np.pi, columns), np.linspace(0, np.pi, rows))
    unit = np.stack([np.sin(down) * np.cos(around), np.cos(down), -np.sin(down) * np.sin(around)], -1).reshape(-1, 3)
    world = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time))
    row, column = np.meshgrid(np.arange(rows - 1), np.arange(columns - 1), indexing="ij")
    first = (row * columns + column).ravel()
    triangles = np.concatenate([np.column_stack([first, first + columns, first + columns + 1]),
                                np.column_stack([first, first + columns + 1, first + 1])])
    if np.linalg.det(world[:3, :3]) < 0:
        triangles = triangles[:, ::-1]
    normals = unit @ np.linalg.inv(world[:3, :3]).T
    return {"positions": (np.column_stack([unit * radius, np.ones(len(unit))]) @ world)[:, :3],
            "normals": normals / np.linalg.norm(normals, axis=1, keepdims=True),
            "texcoords": np.column_stack([around.ravel() / (2 * np.pi), down.ravel() / np.pi]),
            "triangles": triangles, "faces": np.arange(len(triangles)), "colours": None}


def world_normals(prim, indices, corner_faces, time):
    """A mesh's authored normals at every corner, turned into the stage's frame, or None when it has none."""
    from pxr import UsdGeom
    mesh = UsdGeom.Mesh(prim)
    normals = mesh.GetNormalsAttr().Get(time)
    if normals is None or not len(normals):
        return None
    turn = np.linalg.inv(np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time))[:3, :3])
    found = per_corner(normals, mesh.GetNormalsInterpolation(), indices, corner_faces) @ turn.T
    return found / np.maximum(np.linalg.norm(found, axis=1, keepdims=True), 1e-12)


def texcoords(prim, indices, corner_faces, time):
    """A mesh's st at every corner as glTF's texture coordinates (v from the top), or None when it has none."""
    from pxr import UsdGeom
    st = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
    if not st or not st.HasValue():
        return None
    found = per_corner(st.ComputeFlattened(time), st.GetInterpolation(), indices, corner_faces)
    return np.column_stack([found[:, 0], 1.0 - found[:, 1]])


def display_colours(prim, indices, corner_faces, time):
    """A mesh's displayColor at every corner (linear RGB), or None: only ever measured, never drawn."""
    from pxr import UsdGeom
    colour = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
    if not colour or not colour.HasValue():
        return None
    return per_corner(colour.ComputeFlattened(time), colour.GetInterpolation(), indices, corner_faces)[:, :3]


def bound_material(prim):
    from pxr import UsdShade
    return UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]


def surface_groups(prim, triangle_faces):
    """The mesh's triangles by what paints them: [(surface name, material, triangle numbers)]. A part (a GeomSubset
    with a material of its own) is named after itself; the rest after the mesh's material."""
    from pxr import UsdGeom
    taken = np.zeros(len(triangle_faces), dtype=bool)
    groups = []
    for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim)):
        material = bound_material(subset.GetPrim())
        if not material or subset.GetElementTypeAttr().Get() != UsdGeom.Tokens.face:
            continue
        chosen = np.isin(triangle_faces, np.asarray(subset.GetIndicesAttr().Get(), dtype=np.int64)) & ~taken
        taken |= chosen
        groups.append((subset.GetPrim().GetName(), material, np.flatnonzero(chosen)))
    material = bound_material(prim)
    rest = np.flatnonzero(~taken)
    if len(rest):
        groups.append((material.GetPrim().GetName() if material else "unpainted", material, rest))
    return [group for group in groups if len(group[2])]


class Gltf:
    """A glTF document and its one binary buffer, written as `.gltf` and `.bin` into a work folder for gltfpack."""

    def __init__(self, folder):
        self.folder = folder
        self.document = {"asset": {"version": "2.0", "generator": "score_lib web3d"},
                         "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
                         "textures": [], "images": [], "accessors": [], "bufferViews": [], "extensionsUsed": []}
        self.blob = bytearray()
        self.materials, self.images = {}, {}

    def accessor(self, values, kind, target):
        """One accessor over its own buffer view: float32 VEC2 or VEC3 (VEC3 with its bounds), or uint32 indices."""
        data = np.ascontiguousarray(values, dtype=np.uint32 if kind == "SCALAR" else np.float32)
        self.document["bufferViews"].append({"buffer": 0, "byteOffset": len(self.blob), "byteLength": data.nbytes,
                                             "target": target})
        self.blob += data.tobytes()
        entry = {"bufferView": len(self.document["bufferViews"]) - 1, "count": len(data), "type": kind,
                 "componentType": GLTF_UINT if kind == "SCALAR" else GLTF_FLOAT}
        if kind == "VEC3":
            entry.update(min=data.min(axis=0).tolist(), max=data.max(axis=0).tolist())
        self.document["accessors"].append(entry)
        return len(self.document["accessors"]) - 1

    def texture(self, path):
        """A texture of the picture at `path`, each picture once."""
        if path not in self.images:
            self.document["images"].append({"uri": path})
            self.document["textures"].append({"source": len(self.document["images"]) - 1})
            self.images[path] = len(self.document["textures"]) - 1
        return {"index": self.images[path]}

    def roughness_metal(self, rough, metal):
        """The metal-rough texture glTF wants (roughness in green, metalness in blue): the stage's own picture when
        both come from one in those channels, else one put together from the two."""
        from PIL import Image
        if rough[0] == metal[0] == "texture" and rough[1] == metal[1] and (rough[2], metal[2]) == ("g", "b"):
            return self.texture(rough[1])
        key = json.dumps([rough, metal])
        if key not in self.images:
            sizes = [Image.open(source[1]).size for source in (rough, metal) if source[0] == "texture"]
            size = max(sizes, key=lambda each: each[0] * each[1])
            planes = [channel_plane(source, size) for source in (rough, metal)]
            path = self.folder / f"made_{len(self.images)}_metal_roughness.png"
            Image.merge("RGB", [planes[0], planes[0], planes[1]]).save(path)
            self.images[key] = self.texture(str(path))["index"]
        return {"index": self.images[key]}

    def material(self, name, inputs, double_sided):
        """A glTF material for a surface's finish inputs, each distinct one once: its index."""
        key = (name, double_sided, json.dumps(inputs, sort_keys=True))
        if key not in self.materials:
            self.document["materials"].append(material_entry(self, name, inputs, double_sided))
            self.materials[key] = len(self.document["materials"]) - 1
        return self.materials[key]

    def mesh(self, name, primitives):
        """A mesh of the primitives: its index."""
        self.document["meshes"].append({"name": name, "primitives": primitives})
        return len(self.document["meshes"]) - 1

    def node(self, name, mesh, matrix=None):
        """A scene node showing a mesh, moved by a matrix (16 numbers, glTF's column order) when given."""
        node = {"name": name, "mesh": mesh}
        if matrix is not None:
            node["matrix"] = matrix
        self.document["nodes"].append(node)
        self.document["scenes"][0]["nodes"].append(len(self.document["nodes"]) - 1)

    def write(self):
        """The document and its buffer into the work folder, each picture linked in beside it: the .gltf's path."""
        for number, image in enumerate(self.document["images"]):
            source = image["uri"]
            name = f"picture_{number}_{os.path.basename(source)}"
            target = self.folder / name
            if not target.exists():
                os.symlink(os.path.abspath(source), target)
            image["uri"] = name
        self.document["buffers"] = [{"uri": "scene.bin", "byteLength": len(self.blob)}]
        (self.folder / "scene.bin").write_bytes(self.blob)
        path = self.folder / "scene.gltf"
        path.write_text(json.dumps({key: value for key, value in self.document.items() if value != []}))
        return path


def channel_plane(source, size):
    """One greyscale plane at `size` of a texture's channel, or of a value."""
    from PIL import Image
    if source[0] == "value":
        return Image.new("L", size, round(255 * float(np.clip(source[1], 0.0, 1.0))))
    picture = Image.open(source[1]).convert("RGBA").resize(size)
    return picture.getchannel("RGBA".index(source[2].upper()))


def material_entry(gltf, name, inputs, double_sided):
    """A glTF metal-rough material from a finish's inputs (UsdPreviewSurface names, channel_source form)."""
    pbr = {"baseColorFactor": [1.0, 1.0, 1.0, 1.0], "roughnessFactor": 1.0, "metallicFactor": 1.0}
    entry = {"name": name, "pbrMetallicRoughness": pbr, "doubleSided": double_sided}
    diffuse = inputs.get("diffuseColor", ["value", [0.18, 0.18, 0.18]])
    opacity = inputs.get("opacity")
    if diffuse[0] == "texture":
        pbr["baseColorTexture"] = gltf.texture(diffuse[1])
    else:
        pbr["baseColorFactor"] = [*diffuse[1][:3], 1.0]
    rough = inputs.get("roughness", ["value", 0.5])
    metal = inputs.get("metallic", ["value", 0.0])
    if "texture" in (rough[0], metal[0]):
        pbr["metallicRoughnessTexture"] = gltf.roughness_metal(rough, metal)
    else:
        pbr["roughnessFactor"], pbr["metallicFactor"] = float(rough[1]), float(metal[1])
    if inputs.get("normal", ["value"])[0] == "texture":
        entry["normalTexture"] = gltf.texture(inputs["normal"][1])
    glowing(gltf, entry, inputs.get("emissiveColor"))
    see_through(entry, opacity, inputs.get("opacityThreshold"))
    return entry


def glowing(gltf, entry, glow):
    """A glowing surface's emission: its picture, or its colour, over glTF's 0..1 by emissive strength."""
    if glow and glow[0] == "texture":
        entry["emissiveTexture"], entry["emissiveFactor"] = gltf.texture(glow[1]), [1.0, 1.0, 1.0]
        return
    if not glow or max(glow[1]) <= 0:
        return
    strongest = max(glow[1])
    entry["emissiveFactor"] = [value / max(strongest, 1.0) for value in glow[1]]
    if strongest > 1.0:
        entry["extensions"] = {"KHR_materials_emissive_strength": {"emissiveStrength": strongest}}
        if "KHR_materials_emissive_strength" not in gltf.document["extensionsUsed"]:
            gltf.document["extensionsUsed"].append("KHR_materials_emissive_strength")


def see_through(entry, opacity, threshold):
    """A see-through surface's alpha: a cutout where the stage sets a threshold, else blended (a value only: a
    picture's own alpha is never a library finish)."""
    if not opacity or opacity[0] != "value" or opacity[1] >= 1.0:
        return
    if threshold and threshold[0] == "value" and threshold[1] > 0:
        entry["alphaMode"], entry["alphaCutoff"] = "MASK", float(threshold[1])
    else:
        entry["alphaMode"] = "BLEND"
    entry["pbrMetallicRoughness"]["baseColorFactor"][3] = float(opacity[1])


def object_name(prim):
    """The object a mesh belongs to, as the viewer names it: its path under the stage's default prim, an object's
    one mesh (`geo`) by its object's."""
    named = prim.GetParent() if prim.GetName() == "geo" else prim
    root = prim.GetStage().GetDefaultPrim()
    return str(named.GetPath().MakeRelativePath(root.GetPath())) if root else named.GetPath().pathString


def primitives_of(gltf, prim, corners, finishes):
    """One primitive per surface of a mesh, each with the finish the library gives it."""
    from pxr import UsdGeom
    double_sided = bool(UsdGeom.Gprim(prim).GetDoubleSidedAttr().Get())
    primitives = []
    for surface, material, chosen in surface_groups(prim, corners["faces"]):
        used = np.unique(corners["triangles"][chosen])
        renumber = np.full(len(corners["positions"]), -1, dtype=np.int64)
        renumber[used] = np.arange(len(used))
        name, inputs = finishes.resolve(surface, material, corners, chosen)
        attributes = {"POSITION": gltf.accessor(corners["positions"][used], "VEC3", ARRAY_BUFFER)}
        if corners["normals"] is not None:
            attributes["NORMAL"] = gltf.accessor(corners["normals"][used], "VEC3", ARRAY_BUFFER)
        if corners["texcoords"] is not None and uses_pictures(inputs):
            attributes["TEXCOORD_0"] = gltf.accessor(corners["texcoords"][used], "VEC2", ARRAY_BUFFER)
        elif uses_pictures(inputs):
            inputs = finishes.without_pictures(name)
        indices = gltf.accessor(renumber[corners["triangles"][chosen]].ravel(), "SCALAR", ELEMENT_ARRAY_BUFFER)
        primitives.append({"attributes": attributes, "indices": indices,
                           "material": gltf.material(name, inputs, double_sided)})
    return primitives


def uses_pictures(inputs):
    return any(source[0] == "texture" for source in inputs.values())


def corners_of(prim, skinned, time):
    from pxr import UsdGeom
    return sphere_corners(prim, time) if prim.IsA(UsdGeom.Sphere) else mesh_corners(prim, skinned, time)


def add_mesh(gltf, prim, skinned, time, boxes, finishes):
    """One stage mesh as a node, a primitive per surface, its box joined into its object's in `boxes`: its
    triangle count."""
    corners = corners_of(prim, skinned, time)
    if corners is None:
        return 0
    primitives = primitives_of(gltf, prim, corners, finishes)
    name = object_name(prim)
    gltf.node(name, gltf.mesh(name, primitives))
    join_box(boxes, name, corners["positions"])
    return len(corners["triangles"])


def join_box(boxes, name, positions):
    low, high = positions.min(axis=0), positions.max(axis=0)
    if name in boxes:
        low, high = np.minimum(low, boxes[name][0]), np.maximum(high, boxes[name][1])
    boxes[name] = (low, high)


def instance_matrices(instancer, root, time):
    """Each instance's move of a prototype written where it stands (in the stage's frame), as glTF node matrices:
    USD's row-vector matrix read row by row is glTF's column order."""
    from pxr import UsdGeom
    turns = instancer.ComputeInstanceTransformsAtTime(time, time)
    world = np.array(UsdGeom.Xformable(instancer.GetPrim()).ComputeLocalToWorldTransform(time))
    written = np.linalg.inv(np.array(UsdGeom.Xformable(root).ComputeLocalToWorldTransform(time)))
    indices = np.asarray(instancer.GetProtoIndicesAttr().Get(time), dtype=np.int64)
    targets = instancer.GetPrototypesRel().GetTargets()
    chosen = [number for number, index in enumerate(indices) if targets[index] == root.GetPath()]
    return [(written @ np.array(turns[number]) @ world).ravel().tolist() for number in chosen]


def add_instancer(gltf, prim, skinned, time, boxes, finishes):
    """A point instancer's prototypes written once and each instance a node pointing at them: its triangles."""
    from pxr import Usd, UsdGeom
    instancer = UsdGeom.PointInstancer(prim)
    stage = prim.GetStage()
    triangles = 0
    for target in instancer.GetPrototypesRel().GetTargets():
        root = stage.GetPrimAtPath(target)
        matrices = instance_matrices(instancer, root, time)
        for mesh_prim in Usd.PrimRange(root):
            if not drawn(mesh_prim, time):
                continue
            corners = corners_of(mesh_prim, skinned, time)
            if corners is None:
                continue
            name = object_name(mesh_prim)
            mesh = gltf.mesh(name, primitives_of(gltf, mesh_prim, corners, finishes))
            for number, matrix in enumerate(matrices):
                gltf.node(f"{name}#{number}", mesh, matrix)
            triangles += len(corners["triangles"]) * len(matrices)
            join_box(boxes, object_name(prim), corners["positions"])
    return triangles


def sky_scope(stage):
    """The place's sky scope (<default prim>/Sky), or None."""
    root = stage.GetDefaultPrim()
    sky = root.GetChild(SKY) if root else None
    return sky if sky and sky.IsValid() else None


def write_meshes(stage, gltf, time, finishes, keep=None):
    """Every drawn mesh of the stage (or of the prims `keep` accepts) into the document: the objects' boxes, the
    triangle count and what was left out (other shapes counted by kind, the sky)."""
    from pxr import Usd, UsdGeom
    skinned = skins(stage)
    boxes, triangles, left_out = {}, 0, {}
    sky = sky_scope(stage)
    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    prims = iter(Usd.PrimRange(root))
    for prim in prims:
        if sky and prim == sky:
            left_out["sky (a light entry's prims)"] = 1
            prims.PruneChildren()
        elif keep is not None and not keep(prim):
            continue
        elif prim.IsA(UsdGeom.PointInstancer):
            triangles += add_instancer(gltf, prim, skinned, time, boxes, finishes)
            prims.PruneChildren()
        elif prim.IsA(UsdGeom.Points):
            prims.PruneChildren()  # an effect's points: the effect entries hold them
        elif prim.IsA(UsdGeom.Gprim) and not (prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Sphere)):
            if UsdGeom.Imageable(prim).ComputeVisibility(time) != UsdGeom.Tokens.invisible:
                left_out[str(prim.GetTypeName())] = left_out.get(str(prim.GetTypeName()), 0) + 1
            prims.PruneChildren()
        elif drawn(prim, time):
            triangles += add_mesh(gltf, prim, skinned, time, boxes, finishes)
    return boxes, triangles, left_out
