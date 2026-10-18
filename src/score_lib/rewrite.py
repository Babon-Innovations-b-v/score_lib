"""USD asset path rewriting on exported copies. A source layer is opened as an anonymous copy of its file, so nothing
done here can reach the file it was read from; the rewritten layer is exported to a new path."""
import pathlib
import posixpath

from pxr import Sdf, UsdUtils


def open_copy(path):
    """An anonymous layer holding a file's contents (never the registered layer of that file)."""
    return Sdf.Layer.OpenAsAnonymous(str(path))


def asset_paths(layer):
    """Every asset path a layer names (sublayers, references, payloads, asset attributes, clips), as written."""
    found = []
    UsdUtils.ModifyAssetPaths(layer, lambda path: (found.append(path), path)[1])
    return sorted(set(found))


def rewrite(layer, mapping):
    """Replace every asset path the mapping holds; other paths stay as written."""
    UsdUtils.ModifyAssetPaths(layer, lambda path: mapping.get(path, path))


def export(layer, destination):
    """Write a layer to a path (its format from the extension); the path."""
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not layer.Export(str(destination)):
        raise RuntimeError(f"could not export a layer to {destination}")
    return destination


def joined(folder, path):
    """A path written in a layer at `folder` (both relative to an output's root), as a root-relative name."""
    return posixpath.normpath(posixpath.join(folder, path))


def local(path):
    """A path inside an entry, written with ./ as every in-entry path is."""
    return path if path.startswith(("./", "../")) else f"./{path}"


def info(path):
    """{default_prim, up_axis, meters_per_unit, time_codes_per_second} from a layer's metadata (None when unset)."""
    layer = Sdf.Layer.OpenAsAnonymous(str(path), metadataOnly=True)
    root = layer.pseudoRoot
    default = layer.defaultPrim
    return {"default_prim": f"/{default}" if default else None,
            "up_axis": root.GetInfo("upAxis") if root.HasInfo("upAxis") else None,
            "meters_per_unit": root.GetInfo("metersPerUnit") if root.HasInfo("metersPerUnit") else None,
            "time_codes_per_second": layer.timeCodesPerSecond if layer.HasTimeCodesPerSecond() else None}


def gathered(source_layer, source_paths, root_name, extension=".usda"):
    """A new layer with each source prim copied under /<root_name>/<its name>, the root its default prim, and every
    relationship target and connection between the copied prims pointed at the copies."""
    layer = Sdf.Layer.CreateAnonymous(extension)
    root = Sdf.Path(f"/{root_name}")
    Sdf.CreatePrimInLayer(layer, root).specifier = Sdf.SpecifierDef
    layer.GetPrimAtPath(root).typeName = "Xform"
    moved = {}
    for source in sorted(source_paths, key=str):
        target = root.AppendChild(Sdf.Path(source).name)
        if not Sdf.CopySpec(source_layer, Sdf.Path(source), layer, target):
            raise RuntimeError(f"could not copy {source}")
        layer.GetPrimAtPath(target).specifier = Sdf.SpecifierDef
        moved[Sdf.Path(source)] = target
    layer.defaultPrim = root_name
    repoint(layer, moved)
    return layer


def moved_path(path, moved):
    """A path with the prefix of a moved prim replaced by its new place, or the path unchanged."""
    for old, new in moved.items():
        if path.HasPrefix(old):
            return path.ReplacePrefix(old, new)
    return path


def repoint(layer, moved):
    """Point relationship targets and attribute connections at the moved prims."""
    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.RelationshipSpec):
            items = list(spec.targetPathList.GetAddedOrExplicitItems())
            if any(moved_path(item, moved) != item for item in items):
                spec.targetPathList.ClearEdits()
                spec.targetPathList.explicitItems = [moved_path(item, moved) for item in items]
        elif isinstance(spec, Sdf.AttributeSpec):
            items = list(spec.connectionPathList.GetAddedOrExplicitItems())
            if any(moved_path(item, moved) != item for item in items):
                spec.connectionPathList.ClearEdits()
                spec.connectionPathList.explicitItems = [moved_path(item, moved) for item in items]
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)


def copy_prim(source_layer, source_path, destination, prim_name, kind="Xform"):
    """A new layer holding one prim spec copied from a source layer to /<prim_name>, made the default prim; its
    specifier made a def (an over copied alone would hold nothing)."""
    layer = Sdf.Layer.CreateAnonymous(pathlib.Path(destination).suffix)
    target = Sdf.Path(f"/{prim_name}")
    Sdf.CreatePrimInLayer(layer, target)
    if not Sdf.CopySpec(source_layer, Sdf.Path(source_path), layer, target):
        raise RuntimeError(f"could not copy {source_path}")
    spec = layer.GetPrimAtPath(target)
    spec.specifier = Sdf.SpecifierDef
    if not spec.typeName:
        spec.typeName = kind
    layer.defaultPrim = prim_name
    return layer
