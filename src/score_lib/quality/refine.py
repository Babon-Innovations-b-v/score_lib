"""What refining an entry would do: per kind, each way to make it again or mend it, the parts it answers, free or
paid, and for a paid one its price from a measured per-unit cost (else "not measured yet"). A paid option is an item
for the cockpit's basket; nothing here runs it.

Measured unit costs (the ledgers' medians, paper/evidence/cloud/forecast-2026-10-15.txt line 25, the cockpit agent's
ledger measure): a close-up EUR 0.14, a Pixal3D take 0.17, the surface line (finish maps, names, split, paint) 1.90 a
take, so a model made again by the prop route 2.21; a place's surfaces bake 1.00, its light passes 0.54, its settle
0.59. The forecast multiplies every ledger-priced estimate by BILL_OVER_LEDGER for the bill.
"""

FORECAST = "paper/evidence/cloud/forecast-2026-10-15.txt:25 (ledger medians)"
BILL_OVER_LEDGER = 1.21
NOT_MEASURED = "not measured yet"
# A part under this is weak: an option that answers it is suggested.
WEAK_UNDER = 75


def priced(eur, per):
    """A measured price: its ledger euros, the bill's projection and where it was measured."""
    return {"eur": eur, "projected_eur": round(eur * BILL_OVER_LEDGER, 2), "per": per, "from": FORECAST}


def option(action, does, answers, price=None):
    """One refine option; free when it has no price, else paid (price measured or NOT_MEASURED) and a basket item."""
    found = {"action": action, "does": does, "answers": list(answers)}
    if price is None:
        found["cost"] = "free"
        found["basket"] = False
    else:
        found["cost"] = "paid"
        found["price"] = price
        found["basket"] = True
    return found


def model_options(record, found, context):
    code = any(part["name"] == "library" and part.get("status") == "not judged" and "code build" in part["how"]
               for part in found)
    options = []
    if not code:
        options.append(option("re-make from the plan with a code-built body", "a coding agent writes a code builder "
                              "for its kind from its plan and inventory row, built exactly and baked with the library "
                              "(no generated mesh)", ["holes", "crowded edges", "surface noise", "triangles", "seams",
                                                      "library", "outline", "plan shape", "scale"]))
        options.append(option("repaint from the library", "the surface line again: finish map, library names, split "
                              "by finish, painted and baked from the surface library (never the picture's colours)",
                              ["library", "palette", "blotches", "seams", "bake", "bake against close-up"],
                              priced(1.90, "take")))
        options.append(option("re-make by the prop route", "a new close-up, Pixal3D take, closed into a solid and "
                              "the surface line", ["holes", "crowded edges", "surface noise", "triangles", "outline",
                                                   "plan shape"], priced(2.21, "piece")))
    else:
        options.append(option("rebuild from its code", "run its code builder again against its plan (sizes, "
                              "parts) and bake it", ["plan shape", "scale", "holes"]))
    options.append(option("re-bake at higher resolution", "its place's picture sets baked again at a higher texel "
                          "density (more texels a metre for its set)", ["texel density", "seams", "blotches"],
                          priced(1.00, "place bake")))
    return options


def surface_options(record, found, context):
    return [option("re-tune its settings", "its library variant's settings and palette token adjusted in "
                   "data/library/materials.json, checked again here", ["palette fit", "close-range detail",
                                                                        "ink contrast"]),
            option("bake a tile locally", "the surface_recipes algorithm bakes one tile on this machine (Blender), so "
                   "its seams and close-range detail can be measured", ["tiling", "close-range detail"]),
            option("redraw the print", "the print drawn again from its library picture at a higher resolution",
                   ["print resolution", "ink contrast"])]


def effect_options(record, found, context):
    return [option("re-tune against its footage", "the emitter's settings moved toward its footage's measured "
                   "ranges in data/effects/<place>.json and its particles baked again (tools/usd/effects.py, on "
                   "this machine)", ["footage", "fine particles", "loose blobs", "cites footage"]),
            option("measure more footage", "add a footage check for this emitter (a real photograph or film "
                   "measured against a thing of known size)", ["footage"])]


def sound_options(record, found, context):
    return [option("re-level", "brought to its category's loudness target under the -3 dBTP ceiling "
                   "(tools/sound/loudness normalise, ffmpeg, on this machine)", ["loudness", "true peak", "clipping"]),
            option("trim to its brief", "cut to its brief's length with a short fade (ffmpeg)", ["length",
                                                                                              "loop join"]),
            option("regenerate a sound", "eight new MOSS-SoundEffect takes of its brief, the best by CLAP kept",
                   ["prompt match", "clipping", "loop join"], NOT_MEASURED)]


def character_options(record, found, context):
    return [option("re-fit and re-weight the body", "the people maker fits SOMA-X again and moves its skin "
                   "weights (a cloud run)", ["shell", "tucks", "hard parts stretched", "crossings", "joins"],
                   NOT_MEASURED),
            option("repaint from the library", "its looks' parts painted again from the surface library",
                   ["library", "finish"], NOT_MEASURED)]


def light_options(record, found, context):
    return [option("re-pass the place's light", "one paid render per place gives every lamp its own light group, "
                   "the free sweep mixes them again", ["place light"], priced(0.54, "place")),
            option("redraw the sky picture larger", "the sky drawn again at more texels a degree (on this machine)",
                   ["sky resolution", "blown sky"])]


def kit_options(record, found, context):
    return [option("re-plan the partition", "the kit's partition laid again so its groups cover their surfaces "
                   "without gaps or overlaps (free, a coding agent)", ["coverage", "gaps", "overlap"]),
            option("refine its weakest pieces", "each of its pieces under 50 refined by its own options",
                   ["pieces"])]


def layer_options(record, found, context):
    return [option("re-run its stage", "the stage that writes this layer run again for its place; a paid stage "
                   "(light passes, settle) is priced per place", ["gates", "unresolved"]),
            option("refine its weakest entries", "the entries it points at refined by their own options",
                   ["entries"])]


def place_options(record, found, context):
    return [option("refine its weakest layers", "its layers' own options, weakest first", ["layers"]),
            option("close its named gaps", "each named gap's objects refined until its fault clears",
                   ["completion"])]


def algorithm_options(record, found, context):
    return [option("fix its snapshot", "the missing files added to its code snapshot so its tests import and "
                   "pass (free, a coding agent)", ["tests"]),
            option("add a preset picture", "its world 1 output pictured", ["picture"])]


BY_KIND = {"model": model_options, "surface": surface_options, "effect": effect_options, "sound": sound_options,
           "character": character_options, "light": light_options, "kit": kit_options,
           "layer": layer_options, "place": place_options, "algorithm": algorithm_options}


def options(record, found, score, context):
    """The entry's refine options, each marked `suggested` when it answers a weak part, suggested ones first."""
    weak = {part["name"] for part in found if part.get("value") is not None and part["value"] < WEAK_UNDER}
    listed = BY_KIND[record["kind"]](record, found, context)
    for each in listed:
        each["suggested"] = bool(weak & set(each["answers"]))
    return sorted(listed, key=lambda each: not each["suggested"])
