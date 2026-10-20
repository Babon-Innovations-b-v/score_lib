"""A glTF document's pictures as WebP at a side, and gltfpack's compression of it into one .glb.

WebP the library bake's own way (score's tools/props/library/stored.py): colour lossy, metal-roughness lossless (lossy
WebP smears its two channels into each other at every region's edge), normal maps lossless over red and green rounded
to every second level. three.js decodes WebP natively (EXT_texture_webp).

gltfpack (meshoptimizer 0.22.0, MIT) through npx --offline, from the local npm cache, never the network: meshes merged
and compressed with meshopt (EXT_meshopt_compression), positions kept as floats (16-bit positions over a place
kilometres across move surfaces by most of a metre), node names kept (-kn) so the viewer names what is clicked. The
light preview is simplified towards LIGHT_RATIO within gltfpack's 1% error, keeping UV and normal seams; the whole copy
never. Never -sa: it collapses across seams and past any error, which smeared every baked finish into streaks and
dropped a model's small parts (score's window_lit_1.square and parts_trolley_1, 2026-10-20).
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess

GLTFPACK = "gltfpack@0.22.0"
FULL_SIDE = 1024
LIGHT_SIDE = 256
LIGHT_RATIO = 0.1
COLOUR_QUALITY = 85
NORMAL_STEP = 2
THREADS = "1"
BLOB_NAME = re.compile(r"^[0-9a-f]{64}$")


def kind_of(name):
    """Which map a picture is by its name's end: `_metal_roughness`, `_normal`, or colour (anything else)."""
    stem = pathlib.Path(name).stem
    for kind in ("_metal_roughness", "_normal"):
        if stem.endswith(kind):
            return kind
    return "_base_color"


def webp(source, target, side):
    """One picture as WebP at most `side` px a side, the way its kind keeps what is drawn."""
    import numpy as np
    from PIL import Image
    with Image.open(source) as opened:
        picture = opened.convert("RGB")
    picture.thumbnail((side, side), Image.LANCZOS)
    kind = kind_of(pathlib.Path(source).name)
    if kind == "_normal":
        values = np.asarray(picture).astype(np.int32)
        values[..., :2] = np.clip((values[..., :2] + NORMAL_STEP // 2) // NORMAL_STEP * NORMAL_STEP, 0, 255)
        values[..., 2] = 255
        Image.fromarray(values.astype(np.uint8)).save(target, "WEBP", lossless=True, quality=100, method=4)
    elif kind == "_metal_roughness":
        picture.save(target, "WEBP", lossless=True, quality=100, method=4)
    else:
        picture.save(target, "WEBP", quality=COLOUR_QUALITY, method=4)


def cached_webp(source, side, cache):
    """A picture's WebP at a side, made once per picture bytes and side when a cache folder is given: its path."""
    source = pathlib.Path(source).resolve()
    named = BLOB_NAME.match(source.name)  # a picture linked from the blob store is named by its sha256 already
    digest = named.group(0) if named else hashlib.sha256(source.read_bytes()).hexdigest()
    target = cache / f"{digest}-{kind_of(source.name)}-{side}.webp"
    if not target.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(f".partial-{os.getpid()}")
        webp(source, partial, side)
        partial.replace(target)
    return target


def webp_document(gltf_path, folder, side, cache):
    """A copy of the glTF document in `folder` with every picture as WebP at most `side` px: the copy's path."""
    source = pathlib.Path(gltf_path)
    folder.mkdir(parents=True)
    shutil.copyfile(source.parent / "scene.bin", folder / "scene.bin")
    document = json.loads(source.read_text())
    for number, image in enumerate(document.get("images", [])):
        made = cached_webp((source.parent / image["uri"]).resolve(), side, cache)
        name = f"picture_{number}.webp"
        shutil.copyfile(made, folder / name)
        image["uri"] = name
        image.pop("mimeType", None)
    (folder / source.name).write_text(json.dumps(document))
    return folder / source.name


def gltfpack(source, out, simplify):
    """gltfpack the document at `source` into `out` (.glb): meshopt, float positions, names kept, simplified
    within 1% when asked."""
    command = ["npx", "--offline", "-y", GLTFPACK, "-i", str(source), "-o", str(out), "-cc", "-kn", "-km", "-vpf",
               "-vt", "14"]
    if simplify:
        command += ["-si", str(LIGHT_RATIO)]
    done = subprocess.run(command, capture_output=True, text=True,
                          env={**os.environ, "OMP_NUM_THREADS": THREADS, "npm_config_update_notifier": "false"})
    if done.returncode != 0 or not pathlib.Path(out).is_file():
        raise RuntimeError(f"gltfpack failed:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}")


def packed(gltf_path, work, cache):
    """The light preview and the whole copy of a written glTF document: {"light": path, "full": path}."""
    found = {}
    for name, side, simplify in (("light", LIGHT_SIDE, True), ("full", FULL_SIDE, False)):
        document = webp_document(gltf_path, work / f"web-{name}", side, cache)
        found[name] = work / f"{name}.glb"
        gltfpack(document, found[name], simplify)
    return found
