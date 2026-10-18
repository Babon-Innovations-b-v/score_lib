"""A waveform picture of a sound file, drawn from its own samples with numpy and Pillow."""
import pathlib

import numpy as np
import soundfile
from PIL import Image, ImageDraw

WIDTH = 480
HEIGHT = 160
BACKGROUND = (238, 236, 230)
INK = (40, 52, 64)
MIDDLE = (190, 186, 178)


def envelope(samples, columns):
    """(lowest, highest) sample of each column of a mono signal."""
    if not len(samples):
        return np.zeros(columns), np.zeros(columns)
    edges = np.linspace(0, len(samples), columns + 1).astype(int)
    lows = np.array([samples[start:max(end, start + 1)].min() for start, end in zip(edges[:-1], edges[1:])])
    highs = np.array([samples[start:max(end, start + 1)].max() for start, end in zip(edges[:-1], edges[1:])])
    return lows, highs


def mono(path):
    """A sound file's samples, channels averaged, scaled to its peak."""
    samples, _ = soundfile.read(str(path), dtype="float32", always_2d=True)
    signal = samples.mean(axis=1)
    peak = float(np.abs(signal).max()) if len(signal) else 0.0
    return signal / peak if peak > 0 else signal


def render(path, destination):
    """Write a waveform png of a sound file; its duration in seconds."""
    info = soundfile.info(str(path))
    lows, highs = envelope(mono(path), WIDTH)
    picture = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    pen = ImageDraw.Draw(picture)
    half = HEIGHT / 2
    pen.line([(0, half), (WIDTH, half)], fill=MIDDLE)
    for column, (low, high) in enumerate(zip(lows, highs)):
        pen.line([(column, half - high * (half - 6)), (column, half - low * (half - 6))], fill=INK)
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    picture.save(destination, format="PNG")
    return info.duration
