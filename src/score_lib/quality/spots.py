"""Pictures of an entry's worst spot for a part: drawn from the entry's own data with numpy and Pillow, put into the
blob store under their sha256 and named in the part as {sha256, bytes, what}."""
import pathlib
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from score_lib.blobs import file_sha256
from score_lib.preview import mesh as preview

SIZE = 360
FLAGGED = np.array([0.86, 0.12, 0.12])
PLAIN = np.array([0.72, 0.72, 0.70])
# The spot drawn round the worst triangle: the triangles within this share of the model's diagonal.
REACH = 0.18


def stored(picture, blobs, what):
    """Put a Pillow picture into the blob store; its part reference."""
    with tempfile.TemporaryDirectory(prefix="score-lib-spot-") as folder:
        path = pathlib.Path(folder) / "spot.png"
        picture.save(path, format="PNG")
        sha = file_sha256(path)
        blobs.put(sha, path)
        return {"sha256": sha, "bytes": path.stat().st_size, "what": what}


def mesh_spot(model, flagged, worst, blobs, what, colours=None):
    """A close view of the triangles round the worst triangle, seen face on from its outside, in their own colours
    (grey without) with the flagged ones tinted red."""
    corners = model.points[model.triangles]
    middles = corners.mean(axis=1)
    diagonal = float(np.linalg.norm(model.points.max(axis=0) - model.points.min(axis=0))) or 1.0
    near = np.linalg.norm(middles - middles[worst], axis=1) <= REACH * diagonal
    base = np.tile(PLAIN, (len(corners), 1)) if colours is None else colours
    shown = np.where(flagged[:, None], 0.35 * base + 0.65 * FLAGGED, base)[near]
    view = corners[near] @ facing(corners[worst]).T
    return stored(preview.draw(view, shown, SIZE), blobs, what)


def facing(triangle):
    """A turn that brings a triangle's facing towards the viewer (+z, as preview.draw paints larger z nearer),
    tilted a little so its neighbours show depth."""
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    length = float(np.linalg.norm(normal))
    if length <= 0:
        return preview.view_matrix()
    normal = normal / length
    target = np.array([0.0, 0.0, 1.0])
    axis = np.cross(normal, target)
    sine, cosine = float(np.linalg.norm(axis)), float(normal @ target)
    if sine < 1e-9:
        turn = np.eye(3) if cosine > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        axis = axis / sine
        cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        turn = np.eye(3) + sine * cross + (1 - cosine) * cross @ cross
    tilt = np.array([[1, 0, 0], [0, np.cos(0.35), -np.sin(0.35)], [0, np.sin(0.35), np.cos(0.35)]])
    return tilt @ turn


def marked_picture(pixels, marked, blobs, what):
    """A picture (h, w, 3 uint8) with the marked pixels painted red, fitted into SIZE."""
    shown = pixels.copy()
    shown[marked] = (FLAGGED * 255).astype(np.uint8)
    picture = Image.fromarray(shown)
    picture.thumbnail((SIZE * 2, SIZE * 2))
    return stored(picture, blobs, what)


def wave_spot(samples, rate, start, end, blobs, what):
    """A waveform of a sound with the seconds from `start` to `end` shaded red."""
    width, height = SIZE * 2, SIZE // 2
    picture = Image.new("RGB", (width, height), preview.BACKGROUND)
    pen = ImageDraw.Draw(picture)
    seconds = len(samples) / rate
    if seconds > 0:
        pen.rectangle([start / seconds * width, 0, max(end / seconds * width, start / seconds * width + 2), height],
                      fill=(240, 190, 190))
        columns = np.array_split(samples, width)
        for column, chunk in enumerate(columns):
            if len(chunk):
                low, high = float(chunk.min()), float(chunk.max())
                pen.line([column, height / 2 - high * height / 2, column, height / 2 - low * height / 2],
                         fill=(60, 60, 70))
    return stored(picture, blobs, what)


def points_spot(spots, flagged, blobs, what):
    """Particles seen side on (x across, y up), the flagged ones red."""
    width = SIZE * 2
    picture = Image.new("RGB", (width, width), preview.BACKGROUND)
    pen = ImageDraw.Draw(picture)
    if len(spots):
        low, high = spots.min(axis=0), spots.max(axis=0)
        scale = 0.9 * width / max(float(max(high[0] - low[0], high[1] - low[1])), 1e-9)
        for (x_spot, y_spot, _), red in sorted(zip(spots, flagged), key=lambda item: item[1]):
            column = width / 2 + (x_spot - (low[0] + high[0]) / 2) * scale
            row = width / 2 - (y_spot - (low[1] + high[1]) / 2) * scale
            radius = 3 if red else 1
            pen.ellipse([column - radius, row - radius, column + radius, row + radius],
                        fill=(220, 30, 30) if red else (90, 90, 100))
    return stored(picture, blobs, what)
