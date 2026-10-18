"""A model's quality score: its geometry, its finish and its likeness to its close-up and its plan.

    geometry   holes          open edges' share of the edge length, the points welded (a closed solid has none)
               crowded edges  share of edges more than two triangles use (generated meshes only: a code build joins
                              separate closed parts edge to edge by design)
               surface noise  how unevenly the surface bends point to point (generated meshes of 3,000 triangles or
                              more, not rock, soil, plants or hair, which are rough by nature): melted or blobby
               triangles      against the prop route's budget for its size (place_route.faces_for): too few is a
                              gutted mesh, too many costs the room
    finish     library        share of its finishes that are surface library variants (the owner's hard rule: a
                              generated model's own picture colours are never kept)
               palette        the room sweep's rule: share of its texels within reach of what its places' library
                              surfaces bake to (sweep.ON_PALETTE is the line)
               blotches       the sweep's blotchiness, the lightness spread inside 8 x 8 texel tiles
               seams          share of its smooth UV cuts where the colour steps visibly
               texel density  its normal map's texels a metre against the route's density (FAR, DISTANT for far_)
               bake           the surfaces check's colours: each part's bake against its surface and its close-up
    likeness   outline        the likeness stage's outline overlap with its close-up (OUTLINE_LEAST is the line)
               plan shape     its box's proportions against its kit piece's planned size
               scale          its largest side against its kit piece's
               stands level   the surfaces check's foot lifts (STANCE_GAP is the line)
               by eye         needs a paid judge
"""
import functools
import math

import numpy as np

from score_lib.quality import files, mesh as meshes, parts, spots, texture

HOLES = (0.0, 0.001, 0.01)
CROWDED = (0.0, 0.001, 0.01)
NOISE = (0.015, 0.04, 0.08)
NOISE_LEAST_TRIANGLES = 3000
TRIANGLES_UNDER = (0.6, 0.3, 0.1)
TRIANGLES_OVER = (1.05, 1.5, 3.0)
LIBRARY_SHARE = (1.0, 0.999, 0.0)
PALETTE = (0.98, 0.9, 0.6)
# Blotches and seams have no framework gate: their marks are set from a 160-model sample of world 1 (2026-10-18),
# good near the median, the line near the 75th percentile, bad near the 90th (blotches 3.6 / 10.4 / 21.4, seams
# 0.035 / 0.26 / 1.9 metres a metre).
BLOTCHES = (4.0, 10.0, 20.0)
SEAMS = (0.05, 0.3, 1.5)
TEXELS = (1.0, 0.5, 0.25)
BAKE_DELTA_E = (5.0, 20.0, 40.0)
CLOSE_UP_DELTA_E = (16.0, 32.0, 50.0)
OUTLINE = (0.8, 0.5, 0.3)
PLAN_SHAPE = (0.05, 0.25, 0.7)
SCALE = (0.05, 0.2, 0.7)
STANDS = (0.0, 0.03, 0.1)
# Texels a metre of the normal map when the route names none for a model (tools/props/library/route.py): FAR, and
# DISTANT for scenery seen only from afar (a far_ kind).
FAR_TEXELS = 512
DISTANT_TEXELS = 128
DISTANT_PREFIX = "far_"
# Finishes rough by nature, whose surface noise is their look.
ROUGH_FAMILIES = ("growing", "moon_surface", "earth_land", "hair", "water")
ROUGH_VARIANTS = ("mars_regolith", "mars_sand", "mars_basalt", "mars_layered_rock", "foliage_leaf")
ROUGH_WORDS = ("boulder", "rock", "crop", "plant", "palm", "fern", "soil")
# A part below this gets a picture of its worst spot.
PICTURED_UNDER = 75


def route_of(record, made):
    """'code' for a code build, 'prop' for a generated model painted by the surface line, 'fixture' for a model
    brought in whole with its own picture texture."""
    if (made.get("kit") or {}).get("route") == "code":
        return "code"
    if any(name.endswith("_0.png") or "_glow_0" in name for name in record["files"]):
        return "fixture"
    return "prop"


def run(record, context):
    """The model's score parts."""
    made = files.record_json(record, context.blobs, "model.json") or {}
    folder = files.linked(record, context.blobs, context.work / "model" / record["id"])
    model = meshes.read(folder / record["usd"]["root"])
    if model is None:
        return [parts.scored("geometry", 0, 0, "the model draws no triangle")]
    route = route_of(record, made)
    stem = record["provenance"].get("source_name") or made.get("stem") or record["id"].split(".")[0]
    places = record.get("seen_in") or [record["provenance"].get("place")]
    found = geometry_parts(model, route, record, made, context)
    found += finish_parts(model, route, record, made, stem, places, context)
    found += likeness_parts(model, route, stem, places, context)
    return found


# --- geometry -------------------------------------------------------------------------------------------------------

def geometry_parts(model, route, record, made, context):
    found = [holes_part(model, context)]
    if route == "code":
        found.append(parts.unmeasured("crowded edges", "a code build joins separate closed parts edge to edge by "
                                      "design", parts.NOT_JUDGED))
        found.append(parts.unmeasured("surface noise", "a code build's surfaces are exact by its code",
                                      parts.NOT_JUDGED))
        found.append(parts.unmeasured("triangles", "a code build's triangles are its exact shape", parts.NOT_JUDGED))
        return found
    found.append(crowded_part(model, context))
    found.append(noise_part(model, record, made, context))
    found.append(triangles_part(model, made, context))
    return found


def holes_part(model, context):
    share, flagged = meshes.open_edges(model)
    part = parts.ramped("holes", round(share, 5), HOLES, "open edges' share of the edge length (edges one triangle "
                        "uses, points welded)", unit="share")
    return pictured(part, model, flagged, context, "open edges in red")


def crowded_part(model, context):
    share, flagged = meshes.crowded_edges(model)
    part = parts.ramped("crowded edges", round(share, 5), CROWDED, "share of edges more than two triangles use "
                        "(non-manifold)", unit="share")
    return pictured(part, model, flagged, context, "edges more than two triangles use, in red")


def rough_by_nature(model, record, made):
    """Whether the model's finishes or its row's name are rock, soil, plants or hair."""
    if set(model.materials) & set(ROUGH_VARIANTS):
        return True
    words = " ".join([record["id"], made.get("row") or "", made.get("kind") or ""])
    return any(word in words for word in ROUGH_WORDS)


def noise_part(model, record, made, context):
    if len(model.triangles) < NOISE_LEAST_TRIANGLES:
        return parts.unmeasured("surface noise", f"{len(model.triangles)} triangles, under "
                                f"{NOISE_LEAST_TRIANGLES}: too few to tell noise from shape", parts.NOT_JUDGED)
    families = {context.variant_families.get(name) for name in model.materials}
    if families & set(ROUGH_FAMILIES) or rough_by_nature(model, record, made):
        return parts.unmeasured("surface noise", "rock, soil, plants or hair: rough by nature", parts.NOT_JUDGED)
    median, each = meshes.surface_noise(model)
    part = parts.ramped("surface noise", round(median, 4), NOISE, "area-weighted median of how unevenly the "
                        "surface bends point to point, over the edge length (melted or blobby surfaces stand high)",
                        unit="ratio")
    flagged = each >= np.percentile(each, 98)
    return pictured(part, model, flagged, context, "the noisiest 2% of the surface in red", worst=int(each.argmax()))


@functools.cache
def place_route(context):
    context.repo_tool("tools/props/library")
    import place_route as found
    return found


def triangles_part(model, made, context):
    size = float(meshes.size_of(model).max())
    budget = place_route(context).faces_for(made.get("row") or "", [size])
    ratio = len(model.triangles) / budget
    under = parts.ramp(ratio, *TRIANGLES_UNDER)
    over = parts.ramp(ratio, *TRIANGLES_OVER)
    how = (f"{len(model.triangles)} triangles against the prop route's budget of {budget} for its {size:.2f} m "
           f"(place_route.faces_for); under: 100 at {TRIANGLES_UNDER[0]}, 50 at {TRIANGLES_UNDER[1]}, 0 at "
           f"{TRIANGLES_UNDER[2]} of it (gutted); over: 100 at {TRIANGLES_OVER[0]}, 50 at {TRIANGLES_OVER[1]}, 0 at "
           f"{TRIANGLES_OVER[2]}")
    return parts.scored("triangles", min(under, over), round(ratio, 3), how, unit="of budget")


# --- finish ---------------------------------------------------------------------------------------------------------

def finish_parts(model, route, record, made, stem, places, context):
    found = [library_part(model, route, context)]
    glows = (made.get("kit") or {}).get("glows")
    found.append(palette_parts(model, places, glows, context))
    found.append(blotches_part(model, places, context))
    found.append(seams_part(model, context))
    found.append(texels_part(model, record, stem, places, context))
    found += bake_parts(stem, places, context)
    return found


def library_part(model, route, context):
    if route == "code":
        return parts.unmeasured("library", "a code build is painted from the library by its builder; its colours "
                                "are judged by the palette part", parts.NOT_JUDGED)
    names = model.materials
    known = [name for name in names if name in context.variant_families]
    share = len(known) / len(names) if names else 0.0
    others = sorted(set(names) - set(known))
    how = (f"share of its finishes that are surface library variants ({len(known)} of {len(names)}"
           f"{'; not the library: ' + ', '.join(others) if others else ''})")
    if route == "fixture":
        how += "; a model brought in with its own picture texture keeps picture colours (the owner's hard rule)"
    return parts.ramped("library", round(share, 3), LIBRARY_SHARE, how, weight=2.0, unit="share")


def model_palette(places, context):
    found = [context.palette(place) for place in places if place]
    found = [colours for colours in found if colours is not None]
    return np.concatenate(found) if found else None


@functools.lru_cache(maxsize=2)
def judged_texture(model, places, context):
    """The sweep's judgement of the model's colour map (texture.on_palette), made once for its two parts."""
    return texture.on_palette(model, model_palette(places, context), context)


def palette_parts(model, places, glows, context):
    if glows:
        return parts.unmeasured("palette", "a glowing piece is judged by its own light colour", parts.NOT_JUDGED)
    found = judged_texture(model, tuple(places), context)
    if found is None:
        return parts.unmeasured("palette", "no colour map, UVs or place palette to judge")
    judged, off, shown = found
    part = parts.ramped("palette", judged["on_palette"], PALETTE, "the room sweep: share of its texels within "
                        "CIE76 8 of what its places' library surfaces bake to", unit="share")
    if part["value"] < PICTURED_UNDER:
        part["picture"] = spots.marked_picture(shown, off, context.blobs, "its colour map, off-palette texels red")
    return part


def blotches_part(model, places, context):
    found = judged_texture(model, tuple(places), context)
    if found is None:
        return parts.unmeasured("blotches", "no colour map, UVs or place palette to judge")
    return parts.ramped("blotches", found[0]["blotchy"], BLOTCHES, "the room sweep's blotchiness: 75th percentile "
                        "of the lightness spread inside 8 x 8 texel tiles under its UVs (0 to 255)", weight=0.5,
                        unit="levels")


def seams_part(model, context):
    found = texture.seams(model, context)
    if found is None:
        return parts.unmeasured("seams", "no colour map or UVs")
    showing, length, least, flagged = found
    if length <= 0:
        return parts.unmeasured("seams", "no UV cut runs across a smooth surface", parts.NOT_JUDGED)
    reach = math.sqrt(float(meshes.face_areas(model).sum())) or 1.0
    part = parts.ramped("seams", round(showing / reach, 3), SEAMS, f"metres of showing seam over the root of its "
                        f"surface area ({showing:.2f} m of its {length:.2f} m of UV cuts across smooth surface, under "
                        f"{texture.SMOOTH_DEGREES:.0f} degrees, step past CIE76 {least:.1f}: the larger of "
                        f"{texture.SEAM_DELTA_E:.0f} and its own grain's 90th percentile)", unit="m/m")
    worst = int(np.flatnonzero(flagged)[0]) if flagged.any() else None
    return pictured(part, model, flagged, context, "showing seams in red", worst=worst)


@functools.cache
def route_module(context):
    context.repo_tool("tools/props/library")
    import route
    return route


def intended_density(model, stem, places, context):
    """(texels a metre of the normal map the route meant for this model, why): its shared picture set's density
    (route.SET_DENSITY, read off its maps' names), else its own bake job's density (data/surfaces/<place>.json),
    else FAR (DISTANT for a far_ kind)."""
    route = route_module(context)
    name = next(iter(model.maps.values())).name
    for key in sorted(route.SET_DENSITY, key=len, reverse=True):
        if f"_{key}_" in f"_{name}":
            return route.SET_DENSITY[key], f"its picture set '{key}'"
    for place in places:
        job = ((context.repo_json(f"data/surfaces/{place}.json") or {}).get("bake") or {}).get("entries", {})
        # an entry is a dict, or the path of a job file outside the repo (no density this can read)
        if isinstance(job.get(stem), dict) and job[stem].get("density"):
            return int(job[stem]["density"]), f"its bake job in data/surfaces/{place}.json"
    if stem.startswith(DISTANT_PREFIX):
        return DISTANT_TEXELS, "a far_ kind (route.DISTANT)"
    return FAR_TEXELS, "the route's FAR"


def texels_part(model, record, stem, places, context):
    role = "normal" if "normal" in model.maps else "diffuseColor"
    found = meshes.texel_density(model, role)
    if found is None:
        return parts.unmeasured("texel density", "no map or UVs")
    density, each = found
    density = density if role == "normal" else density * 2  # colour is baked at half the normal map's density
    target, why = intended_density(model, stem, places, context)
    part = parts.ramped("texel density", round(density / target, 3), TEXELS, f"{density:.0f} normal-map texels a "
                        f"metre (area-weighted median) against {target}, {why}", unit="of target")
    flagged = each * (1 if role == "normal" else 2) < target * TEXELS[1]
    return pictured(part, model, flagged, context, "triangles under half the target density in red",
                    worst=int(np.argmin(np.where(each > 0, each, np.inf))))


def bake_parts(stem, places, context):
    checked = [context.surface_checks[(place, stem)] for place in places if (place, stem) in context.surface_checks]
    if not checked:
        return [parts.unmeasured("bake", "no surfaces check of this model in the store")]
    surfaces = checked[0].get("surfaces", {})
    worst_surface = max((surface.get("delta_e", 0.0), name) for name, surface in surfaces.items()) if surfaces \
        else (0.0, "")
    found = [parts.ramped("bake", worst_surface[0], BAKE_DELTA_E, f"worst part's bake against its library surface "
                          f"({worst_surface[1]}; the surfaces check, {checked[0]['node']})", unit="CIE76")]
    close_up = checked[0].get("close_up", {})
    steps = [(math.dist(surfaces[name]["baked_lab"], colour), name) for name, colour in close_up.items()
             if name in surfaces and "baked_lab" in surfaces[name]]
    if steps:
        worst = max(steps)
        found.append(parts.ramped("bake against close-up", round(worst[0], 1), CLOSE_UP_DELTA_E, f"worst part's "
                                  f"bake against its colour in the close-up ({worst[1]})", unit="CIE76"))
    lows = checked[0].get("stance_lows") or []
    if lows:
        found.append(parts.ramped("stands level", round(max(lows), 3), STANDS, "highest foot lift off its floor "
                                  "(the surfaces check's quadrants)", unit="m"))
    return found


# --- likeness -------------------------------------------------------------------------------------------------------

def likeness_parts(model, route, stem, places, context):
    found = [outline_part(stem, places, context)]
    found += plan_parts(model, stem, places, context)
    found.append(parts.unmeasured("by eye", "whether it reads as its close-up to a viewer", parts.NEEDS_PAID_JUDGE))
    return found


def outline_part(stem, places, context):
    measured = [context.likeness[(place, stem)] for place in places if (place, stem) in context.likeness]
    if not measured:
        return parts.unmeasured("outline", "no likeness measure of this model in the store (no close-up judged)")
    outline = measured[0].get("outline")
    if outline is None:
        return parts.unmeasured("outline", f"its likeness record holds no outline ({measured[0]['node']})")
    return parts.ramped("outline", outline, OUTLINE, f"best of eight outline overlaps with its close-up (the "
                        f"likeness stage, {measured[0]['node']})", weight=2.0, unit="overlap")


def planned_sizes(stem, places, context):
    """Every planned size (wide, tall, deep) of the kit pieces that lay this model, in its places' kits."""
    found = []
    for place in places:
        for piece in (context.kit(place) or {}).get("pieces", []):
            if piece.get("model") == stem and piece.get("size"):
                found.append(np.asarray(piece["size"], dtype=float))
    return found


def plan_parts(model, stem, places, context):
    planned = planned_sizes(stem, places, context)
    if not planned:
        return [parts.unmeasured("plan shape", "no kit piece lays this model"),
                parts.unmeasured("scale", "no kit piece lays this model")]
    size = np.maximum(meshes.size_of(model), 1e-4)
    wanted = np.maximum(np.median(np.stack(planned), axis=0), 1e-4)
    scale = abs(math.log(float(size.max()) / float(wanted.max())))
    thick = 0.02  # a side thinner than 2 cm is a plate's; its ratio is left out of the shape
    judged = (size > thick) & (wanted > thick)
    proportions = np.abs(np.log(size / size.max()) - np.log(wanted / wanted.max()))
    shape = float(proportions[judged].max()) if judged.any() else 0.0
    sizes = f"{np.round(size, 3).tolist()} m made, {np.round(wanted, 3).tolist()} m planned (wide, tall, deep)"
    return [parts.ramped("plan shape", round(shape, 3), PLAN_SHAPE, f"largest |log| of its sides' proportions "
                         f"against the plan's, sides under 2 cm left out; {sizes}", unit="log"),
            parts.ramped("scale", round(scale, 3), SCALE, f"|log| of its largest side against the plan's; {sizes}",
                         unit="log")]


# --- pictures -------------------------------------------------------------------------------------------------------

def pictured(part, model, flagged, context, what, worst=None):
    """The part with a close picture of its worst spot when it scores under PICTURED_UNDER and has one."""
    if part.get("value") is None or part["value"] >= PICTURED_UNDER or not flagged.any():
        return part
    worst = int(np.flatnonzero(flagged)[0]) if worst is None else worst
    part["picture"] = spots.mesh_spot(model, flagged, worst, context.blobs, what, triangle_colours(model))
    return part


@functools.lru_cache(maxsize=2)
def triangle_colours(model):
    """Each triangle's colour from its colour map at its UV middle (0..1), or None without one."""
    path = model.maps.get("diffuseColor")
    if path is None or model.uvs is None:
        return None
    picture = texture.rgb(path)
    middles = np.nan_to_num(model.uvs.mean(axis=1))
    height, width = picture.shape[:2]
    columns = (np.mod(middles[:, 0], 1.0) * (width - 1)).astype(int)
    rows = ((1.0 - np.mod(middles[:, 1], 1.0)) * (height - 1)).astype(int)
    return picture[rows, columns].astype(np.float64) / 255.0
