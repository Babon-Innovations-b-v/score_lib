"""A top-down map of one sublayer: where its prims stand in the composed place, drawn as dots over the place's kit
(faint), x across and z down like the kit plan. Positions come from the place's own stage; nothing is rendered."""
import numpy as np
from pxr import Sdf, Usd, UsdGeom

from score_lib.preview import plan

DOT = "#b4583a"


def leaf_paths(layer):
    """Paths of the prims a layer writes that have no child prims of their own in that layer."""
    found = []

    def walk(spec):
        for child in spec.nameChildren:
            if child.nameChildren:
                walk(child)
            else:
                found.append(child.path)

    walk(layer.pseudoRoot)
    return found


def positions(stage, layer_path):
    """(x, z) in metres of each leaf prim a sublayer writes, from the composed stage at its earliest time."""
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    cache = UsdGeom.XformCache(Usd.TimeCode.EarliestTime())
    found = []
    for path in leaf_paths(layer):
        prim = stage.GetPrimAtPath(path)
        if prim and prim.IsA(UsdGeom.Xformable):
            moved = cache.GetLocalToWorldTransform(prim).ExtractTranslation()
            found.append((moved[0], moved[2]))
    return np.array(sorted(set(found)), dtype=float).reshape(-1, 2)


def render(kit, points, destination):
    """Draw a sublayer's prim positions over its place's kit to a png; the number of dots drawn."""
    figure, axes = plan.blank_axes()
    plan.draw_pieces(axes, kit, alpha=0.18)
    if len(points):
        axes.scatter(points[:, 0], points[:, 1], s=9, c=DOT, edgecolors="#1d2026", linewidths=0.3, zorder=3)
    plan.save(figure, axes, destination)
    return len(points)
