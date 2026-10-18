"""An effect's quality score, against real footage (the owner's rule: effects are modelled on real footage first,
many fine particles tied to the world, never thinned to draw cheaper):

    footage         each check of its place's footage block that names this emitter, from the effects stage's
                    accepted judgement: inside the footage's range 75 to 100 by its margin, outside under 50
    loose blobs     a cloud (a puff look): on its own baked clip at its fullest moment, the share of its particles
                    touching no other (the stage's `loose`; the footage checks' 0.01 is the line); specks (grains,
                    sparks, ice) fly apart by nature and are not judged
    how fine its particles are is judged only by a footage check that measures it (the launch's fineness): a breath's
    puffs and a cloud's billows are each as large as their footage shows
    cites footage   its settings name real footage it was measured on, not only the source game's lines
"""
import functools
import re

import numpy as np
from pxr import Usd, UsdGeom

from score_lib.quality import files, parts, spots

LOOSE = (0.0, 0.01, 0.05)
SEEN = 0.02  # the effects stage's SEEN: a particle thinner than this does not show
SEEN_SHARE = 0.14  # the effects stage's SEEN_SHARE: a fog's particle shows past this share of its thickest
READS = 12  # moments read along the clip to find its fullest


@functools.cache
def effects_stage(context):
    context.repo_tool(".")
    from score.stages import effects
    return effects


def emitter_name(record):
    """The emitter's name in its place's record: the entry id without its place and any laid cell (drift_2_3)."""
    name = record["id"].split(".")[0]
    return re.sub(r"_\d+_\d+$", "", name)


def fullest(points_prim):
    """(spots (n, 3), widths (n,)) of the particles that show at the clip's fullest read moment: thicker than SEEN,
    or than SEEN_SHARE of its own thickest for a particle as thin as a fog (the effects stage's `seen`)."""
    points = UsdGeom.Points(points_prim)
    times = points.GetPointsAttr().GetTimeSamples() or [Usd.TimeCode.Default()]
    opacity_var = UsdGeom.PrimvarsAPI(points_prim).GetPrimvar("displayOpacity")
    read = []
    for time in times[::max(1, len(times) // READS)]:
        spots_now = np.array(points.GetPointsAttr().Get(time) or [], dtype=np.float64).reshape(-1, 3)
        widths = np.array(points.GetWidthsAttr().Get(time) or [], dtype=np.float64)
        if len(widths) != len(spots_now):
            widths = np.full(len(spots_now), float(widths[0]) if len(widths) else 0.0)
        opacity = np.array(opacity_var.Get(time) or [], dtype=np.float64) if opacity_var else np.zeros(0)
        if len(opacity) != len(spots_now):
            opacity = np.ones(len(spots_now))
        read.append((spots_now, widths, opacity))
    thickest = max((float(each[2].max()) for each in read if len(each[2])), default=1.0)
    least = min(SEEN, SEEN_SHARE * thickest)
    best = (np.zeros((0, 3)), np.zeros(0))
    for spots_now, widths, opacity in read:
        shown = (opacity > least) & (widths > 0)
        if shown.sum() > len(best[0]):
            best = (spots_now[shown], widths[shown])
    return best


def clip_particles(record, context):
    folder = files.linked(record, context.blobs, context.work / "effect" / record["id"])
    clip = next((name for name in files.named(record, "usd") if name.startswith("clip")), None)
    if clip is None:
        return None
    stage = Usd.Stage.Open(str(folder / clip))
    found = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Points)]
    if not found:
        return None
    gathered = [fullest(prim) for prim in found]
    return np.concatenate([each[0] for each in gathered]), np.concatenate([each[1] for each in gathered])


def run(record, context):
    settings = files.record_json(record, context.blobs, "settings.json") or {}
    found = footage_parts(record, context)
    if settings.get("look") != "puff":
        found.append(parts.unmeasured("loose blobs", f"its look is '{settings.get('look')}': specks fly apart by "
                                      "nature", parts.NOT_JUDGED))
    else:
        particles = clip_particles(record, context)
        if particles is None or len(particles[0]) < 2:
            found.append(parts.unmeasured("loose blobs", "no baked particles show in its clip"))
        else:
            found.append(loose_part(particles, context))
    cited = (settings.get("from") or "").startswith("footage:")
    found.append(parts.scored("cites footage", 100 if cited else 0, cited, "its settings name the real footage they "
                              "were measured on (100), or only the source game's lines (0): "
                              f"'{(settings.get('from') or '')[:80]}'"))
    return found


def footage_parts(record, context):
    place = record["provenance"].get("place") or (record.get("seen_in") or [None])[0]
    effects_record = context.repo_json(f"data/effects/{place}.json") or {}
    name = emitter_name(record)
    checks = [check for check in effects_record.get("footage", {}).get("checks", [])
              if name in check.get("emitters", [])]
    if not checks:
        return [parts.unmeasured("footage", "no footage check of its place names this emitter")]
    output, verdicts = context.accepted_verdicts(f"effects-layer@{place}")
    measures = (verdicts.get("footage") or {}).get("measures", {})
    judged = [(check, measures.get(check["name"])) for check in checks]
    judged = [(check, value) for check, value in judged if value is not None]
    if not judged:
        return [parts.unmeasured("footage", f"the accepted effects-layer@{place} judgement holds no measure of its "
                                 f"{len(checks)} checks")]
    values = [(within_value(value, check["range"]), check, value) for check, value in judged]
    worst = min(values, key=lambda item: item[0])
    listed = "; ".join(f"{check['name']} {value:.3f} in {check['range']}" for _, check, value in values)
    return [parts.scored("footage", worst[0], round(worst[2], 4), f"worst of {len(values)} footage checks of the "
                         f"effects stage (effects-layer@{place}): {listed}; inside its range 75 to 100 by its margin, "
                         f"50 at an end, 0 a range's width (or the bound's size) past it", weight=2.0,
                         unit=worst[1]["measure"])]


def within_value(value, bounds):
    """0..100 for a measure against a footage range [low, high] (either may be None): 75 to 100 inside by the margin
    to the nearer end over half the range, 50 at an end, falling to 0 one span past it."""
    low, high = bounds
    span = (high - low) if low is not None and high is not None else abs(low if low is not None else high) or 1.0
    span = span or 1.0
    if (low is None or value >= low) and (high is None or value <= high):
        margins = [value - low if low is not None else None, high - value if high is not None else None]
        margin = min(each for each in margins if each is not None)
        return round(75 + 25 * min(margin / (span / 2), 1.0))
    past = (low - value) if low is not None and value < low else (value - high)
    return round(max(0.0, 50 * (1 - past / span)))


def loose_part(particles, context):
    spots_now, widths = particles
    loose_share = effects_stage(context).loose(spots_now, widths)
    found = parts.ramped("loose blobs", round(loose_share, 4), LOOSE, f"share of its {len(spots_now)} showing "
                         "particles at its fullest moment touching no other (the effects stage's loose: the nearest "
                         "middle farther than its width)", unit="share")
    if found["value"] < 75:
        from scipy.spatial import cKDTree
        nearest, _ = cKDTree(spots_now).query(spots_now, k=2)
        flagged = nearest[:, 1] > widths
        found["picture"] = spots.points_spot(spots_now, flagged, context.blobs, "loose particles in red, seen side on")
    return found
