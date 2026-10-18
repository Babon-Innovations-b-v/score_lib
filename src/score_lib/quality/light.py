"""A light entry's quality score (one sky picture with the prims that draw it):

    sky resolution  its picture's texels a degree round the sky
    blown sky       share of its pixels at full white in every channel
    place light     the light stage's gates (brightness within 1.5 of its concept, lamps, sky, earth) on every place
                    that shows it
"""
import numpy as np
from PIL import Image

from score_lib.quality import files, parts

TEXELS_A_DEGREE = (8.0, 4.0, 1.0)
BLOWN = (0.001, 0.01, 0.05)


def run(record, context):
    picture = next((name for name in files.named(record, "picture", ".png")), None)
    found = []
    if picture is None:
        found += [parts.unmeasured("sky resolution", "no sky picture"), parts.unmeasured("blown sky", "no sky picture")]
    else:
        with Image.open(files.blob_path(record, context.blobs, picture)) as opened:
            pixels = np.asarray(opened.convert("RGB"))
        width = pixels.shape[1]
        found.append(parts.ramped("sky resolution", round(width / 360.0, 2), TEXELS_A_DEGREE, f"its picture's "
                                  f"{width} pixels round 360 degrees", unit="px/degree"))
        blown = float((pixels >= 255).all(axis=2).mean())
        found.append(parts.ramped("blown sky", round(blown, 4), BLOWN, "share of its pixels at full white in every "
                                  "channel", unit="share"))
    for place in record.get("seen_in") or []:
        output, verdicts = context.accepted_verdicts(f"light@{place}")
        found.append(parts.gates(f"place light ({place})", verdicts, f"light@{place}"))
    return found
