"""score-lib take-in hub: an adopted set, one pinned revision of a Hugging Face dataset repository of OpenUSD assets,
taken in whole as draft entries (score #155), its bytes copied into the blob store.

The source is a `config/sources.toml` row with a `take_in` table: the repository (`repo`), where each asset folder
keeps its bill of materials (`bom`: {items: [{relative_path, size, hash: {sha256}}]}) and its root file list
(`roots`: {entries: [<path>]}), the prefix of the ids it writes and the credit and licence words of each entry's
licence block, and where the repository is (`url`, a form naming `{repo}`). The repository's host, company and name
live in that row only.

Per asset folder, at the revision given (a 40-hex commit, never a branch):

- one `model` entry: every file of the bill under the root file's folder, kept under its own path there (the physics
  payloads of every simulator included), less the `.thumbs` pictures and the `source_assets` copies of files it
  holds elsewhere; its `usd.root` the root file; its tags the Wikidata class, Q-code and one-line description its
  layers give, and the simulators it carries;
- one `surface` entry per distinct material, keyed by the sha256 of its maps (a material with no maps by its
  constants), shared by every model that uses the same maps: the maps under their role (`albedo.png`, `normal.png`,
  `orm.png`, ...), our own `material.usda` (a UsdPreviewSurface over them, ORM's green to roughness and blue to
  metallic) and `surface.json` (the material's settings as the source gives them, its normal map's convention and
  `tile_metres`, the metres one repeat of its maps covers on the asset); a set painted for one model (its
  maps unwrapped on that model's own UVs, no `tile` in their names) keeps its maps only, its recipe a written gap;
- each of the model's own materials names its surface (`score:surface`, the USD attribute, written into the layer
  that defines it; that change is marked on the file's files_from row and in the model's credit).

Every entry carrying the source's bytes names its licence and credit (the top-level `licence` block). Bytes go from
the source into a capped cache and from there into the blob store, and the cache is emptied after each put (the disk
rule): a file the store already holds is not downloaded, only stood in for by an empty file while its asset's USD is
checked. An asset whose model entry already holds this revision is left as it is, so a stopped run picks up where it
stopped; with `save`, only when that entry is committed too, so entries a run without `save` (or a stopped save)
left in the checkout are saved by the next one. `retake` takes such an asset again, its entries settled as any
take-in's (unchanged, a changed draft, or a locked one's next version). Whatever cannot be taken is a gap row naming
the asset and why.

A material whose map names UDIM tiles (`<UDIM>` in its path) gets a surface holding every tile the asset holds as its
own file under the map's role (`albedo.1001.png`, `albedo.1002.png`), its material.usda naming them by the pattern
(`./albedo.<UDIM>.png`).

`jobs` assets are taken side by side on threads of one process. Every request to the source goes through one shared
Pacer: under the anonymous limit (PACE_LIMIT in PACE_WINDOW seconds) unless a Hugging Face token is set, and a
refusal for rate (HTTP 429) or a passing server fault backs every thread off for the wait the hub names before the
request is tried again (up to TRIES times). The cache is put and its entries written (and saved, one commit each) a
batch at a time, at most BATCH_LIMIT blob store commits an hour (the hub allows 128 a repository).
"""
import collections
import concurrent.futures
import hashlib
import json
import os
import pathlib
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import time

from score_lib import entry as entries
from score_lib import handover, index, take_in
from score_lib.admission import NotAdmitted, Sources
from score_lib.blobs import BadBlob, check_sha, put_all
from score_lib.blobs.hub import COMMIT_FILES

REPO_TYPE = "dataset"
REVISION = re.compile(r"^[0-9a-f]{40}$")
PACE_LIMIT = 450  # requests in one window, under the hub's anonymous 500 per 300 s
PACE_WINDOW = 300.0
TRIES = 8  # times one request is tried while the hub refuses it for rate or a passing fault
RETRY = (429, 500, 502, 503, 504)  # the refusals a request is tried again after
BACK_OFF = 30.0  # seconds every thread waits after a refusal naming no wait, doubled each time it is refused again
BATCH_LIMIT = 100  # blob store commits in one hour, under the hub's 128 a repository, room left for other writers
BATCH_WINDOW = 3600.0
LIBRARY_BRANCH = "main"  # the library branch a save pushes to (HEAD:main), from any branch or worktree
UDIM = "<UDIM>"
LIST_PAGE = 1000  # entries the hub returns in one page of a tree listing
CACHE_BYTES = 4 << 30  # bytes the cache holds before they are put into the blob store and removed
LAYERS = (".usd", ".usda", ".usdc")
THUMBS = ".thumbs"
COPIES = "source_assets"
CHANGED = "changed: each Material names its library surface (score:surface)"
SURFACE_ATTRIBUTE = "score:surface"
SEMANTIC_TYPE = ":semanticType"  # a semantics attribute naming its label's type (`wikidata_class`), not the label
# What a UsdPreviewSurface input a map feeds says about the map.
INPUT_ROLES = {"diffuseColor": "albedo", "normal": "normal", "roughness": "roughness", "metallic": "metallic",
               "occlusion": "occlusion", "emissiveColor": "emission", "opacity": "opacity",
               "displacement": "displacement"}
SRGB_ROLES = ("albedo", "emission")
LOOK_WRITTEN = "written by score-lib take-in hub (a UsdPreviewSurface over the maps)"
RECORD_WRITTEN = "written by score-lib take-in hub (the material's settings as the source gives them)"
NORMAL_WORDS = {"directx": ("directx", "_dx"), "opengl": ("opengl", "normalgl", "_gl")}  # a normal map's name says
NO_MAPS = "no maps: a constant finish has no repeat"
ONE_MODEL_TILE = "painted for one model: its maps cover that model once, not as a repeat"
NO_MESH = "no mesh of the asset is bound to this material"
NO_UV_AREA = "the meshes bound to this material have no UV area in the UV set it reads"
ONE_MODEL_GAP = ("painted for one model: its maps are unwrapped on that model's own UVs, so no tiling recipe can look "
                 "the same; the maps are the surface")


class Gap(Exception):
    """An asset, or a part of one, that the take-in cannot take; the reason is the message."""


class Pacer:
    """At most `limit` requests in any `window` seconds (no count when `limit` is None), shared by every thread: a
    request over it waits for the oldest to leave, and every request waits out a back-off a refusal set."""

    def __init__(self, limit=PACE_LIMIT, window=PACE_WINDOW, clock=time.monotonic, sleep=time.sleep):
        self.limit, self.window, self.clock, self.sleep = limit, window, clock, sleep
        self.times = collections.deque()
        self.until = None
        self.lock = threading.Lock()

    def tick(self):
        """Count one request, waiting first for a back-off and when the window is full."""
        with self.lock:
            now = self.clock()
            if self.until is not None and now < self.until:
                self.sleep(self.until - now)
                now = self.clock()
            if self.limit is None:
                return
            while self.times and now - self.times[0] >= self.window:
                self.times.popleft()
            if len(self.times) >= self.limit:
                self.sleep(self.window - (now - self.times[0]))
                self.times.popleft()
                now = self.clock()
            self.times.append(now)

    def back_off(self, seconds):
        """Hold every request for `seconds` from now (a later hold already set kept)."""
        with self.lock:
            until = self.clock() + seconds
            self.until = until if self.until is None else max(self.until, until)


def pacer_for_token(token):
    """The run's one Pacer: counting under the anonymous limit when no Hugging Face token is set, else only backing
    off."""
    return Pacer(limit=None) if token else Pacer()


def refusal_status(error):
    """The HTTP status of a hub refusal, or None for any other fault."""
    from huggingface_hub.errors import HfHubHTTPError
    response = getattr(error, "response", None) if isinstance(error, HfHubHTTPError) else None
    return getattr(response, "status_code", None)


def refusal_wait(error, attempt):
    """The seconds to wait after a refusal: the wait it names (Retry-After), else BACK_OFF doubled each attempt."""
    named = (getattr(error.response, "headers", None) or {}).get("Retry-After")
    try:
        return float(named)
    except (TypeError, ValueError):
        return BACK_OFF * 2 ** attempt


class HubSource:
    """One dataset repository at one revision, read through a huggingface_hub HfApi, every request paced by one
    Pacer shared by the run's threads and tried again after a refusal; `measure` counts requests, refusals for rate
    (`throttled`), retries and bytes downloaded."""

    def __init__(self, api, repo, revision, pacer=None):
        self.api, self.repo, self.revision = api, repo, revision
        self.pacer = pacer or Pacer(limit=None)
        self.measure = {"requests": 0, "throttled": 0, "retries": 0, "bytes": 0}
        self.counting = threading.Lock()

    def counted(self, name, amount=1):
        with self.counting:
            self.measure[name] += amount

    def asked(self, call):
        """`call()` (one request) paced, tried again while the hub refuses it for rate or a passing fault, every
        thread held for the wait the refusal names; the last refusal raised."""
        for attempt in range(TRIES):
            self.pacer.tick()
            self.counted("requests")
            try:
                return call()
            except Exception as error:
                status = refusal_status(error)
                if status not in RETRY or attempt == TRIES - 1:
                    raise
                wait = refusal_wait(error, attempt)
                self.counted("throttled" if status == 429 else "retries")
                print(f"take-in hub: refused ({status}), every request waits {wait:.0f} s ({attempt + 1}/{TRIES})",
                      file=sys.stderr, flush=True)
                self.pacer.back_off(wait)

    def assets(self):
        """The repository's top-level folders, sorted (hidden ones left out)."""
        from huggingface_hub.hf_api import RepoFolder

        def listed():
            found = []
            listing = self.api.list_repo_tree(self.repo, recursive=False, revision=self.revision, repo_type=REPO_TYPE)
            for count, item in enumerate(listing, 1):
                if count % LIST_PAGE == 0:
                    self.pacer.tick()
                if isinstance(item, RepoFolder) and not item.path.startswith("."):
                    found.append(item.path)
            return found
        return sorted(self.asked(listed))

    def download(self, path, destination):
        """One file of the repository at the revision to `destination`; the download's own folder removed after."""
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".hub-") as folder:
            found = self.asked(lambda: self.api.hf_hub_download(self.repo, path, repo_type=REPO_TYPE,
                                                                revision=self.revision, local_dir=folder))
            shutil.move(found, destination)
        self.counted("bytes", destination.stat().st_size)
        return destination


def row_of(sources, repo):
    """The sources.toml row whose take_in table names the repository; Gap when none does."""
    for row in sources.rows:
        if (row.get("take_in") or {}).get("repo") == repo:
            return row
    raise Gap(f"{repo}: no config/sources.toml row has a take_in table naming this repository")


def snake(name):
    """An entry id part of a source name: camel case split, lower case, anything else an underscore."""
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", split)).strip("_")


def where_of(row, repo, revision):
    """Where a revision of the source is: the row's `url` form filled with the repository, at the revision."""
    return f"{row['take_in']['url'].format(repo=repo)} at revision {revision}"


def credit_of(row, repo, revision, asset, changed=False):
    """The credit line of an entry carrying one asset's bytes, its change marked when a layer was changed (CC BY 4.0
    Section 3(a)(1)(B))."""
    words = row["take_in"]
    credit = f"{asset} by {words['creator']}, from {where_of(row, repo, revision)}, {words['licence_name']}"
    return f"{credit}; {CHANGED}" if changed else credit


def licence_of(row, repo, revision, asset, changed=False):
    """The licence block of an entry carrying one asset's bytes (`changed` when a layer of it was changed)."""
    return {"id": row["licence"], "source": row["id"], "credit": credit_of(row, repo, revision, asset, changed),
            "url": row["take_in"]["licence_url"]}


def read_json(source, path, folder):
    """A small JSON file of the repository, read through the cache and removed after."""
    target = source.download(path, pathlib.Path(folder) / "read.json")
    try:
        return json.loads(target.read_text())
    finally:
        target.unlink()


def bill_of(source, asset, row, folder):
    """(root file path, {path: {sha256, bytes}}) of an asset from its root list and bill of materials."""
    words = row["take_in"]
    try:
        roots = read_json(source, f"{asset}/{words['roots']}", folder).get("entries") or []
        items = read_json(source, f"{asset}/{words['bom']}", folder).get("items") or []
    except Exception as why:  # a missing or unreadable record of the asset: the asset is a gap, the run goes on
        raise Gap(f"its root list or bill of materials could not be read ({type(why).__name__}: {why})") from why
    if not roots:
        raise Gap("its root list names no root file")
    bill = {item["relative_path"]: {"sha256": item["hash"]["sha256"], "bytes": item["size"]} for item in items}
    if roots[0] not in bill:
        raise Gap(f"its root file {roots[0]} is not in its bill of materials")
    return roots[0], bill


def planned(root_path, bill):
    """(root name, kept, left out): the bill's files under the root file's folder by their name there, those it keeps
    and those it leaves out (the `.thumbs` pictures, the `source_assets` copies of files it holds elsewhere)."""
    base = posixpath.dirname(root_path)
    under = {path[len(base) + 1:] if base else path: {**about, "path": path} for path, about in bill.items()
             if not base or path.startswith(base + "/")}
    kept, left = {}, {}
    for name, about in under.items():
        parts = name.split("/")
        (left if THUMBS in parts else kept)[name] = about
    elsewhere = {about["sha256"] for name, about in kept.items() if name.split("/")[0] != COPIES}
    for name in [name for name in kept if name.split("/")[0] == COPIES and kept[name]["sha256"] in elsewhere]:
        left[name] = kept.pop(name)
    return posixpath.basename(root_path), kept, left


def unresolved_in(folder, root_name):
    """The paths the root file's USD (every layer it reaches) names that do not resolve, by name under `folder` where
    they fall inside it."""
    from pxr import UsdUtils
    _, _, missing = UsdUtils.ComputeAllDependencies(str(pathlib.Path(folder) / root_name))
    found = []
    for path in missing:
        try:
            found.append(pathlib.Path(path).resolve().relative_to(pathlib.Path(folder).resolve()).as_posix())
        except ValueError:
            found.append(path)
    return sorted(set(found))


def plain(value):
    """A USD value as JSON: numbers rounded, vectors and arrays as lists, asset paths as their path."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if hasattr(value, "path") and not hasattr(value, "__len__"):
        return value.path
    try:
        return [plain(each) for each in value]
    except TypeError:
        return str(value)


def default_of(spec, name):
    """The default value of a prim spec's attribute, or None."""
    return spec.attributes[name].default if name in spec.attributes else None


def shaders_under(spec):
    """Every prim spec under a spec that has an info:id, by its id: {id: [spec]}."""
    found = collections.defaultdict(list)
    for child in spec.nameChildren:
        shader_id = default_of(child, "info:id")
        if shader_id:
            found[str(shader_id)].append(child)
        for key, specs in shaders_under(child).items():
            found[key] += specs
    return found


def material_specs(spec):
    """Every Material prim spec under a spec (the layer's pseudo-root for all of them)."""
    found = []
    for child in spec.nameChildren:
        if child.typeName == "Material":
            found.append(child)
        found += material_specs(child)
    return found


def map_role(inputs):
    """A map's role from the UsdPreviewSurface inputs it feeds: `orm` for one feeding roughness and metallic."""
    if {"roughness", "metallic"} <= set(inputs):
        return "orm"
    return "_".join(sorted(INPUT_ROLES.get(each, snake(each)) for each in inputs))


def tiles_of(name, kept):
    """[(tile, name)] of a map's files the asset holds: (None, name) for one map, each UDIM tile's number and name
    for a pattern naming `<UDIM>`."""
    if UDIM not in name:
        return [(None, name)] if name in kept else []
    head, tail = name.split(UDIM, 1)
    pattern = re.compile(re.escape(head) + r"(\d{4})" + re.escape(tail))
    return sorted((found.group(1), other) for other in kept if (found := pattern.fullmatch(other)))


def map_name(role, name, tile=None):
    """A surface's own name of one map: its role and the source's ending (`albedo.png`), a UDIM tile's number
    (`albedo.1001.png`) or, for the pattern, `<UDIM>` (`albedo.<UDIM>.png`) between."""
    middle = f".{tile}" if tile else f".{UDIM}" if UDIM in name else ""
    return f"{role}{middle}{posixpath.splitext(name)[1].lower()}"


def texture_name(layer_name, asset_path, kept):
    """The entry name of a map a layer names (a UDIM pattern as it is); Gap when the asset holds no file of it."""
    name = posixpath.normpath(posixpath.join(posixpath.dirname(layer_name), asset_path))
    if not tiles_of(name, kept):
        raise Gap(f"{layer_name} names a map {asset_path}, which the asset does not hold")
    return name


def preview_of(layer, material, layer_name, kept):
    """(maps {role: entry name}, constants {input: value}) of a Material spec's UsdPreviewSurface; Gap when it has
    none."""
    shaders = shaders_under(material)
    if not shaders.get("UsdPreviewSurface"):
        raise Gap(f"{layer_name} {material.path}: no UsdPreviewSurface, so no surface can name its look")
    feeds, constants = collections.defaultdict(list), {}
    for attribute in shaders["UsdPreviewSurface"][0].attributes:
        if not attribute.name.startswith("inputs:"):
            continue
        source = attribute.connectionPathList.GetAddedOrExplicitItems()
        texture = layer.GetPrimAtPath(source[0].GetPrimPath()) if source else None
        file_path = default_of(texture, "inputs:file") if texture else None
        if file_path is not None:
            feeds[texture_name(layer_name, file_path.path, kept)].append(attribute.name[len("inputs:"):])
        elif attribute.default is not None:
            constants[attribute.name[len("inputs:"):]] = plain(attribute.default)
    maps = {}
    for name, inputs in sorted(feeds.items()):
        role = map_role(inputs)
        maps[role if role not in maps else f"{role}_{len(maps)}"] = name
    return maps, constants


def normal_convention_of(layer, material):
    """(convention, assumed) of a Material spec's normal map: `directx` when the map's reader flips green (a negative
    green scale) or its name says DirectX, `opengl` when its name says OpenGL, else ("opengl", True)."""
    shaders = shaders_under(material).get("UsdPreviewSurface") or []
    attribute = shaders[0].attributes["inputs:normal"] if shaders and "inputs:normal" in shaders[0].attributes else None
    source = attribute.connectionPathList.GetAddedOrExplicitItems() if attribute else []
    texture = layer.GetPrimAtPath(source[0].GetPrimPath()) if source else None
    if texture is None:
        return "opengl", True
    scale = default_of(texture, "inputs:scale")
    if scale is not None and scale[1] < 0:
        return "directx", False
    file_path = default_of(texture, "inputs:file")
    name = posixpath.basename(file_path.path).lower() if file_path is not None else ""
    for convention, words in NORMAL_WORDS.items():
        if any(word in name for word in words):
            return convention, False
    return "opengl", True


def settings_of(material):
    """The material's own settings: the tile scale of its UV transform, its non-visual tags and its physics."""
    scale = [default_of(spec, "inputs:scale") for spec in shaders_under(material).get("UsdTransform2d", [])]
    nonvisual, physics = {}, {}
    for attribute in material.attributes:
        if attribute.name.endswith(":nonvisual:base") or attribute.name.endswith(":nonvisual:coating"):
            nonvisual[attribute.name.rsplit(":", 1)[1]] = plain(attribute.default)
        elif "physics_" in attribute.name:
            physics[attribute.name.rsplit("physics_", 1)[1]] = plain(attribute.default)
    return {"tile_scale": plain(scale[0]) if scale and scale[0] is not None else None, "nonvisual": nonvisual,
            "physics": physics}


def looks_in(material):
    """Whether a Material spec defines a look: a shader under it or an output. An empty one is a physics material,
    its PhysicsMaterialAPI given by another layer and bound for physics only (`material:binding:physics`)."""
    return bool(material.nameChildren) or any(attribute.name.startswith("outputs:")
                                              for attribute in material.attributes)


def materials_in(folder, kept):
    """Every Material the asset's layers define with a look: [{layer, prim, name, maps, constants, settings}]."""
    from pxr import Sdf
    found = []
    for name in sorted(name for name in kept if name.endswith(LAYERS)):
        layer = Sdf.Layer.FindOrOpen(str(pathlib.Path(folder) / name))
        if layer is None:
            raise Gap(f"{name} does not open as a USD layer")
        for material in material_specs(layer.pseudoRoot):
            if material.specifier != Sdf.SpecifierDef or not looks_in(material):
                continue
            maps, constants = preview_of(layer, material, name, kept)
            convention, assumed = normal_convention_of(layer, material)
            found.append({"layer": name, "prim": str(material.path), "name": material.name, "maps": maps,
                          "constants": constants, "settings": {**settings_of(material), "normal_convention": convention,
                                                               "normal_convention_assumed": assumed}})
    return found


def face_areas(mesh, uv_name, xform):
    """(world area per face, UV area per face) of a mesh, each face fanned into triangles; the UV areas zero when
    the mesh has no per-vertex or per-face-vertex UVs of that name."""
    import numpy
    from pxr import UsdGeom
    counts = numpy.array(mesh.GetFaceVertexCountsAttr().Get() or [], dtype=numpy.int64)
    indices = numpy.array(mesh.GetFaceVertexIndicesAttr().Get() or [], dtype=numpy.int64)
    points = numpy.array(mesh.GetPointsAttr().Get() or [], dtype=numpy.float64).reshape(-1, 3)
    if counts.sum() != len(indices) or (len(indices) and indices.max() >= len(points)) or (counts < 0).any():
        return numpy.zeros(len(counts)), numpy.zeros(len(counts))  # a mesh its own counts do not describe: no area
    world = (numpy.hstack([points, numpy.ones((len(points), 1))]) @ numpy.array(xform))[:, :3]
    triangles = numpy.clip(counts - 2, 0, None)
    face = numpy.repeat(numpy.arange(len(counts)), triangles)
    step = numpy.arange(len(face)) - numpy.repeat(numpy.cumsum(triangles) - triangles, triangles) + 1
    first = (numpy.cumsum(counts) - counts)[face]
    corners = [first, first + step, first + step + 1]
    spans = [world[indices[corner]] for corner in corners]
    world_area = numpy.linalg.norm(numpy.cross(spans[1] - spans[0], spans[2] - spans[0]), axis=1) / 2
    uv_area = numpy.zeros(len(face))
    primvar = UsdGeom.PrimvarsAPI(mesh.GetPrim()).GetPrimvar(uv_name)
    values = primvar.ComputeFlattened() if primvar and primvar.HasValue() else None
    interpolation = primvar.GetInterpolation() if values is not None else None
    if interpolation in ("faceVarying", "vertex", "varying"):
        uv = numpy.array(values, dtype=numpy.float64).reshape(-1, 2)
        at = [corner if interpolation == "faceVarying" else indices[corner] for corner in corners]
        if all(len(each) == 0 or each.max() < len(uv) for each in at):
            edges = [uv[at[1]] - uv[at[0]], uv[at[2]] - uv[at[0]]]
            uv_area = numpy.abs(edges[0][:, 0] * edges[1][:, 1] - edges[0][:, 1] * edges[1][:, 0]) / 2
    sums = numpy.zeros((2, len(counts)))
    numpy.add.at(sums[0], face, world_area)
    numpy.add.at(sums[1], face, uv_area)
    return sums[0], sums[1]


def bound_areas(stage, uv_name_of):
    """{material prim path: [world area, UV area]} over every mesh of a stage, each face counted under the material
    bound to it (a material-bind subset's, else its mesh's); `uv_name_of(material prim)` names the UV set read."""
    from pxr import Usd, UsdGeom, UsdShade
    found = collections.defaultdict(lambda: [0.0, 0.0])
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        binding = UsdShade.MaterialBindingAPI(prim)
        own = binding.ComputeBoundMaterial()[0]
        bound = [(UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial()[0],
                  list(subset.GetIndicesAttr().Get() or [])) for subset in binding.GetMaterialBindSubsets()]
        mesh = UsdGeom.Mesh(prim)
        counts = len(mesh.GetFaceVertexCountsAttr().Get() or [])
        bound = [(material, [index for index in faces if 0 <= index < counts]) for material, faces in bound]
        taken = {index for _, faces in bound for index in faces}
        bound.append((own, [index for index in range(counts) if index not in taken]))
        xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        cache = {}
        for material, faces in bound:
            if not material or not faces:
                continue
            uv_name = uv_name_of(material.GetPrim())
            if uv_name not in cache:
                cache[uv_name] = face_areas(mesh, uv_name, xform)
            world_area, uv_area = cache[uv_name]
            found[material.GetPath()][0] += float(world_area[faces].sum())
            found[material.GetPath()][1] += float(uv_area[faces].sum())
    return found


def uv_name_of(material):
    """The UV set a composed Material reads (its UsdPrimvarReader_float2's varname), `st` when it names none."""
    from pxr import Usd
    for prim in Usd.PrimRange(material):
        if prim.GetAttribute("info:id").Get() == "UsdPrimvarReader_float2":
            name = prim.GetAttribute("inputs:varname").Get()
            if name:
                return str(name)
    return "st"


def composed_materials(stage, folder):
    """{(prim path, layer name): [composed Material path]}: where each Material spec of the asset's layers lands on
    its stage (a referenced or sublayered spec may land under another path, or more than once)."""
    from pxr import Usd
    found = collections.defaultdict(list)
    base = pathlib.Path(folder).resolve()
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.GetTypeName() != "Material":
            continue
        for spec in prim.GetPrimStack():
            if spec.layer.realPath and pathlib.Path(spec.layer.realPath).resolve().is_relative_to(base):
                name = pathlib.Path(spec.layer.realPath).resolve().relative_to(base).as_posix()
                found[str(spec.path), name].append(prim.GetPath())
    return found


def tile_reason(material, kept, world_area, uv_area, scale):
    """Why a material's maps have no tile size on the asset, or None."""
    if not material["maps"]:
        return NO_MAPS
    if not is_tiling(material, kept):
        return ONE_MODEL_TILE
    if world_area <= 0:
        return NO_MESH
    if uv_area <= 0 or scale[0] * scale[1] == 0:
        return NO_UV_AREA
    return None


def tile_metres_of(folder, root_name, materials, kept):
    """Each material's `tile_metres` (the metres one repeat of its maps covers on the asset: the square root of its
    meshes' world area over their UV area, over its UV transform's scale, in the stage's meters per unit) and
    `tile_metres_reason` (None, or why there is no size), written into its settings."""
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.Open(str(pathlib.Path(folder) / root_name))
    units = UsdGeom.GetStageMetersPerUnit(stage)
    areas = bound_areas(stage, uv_name_of)
    landed = composed_materials(stage, folder)
    for material in materials:
        paths = [path for path in landed.get((material["prim"], material["layer"]), []) if path in areas]
        world_area, uv_area = (sum(areas[path][axis] for path in paths) for axis in (0, 1))
        scale = material["settings"]["tile_scale"] or [1.0, 1.0]
        reason = tile_reason(material, kept, world_area, uv_area, scale)
        size = None if reason else (world_area / uv_area) ** 0.5 * units / abs(scale[0] * scale[1]) ** 0.5
        material["settings"].update(tile_metres=None if reason else round(size, 4), tile_metres_reason=reason)


def maps_key(material, kept):
    """The sha256 a surface is keyed by: of its maps' bytes by role, or of its constants when it has no maps."""
    content = ({"maps": {role: kept[name]["sha256"] if UDIM not in name else
                         {tile: kept[each]["sha256"] for tile, each in tiles_of(name, kept)}
                         for role, name in material["maps"].items()}}
               if material["maps"] else {"constants": material["constants"]})
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def is_tiling(material, kept):
    """A material whose maps tile (a shared set, `tile` in its maps' names), or one with no maps."""
    return all("tile" in posixpath.basename(name).lower() for name in material["maps"].values())


def look_layer(prim_name, maps, constants, scale, convention="opengl"):
    """Our own material.usda text: a UsdPreviewSurface over the surface's maps under their entry names, a normal map
    read in its `convention`."""
    from pxr import Sdf, Usd, UsdShade
    types = Sdf.ValueTypeNames
    wiring = {"albedo": [("diffuseColor", "rgb", types.Color3f)], "normal": [("normal", "rgb", types.Normal3f)],
              "orm": [("occlusion", "r", types.Float), ("roughness", "g", types.Float), ("metallic", "b", types.Float)],
              "roughness": [("roughness", "r", types.Float)], "metallic": [("metallic", "r", types.Float)],
              "occlusion": [("occlusion", "r", types.Float)], "emission": [("emissiveColor", "rgb", types.Color3f)],
              "opacity": [("opacity", "r", types.Float)]}
    constant_types = {"diffuseColor": types.Color3f, "emissiveColor": types.Color3f, "specularColor": types.Color3f,
                      "roughness": types.Float, "metallic": types.Float, "opacity": types.Float, "ior": types.Float,
                      "occlusion": types.Float, "clearcoat": types.Float, "clearcoatRoughness": types.Float}
    stage = Usd.Stage.CreateInMemory()
    material = UsdShade.Material.Define(stage, f"/{prim_name}")
    stage.SetDefaultPrim(material.GetPrim())
    surface = UsdShade.Shader.Define(stage, f"/{prim_name}/surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    wired = set()
    if maps:
        reader = UsdShade.Shader.Define(stage, f"/{prim_name}/st")
        reader.CreateIdAttr("UsdPrimvarReader_float2")
        reader.CreateInput("varname", types.String).Set("st")
        mapping = UsdShade.Shader.Define(stage, f"/{prim_name}/mapping")
        mapping.CreateIdAttr("UsdTransform2d")
        mapping.CreateInput("in", types.Float2).ConnectToSource(reader.CreateOutput("result", types.Float2))
        if scale:
            mapping.CreateInput("scale", types.Float2).Set(tuple(scale[:2]))
        for role, name in sorted(maps.items()):
            texture = UsdShade.Shader.Define(stage, f"/{prim_name}/{role}")
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", types.Asset).Set(Sdf.AssetPath(f"./{name}"))
            texture.CreateInput("st", types.Float2).ConnectToSource(mapping.CreateOutput("result", types.Float2))
            texture.CreateInput("sourceColorSpace", types.Token).Set("sRGB" if role in SRGB_ROLES else "raw")
            if role == "normal":  # a DirectX map's green flipped into the OpenGL tangent space USD reads
                green = -1.0 if convention == "directx" else 1.0
                texture.CreateInput("scale", types.Float4).Set((2.0, 2.0 * green, 2.0, 1.0))
                texture.CreateInput("bias", types.Float4).Set((-1.0, -green, -1.0, 0.0))
            for field, channel, kind in wiring.get(role, []):
                output = texture.CreateOutput(channel, types.Float3 if channel == "rgb" else types.Float)
                surface.CreateInput(field, kind).ConnectToSource(output)
                wired.add(field)
    for field, value in sorted(constants.items()):
        if field in constant_types and field not in wired:
            surface.CreateInput(field, constant_types[field]).Set(tuple(value) if isinstance(value, list) else value)
    return stage.GetRootLayer().ExportToString()


def surface_record(row, material, tiling):
    """Our own surface.json: the material's settings as the source gives them, and its recipe (none yet; a written
    gap for a set painted for one model)."""
    return {"source": row["id"], "family": "image" if material["maps"] else "constant", "material": material["name"],
            "maps": {role: map_name(role, name) for role, name in material["maps"].items()},
            "constants": material["constants"], **material["settings"], "tiling": tiling, "recipe": None,
            "recipe_gap": None if tiling else ONE_MODEL_GAP}


def asset_tags(folder, kept):
    """The model's tags its layers give: Wikidata class and Q-code, a one-line description, and its simulators."""
    from pxr import Sdf
    tags = collections.defaultdict(list)
    for name in sorted(name for name in kept if name.endswith(LAYERS)):
        layer = Sdf.Layer.FindOrOpen(str(pathlib.Path(folder) / name))
        for prim in layer.pseudoRoot.nameChildren if layer else []:
            for attribute in prim.attributes:
                for word, tag in (("wikidata_class", "wikidata_class"), ("wikidata_qcode", "wikidata_qcode"),
                                  (":documentation", "description")):
                    value = plain(attribute.default)
                    if word in attribute.name and value and not attribute.name.endswith(SEMANTIC_TYPE):
                        tags[tag] += [str(each) for each in (value if isinstance(value, list) else [value])]
    simulators = {word for name in kept for word in ("newton", "mujoco", "physx") if word in name.lower()}
    found = {tag: sorted(set(values)) for tag, values in tags.items()}
    return {**found, "engine": ["usd"], **({"simulators": sorted(simulators)} if simulators else {})}


def root_usd(folder, root_name):
    """The model's `usd` block (less its library path): its root file laid by reference, and what the root authors."""
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(pathlib.Path(folder) / root_name))
    info = layer.pseudoRoot
    return {"root": root_name, "use": "reference", "layer": "base",
            "default_prim": f"/{layer.defaultPrim}" if layer.defaultPrim else None,
            "up_axis": str(info.GetInfo("upAxis")) if info.HasInfo("upAxis") else None,
            "meters_per_unit": float(info.GetInfo("metersPerUnit")) if info.HasInfo("metersPerUnit") else None}


def named_surfaces(folder, materials, addresses):
    """Each Material's layer given `score:surface` = its surface's address and saved; the layer names changed."""
    from pxr import Sdf
    changed = sorted({material["layer"] for material in materials})
    for name in changed:
        layer = Sdf.Layer.FindOrOpen(str(pathlib.Path(folder) / name))
        for material in [each for each in materials if each["layer"] == name]:
            spec = layer.GetPrimAtPath(material["prim"])
            attribute = Sdf.AttributeSpec(spec, SURFACE_ATTRIBUTE, Sdf.ValueTypeNames.String, Sdf.VariabilityUniform,
                                          True)
            attribute.default = addresses[material["prim"], material["layer"]]
        if not layer.Save():
            raise Gap(f"{name}: the layer naming its surfaces could not be saved")
    return changed


def unchanged_copies(changed, kept, folder, pairs):
    """The queued files whose layer was changed to name its surfaces: each source sha put from an unchanged copy in
    the asset, or no longer put when there is none."""
    for name in changed:
        sha256 = kept[name]["sha256"]
        if pairs.get(sha256) != folder / name:
            continue
        copies = [other for other in sorted(kept) if kept[other]["sha256"] == sha256 and other not in changed]
        if copies:
            pairs[sha256] = folder / copies[0]
        else:
            del pairs[sha256]


class Run:
    """One take-in of a repository at a revision: what waits in the cache to be put, and the report."""

    def __init__(self, root, blobs, source, sources, row, world, cache, cache_bytes=CACHE_BYTES, save=None,
                 retake=False):
        self.root, self.blobs, self.source, self.sources, self.row = root, blobs, source, sources, row
        self.world, self.cache, self.cache_bytes, self.save = world, pathlib.Path(cache), cache_bytes, save
        self.retake = retake
        self.prefix = row["take_in"]["prefix"]
        self.pairs, self.records, self.cached, self.queued_assets = {}, [], 0, []
        self.stored = set()
        self.lock = threading.RLock()  # the run's shared state: surfaces, the queue and its batch's put
        self.order, self.ended, self.turn = {}, set(), 0  # assets settle in the run's order, however many side by side
        self.turns = threading.Condition()
        self.batches = Pacer(limit=BATCH_LIMIT, window=BATCH_WINDOW)
        self.surfaces = self.known_surfaces()
        self.ids = {}
        self.report = {"new": [], "unchanged": [], "changed draft": [], "new version": [], "refused": [], "gaps": []}

    def known_surfaces(self):
        """{maps key: address} of the library's surfaces from this source, newest version of each id."""
        found = {}
        for record in entries.all_entries(self.root, "surface"):
            for key in record.get("tags", {}).get("maps_key", []):
                if record["state"] != "withdrawn":
                    found[key] = record["address"]
        return found

    def about(self, asset):
        """The provenance an entry of one asset is taken in with."""
        return {"world": self.world, "commit": self.source.revision,
                "taken_from": f"{where_of(self.row, self.source.repo, self.source.revision)}: {asset}"}

    def held(self, sha256):
        """Whether the blob store holds a blob, or this run puts it before any record naming it is written."""
        if sha256 in self.stored or sha256 in self.pairs:
            return True
        if self.blobs.has(sha256):
            self.stored.add(sha256)
            return True
        return False

    def fetched(self, asset, names, kept, folder, pairs):
        """The named files of an asset into its cache folder: each downloaded and checked against its bill, or an
        empty stand-in when the store already holds it (a layer always downloaded, to be read)."""
        got = {}
        for name in names:
            about = kept[name]
            target = entries.inside(folder, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if about["sha256"] in got:  # a layer copied, not linked, so naming its surfaces changes it alone
                (shutil.copyfile if name.endswith(LAYERS) else os.link)(got[about["sha256"]], target)
            elif not name.endswith(LAYERS) and self.held(about["sha256"]):
                target.touch()
            else:
                self.source.download(f"{asset}/{about['path']}", target)
                try:
                    check_sha(target, about["sha256"])
                except BadBlob as why:
                    raise Gap(f"{name}: {why}") from why
                got[about["sha256"]] = target
                pairs[about["sha256"]] = target

    def completed(self, asset, root_name, kept, left, folder, pairs):
        """Every file the asset's USD names in its cache folder: a left-out file it names taken back, the rest
        downloaded; the paths that resolve outside it (bare MDL library names) returned; Gap for any other."""
        self.fetched(asset, sorted(kept), kept, folder, pairs)
        while True:
            missing = unresolved_in(folder, root_name)
            back = [name for name in missing if name in left]
            if not back:
                break
            for name in back:
                kept[name] = left.pop(name)
            self.fetched(asset, back, kept, folder, pairs)
        outside = [path for path in missing if "/" not in path and path.endswith(".mdl")]
        unheld = [path for path in missing if path not in outside]
        if unheld:
            raise Gap(f"its USD names {', '.join(unheld)}, which the asset does not hold")
        return outside

    def surface_listed(self, asset, material, kept, key, folder):
        """The take-in list row of one material's surface entry, its own two files written into `folder`."""
        tiling = is_tiling(material, kept)
        entry_id = f"{self.prefix}_{snake(material['name'])}_{key[:8]}"
        folder = pathlib.Path(folder) / entry_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "material.usda").write_text(look_layer(entry_id, {role: map_name(role, name)
                                                                    for role, name in material["maps"].items()},
                                                         material["constants"], material["settings"]["tile_scale"],
                                                         material["settings"]["normal_convention"]))
        (folder / "surface.json").write_text(entries.dumps(surface_record(self.row, material, tiling)))
        files = {"material.usda": {"path": str(folder / "material.usda"), "from": LOOK_WRITTEN},
                 "surface.json": {"path": str(folder / "surface.json"), "from": RECORD_WRITTEN}}
        for role, name in material["maps"].items():
            for tile, each in tiles_of(name, kept):
                files[map_name(role, name, tile)] = {
                    "path": str(pathlib.Path(self.cache) / asset / each), "from": f"{asset}/{kept[each]['path']}",
                    "sha256": kept[each]["sha256"], "bytes": kept[each]["bytes"]}
        made_with = [{"what": "its look and record", "source": "score"}]
        if material["maps"]:
            made_with.append({"what": f"the {material['name']} maps of {asset}", "source": self.row["id"],
                              "carried": True})
        else:
            made_with.append({"what": f"the {material['name']} settings of {asset}", "source": self.row["id"]})
        listed = {"kind": "surface", "id": entry_id, "title": material["name"], "files": files,
                  "made_with": made_with, "tags": {"maps_key": [key], "engine": ["usd"]}}
        if not tiling:
            listed["tags"]["painted_for"] = [asset]
        if material["maps"]:
            listed["licence"] = licence_of(self.row, self.source.repo, self.source.revision, asset)
        return listed

    def surfaces_of(self, asset, materials, kept, folder, queued, pairs):
        """{(prim, layer): address} of an asset's materials, each new surface's record and files queued."""
        addresses, mine = {}, {}
        for material in materials:
            key = maps_key(material, kept)
            if key not in self.surfaces and key not in mine:
                listed = self.surface_listed(asset, material, kept, key, folder / ".surfaces")
                status, record = take_in.settle(self.root, listed, self.about(asset), self.sources)
                mine[key] = record["address"]
                if status != "unchanged":
                    queued.append((status, record))
                    for name in ("material.usda", "surface.json"):
                        pairs[record["files"][name]["sha256"]] = pathlib.Path(listed["files"][name]["path"])
            addresses[material["prim"], material["layer"]] = self.surfaces.get(key) or mine[key]
        return addresses, mine

    def model_listed(self, asset, entry_id, root_name, kept, changed, outside, folder, surfaces):
        """The take-in list row of an asset's model entry."""
        files = {}
        for name, about in sorted(kept.items()):
            files[name] = {"path": str(folder / name), "from": f"{asset}/{about['path']}"}
            if name in changed:
                files[name]["from"] += f" ({CHANGED})"
            else:
                files[name].update(sha256=about["sha256"], bytes=about["bytes"])
        return {"kind": "model", "id": entry_id, "title": asset, "files": files, "usd": root_usd(folder, root_name),
                "requires": sorted(set(surfaces)), "unresolved": outside, "tags": asset_tags(folder, kept),
                "made_with": [{"what": f"{asset}: its model, physics and maps", "source": self.row["id"],
                               "carried": True},
                              {"what": "the library surface each material names (score:surface)", "source": "score"}],
                "licence": licence_of(self.row, self.source.repo, self.source.revision, asset, bool(changed))}

    def entry_id(self, asset):
        """The model entry id of an asset; Gap when another asset of the run maps to the same id."""
        entry_id = f"{self.prefix}_{snake(asset)}"
        if not entries.ID_PATTERN.match(entry_id):
            raise Gap(f"{entry_id}: not an entry id")
        if self.ids.setdefault(entry_id, asset) != asset:
            raise Gap(f"its id {entry_id} is the id of {self.ids[entry_id]} too")
        return entry_id

    def taken(self, asset, entry_id):
        """Whether the asset's model entry already holds this revision (with `save`, committed too)."""
        found = entries.versions(self.root, "model", entry_id)
        if not found or found[-1]["provenance"].get("taken_from") != self.about(asset)["taken_from"]:
            return False
        return not (self.save and uncommitted(self.root, f"entries/model/{entry_id}"))

    def in_order(self, assets):
        """Settle `assets` in this order: a surface two of them share is settled by the first, as one at a time
        would."""
        self.order = {asset: place for place, asset in enumerate(assets)}

    def wait_turn(self, asset):
        """Wait until every asset before this one in the run's order has ended."""
        with self.turns:
            self.turns.wait_for(lambda: self.turn >= self.order.get(asset, self.turn))

    def end_turn(self, asset):
        """Mark this asset ended, letting the next waiting one settle."""
        with self.turns:
            self.ended.add(self.order.get(asset))
            while self.turn in self.ended:
                self.turn += 1
            self.turns.notify_all()

    def take(self, asset):
        """One asset: its surfaces and model planned, fetched and queued, or a gap row; the cache put when full."""
        try:
            self.taken_or_gap(asset)
        finally:
            self.end_turn(asset)

    def taken_or_gap(self, asset):
        """One asset taken (its entries queued, the cache put when full), left as taken, or a gap row."""
        print(f"take-in hub: {asset}", file=sys.stderr, flush=True)
        try:
            entry_id = self.entry_id(asset)
            if not self.retake and self.taken(asset, entry_id):
                self.report["unchanged"].append(entries.address_of("model", entry_id,
                                                                   entries.versions(self.root, "model",
                                                                                    entry_id)[-1]["version"]))
                return
            self.queue(asset, entry_id)
        except (Gap, take_in.Refused, NotAdmitted, entries.UnsafeName) as why:
            self.report["gaps"].append({"asset": asset, "why": str(why)})
            shutil.rmtree(self.cache / asset, ignore_errors=True)
            return
        with self.lock:
            if self.cached >= self.cache_bytes:
                self.flush()

    def queue(self, asset, entry_id):
        """An asset's entries settled and their files queued for the store (Gap and the rest raised)."""
        folder = self.cache / asset
        root_path, bill = bill_of(self.source, asset, self.row, folder)
        root_name, kept, left = planned(root_path, bill)
        for name in kept:
            entries.checked_name(name)
        pairs, queued = {}, []
        outside = self.completed(asset, root_name, kept, left, folder, pairs)
        materials = materials_in(folder, kept)
        tile_metres_of(folder, root_name, materials, kept)
        self.wait_turn(asset)
        with self.lock:  # a surface new to the run settled once, however many assets side by side name it
            addresses, mine = self.surfaces_of(asset, materials, kept, folder, queued, pairs)
            changed = named_surfaces(folder, materials, addresses)
            unchanged_copies(changed, kept, folder, pairs)
            listed = self.model_listed(asset, entry_id, root_name, kept, changed, outside, folder, addresses.values())
            status, record = take_in.settle(self.root, listed, self.about(asset), self.sources)
            for name in changed:
                pairs[record["files"][name]["sha256"]] = folder / name
            queued.append((status, record))
            self.surfaces.update(mine)
            self.pairs.update(pairs)
            self.records += queued
            self.queued_assets.append(asset)
            self.cached += sum(path.stat().st_size for path in pairs.values())

    def flush(self):
        """One batch: every queued file into the blob store (at most BATCH_LIMIT store commits an hour), then the
        records naming them written and, with --save, saved with the surfaces their models require; the queued
        assets' cache folders removed."""
        with self.lock:
            for _ in range(-(-len(self.pairs) // COMMIT_FILES)):
                self.batches.tick()
            put_all(self.blobs, sorted(self.pairs.items()))
            self.stored.update(self.pairs)
            for status, record in self.records:
                entries.write(self.root, record)
                self.report[status].append(record["address"])
            written = sorted({address for _, record in self.records
                              for address in (record["address"], *record.get("requires", []))})
            for asset in self.queued_assets:
                shutil.rmtree(self.cache / asset, ignore_errors=True)
            self.pairs, self.records, self.cached, self.queued_assets = {}, [], 0, []
            if self.save and written:
                self.save(written)


def uncommitted(root, path):
    """Whether a path of the library differs from its last commit (written, changed or untracked); never in a
    library that is no git repository."""
    if not handover.is_repository(root):
        return False
    done = handover.git(root, "status", "--porcelain", "--", path)
    return done.returncode != 0 or bool(done.stdout.strip())


def taken_side_by_side(run, assets, jobs):
    """Every asset through `run.take` on `jobs` threads; the first fault raised once the assets begun are done, the
    rest not begun."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(run.take, asset) for asset in assets]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise


def saver(root, repo, revision, report):
    """A function saving one put's entries and the index under the handover's lock (committed and pushed), each
    save's block appended to the report's `saved` list."""
    report.setdefault("saved", [])

    def save(written):
        wait = float(os.environ.get("SCORE_LIB_LOCK_WAIT", handover.LOCK_WAIT))
        with handover.locked(root, wait):
            index.rebuild(root)
            folders = sorted({"entries/{0}/{1}".format(*entries.parse_address(address)[:2]) for address in written})
            message = f"take-in hub: {len(written)} entries from {repo} at {revision[:12]}"
            report["saved"].append(handover.saved(root, message, (*folders, "index"), LIBRARY_BRANCH))
    return save


def take_in_hub(root, blobs, source, only=None, sources=None, world="world1", cache_bytes=CACHE_BYTES, save=False,
                cache_root=None, jobs=1, retake=False):
    """Take in every asset of a source (or those `only` names), `jobs` side by side, its cache a new folder under
    `cache_root` (the system's temp folder by default) removed after; the report {status: [address], gaps: [{asset,
    why}], saved: [...] with `save`, measure: {assets, jobs, seconds, requests, throttled, retries, bytes}}; the index
    rebuilt."""
    if not REVISION.match(source.revision or ""):
        raise Gap(f"{source.revision!r}: take in a pinned revision (a 40-hex commit), never a branch")
    sources = sources or Sources.load()
    row = row_of(sources, source.repo)
    cache = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-take-in-hub-", dir=cache_root))
    run = Run(root, blobs, source, sources, row, world, cache, cache_bytes, retake=retake)
    if save:
        run.save = saver(root, source.repo, source.revision, run.report)
    started = time.monotonic()
    try:
        names = source.assets()
        wanted = sorted(only) if only else names
        for asset in [asset for asset in wanted if asset not in names]:
            run.report["gaps"].append({"asset": asset, "why": "not in the repository at this revision"})
        present = [asset for asset in wanted if asset in names]
        run.in_order(present)
        taken_side_by_side(run, present, jobs)
        run.flush()
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    index.rebuild(root)
    for status, found in run.report.items():
        if status != "saved":
            found.sort(key=lambda each: each["asset"] if isinstance(each, dict) else each)
    run.report["measure"] = {"assets": len(present), "jobs": jobs, "seconds": round(time.monotonic() - started, 1),
                             **getattr(source, "measure", {})}
    return run.report


def hub_source(repo, revision):
    """The real source: the Hugging Face Hub, read with the login's token when there is one, else paced."""
    from huggingface_hub import HfApi, get_token
    return HubSource(HfApi(), repo, revision, pacer_for_token(get_token()))
