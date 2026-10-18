"""A character's quality score, from the people maker's own checks carried in the entry (checks/*.json) for a body,
and from its dress record for a dress:

    body   shell            mean offset of the look from the SOMA-X body fitted inside it (the shell gate's 5 cm)
           tucks            worst tuck out of a garment over its shipped clips (the clip gate's 5 mm is the line)
           hard parts       worst stretch of a hard part over its bind length (1.2 is the line)
           crossings        share of read frames where body crosses cloth more than at rest
           joins            the wrist, ankle and stance joins' verdicts
           outline          its close-up likeness outline (the likeness stage's 0.50 is the line)
           far triangles    its far body's triangles (FAR_TRIANGLES 1,220)
           library          share of its parts painted from a library family
           face             needs a paid judge
    dress  library          share of its surfaces painted from the library
           skin as skin     its skin surfaces painted from the skin family
           painted as named share of its surfaces painted with the variant its names chose
           its body         its body's quality score
"""
from score_lib.quality import files, parts

SHELL_MEAN = (0.01, 0.025, 0.05)
TUCK_MM = (0.0, 5.0, 15.0)
STRETCH = (1.0, 1.2, 1.5)
CROSSING_FRAMES = (0.0, 0.25, 0.75)
OUTLINE = (0.8, 0.5, 0.3)
FAR_TRIANGLES = (1220, 1400, 2500)
SHARE = (1.0, 0.999, 0.0)


def run(record, context):
    if "dress.json" in record["files"]:
        return dress_parts(record, context)
    return body_parts(record, context)


def check(record, context, name):
    return files.record_json(record, context.blobs, f"checks/{name}.json")


def body_parts(record, context):
    found = [shell_part(check(record, context, "shell"))]
    found += clip_parts(check(record, context, "pokes_shipped") or check(record, context, "pokes"))
    found.append(joins_part(check(record, context, "joins")))
    found.append(outline_part(check(record, context, "likeness")))
    found.append(far_part(check(record, context, "build")))
    found.append(library_part(check(record, context, "parts"), context))
    found.append(parts.unmeasured("face", "whether its face reads as a person's", parts.NEEDS_PAID_JUDGE))
    return found


def shell_part(shell):
    if not shell:
        return parts.unmeasured("shell", "no shell check in the entry")
    return parts.ramped("shell", shell["offset_mean_m"], SHELL_MEAN, f"mean offset of its look from the fitted body "
                        f"(worst point {shell.get('offset_most_m')} m)", weight=2.0, unit="m")


def clip_parts(pokes):
    if not pokes or not pokes.get("clips"):
        why = "no clip check in the entry (no clips judged for this look)"
        return [parts.unmeasured(name, why) for name in ("tucks", "hard parts", "crossings")]
    clips = pokes["clips"]
    tuck = max(clip.get("worst_tuck_out_mm", 0.0) for clip in clips.values())
    stretch = max(clip.get("rigid_stretch", 1.0) for clip in clips.values())
    read = sum(clip.get("frames_crossings_read", 0) for clip in clips.values())
    over = sum(clip.get("frames_crossing_more_than_rest", 0) for clip in clips.values())
    crossing = over / read if read else 0.0
    return [parts.ramped("tucks", tuck, TUCK_MM, f"worst tuck out of a garment over its {len(clips)} clips",
                         unit="mm"),
            parts.ramped("hard parts", stretch, STRETCH, "worst stretch of a hard part over its bind length",
                         unit="ratio"),
            parts.ramped("crossings", round(crossing, 3), CROSSING_FRAMES, f"share of {read} read frames where body "
                         f"crosses cloth more than at rest ({pokes.get('rest_crossings')} crossings at rest)",
                         unit="share")]


def joins_part(joins):
    if not joins or not joins.get("verdict"):
        return parts.unmeasured("joins", "no joins check in the entry")
    verdicts = joins["verdict"]
    passing = sum(bool(verdict.get("pass")) for verdict in verdicts.values())
    listed = "; ".join(f"{name} {'pass' if verdict.get('pass') else 'fail'}" for name, verdict in verdicts.items())
    return parts.scored("joins", round(100 * passing / len(verdicts)), f"{passing} of {len(verdicts)} pass",
                        f"share of its join verdicts that pass: {listed}")


def outline_part(likeness):
    if not likeness or likeness.get("outline") is None:
        return parts.unmeasured("outline", "no likeness check in the entry")
    return parts.ramped("outline", likeness["outline"], OUTLINE, "best outline overlap with its close-up",
                        unit="overlap")


def far_part(build):
    far = ((build or {}).get("levels") or {}).get("far", {}).get("triangles")
    if far is None:
        return parts.unmeasured("far triangles", "no far body in its build check")
    return parts.ramped("far triangles", far, FAR_TRIANGLES, "its far body's triangles (FAR_TRIANGLES 1,220)",
                        weight=0.5, unit="triangles")


def library_part(made_parts, context):
    listed = (made_parts or {}).get("parts") or []
    if not listed:
        return parts.unmeasured("library", "no parts check in the entry")
    families = set(context.library["families"])
    known = [part for part in listed if part.get("family") in families]
    return parts.ramped("library", round(len(known) / len(listed), 3), SHARE, f"share of its {len(listed)} parts "
                        f"whose finish is a surface library family", unit="share")


def dress_parts(record, context):
    dress = files.record_json(record, context.blobs, "dress.json") or {}
    surfaces = dress.get("surfaces") or {}
    if not surfaces:
        return [parts.unmeasured("library", "its dress record names no surface")]
    known = [name for name, surface in surfaces.items() if surface.get("known")]
    found = [parts.ramped("library", round(len(known) / len(surfaces), 3), SHARE, f"share of its {len(surfaces)} "
                          f"surfaces painted from the library", unit="share")]
    skins = {name: surface for name, surface in surfaces.items() if surface.get("role") == "skin"}
    if skins:
        right = [name for name, surface in skins.items()
                 if context.variant_families.get(surface.get("painted")) == "skin"]
        wrong = ", ".join(f"{name} painted {skins[name].get('painted')}" for name in skins if name not in right)
        found.append(parts.ramped("skin as skin", round(len(right) / len(skins), 3), SHARE, f"share of its skin "
                                  f"surfaces painted from the skin family{'; ' + wrong if wrong else ''}",
                                  weight=2.0, unit="share"))
    named = {name: surface for name, surface in surfaces.items() if surface.get("library") and surface.get("painted")}
    if named:
        same = [name for name, surface in named.items() if surface["library"] == surface["painted"]]
        others = ", ".join(f"{name} named {named[name]['library']}, painted {named[name]['painted']}"
                           for name in named if name not in same)
        found.append(parts.ramped("painted as named", round(len(same) / len(named), 3), SHARE, f"share of its "
                                  f"surfaces painted with the variant its names chose{'; ' + others if others else ''}",
                                  unit="share"))
    body = body_address(record, context)
    if body:
        found.append(parts.held("its body", {body: context.scores.get(body)}, what="body"))
    else:
        found.append(parts.unmeasured("its body", "no body entry of its look"))
    return found


def body_address(record, context):
    """The body entry of a dress's look: body_<look> for dress_<look>[.place]."""
    look = record["id"].split(".")[0].removeprefix("dress_")
    found = [address for address in context.records if address.startswith(f"character/body_{look}@")]
    return found[0] if found else None
