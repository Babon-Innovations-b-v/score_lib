"""A ground's quality score. A planet's terrain from the terrain stage's own measures (measures.json); a place's
ground cut from its mesh:

    planet  relief follows   how well the relief follows its concept's (the registered correlation)
            relief size      its relief's RMS against the planned
            hills            each named hill's highest point inside its planned range
            faults           the terrain stage's scale and slope faults
    cut     cell             its median edge length (metres between ground points)
            spikes           share of points standing off their neighbours' middle by more than their edge length
            its planet       its planet's quality score
"""
import math

import numpy as np

from score_lib.quality import files, mesh as meshes, parts

FOLLOWS = (0.8, 0.5, 0.2)
RELIEF_SIZE = (0.1, 0.3, 0.7)
FAULTS = (0, 1, 4)
CELL = (0.5, 2.0, 8.0)
SPIKES = (0.0, 0.001, 0.01)


def run(record, context):
    measures = files.record_json(record, context.blobs, "measures.json")
    if measures is not None:
        return planet_parts(measures)
    return cut_parts(record, context)


def planet_parts(measures):
    found = []
    relief = measures.get("relief") or {}
    if relief.get("correlation") is not None:
        found.append(parts.ramped("relief follows", relief["correlation"], FOLLOWS, f"correlation of its relief with "
                                  f"its concept's ({relief.get('picture')})", unit="correlation"))
    scale = measures.get("scale") or {}
    planned = (scale.get("relief") or {})
    if planned.get("rms_m") and planned.get("planned_m"):
        off = abs(math.log(planned["rms_m"] / planned["planned_m"]))
        found.append(parts.ramped("relief size", round(off, 3), RELIEF_SIZE, f"|log| of its relief's RMS "
                                  f"{planned['rms_m']} m against the planned {planned['planned_m']} m", unit="log"))
    hills = scale.get("hills") or {}
    if hills:
        inside = [name for name, hill in hills.items()
                  if hill["planned"][0] <= hill["highest"] <= hill["planned"][1]]
        found.append(parts.scored("hills", round(100 * len(inside) / len(hills)), f"{len(inside)} of {len(hills)}",
                                  "share of its named hills whose highest point lies in its planned range: "
                                  + "; ".join(f"{name} {hill['highest']} m in {hill['planned']}"
                                              for name, hill in hills.items())))
    faults = list(scale.get("faults") or []) + list((measures.get("slope") or {}).get("faults") or [])
    found.append(parts.ramped("faults", len(faults), FAULTS, f"the terrain stage's scale and slope faults"
                              f"{': ' + '; '.join(map(str, faults)) if faults else ''}", unit="faults"))
    return found


def cut_parts(record, context):
    folder = files.linked(record, context.blobs, context.work / "ground" / record["id"])
    model = meshes.read(folder / record["usd"]["root"])
    if model is None:
        return [parts.scored("ground", 0, 0, "the ground draws no triangle")]
    edges, _, _ = meshes.edges_of(model)
    lengths = meshes.edge_lengths(model, edges)
    cell = float(np.median(lengths))
    found = [parts.ramped("cell", round(cell, 3), CELL, "its median edge length", unit="m")]
    found.append(spikes_part(model, edges, lengths))
    planet = planet_address(record, context)
    found.append(parts.held("its planet", {planet: context.scores.get(planet)}, what="planet") if planet else
                 parts.unmeasured("its planet", "no planet entry"))
    return found


def spikes_part(model, edges, lengths):
    """Share of points standing off their neighbours' middle, along up, by more than their mean edge length."""
    count = len(model.points)
    total, neighbours, length_sum = np.zeros(count), np.zeros(count), np.zeros(count)
    for this, other in ((0, 1), (1, 0)):
        np.add.at(total, edges[:, this], model.points[edges[:, other], 1])
        np.add.at(neighbours, edges[:, this], 1)
        np.add.at(length_sum, edges[:, this], lengths)
    has = neighbours > 0
    off = np.abs(model.points[:, 1] - np.divide(total, neighbours, out=model.points[:, 1].copy(), where=has))
    reach = np.divide(length_sum, neighbours, out=np.ones(count), where=has)
    share = float((off > reach).mean())
    return parts.ramped("spikes", round(share, 5), SPIKES, "share of its points standing off their neighbours' "
                        "middle height by more than their mean edge length", unit="share")


def planet_address(record, context):
    planet = record["id"].split(".")[0]
    found = [address for address in context.records if address.startswith(f"ground/{planet}@")]
    return found[0] if found else None
