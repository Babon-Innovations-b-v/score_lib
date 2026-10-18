"""A swatch from a material's own constants: a ball lit by one light, its colour the UsdPreviewSurface's
diffuseColor, its highlight narrowed by the roughness and tinted by the metallic. Numpy only."""
import pathlib

import numpy as np
from PIL import Image

SIZE = 256
BACKGROUND = np.array([238, 236, 230]) / 255.0
LIGHT = np.array([-0.45, 0.6, 0.66]) / np.linalg.norm([-0.45, 0.6, 0.66])


def ball(colour, roughness, metallic, size=SIZE):
    """An RGB array of a lit ball on the background."""
    across = np.linspace(-1.1, 1.1, size)
    x, y = np.meshgrid(across, -across)
    inside = x ** 2 + y ** 2 <= 1.0
    z = np.sqrt(np.clip(1.0 - x ** 2 - y ** 2, 0, None))
    normals = np.stack([x, y, z], axis=-1)
    diffuse = np.clip(normals @ LIGHT, 0, None)[..., None]
    halfway = (LIGHT + np.array([0, 0, 1.0])) / np.linalg.norm(LIGHT + np.array([0, 0, 1.0]))
    power = 2.0 / max(roughness, 0.03) ** 2
    specular = (np.clip(normals @ halfway, 0, None) ** power)[..., None]
    base = np.asarray(colour, dtype=np.float64) ** (1 / 2.2)
    highlight = (1 - metallic) * np.ones(3) + metallic * base
    shaded = base * (0.18 + 0.82 * diffuse) * (1 - 0.6 * metallic) + 0.5 * specular * highlight
    picture = np.where(inside[..., None], np.clip(shaded, 0, 1), BACKGROUND)
    return (picture * 255).astype(np.uint8)


def render(colour, roughness, metallic, destination, size=SIZE):
    """Write a swatch png; the path."""
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(ball(colour, roughness, metallic, size)).save(destination, format="PNG")
    return destination
