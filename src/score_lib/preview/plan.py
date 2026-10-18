"""A top-down plan of a kit: each piece's box (its centre, axes and size) projected onto the ground plane and drawn
as its outline with matplotlib, coloured by the piece's group."""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

BACKGROUND = "#eeece6"
GROUP_COLOURS = ("#4c6a8a", "#b4583a", "#6f8f4e", "#8a6f9e", "#c79a3b", "#3f8f8a", "#9e4f63", "#6b6862")


def footprint(piece):
    """The outline on the ground (x, z) of a piece's box: the hull of its eight corners seen from above."""
    centre = np.array(piece.get("at", [0, 0, 0]), dtype=float)
    axes = [np.array(piece.get(name, default), dtype=float) for name, default in
            (("x", [1, 0, 0]), ("y", [0, 1, 0]), ("z", [0, 0, 1]))]
    size = np.array(piece.get("size", [0.1, 0.1, 0.1]), dtype=float)
    corners = [centre + sum(sign * half * axis for sign, half, axis in zip(signs, size / 2, axes))
               for signs in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)]
    return hull(np.array(corners)[:, [0, 2]])


def hull(points):
    """The convex hull of 2D points (monotone chain), in order."""
    ordered = sorted(map(tuple, np.round(points, 6)))
    if len(ordered) <= 2:
        return np.array(ordered)

    def turn(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower, upper = [], []
    for point in ordered:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(ordered):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return np.array(lower[:-1] + upper[:-1])


def draw_pieces(axes, kit, alpha=0.55):
    """Draw a kit's piece outlines onto matplotlib axes, coloured by group."""
    pieces = kit.get("pieces") or []
    groups = sorted({((kit.get("kinds") or {}).get(piece.get("kind")) or {}).get("group", "") for piece in pieces})
    for piece in pieces:
        group = ((kit.get("kinds") or {}).get(piece.get("kind")) or {}).get("group", "")
        colour = GROUP_COLOURS[groups.index(group) % len(GROUP_COLOURS)]
        outline = footprint(piece)
        if len(outline) >= 3:
            axes.add_patch(Polygon(outline, closed=True, facecolor=colour, edgecolor="#1d2026", linewidth=0.3,
                                   alpha=alpha))
    return len(pieces)


def blank_axes():
    """(figure, axes) of an empty square plan with the background colour and no axis lines."""
    figure, axes = plt.subplots(figsize=(4.5, 4.5), dpi=80)
    figure.patch.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    axes.set_axis_off()
    return figure, axes


def save(figure, axes, destination):
    """Fit the view to what is drawn (x across, z down, metres equal), write the png and close the figure."""
    axes.autoscale_view()
    axes.set_aspect("equal", adjustable="datalim")
    axes.invert_yaxis()
    pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, format="png", facecolor=BACKGROUND, metadata={"Software": None})
    plt.close(figure)


def render(kit, destination):
    """Draw a kit's pieces from above to a png; the number of pieces drawn."""
    figure, axes = blank_axes()
    drawn = draw_pieces(axes, kit)
    save(figure, axes, destination)
    return drawn
