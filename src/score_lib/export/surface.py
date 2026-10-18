"""surface entries: every variant of the surface library (`data/library/materials.json`) with the Material a place's
base layer writes for it (spec 4.3)."""
import hashlib
import json
import posixpath

from pxr import Usd, UsdShade

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.admission import SCORE, made_by
from score_lib.preview import swatch
from score_lib.rewrite import copy_prim, export, open_copy

LIBRARY = "data/library/materials.json"
PICTURES = "data/library/pictures"
SPLIT = ("recipe", "token", "reference")


def plan(context):
    """Every library variant as a candidate (one per distinct Material when places write it differently)."""
    library = context.repo.read_json(LIBRARY)
    variants = library_variants(library)
    written = place_materials(context, set(variants))
    candidates = []
    for name, (family, variant) in sorted(variants.items()):
        groups = {}
        for place, text_path in written.get(name, []):
            groups.setdefault(text_path.read_text(), []).append((place, text_path))
        if not groups:
            candidates.append(candidate(context, name, family, variant, [], None))
        for text, uses in sorted(groups.items(), key=lambda item: item[1][0][0]):
            candidates.append(candidate(context, name, family, variant, [place for place, _ in uses], uses[0][1]))
    return candidates


def library_variants(library):
    """{variant name: (family name, variant record)} of the library."""
    found = {}
    for family, record in sorted(library.get("families", {}).items()):
        for name, variant in sorted(record.get("variants", {}).items()):
            found[name] = (family, variant)
    return found


def place_materials(context, names):
    """{variant: [(place, material.usda path)]}: each place's Library Material copied into a layer of its own."""
    found = {}
    for place in context.places:
        output, listing = context.assembly(place)
        if "layers/base.usda" not in listing:
            continue
        layer = open_copy(context.tree(output) / "layers/base.usda")
        work = context.work("surface", place)
        for root in layer.rootPrims:
            library = layer.GetPrimAtPath(root.path.AppendChild("Library"))
            if not library:
                continue
            for child in library.nameChildren:
                if child.typeName == "Material" and child.name in names:
                    copied = copy_prim(layer, child.path, f"{child.name}.usda", child.name, kind="Material")
                    found.setdefault(child.name, []).append((place, export(copied, work / f"{child.name}.usda")))
    return found


def surface_record(name, family, variant):
    """surface.json: the variant's recipe, token, settings, family and reference."""
    return {"variant": name, "family": family, "recipe": variant.get("recipe"), "token": variant.get("token"),
            "reference": variant.get("reference"),
            "settings": {key: value for key, value in sorted(variant.items()) if key not in SPLIT}}


def constants(material_path):
    """(diffuse colour, roughness, metallic) of a Material's UsdPreviewSurface constants."""
    stage = Usd.Stage.Open(str(material_path))
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            values = {}
            for field, default in (("diffuseColor", (0.5, 0.5, 0.5)), ("roughness", 0.5), ("metallic", 0.0)):
                found = shader.GetInput(field)
                values[field] = found.Get() if found and found.Get() is not None else default
            return tuple(values["diffuseColor"]), float(values["roughness"]), float(values["metallic"])
    return None


def printed(context, variant, work, files, roles):
    """A printed variant's print or screen picture (data/library/pictures/<name>.png) as the entry's print.png and
    its picture, or None for a variant that prints nothing."""
    source = f"{PICTURES}/{variant['picture']}.png" if variant.get("picture") else None
    if not source or not context.repo.exists(source):
        return None
    files["print.png"] = context.repo.export(source, work / "print.png")
    roles["print.png"] = "texture"
    return {"file": "print.png", "made_by": "source picture", "of": source}


def picture(context, variant, material, work, files, roles):
    """The surface's picture: its print, else its reference picture when the repo holds it, else a swatch of the
    Material."""
    shown = printed(context, variant, work, files, roles)
    if shown:
        return shown
    reference = (variant.get("reference") or {}).get("picture")
    if reference and context.repo.exists(reference):
        name = "reference" + posixpath.splitext(reference)[1]
        files[name] = context.repo.export(reference, work / name)
        roles[name] = "picture"
        return {"file": name, "made_by": "source picture", "of": reference}
    found = constants(material) if material else None
    if found:
        files["preview.png"] = swatch.render(*found, work / "preview.png")
        roles["preview.png"] = "picture"
        return {"file": "preview.png", "made_by": "preview/swatch.py", "of": "material.usda constants"}
    reason = "no place's stage writes this variant and its reference picture is not in the repository"
    return {"reason": reason}


def candidate(context, name, family, variant, places, material):
    """One surface candidate."""
    record = surface_record(name, family, variant)
    text = json.dumps(record, sort_keys=True) + (material.read_text() if material else "")
    identity = hashlib.sha256(text.encode()).hexdigest()
    work = context.work("surface-entry", name, identity[:12])
    files = {"surface.json": write_json(work / "surface.json", record)}
    roles = {"surface.json": "record"}
    if material:
        files["material.usda"] = material
        roles["material.usda"] = "usd"
    shown = picture(context, variant, material, work, files, roles)
    algorithm = context.by_id.get(("algorithm", "surface_recipes"))
    return Candidate(kind="surface", base_id=place_reads.sanitised(name), identity=identity,
                     title=name.replace("_", " ").capitalize(), places=places, files=files, roles=roles,
                     made_with=[dict(SCORE), made_by("baked by the surface recipes on ProcFunc", "procfunc"),
                                made_by("relief from infinigen2's base materials", "infinigen2")],
                     usd={"root": "material.usda", "layer": "base", "use": "reference"} if material else None,
                     requires=[algorithm] if algorithm else [],
                     provenance={"source": f"{LIBRARY} at the world's commit", "family": family,
                                 "place": places[0] if places else None},
                     tags={"family": [family], "recipe": [variant.get("recipe")] if variant.get("recipe") else []},
                     picture=shown)

