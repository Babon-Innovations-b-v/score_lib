"""`score-lib adopted match`: an adopted set's overlap with our older entries (score #155, plan step 20). For each of
our model and surface entries the set's entries do not hold, the adopted entry of the same kind of object or finish it
overlaps is proposed. A model's kind is its Wikidata class first, its name's object only within that class (a mug
among drinking vessels); ours pairs only when our id's object (its core noun with the word compounding it, synonyms
mapped) is that same thing, never for a shared word (a bedside lamp is not a head light, a bay panel not an electric
panel). A surface pairs only on the same finish words and the same finish states (rusted, tinted, emissive, gloss).
A pair is accepted only when the kind is the same and nothing clashes: a setting the adopted object is made for and
ours is not in, a colour or material, a part of a whole (a car's door), a stack or set against one, a container holding
its content (a potted plant for a pot), a size more than twice or half ours, a finish painted for one model or made for
another use (a blanket's fabric for a suit's). Among equal candidates one is chosen only by evidence (the words besides
the object that both names share, then the only one within twice our size, or for a surface every candidate the same
maps); when nothing tells them apart the pair is refused, never the first one taken. The proposals file holds every
older entry's row (accepted, refused or no match, with its score and reason); `--apply` withdraws each accepted one
naming its replacement (`replaced_by`), so places take the replacement at their next build.

A model's size is its bounding box's sides in metres: its record's `model.json` `size_m`, else its glTF's bounds, else
its USD's, the files fetched through a cache folder emptied after each read (`Sizes`).

An adopted set is a `config/sources.toml` row with a `take_in` table; its entries are those whose licence block or
provenance names that row."""
import json
import pathlib
import re
import shutil

import numpy as np

from score_lib import entry as entries
from score_lib import fetch, index, locks
from score_lib.admission import Sources
from score_lib.blobs import MissingBlob

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
                           "storage", "dock"},
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
# An adopted model whose name says it is a part of a whole (a car body's door, an engine's valve lifter, a cooling
# system's radiator) is not the whole object ours is.
PART_WORDS = {"part", "parts", "component", "components", "system"}
# Words naming several of a thing as one (a bowl stack, a utensil set): a group is never one of them, nor one a group.
GROUP_WORDS = {"stack", "stacked", "pile", "set", "group", "bundle", "heap"}
# Plural words naming one thing (a corner shelves is one shelving unit).
SINGULAR_S = {"pliers", "scissors", "glasses", "goggles", "tongs", "shears", "trousers", "binoculars", "headphones",
              "lens", "canvas", "chassis", "series", "shelves", "stairs", "steps"}
# Words saying a container holds its content (a potted plant): never the bare container, nor the content alone.
CONTENT_WORDS = {"potted", "filled", "loaded", "stuffed"}
# Where a light stands, named after it in a noun-first id (a Kenney lamp_round_floor is a floor lamp).
PLACEMENTS = {"floor", "ceiling", "table", "wall", "desk"}
# An adopted surface's words that say nothing of its finish or use (its layer and opacity marks, single letters).
SURFACE_NEUTRAL = {"m", "opaque", "trans", "clear", "semi", "colored", "misc", "aniso"}
# Two models are the same size when the longer side of either is at most this many times the other's.
SIZE_RATIO = 2.0
# A model's files read for its size at most this many bytes (one model's USD in the cache at a time).
SIZE_READ_BYTES = 2 << 30
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


def our_object(words):
    """Our id's object: its words' object, a light named before where it stands taken as that light (a Kenney
    lamp_round_floor is a floor light)."""
    if len(words) > 1 and words[-1] in PLACEMENTS and "light" in words[:-1]:
        return f"{words[-1]} light"
    return object_of(words)


def raw_tokens(text):
    """A name's lowercased tokens as written, camelCase and punctuation split (plurals and group words kept)."""
    return [part.lower() for chunk in re.split(r"[^A-Za-z0-9]+", text or "") for part in CAMEL.split(chunk) if part]


def is_plural(token):
    """Whether a token is a plural (boxes, pipes), not a plural word naming one thing (pliers) or a word ending in ss,
    us or is."""
    return singular(token) != token and token not in SINGULAR_S


def many_of(text, head):
    """Whether a name names several of the thing `head` as one: a group word (a bowl stack, a utensil set), or that
    thing's own word in the plural (storage boxes, pipes)."""
    tokens = raw_tokens(text)
    if GROUP_WORDS & set(tokens):
        return True
    return any(is_plural(token) and singular(SYNONYMS.get(token, token)) == head for token in tokens)


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
    """One adopted entry as the matcher reads it: its record and address, its words, its classes, its name's object,
    its settings, colours, finishes and its maps."""

    def __init__(self, record):
        self.record = record
        self.address = record["address"]
        self.title = record["title"]
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
        self.maps = tuple(tags.get("maps_key", []))


def exact_finishes(words):
    """The finish words, "metal" dropped where a metal or paint names it more exactly."""
    finishes = set(words) & FINISHES
    return finishes - {"metal"} if finishes & (METALS | {"paint"}) else finishes


def states_of(words):
    """The finish states the words name."""
    return {FINISH_STATES[word] for word in words if word in FINISH_STATES}


def kind_of(ours, words, candidate):
    """How our object is the adopted model's kind: "its Wikidata class" when it is the class, "its name, within its
    Wikidata class" when it is the name's object and the name keeps to the class (the same head noun with the class's
    other words ours too or our object a compound of its own, or a member of a broader class: a bare door is not a
    vehicle door), "its name" when there is no class; None when it is not the same thing."""
    if ours in candidate.classes:
        return "its Wikidata class"
    if ours != candidate.named:
        return None
    if not candidate.classes:
        return "its name"
    head = ours.split()[-1]
    for each in candidate.classes:
        if head in CLASS_MEMBERS.get(each, ()):
            return "its name, within its Wikidata class"
        if head == each.split()[-1] and (" " in ours or set(each.split()[:-1]) <= set(words)):
            return "its name, within its Wikidata class"
    return None


def overlap(ours, candidate):
    """How much of the two names is shared: shared words over the words of both (Jaccard), 0 to 1."""
    both = set(ours) | set(candidate.words)
    return round(len(set(ours) & set(candidate.words)) / len(both), 3) if both else 0.0


def finishes_agree(ours, theirs):
    """Whether two sets of finish words can name one material: either names none, they share one, or one says metal
    where the other names a metal."""
    if not ours or not theirs or ours & theirs:
        return True
    return ("metal" in ours and bool(theirs & METALS)) or ("metal" in theirs and bool(ours & METALS))


def whole_refusal(record, words, ours, candidate):
    """Why the adopted model is not ours whole: a part of a whole, a container holding its content, a group against
    one; None when it is one whole object as ours is."""
    if PART_WORDS & set(candidate.words) and not PART_WORDS & set(words):
        return f"a part of a whole (its name: {candidate.title}), not a whole {ours}"
    held = CONTENT_WORDS & set(candidate.words)
    if held != CONTENT_WORDS & set(words):
        return f"a container holding its content ({', '.join(sorted(held)) or 'ours'}), not the same thing"
    head = ours.split()[-1]
    ours_many = many_of(record["id"].partition(".")[0], head)
    if ours_many != many_of(candidate.title, head):
        return "a stack or set of them is not one" if ours_many else "one is not a stack or set of them"
    return None


def look_refusal(words, candidate):
    """Why the adopted model looks otherwise than our id says: another colour or another material; None when
    nothing clashes."""
    colours = set(words) & COLOURS
    if colours and candidate.colours and not colours & candidate.colours:
        return f"not the same colour ({', '.join(sorted(colours))}; it is {', '.join(sorted(candidate.colours))})"
    finishes = exact_finishes(words)
    if not finishes_agree(finishes, candidate.finishes):
        return (f"not the same material ({', '.join(sorted(finishes))}; it is "
                f"{', '.join(sorted(candidate.finishes))})")
    return None


def name_refusal(ours, candidate):
    """Why a model matched by its Wikidata class alone is not ours: its name does not name our object (a tilt truck
    classed as a dumpster, a box of face masks classed as a box); None when it does."""
    head = ours.split()[-1]
    if head in {singular(word) for word in candidate.words}:
        return None
    return f"its Wikidata class is {ours}, but its name ({candidate.title}) names no {head}"


def model_refusal(record, words, places, candidate):
    """Why an adopted model may not replace our model, or None when it is the same kind of object."""
    if record["id"].startswith(FAR_PREFIX):
        return "a far copy, a backdrop seen from afar, is not replaced by a simulated object"
    ours = our_object(words)
    if ours is None:
        return "our id names no object, only how it looks"
    kind = kind_of(ours, words, candidate)
    if kind is None:
        theirs = ", ".join(sorted(candidate.classes)) or candidate.named
        return f"not the same kind of object ({ours}; it is {theirs})"
    refusal = ((name_refusal(ours, candidate) if kind == "its Wikidata class" else None)
               or whole_refusal(record, words, ours, candidate) or look_refusal(words, candidate))
    if refusal:
        return refusal
    settings = settings_of(words + places)
    missing = candidate.settings - settings
    if missing:
        return (f"it is made for a {', '.join(sorted(missing))} setting, and ours is "
                f"{', '.join(sorted(settings)) or 'named for none'}")
    return None


def use_words(words):
    """A surface's words besides its finish, colour, state, qualifiers and layer marks: the use or part it is made
    for (a blanket, a body)."""
    return {word for word in words if word not in FINISHES | COLOURS | QUALIFIERS | SURFACE_NEUTRAL
            and word not in FINISH_STATES and len(word) > 1}


def surface_refusal(record, words, candidate):
    """Why an adopted surface may not replace our surface, or None when it is the same finish."""
    if candidate.painted_for:
        return f"a finish painted for one model ({', '.join(candidate.painted_for)}), not a library finish"
    family = [word for each in record.get("tags", {}).get("family", []) for word in words_of(each)]
    finishes = exact_finishes(words + family)
    if not finishes:
        return "our surface names no finish the adopted set's materials name (a picture, a glow or a ground)"
    if finishes != candidate.finishes:
        return (f"not the same finish ({', '.join(sorted(finishes))}; it is "
                f"{', '.join(sorted(candidate.finishes)) or 'none named'})")
    states = states_of(words)
    if states != candidate.states:
        return (f"not the same finish state ({', '.join(sorted(states)) or 'unmarked'}; it is "
                f"{', '.join(sorted(candidate.states)) or 'unmarked'})")
    other_use = use_words(candidate.words) - set(words + family)
    if other_use:
        return (f"a finish made for another use ({', '.join(sorted(other_use))}), and ours is "
                f"{' '.join(words)}")
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


def shared_words(record, words, candidate):
    """The words besides the object (or finish) itself both names share: a colour, a material, a kind of it."""
    named = set((our_object(words) or "").split()) if record["kind"] == "model" else exact_finishes(words)
    return sorted((set(words) & set(candidate.words)) - named)


def size_ratio(ours, theirs):
    """How many times the longer of two models' longest sides is the shorter's."""
    longer, shorter = max(ours[0], theirs[0]), min(ours[0], theirs[0])
    return float("inf") if shorter <= 0 else longer / shorter


def only_size_fit(record, tied, sizes):
    """The one tied candidate within SIZE_RATIO of our size when every other one is known to be outside it, else
    None (read no further than a second fit or an unknown size)."""
    ours = sizes(record) if sizes else None
    if not ours:
        return None
    fits = []
    for candidate in tied:
        found = sizes(candidate.record)
        if found is None:
            return None
        if size_ratio(ours, found) <= SIZE_RATIO:
            fits.append(candidate)
            if len(fits) > 1:
                return None
    return fits[0] if fits else None


def size_refusal(record, candidate, sizes):
    """Why the chosen model is not our size (its longest side more than SIZE_RATIO times ours or less than its
    share), or None when it is or a size is unknown."""
    ours = sizes(record) if sizes and record["kind"] == "model" else None
    theirs = sizes(candidate.record) if ours else None
    if ours and theirs and size_ratio(ours, theirs) > SIZE_RATIO:
        return f"not its size (ours is {ours[0]:.2f} m long, it is {theirs[0]:.2f} m)"
    return None


def settle(record, words, standing, sizes):
    """(the one standing candidate, why) when evidence tells it from the others, else (None, why it is tied): the most
    shared words besides the object, then the one whose name adds no word to ours, then the only one within twice our
    size, then for a surface the same maps in all."""
    shared = {candidate.address: shared_words(record, words, candidate) for candidate in standing}
    most = max(len(found) for found in shared.values())
    best = [candidate for candidate in standing if len(shared[candidate.address]) == most]
    why = f"told apart by its words ({', '.join(shared[best[0].address])})" if most and len(standing) > 1 else ""
    plain = [candidate for candidate in best if set(candidate.words) <= set(words)]
    if len(best) > 1 and len(plain) == 1:
        best, why = plain, f"the one of {len(best)} whose name says nothing ours does not"
    if len(best) > 1:
        fit = only_size_fit(record, best, sizes)
        if fit:
            best, why = [fit], f"the only one of {len(best)} within {SIZE_RATIO:g} times its size"
    if len(best) > 1 and record["kind"] == "surface" and len({candidate.maps for candidate in best}) == 1:
        best, why = [best[0]], f"the same maps in all {len(best)} equal candidates"
    if len(best) > 1:
        names = ", ".join(candidate.address for candidate in best[:5]) + (", ..." if len(best) > 5 else "")
        return None, f"tied, nothing tells them apart ({len(best)} equal candidates: {names})"
    return best[0], why


def proposal(record, candidates, sizes=None):
    """Our entry's proposal row: {old, new, score, status accepted|refused|no match, reason}."""
    words, places = our_name(record)
    rows = candidate_rows(record, words, places, candidates)
    if not rows:
        return {"old": record["address"], "new": None, "score": 0.0, "status": "no match",
                "reason": "no adopted entry names its object or finish"}
    score, best, refusal = rows[0]
    if refusal:
        return {"old": record["address"], "new": best.address, "score": score, "status": "refused", "reason": refusal}
    chosen, why = settle(record, words, [row[1] for row in rows if row[2] is None], sizes)
    if chosen is None:
        return {"old": record["address"], "new": None, "score": score, "status": "refused", "reason": why}
    score = next(row[0] for row in rows if row[1] is chosen)
    refusal = size_refusal(record, chosen, sizes)
    if refusal:
        return {"old": record["address"], "new": chosen.address, "score": score, "status": "refused",
                "reason": refusal}
    if record["kind"] == "model":
        ours = our_object(words)
        reason = f"the same object ({ours}), by {kind_of(ours, words, chosen)}"
    else:
        reason = f"the same finish ({', '.join(sorted(chosen.finishes | chosen.colours | chosen.states))})"
    reason += f"; {why}" if why else ""
    return {"old": record["address"], "new": chosen.address, "score": score, "status": "accepted", "reason": reason}


def newest_candidates(root, kind, source_ids):
    """The adopted set's newest standing entry of each id of one kind."""
    newest = {}
    for record in entries.all_entries(root, kind):
        if is_adopted(record, source_ids) and record["state"] != "withdrawn":
            newest[record["id"]] = record
    return [Candidate(record) for _, record in sorted(newest.items())]


def match(root, sources=None, source_ids=None, sizes=None):
    """The proposals of every older model and surface entry (not the set's own, not already replaced); `sizes` (a
    `Sizes`) reads each model's size when a pair needs it, and the sizes read are in the report."""
    source_ids = list(source_ids or adopted_sources(sources or Sources.load()))
    proposals = []
    for kind in KINDS:
        candidates = newest_candidates(root, kind, source_ids)
        for record in entries.all_entries(root, kind):
            if not is_adopted(record, source_ids) and not record.get("replaced_by"):
                proposals.append(proposal(record, candidates, sizes))
    counts = {status: sum(1 for row in proposals if row["status"] == status)
              for status in ("accepted", "refused", "no match")}
    report = {"sources": source_ids, "counts": counts, "proposals": proposals}
    if sizes:
        report["sizes"] = {address: found for address, found in sorted(sizes.found.items())}
        report["sizes_unread"] = sorted(sizes.unread)
    return report


def node_matrix(node):
    """A glTF node's local transform as a 4x4 matrix (its matrix, else its translation, rotation and scale)."""
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    x, y, z, w = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation * np.array(node.get("scale", [1.0, 1.0, 1.0]))
    matrix[:3, 3] = node.get("translation", [0.0, 0.0, 0.0])
    return matrix


def gltf_corners(document):
    """The corners of every mesh's POSITION bounds (the accessor's min and max) through its node's world transform."""
    nodes = document.get("nodes", [])
    scenes = document.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    pending = [(number, np.eye(4)) for number in scenes[document.get("scene", 0)].get("nodes", [])]
    corners = []
    while pending:
        number, parent = pending.pop()
        node = nodes[number]
        world = parent @ node_matrix(node)
        pending += [(child, world) for child in node.get("children", [])]
        for primitive in document["meshes"][node["mesh"]]["primitives"] if "mesh" in node else []:
            found = document["accessors"][primitive["attributes"]["POSITION"]]
            low, high = found["min"], found["max"]
            box = np.array([[xs[0], ys[1], zs[2], 1.0] for xs in (low, high) for ys in (low, high)
                            for zs in (low, high)])
            corners.append((box @ world.T)[:, :3])
    return np.concatenate(corners) if corners else None


def sides_of(low, high, metres_per_unit=1.0):
    """A bounding box's sides in metres, longest first."""
    return sorted((round(float(side) * metres_per_unit, 4) for side in np.asarray(high) - np.asarray(low)),
                  reverse=True)


def gltf_sides(document):
    """A glTF document's bounding box sides (glTF is in metres), or None when it has no mesh."""
    corners = gltf_corners(document)
    return None if corners is None else sides_of(corners.min(axis=0), corners.max(axis=0))


def usd_sides(path):
    """A USD stage's bounding box sides in metres (its default and render geometry), or None when it is empty."""
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.Open(str(path))
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    found = bounds.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    if found.IsEmpty():
        return None
    return sides_of(found.GetMin(), found.GetMax(), UsdGeom.GetStageMetersPerUnit(stage))


def record_sides(path):
    """The sides a model's own record file gives (`size_m`), longest first, or None."""
    size = json.loads(pathlib.Path(path).read_text()).get("size_m")
    return sorted((float(side) for side in size), reverse=True) if size else None


def size_reads(record):
    """The ways to read a model's size, cheapest first: (the files to fetch, the one to read, its reader)."""
    files = record["files"]
    reads = [(["model.json"], "model.json", record_sides)] if "model.json" in files else []
    reads += [([name], name, lambda path: gltf_sides(json.loads(pathlib.Path(path).read_text())))
              for name in sorted(files) if name.endswith(".gltf")]
    usd = (record.get("usd") or {}).get("root") or next((name for name in sorted(files)
                                                        if name.endswith((".usdc", ".usd", ".usda"))), None)
    if usd:
        layers = [name for name, described in files.items() if described["role"] == "usd" or name == usd]
        if sum(files[name]["bytes"] for name in layers) <= SIZE_READ_BYTES:
            reads.append((layers, usd, usd_sides))
    from score_lib.review import glb_parts
    reads += [([name], name, lambda path: gltf_sides(glb_parts(path)[0]))
              for name in sorted(files) if name.endswith(".glb") and files[name]["bytes"] <= SIZE_READ_BYTES]
    return reads


class Sizes:
    """Each model's bounding box sides in metres, longest first, read once a run through a cache folder emptied after
    each read; `blobs` is the blob store or a function giving it (opened at the first read). `found` holds each size
    read (None when unknown), `unread` the models whose bytes the store did not give."""

    def __init__(self, root, blobs, cache):
        self.root, self.blobs, self.cache = root, blobs, pathlib.Path(cache)
        self.found, self.unread = {}, set()

    def __call__(self, record):
        if record["kind"] != "model":
            return None
        if record["address"] not in self.found:
            self.found[record["address"]] = self.read(record)
        return self.found[record["address"]]

    def store(self):
        """The blob store, opened once."""
        if callable(self.blobs):
            self.blobs = self.blobs()
        return self.blobs

    def read(self, record):
        """One model's size: the first of its reads that gives one, or None."""
        folder = self.cache / record["id"]
        for names, name, reader in size_reads(record):
            try:
                fetch.fetch(self.root, self.store(), [record["address"]], folder, names=names)
                found = reader(fetch.destination(folder, record, name))
            except MissingBlob:
                self.unread.add(record["address"])
                found = None
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            if found:
                return found
        return None


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
