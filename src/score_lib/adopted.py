"""`score-lib adopted match`: an adopted set's overlap with our older entries (score #155, plan step 20). For each of
our model and surface entries the set's entries do not hold, the adopted entry of the same kind of object or finish it
overlaps is proposed. A model's kind is its Wikidata class first, its name's object only within that class (a mug
among drinking vessels); ours pairs only when our id's object (its core noun with the word compounding it, synonyms
mapped) is that same thing, never for a shared word (a bedside lamp is not a head light, a bay panel not an electric
panel). A surface pairs only on the same finish words and the same finish states (rusted, tinted, emissive, gloss).
A pair is accepted only when the kind is the same and nothing clashes: a setting the adopted object is made for and
ours is not in, a colour, a finish painted for one model. The proposals file holds every older entry's row (accepted,
refused or no match, with its score and reason); `--apply` withdraws each accepted one naming its replacement
(`replaced_by`), so places take the replacement at their next build.

An adopted set is a `config/sources.toml` row with a `take_in` table; its entries are those whose licence block or
provenance names that row."""
import json
import pathlib
import re

from score_lib import entry as entries
from score_lib import index, locks
from score_lib.admission import Sources

KINDS = ("model", "surface")
# Our id's prefixes and words that say how or where an entry was made, not what it is.
MADE_PREFIXES = ("kenney_", "polyhaven_", "quaternius_", "ambientcg_")
MADE_WORDS = {"trellis", "gltf", "piece", "model", "mesh", "asset", "simready", "sm", "geo"}
# A far copy is a place's backdrop seen from afar, never an object to put a simulated one in for.
FAR_PREFIX = "far_"
# Words naming several words as one: phrases first (as token runs), then single words, each to its canonical word.
PHRASES = {("trash", "can"): "bin", ("waste", "bin"): "bin", ("garbage", "can"): "bin", ("fire", "extinguisher"):
           "extinguisher", ("traffic", "cone"): "cone", ("hand", "truck"): "trolley", ("fork", "lift"): "forklift",
           ("filing", "cabinet"): "cabinet", ("tool", "box"): "toolbox", ("card", "board"): "cardboard",
           ("stainless", "steel"): "steel", ("sheet", "metal"): "metal", ("power", "strip"): "socket",
           ("head", "light"): "headlight", ("head", "lamp"): "headlight", ("glass", "fibre"): "fibreglass",
           ("fibre", "glass"): "fibreglass", ("fiber", "glass"): "fibreglass"}
SYNONYMS = {"spanner": "wrench", "sofa": "couch", "settee": "couch", "cupboard": "cabinet", "dustbin": "bin",
            "trashcan": "bin", "garbage": "bin", "trash": "bin", "lorry": "truck", "aluminum": "aluminium",
            "alu": "aluminium", "colour": "color", "grey": "gray", "cloth": "fabric", "textile": "fabric",
            "canvas": "fabric", "carton": "box", "crate": "box", "case": "box", "lamp": "light", "luminaire":
            "light", "seat": "chair", "monitor": "screen", "display": "screen", "rug": "carpet",
            "barrel": "drum", "keg": "drum", "bucket": "pail", "shelving": "shelf", "shelves":
            "shelf", "shelve": "shelf", "racking": "rack", "pipes": "pipe", "cables": "cable",
            "boxes": "box", "crates": "box", "chairs": "chair", "tables": "table", "lights": "light", "lamps": "light",
            "screws": "screw", "bolts": "bolt", "nuts": "nut", "rock": "stone", "boulder": "stone", "timber": "wood",
            "wooden": "wood", "plank": "board", "planks": "board", "boards": "board", "metallic": "metal",
            "plastics": "plastic", "rubbers": "rubber", "tiles": "tile", "tiled": "tile", "painted": "paint",
            "cement": "concrete", "flooring": "floor",
            "headlamp": "headlight", "headlights": "headlight", "electrical": "electric"}
# Words that qualify an object or a finish rather than name it: an object is the last word that is none of these.
COLOURS = {"black", "white", "gray", "red", "green", "blue", "yellow", "orange", "purple", "pink", "brown", "beige",
           "cream", "gold", "silver", "charcoal", "khaki", "teal", "amber", "olive", "navy", "tan", "ivory", "natural"}
QUALIFIERS = {"small", "large", "big", "medium", "tall", "short", "long", "wide", "narrow", "old", "new", "open",
              "closed", "left", "right", "top", "bottom", "front", "back", "rear", "inner", "outer", "upper", "lower",
              "mid", "centre", "center", "corner", "single", "double", "triple", "flat", "round", "square", "dark",
              "bright", "dull", "dim", "aged", "worn", "dirty", "clean", "fine", "rough", "smooth", "glossy",
              "gloss", "matte", "hand", "tool", "handtool", "stack", "pile", "set", "group", "variant", "simple",
              "basic", "standard", "generic", "heavy", "portable", "mobile", "empty", "full"}
# The setting an object is made for, as the words of its name or of our place; an adopted object made for a setting
# replaces only an entry of that setting (a hospital bed is not the bed of a flat).
SETTINGS = {"medical": {"hospital", "medical", "clinic", "surgical", "iv", "patient", "exam"},
            "industrial": {"warehouse", "industrial", "factory", "workshop", "garage", "engineroom", "hangar",
                           "storage", "plant", "dock"},
            "domestic": {"domestic", "home", "house", "kitchen", "bedroom", "bathroom", "flat", "apartment",
                         "quarters", "habitat", "living", "stairwell", "galley"},
            "office": {"office"},
            "retail": {"retail", "shop", "store", "supermarket", "grocery", "market"},
            "outdoor": {"outdoor", "garden", "street", "square", "park", "road", "campgrounds", "camp"}}
SETTING_WORDS = {word for words in SETTINGS.values() for word in words}
# The kind of finish: a surface's material word. A surface naming none is a picture, a glow or a ground of its own.
FINISHES = {"steel", "iron", "aluminium", "copper", "brass", "bronze", "chrome", "metal", "zinc", "tin", "wood",
            "plastic", "rubber", "glass", "fabric", "leather", "concrete", "brick", "plaster", "ceramic", "porcelain",
            "tile", "stone", "marble", "granite", "gravel", "sand", "soil", "paper", "cardboard", "foam", "carpet",
            "paint", "vinyl", "cork", "felt", "cotton", "wool", "nylon", "polyester", "acrylic", "asphalt",
            "terrazzo", "enamel", "galvanized", "anodized", "carbon", "fibreglass", "composite", "bakelite"}
# A finish's state that makes it another finish: ours pairs only with the same states (a tinted glass is not a clean
# one, a red plastic not a red LED's glow). The unmarked state (new, clean, clear) is none.
FINISH_STATES = {"rusted": "rusted", "rusty": "rusted", "rust": "rusted", "perforated": "perforated", "cast": "cast",
                 "frosted": "frosted", "smoked": "smoked", "tinted": "tinted", "scorched": "scorched",
                 "burnt": "scorched", "old": "old", "worn": "worn", "aged": "aged", "dirty": "dirty",
                 "weathered": "weathered", "emissive": "emissive", "led": "emissive", "glowing": "emissive",
                 "gloss": "gloss", "glossy": "gloss", "matte": "matte", "rough": "rough", "brushed": "brushed",
                 "polished": "polished", "bare": "bare"}
# Metals a finish names more exactly than "metal"; paint names a metal's finish too (paint on metal is paint).
METALS = {"steel", "iron", "aluminium", "copper", "brass", "bronze", "chrome", "zinc", "tin"}
# A Wikidata class broader than the objects it holds: an adopted model's name narrows it (a mug, of drinking vessels).
CLASS_MEMBERS = {"drinking vessel": {"mug", "cup", "tumbler", "beaker"}}
VARIANT = re.compile(r"^([a-z]?\d+[a-z]?|\d+p\d+|v\d+)$")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def words_of(text):
    """The canonical words of a name: camelCase and punctuation split, lowercased, variant codes (a01, 2, 1p4)
    dropped, phrases joined and synonyms mapped."""
    raw = [part.lower() for chunk in re.split(r"[^A-Za-z0-9]+", text or "") for part in CAMEL.split(chunk) if part]
    raw = [word for word in raw if not VARIANT.match(word) and word not in MADE_WORDS]
    joined, position = [], 0
    while position < len(raw):
        pair = tuple(raw[position:position + 2])
        if pair in PHRASES:
            joined.append(PHRASES[pair])
            position += 2
        else:
            joined.append(raw[position])
            position += 1
    return [SYNONYMS.get(word, word) for word in joined]


def singular(word):
    """A plural's singular for comparing objects (poles, carts); a word ending in ss, us or is is kept."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")) else word


def object_of(words):
    """The thing the words name: their core noun with the word compounding it before it (bedside lamp, electric panel),
    singular; None when no word names a thing."""
    for position in range(len(words) - 1, -1, -1):
        word = words[position]
        if word in COLOURS or word in QUALIFIERS or word in SETTING_WORDS:
            continue
        before = words[position - 1] if position else None
        if before and before not in COLOURS | QUALIFIERS | SETTING_WORDS | FINISHES:
            return f"{singular(before)} {singular(word)}"
        return singular(word)
    return None


def name_object(title):
    """An adopted model's object as its name gives it: the object of the last name part that names one (its parts are
    underscore-separated, so bodyPart_headLight_left is a headlight)."""
    found = None
    for part in re.split(r"_+", title or ""):
        found = object_of(words_of(part)) or found
    return found


def class_objects(values):
    """The objects a Wikidata class names, one per item of a list class (IV Poles & Carts: pole, cart)."""
    items = [item for value in values for item in re.split(r"\s*(?:&|,|/|\band\b)\s*", value)]
    return {found for found in (object_of(words_of(item)) for item in items) if found}


def own_values(tags, key):
    """A tag's values without its own name (a label's type taken as a label once wrote wikidata_class into it)."""
    return [value for value in tags.get(key, []) if value != key]


def settings_of(words):
    """The settings any of the words name."""
    return {name for name, members in SETTINGS.items() if members & set(words)}


def our_name(record):
    """(the words of our entry's id without how it was made, the words of the place it was made for)."""
    name, _, place = record["id"].partition(".")
    for prefix in MADE_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
    places = [place] + list(record.get("tags", {}).get("place", [])) + [record["provenance"].get("place") or ""]
    return words_of(name), [word for each in places for word in words_of(each)]


def adopted_sources(sources):
    """The ids of the adopted sets: rows with a `take_in` table."""
    return [row["id"] for row in sources.rows if row.get("take_in")]


def is_adopted(record, source_ids):
    """Whether an entry is one of an adopted set's: its licence block or a made_with item names the set's row."""
    if (record.get("licence") or {}).get("source") in source_ids:
        return True
    return any(item.get("by") in source_ids for item in record["provenance"].get("made_with", []))


class Candidate:
    """One adopted entry as the matcher reads it: its address, its words, its classes, its name's object
    and its settings."""

    def __init__(self, record):
        self.address = record["address"]
        tags = record.get("tags", {})
        self.words = words_of(record["title"])
        classes = [words_of(each) for each in own_values(tags, "wikidata_class")]
        described = [word for each in own_values(tags, "description") for word in words_of(each)]
        self.known = set(self.words) | {word for each in classes for word in each} | set(described)
        self.classes = class_objects(own_values(tags, "wikidata_class"))
        self.named = name_object(record["title"])
        self.settings = settings_of(self.words)
        self.colours = set(self.words) & COLOURS
        self.finishes = exact_finishes(self.words)
        self.states = states_of(self.words)
        self.painted_for = tags.get("painted_for", [])


def exact_finishes(words):
    """The finish words, "metal" dropped where a metal or paint names it more exactly."""
    finishes = set(words) & FINISHES
    return finishes - {"metal"} if finishes & (METALS | {"paint"}) else finishes


def states_of(words):
    """The finish states the words name."""
    return {FINISH_STATES[word] for word in words if word in FINISH_STATES}


def kind_of(ours, candidate):
    """How our object is the adopted model's kind: "its Wikidata class" when it is the class, "its name, within its
    Wikidata class" when it is the name's object and the name keeps to the class (the same head noun, or a member of a
    broader class), "its name" when there is no class; None when it is not the same thing."""
    if ours in candidate.classes:
        return "its Wikidata class"
    if ours != candidate.named:
        return None
    if not candidate.classes:
        return "its name"
    head = ours.split()[-1]
    if any(head == each.split()[-1] or head in CLASS_MEMBERS.get(each, ()) for each in candidate.classes):
        return "its name, within its Wikidata class"
    return None


def overlap(ours, candidate):
    """How much of the two names is shared: shared words over the words of both (Jaccard), 0 to 1."""
    both = set(ours) | set(candidate.words)
    return round(len(set(ours) & set(candidate.words)) / len(both), 3) if both else 0.0


def model_refusal(record, words, places, candidate):
    """Why an adopted model may not replace our model, or None when it is the same kind of object."""
    if record["id"].startswith(FAR_PREFIX):
        return "a far copy, a backdrop seen from afar, is not replaced by a simulated object"
    ours = object_of(words)
    if ours is None:
        return "our id names no object, only how it looks"
    if kind_of(ours, candidate) is None:
        theirs = ", ".join(sorted(candidate.classes)) or candidate.named
        return f"not the same kind of object ({ours}; it is {theirs})"
    settings = settings_of(words + places)
    missing = candidate.settings - settings
    if missing:
        return (f"it is made for a {', '.join(sorted(missing))} setting, and ours is "
                f"{', '.join(sorted(settings)) or 'named for none'}")
    return None


def surface_refusal(record, words, candidate):
    """Why an adopted surface may not replace our surface, or None when it is the same finish."""
    if candidate.painted_for:
        return f"a finish painted for one model ({', '.join(candidate.painted_for)}), not a library finish"
    finishes = exact_finishes(words + [word for each in record.get("tags", {}).get("family", [])
                                       for word in words_of(each)])
    if not finishes:
        return "our surface names no finish the adopted set's materials name (a picture, a glow or a ground)"
    if finishes != candidate.finishes:
        return (f"not the same finish ({', '.join(sorted(finishes))}; it is "
                f"{', '.join(sorted(candidate.finishes)) or 'none named'})")
    states = states_of(words)
    if states != candidate.states:
        return (f"not the same finish state ({', '.join(sorted(states)) or 'unmarked'}; it is "
                f"{', '.join(sorted(candidate.states)) or 'unmarked'})")
    colours = set(words) & COLOURS
    if colours != candidate.colours:
        if not colours:
            return ("ours takes its colour from the palette, and one coloured set would fix it "
                    f"({', '.join(sorted(candidate.colours))})")
        theirs = ", ".join(sorted(candidate.colours)) or "unnamed"
        return f"not the same colour ({', '.join(sorted(colours))}; it is {theirs})"
    return None


def candidate_rows(record, words, places, candidates):
    """Every adopted candidate sharing a word with our entry, scored, standing ones first and best first, each with its
    refusal or None."""
    rows = []
    for candidate in candidates:
        if record["kind"] == "model":
            if not set(words) & (candidate.known - QUALIFIERS - COLOURS):
                continue
            refusal = model_refusal(record, words, places, candidate)
        else:
            if not set(words) & set(candidate.words):
                continue
            refusal = surface_refusal(record, words, candidate)
        rows.append((overlap(words, candidate), candidate, refusal))
    return sorted(rows, key=lambda row: (row[2] is not None, -row[0], len(row[1].words), row[1].address))


def proposal(record, candidates):
    """Our entry's proposal row: {old, new, score, status accepted|refused|no match, reason}."""
    words, places = our_name(record)
    rows = candidate_rows(record, words, places, candidates)
    if not rows:
        return {"old": record["address"], "new": None, "score": 0.0, "status": "no match",
                "reason": "no adopted entry names its object or finish"}
    score, best, refusal = rows[0]
    if refusal:
        return {"old": record["address"], "new": best.address, "score": score, "status": "refused", "reason": refusal}
    if record["kind"] == "model":
        ours = object_of(words)
        reason = f"the same object ({ours}), by {kind_of(ours, best)}"
    else:
        reason = f"the same finish ({', '.join(sorted(best.finishes | best.colours | best.states))})"
    equal = [row for row in rows[1:] if row[2] is None and row[0] == score]
    if equal:
        reason += f"; the first of {len(equal) + 1} equal candidates"
    return {"old": record["address"], "new": best.address, "score": score, "status": "accepted", "reason": reason}


def newest_candidates(root, kind, source_ids):
    """The adopted set's newest standing entry of each id of one kind."""
    newest = {}
    for record in entries.all_entries(root, kind):
        if is_adopted(record, source_ids) and record["state"] != "withdrawn":
            newest[record["id"]] = record
    return [Candidate(record) for _, record in sorted(newest.items())]


def match(root, sources=None, source_ids=None):
    """The proposals of every older model and surface entry (not the set's own, not already replaced)."""
    source_ids = list(source_ids or adopted_sources(sources or Sources.load()))
    proposals = []
    for kind in KINDS:
        candidates = newest_candidates(root, kind, source_ids)
        for record in entries.all_entries(root, kind):
            if not is_adopted(record, source_ids) and not record.get("replaced_by"):
                proposals.append(proposal(record, candidates))
    counts = {status: sum(1 for row in proposals if row["status"] == status)
              for status in ("accepted", "refused", "no match")}
    return {"sources": source_ids, "counts": counts, "proposals": proposals}


def apply(root, report):
    """Withdraw every accepted pair's old entry naming its replacement; the index rebuilt. The addresses withdrawn."""
    done = []
    for row in report["proposals"]:
        if row["status"] == "accepted":
            locks.withdraw(root, row["old"], replaced_by=row["new"])
            done.append(row["old"])
    index.rebuild(root)
    return done


def write_report(path, report):
    """The proposals file, as JSON."""
    pathlib.Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
