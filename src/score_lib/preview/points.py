"""A frame of an effect's baked particles: the clip's Points at one time, drawn side on with matplotlib (each
particle its displayColor and displayOpacity)."""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from pxr import Usd, UsdGeom  # noqa: E402

BACKGROUND = "#1d2026"
SECONDS = 5.0


def frame_time(points, seconds, rate):
    """The clip time nearest `seconds` that the points hold samples at (the last one when the clip is shorter)."""
    samples = points.GetPointsAttr().GetTimeSamples()
    if not samples:
        return Usd.TimeCode.Default()
    wanted = seconds * rate
    return min(samples, key=lambda sample: abs(sample - wanted)) if wanted <= samples[-1] else samples[-1]


def first_points(stage):
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Points):
            return UsdGeom.Points(prim)
    return None


def colours(points, time, count, colour):
    """RGBA per particle: displayColor (the clip's, else the emitter's colour) and displayOpacity scaled so the
    thickest particle reads at half strength (thousands of faint particles overlap in a frame)."""
    primvars = UsdGeom.PrimvarsAPI(points)
    found = primvars.GetPrimvar("displayColor")
    opacity = primvars.GetPrimvar("displayOpacity")
    rgb = found.Get(time) if found and found.Get(time) is not None else [colour or (0.8, 0.8, 0.8)]
    rgb = np.array(rgb, dtype=float).reshape(-1, 3)
    alpha = np.array(opacity.Get(time) if opacity and opacity.Get(time) is not None else [1.0], dtype=float)
    alpha = alpha / alpha.max() * 0.5 if alpha.size and alpha.max() > 0 else np.ones(1)
    rgb = np.broadcast_to(rgb ** (1 / 2.2), (count, 3)) if len(rgb) in (1, count) else np.full((count, 3), 0.8)
    alpha = np.broadcast_to(alpha, (count,)) if len(alpha) in (1, count) else np.ones(count)
    return np.c_[np.clip(rgb, 0, 1), np.clip(alpha, 0.03, 1)]


def render(path, destination, colour=None, seconds=SECONDS):
    """Draw one frame of a clip's particles to a png (colour: the emitter's, when the clip holds none); (clip time
    drawn, particles drawn)."""
    stage = Usd.Stage.Open(str(path))
    points = first_points(stage)
    figure, axes = plt.subplots(figsize=(4, 4), dpi=80)
    figure.patch.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    axes.set_axis_off()
    time, count = None, 0
    if points:
        rate = stage.GetTimeCodesPerSecond() or 24.0
        time = frame_time(points, seconds, rate)
        positions = np.array(points.GetPointsAttr().Get(time) or [], dtype=float).reshape(-1, 3)
        count = len(positions)
        if count:
            axes.scatter(positions[:, 0], positions[:, 1], s=1.5, c=colours(points, time, count, colour), linewidths=0)
            axes.set_aspect("equal", adjustable="datalim")
    pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, format="png", facecolor=BACKGROUND, metadata={"Software": None})
    plt.close(figure)
    return (time.GetValue() if hasattr(time, "GetValue") else time), count
