"""A surface's quality score. A recipe surface's entry holds its recipe's settings and constant colour, no baked
tile; a print's holds its picture.

    palette fit         a recipe: its base colour against its palette token's colour (CIE76; the sweep's NEAR 8 is
                        the line). A print: the sweep's rule on its picture, the share of its pixels within NEAR of
                        its ground token, its ink and their mixes (a drawn photograph's own colours are its own)
    ink contrast        a print: CIE76 between its two main colours (ink against ground)
    print resolution    a print: its picture's longer side in pixels (a label is long and thin)
    tiling, close-range detail
                        a recipe: no baked tile in the entry, so not measured here; its seams and grain are judged on
                        every model baked from it (the model's seams and blotches)
"""
import functools

import numpy as np
from PIL import Image
from pxr import Usd, UsdShade

from score_lib.quality import files, parts, spots

TOKEN_DELTA_E = (2.0, 8.0, 20.0)
PRINT_PALETTE = (0.98, 0.9, 0.6)
INK_CONTRAST = (40.0, 20.0, 8.0)
PRINT_SIDE = (1024, 512, 128)
MIXES = np.linspace(0.0, 1.0, 6)


@functools.cache
def library_tools(context):
    context.repo_tool("tools/props/library")
    import library
    import sweep
    return library, sweep


def base_colour(record, context):
    """The material's constant base colour (linear RGB), or None when a texture drives it."""
    folder = files.linked(record, context.blobs, context.work / "surface" / record["id"])
    stage = Usd.Stage.Open(str(folder / record["usd"]["root"]))
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            colour = shader.GetInput("diffuseColor")
            if colour and not colour.HasConnectedSource() and colour.Get() is not None:
                return np.array(colour.Get(), dtype=np.float64)
    return None


def run(record, context):
    about = files.record_json(record, context.blobs, "surface.json") or {}
    if "print.png" in record["files"]:
        return print_parts(record, about, context)
    return recipe_parts(record, about, context)


def recipe_parts(record, about, context):
    library, sweep = library_tools(context)
    tokens = library.token_colours()
    colour = base_colour(record, context)
    token = about.get("token")
    if colour is None or token not in tokens:
        found = [parts.unmeasured("palette fit", f"no constant base colour or no palette token ('{token}')")]
    else:
        step = float(np.linalg.norm(sweep.lab(colour[None])[0] - sweep.lab(np.array(tokens[token])[None])[0]))
        found = [parts.ramped("palette fit", round(step, 2), TOKEN_DELTA_E, f"its base colour against its palette "
                              f"token '{token}' (design/tokens/tokens.json)", weight=2.0, unit="CIE76")]
    why = ("the entry holds its recipe's settings, no baked tile; seams and grain are judged on the models baked "
           "from it")
    found.append(parts.unmeasured("tiling", why))
    found.append(parts.unmeasured("close-range detail", why))
    return found


def print_colours(about, context):
    """The colours a print may show, as L*a*b*: its ground token, its picture's ink, accent and panel tokens and the
    mixes of each with the ground (a stroke's soft edge)."""
    library, sweep = library_tools(context)
    tokens = library.token_colours()
    picture = context.library["pictures"].get(about.get("settings", {}).get("picture", ""), {})
    ground = tokens.get(about.get("token"))
    found = [np.array(ground)] if ground is not None else []
    for key in ("ink", "accent", "panel", "second"):
        if picture.get(key) in tokens:
            ink = np.array(tokens[picture[key]])
            found += [ink * (1 - share) + (found[0] if found else ink) * share for share in MIXES]
    return (sweep.lab(np.array(found)) if found else None), picture


def print_parts(record, about, context):
    library, sweep = library_tools(context)
    path = files.blob_path(record, context.blobs, "print.png")
    with Image.open(path) as opened:
        pixels = on_ground(opened, library.token_colours().get(about.get("token")))
    height, width = pixels.shape[:2]
    found = [parts.ramped("print resolution", max(width, height), PRINT_SIDE, f"its picture's longer side "
                          f"({width} x {height} pixels)", unit="px")]
    small = np.asarray(Image.fromarray(pixels).resize((min(width, 256), min(height, 256))))
    points = sweep.lab(sweep.linear_of(small.reshape(-1, 3)))
    found.append(contrast_part(points))
    palette, picture = print_colours(about, context)
    if picture.get("style") == "drawn":
        found.append(parts.unmeasured("palette fit", "a drawn photograph's own colours are the print's",
                                      parts.NOT_JUDGED))
    elif palette is None:
        found.append(parts.unmeasured("palette fit", "its palette tokens are not in the token file"))
    else:
        nearest = np.min(np.linalg.norm(points[:, None, :] - palette[None, :, :], axis=2), axis=1)
        share = float((nearest < sweep.NEAR).mean())
        part = parts.ramped("palette fit", round(share, 3), PRINT_PALETTE, "share of its pixels within CIE76 8 of "
                            "its ground token, its ink tokens and their mixes", weight=2.0, unit="share")
        if part["value"] < 75:
            part["picture"] = spots.marked_picture(small, (nearest >= sweep.NEAR).reshape(small.shape[:2]),
                                                   context.blobs, "pixels off its palette in red")
        found.append(part)
    found.append(parts.unmeasured("tiling", "a print is laid once, never tiled", parts.NOT_JUDGED))
    return found


def on_ground(picture, ground):
    """A print's pixels (h, w, 3 uint8) as laid: a picture with alpha over its ground token's colour (linear RGB;
    white when it has none), as the material lays its ink over the ground."""
    if "A" not in picture.getbands():
        return np.asarray(picture.convert("RGB"))
    rgba = np.asarray(picture.convert("RGBA")).astype(np.float64) / 255.0
    linear = np.array(ground if ground is not None else (1.0, 1.0, 1.0), dtype=np.float64)
    srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
    alpha = rgba[..., 3:4]
    return np.round(255 * (rgba[..., :3] * alpha + srgb * (1 - alpha))).astype(np.uint8)


def contrast_part(points):
    """CIE76 between the print's two main colours (two means over lightness, split at the middle)."""
    lightness = points[:, 0]
    split = (lightness.min() + lightness.max()) / 2
    dark, light = points[lightness <= split], points[lightness > split]
    if not len(dark) or not len(light):
        return parts.scored("ink contrast", 0, 0.0, "one flat colour: no ink shows", unit="CIE76")
    step = float(np.linalg.norm(np.median(light, axis=0) - np.median(dark, axis=0)))
    return parts.ramped("ink contrast", round(step, 1), INK_CONTRAST, "CIE76 between its ink and its ground (the "
                        "median colours either side of its lightness middle)", unit="CIE76")
