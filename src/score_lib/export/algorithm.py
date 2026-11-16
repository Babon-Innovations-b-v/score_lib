"""algorithm entries: one engine algorithm as a code snapshot plus a settings schema, the world 1 preset, its tests
and a run line (spec 4.10), from a hand-written definitions/algorithms/<name>.toml.

The snapshot is the closure of repository modules imported from the roots, found with score's code_version recipe
(score/graph/_code.py:96; its FRAMEWORK files left out), re-implemented here over git's objects at the commit. The
code hash follows the same recipe: the sha256 of the sorted JSON of ["python <major.minor>", "<path> <sha256>"...].
"""
import ast
import fnmatch
import hashlib
import importlib.util
import json
import posixpath
import re
import tomllib

from score_lib.export import places as place_reads
from score_lib.export.run import Candidate, write_json
from score_lib.admission import SCORE
from score_lib.preview import audio

DEFINITIONS = "definitions/algorithms"
PYTHON = "3.12"
# score/graph/_code.py:18-26: what runs a stage, not what a stage runs; never hashed, never followed.
FRAMEWORK = {"score/__main__.py", "score/graph/__init__.py", "score/graph/_code.py", "score/review/__init__.py",
             "score/review/__main__.py", "score/adapters/compute/__init__.py", "score/stages/__init__.py",
             "score/stages/__main__.py", "tools/props/cloud/batch.py", "tools/props/cloud/capacity.py",
             "tools/props/cloud/park.py", "tools/props/cloud/blender_cloud.py", "tools/props/cloud/ledger.py"}
# The cloud runners (renting machines, the paid parts) are hashed with the code, as score's recipe does, but never
# shipped: the library carries no provider code.
CLOUD = ("tools/props/cloud/", "tools/cloud/")
# A code file whose head names a licence other than MIT (a port of someone else's code) is never shipped.
SPDX = re.compile(r"SPDX-License-Identifier:\s*([\w.+-]+)")
HEAD = 2000
FIELD = re.compile(r"^\s*(score:[\w:]+)\s+([\w\[\]]+)\s+(.*)$")
TYPES = {"string": "string", "token": "string", "float": "number", "double": "number", "int": "integer",
         "bool": "boolean", "float2": "array", "float3": "array", "float4": "array", "color3f": "array",
         "float2[]": "array"}


def plan(context):
    """One candidate per definition file of the library's definitions/algorithms."""
    return [candidate(context, load(path)) for path in sorted((context.root / DEFINITIONS).glob("*.toml"))]


def load(path):
    with open(path, "rb") as source:
        return tomllib.load(source)


def python_files(context):
    """Every .py path of the repository at the commit."""
    return context.remembered(("python_files",), lambda: {name for name in context.repo.files()
                                                           if name.endswith(".py")})


def package_of(path, files):
    """The dotted package a repository file sits in ('' outside one), from the __init__.py files beside it."""
    parts = []
    folder = posixpath.dirname(path)
    while folder and posixpath.join(folder, "__init__.py") in files:
        parts.insert(0, posixpath.basename(folder))
        folder = posixpath.dirname(folder)
    return ".".join(parts)


def imported_names(text, package):
    """Every module name a source imports, anywhere in it, made absolute (score/graph/_code.py imported_names)."""
    names = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            else:
                base = node.module
            names += [base] + [f"{base}.{alias.name}" for alias in node.names]
    return names


def module_files(name, search, files):
    """The repository files an import name could be found as in each search folder."""
    parts = name.split(".")
    found = set()
    for folder in search:
        stem = posixpath.join(folder, *parts) if folder else posixpath.join(*parts)
        for path in (f"{stem}.py", posixpath.join(stem, "__init__.py")):
            if path in files:
                found.add(path)
    return found


class Missing(ValueError):
    """A definition names a repository file the world's commit does not hold: exported, it would be hollow."""


def refuse_missing(context, paths, what):
    """Raise Missing naming every path of `paths` the repository does not hold at the commit."""
    missing = sorted(set(paths) - python_files(context))
    if missing:
        raise Missing(f"{what} names {', '.join(missing)}, not in the repository at {context.world['commit']}")


def closure(context, roots, search):
    """Every repository file the roots are or import, followed import by import, the framework's files left out;
    Missing when a root is not in the repository (score moved or deleted it)."""
    files = python_files(context)
    refuse_missing(context, roots, "roots")
    folders = [""] + list(search)
    pending = list(roots)
    found = set()
    while pending:
        path = pending.pop()
        if path in found or path in FRAMEWORK or path not in files:
            continue
        found.add(path)
        text = context.repo.read_text(path)
        for name in imported_names(text, package_of(path, files)):
            pending += [module for module in module_files(name, folders, files) if module not in found]
    return sorted(found)


def parents(paths, files):
    """The __init__.py of every package the files sit in (needed to import them, not part of the hash)."""
    found = set()
    for path in paths:
        folder = posixpath.dirname(path)
        while folder:
            init = posixpath.join(folder, "__init__.py")
            if init in files:
                found.add(init)
            folder = posixpath.dirname(folder)
    return sorted(found - set(paths))


def code_hash(context, paths):
    """score's code version of a set of files: sha256 of the sorted JSON of the python version and path hashes."""
    parts = [f"python {PYTHON}"] + [f"{path} {hashlib.sha256(context.repo.read_bytes(path)).hexdigest()}"
                                    for path in sorted(paths)]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def arithmetic(node):
    """The value of a literal, or of plain arithmetic on literals (1.0 / 30.0); ValueError otherwise."""
    try:
        return ast.literal_eval(node)
    except ValueError:
        pass
    if isinstance(node, ast.BinOp):
        left, right = arithmetic(node.left), arithmetic(node.right)
        operations = {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                      ast.Div: lambda: left / right, ast.Pow: lambda: left ** right}
        if type(node.op) in operations:
            return operations[type(node.op)]()
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -arithmetic(node.operand)
    raise ValueError(f"not a literal: {ast.dump(node)[:80]}")


def constant(context, reference):
    """{name, default or expression, $comment} of a module constant `path:NAME` (its top-level assignment)."""
    path, name = reference.rsplit(":", 1)
    text = context.repo.read_text(path)
    for node in ast.parse(text).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
            else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            found = {"$comment": f"{path}:{node.lineno}"}
            try:
                found["default"] = arithmetic(node.value)
            except ValueError:
                found["expression"] = ast.get_source_segment(text, node.value)
            return name, found
    raise KeyError(f"{reference}: no top-level assignment of {name}")


def schema_type(value):
    """The JSON Schema type of a default value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    return "object"


def record_fields(context, reference):
    """{field: {type, description}} from a record field list `path:first-last` (lines naming score:* fields)."""
    if not reference:
        return {}
    path, span = reference.rsplit(":", 1)
    first, last = (int(each) for each in span.split("-"))
    lines = context.repo.read_text(path).splitlines()[first - 1:last]
    found = {}
    for line in lines:
        matched = FIELD.match(line)
        if matched:
            found[matched[1]] = {"type": TYPES.get(matched[2], "string"), "description": matched[3].strip(),
                                 "$comment": f"{path} ({matched[2]})"}
    return found


def settings_schema(context, definition):
    """The settings JSON Schema: module constants (their values the defaults) and the record field list."""
    constants = {}
    for reference in definition.get("constants", []):
        name, found = constant(context, reference)
        if "default" in found:
            found["type"] = schema_type(found["default"])
            if isinstance(found["default"], tuple):
                found["default"] = list(found["default"])
        constants[name] = found
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{definition['name']} settings", "type": "object",
            "properties": {"constants": {"type": "object", "properties": constants},
                           "record": {"type": "object", "properties": record_fields(context,
                                                                                    definition.get("record_fields"))}}}


def stage_nodes(context, stage):
    """Accepted nodes of a stage (place grain `<stage>@<place>` or the world node `<stage>`)."""
    if not stage:
        return []
    return [node for node in context.store.nodes() if node.split("@", 1)[0] == stage and context.store.accepted(node)]


def seeds(context, definition):
    """seed.json: the world seed, the node seed recipe and each world 1 node's recorded seed."""
    nodes = {}
    for node in stage_nodes(context, definition.get("stage")):
        accept = context.store.current_accept(node)
        nodes[node] = context.store.record(node, accept["key"]).get("seed")
    return {"world": context.world["name"], "world_seed": context.world["seed"],
            "recipe": "int(sha256(f'{world_seed}/{stage}/{place}')[:8], 16) (score/graph/__init__.py:177 node_seed)",
            "nodes": nodes}


def reproduces(context, definition, digest):
    """World 1 node keys whose finish record's code equals this snapshot's code hash."""
    found = []
    for node in stage_nodes(context, definition.get("stage")):
        accept = context.store.current_accept(node)
        if context.store.record(node, accept["key"]).get("code") == digest:
            found.append(f"{node}:{accept['key']}")
    return found


def outside_paths(definition):
    """The repository path globs of the definition's outside requirements: what may not go out under MIT."""
    return [pattern for each in definition.get("outside", []) for pattern in each.get("paths", [])]


def kept_out(path, definition):
    """Whether a repository file (code or preset) belongs to an outside requirement, so the snapshot leaves it out."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in outside_paths(definition))


def foreign_licence(context, path):
    """The SPDX licence a repository code file's head names when it is not MIT; None otherwise."""
    found = SPDX.search(context.repo.read_text(path)[:HEAD])
    return found[1] if found and found[1] != "MIT" else None


def foreign_code(context, paths, definition):
    """{path: licence} of the code files that name a licence other than MIT and no outside requirement keeps out."""
    found = {path: foreign_licence(context, path) for path in paths if not kept_out(path, definition)}
    return {path: licence for path, licence in sorted(found.items()) if licence}


def foreign_outside(context, foreign):
    """An outside requirement for each foreign code file: what the entry needs but may not carry."""
    return [{"name": path, "version": f"score at {context.world['commit']}", "where": f"the score repository: {path}",
             "why": f"{licence}: its licence and notices must stay with its code"} for path, licence in foreign.items()]


def preset_files(context, definition):
    """Repository data files the preset globs name, at the commit, without those of an outside requirement."""
    found = []
    for pattern in definition.get("preset", []):
        found += [name for name in context.repo.glob(pattern) if fnmatch.fnmatchcase(name, pattern)]
    return sorted(name for name in set(found) if not kept_out(name, definition))


def outside_record(definition):
    """The outside requirements as an entry names them: {name, version, where, why} each (no path globs)."""
    return [{key: each[key] for key in ("name", "version", "where", "why")} for each in definition.get("outside", [])]


def picture(context, definition, work, files, roles, known):
    """The picture: a world 1 output already in the store ([node, file], or [node, "audio"] for a waveform of the
    output's first audio file)."""
    named = definition.get("picture") or []
    if len(named) != 2:
        return {"reason": "no world 1 output in the store pictures this algorithm"}
    node, name = named
    output = context.store.accepted(node)
    if not output:
        return {"reason": f"{node} has no accepted output"}
    listing = context.store.listing(output)
    if name == "audio":
        sounds = sorted(each for each in listing if each.endswith((".wav", ".ogg")))
        if not sounds:
            return {"reason": f"{node} holds no audio file"}
        audio.render(context.tree(output) / sounds[0], work / "picture.png")
        files["picture.png"] = work / "picture.png"
        roles["picture.png"] = "picture"
        return {"file": "picture.png", "made_by": "preview/audio.py", "of": f"{node}: {sounds[0]}"}
    if name not in listing:
        return {"reason": f"{node} holds no {name}"}
    target = "picture" + posixpath.splitext(name)[1]
    files[target] = context.tree(output) / name
    known[target] = listing[name]
    roles[target] = "picture"
    return {"file": target, "made_by": "source picture", "of": f"{node}: {name}"}


def run_record(definition, outside):
    """run.json: what a world needs to run the algorithm and the command it runs."""
    return {"python": PYTHON, "requires": definition.get("requires", []), "entry": definition["entry"],
            "bake": definition.get("bake", []), "needs": definition.get("needs", []),
            "paid": definition.get("paid", []), "contract": definition.get("contract", []),
            "outside": outside,
            "how": f"score-lib run algorithm/{definition['name']}@<version> --preset world1 --out <dir>"}


def candidate(context, definition):
    """One algorithm candidate."""
    name = definition["name"]
    refuse_missing(context, [*definition.get("tests", []),
                             *(reference.rsplit(":", 1)[0] for reference in definition.get("constants", []))],
                   f"algorithm {name}'s tests and constants")
    work = context.work("algorithm", name)
    hashed = closure(context, definition["roots"], definition.get("search", []))
    foreign = foreign_code(context, hashed, definition)
    outside = outside_record(definition) + foreign_outside(context, foreign)
    shipped = [path for path in hashed
               if not path.startswith(CLOUD) and not kept_out(path, definition) and path not in foreign]
    every = python_files(context)
    files, roles, known = {}, {}, {}
    for path in shipped + parents(shipped, every):
        files[f"code/{path}"] = context.repo.export(path, work / "code" / path)
        roles[f"code/{path}"] = "code"
    digest = code_hash(context, hashed)
    code = {"commit": context.world["commit"], "python": PYTHON, "code_hash": digest,
            "files": {path: hashlib.sha256(context.repo.read_bytes(path)).hexdigest() for path in shipped},
            "left_out": {path: hashlib.sha256(context.repo.read_bytes(path)).hexdigest() for path in hashed
                         if path not in shipped},
            "left_out_why": "cloud runner code (paid parts) and outside requirements' code: hashed in code_hash, "
                            "not shipped",
            "imports_only": parents(shipped, every), "reproduces": reproduces(context, definition, digest)}
    files["code.json"] = write_json(work / "code.json", code)
    files["settings.schema.json"] = write_json(work / "settings.schema.json", settings_schema(context, definition))
    files["presets/world1/seed.json"] = write_json(work / "presets/world1/seed.json", seeds(context, definition))
    for path in preset_files(context, definition):
        files[f"presets/world1/{path}"] = context.repo.export(path, work / "presets/world1" / path)
    for path in definition.get("tests", []):
        files[f"test/{path}"] = context.repo.export(path, work / "test" / path)
    files["run.json"] = write_json(work / "run.json", run_record(definition, outside))
    files["definition.json"] = write_json(work / "definition.json", definition)
    for each in files:
        roles.setdefault(each, "code" if each.startswith(("code/", "test/")) else "record")
    shown = picture(context, definition, work, files, roles, known)
    made = Candidate(kind="algorithm", base_id=name, identity=digest, title=definition["title"], places=[],
                     files=files, roles=roles, made_with=[dict(SCORE)],
                     provenance={"stage": definition.get("stage") or None, "code": digest,
                                 "outside": outside,
                                 "nodes": stage_nodes(context, definition.get("stage"))},
                     tags={"needs": definition.get("needs", []), "runnable": [not definition.get("needs")]},
                     picture=shown, known=known)
    made.entry_id = name
    made.places = sorted({place_reads.sanitised(node.split("@", 1)[1]) for node in made.provenance["nodes"]
                          if "@" in node})
    return made
