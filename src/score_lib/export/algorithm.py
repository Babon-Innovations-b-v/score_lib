"""algorithm entries: one engine algorithm as a code snapshot plus a settings schema, the world 1 preset, its tests
and a run line (spec 4.10), from a hand-written definitions/algorithms/<name>.toml.

The snapshot is the closure of repository modules imported from the roots, found with score's code_version recipe
(score/graph/_code.py:96; its FRAMEWORK files left out), re-implemented here over git's objects at the commit. The
code hash follows the same recipe: the sha256 of the sorted JSON of ["python <major.minor>", "<path> <sha256>"...].
"""
import ast
import fnmatch
import functools
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


@functools.lru_cache(maxsize=None)
def parse(text):
    """A source's syntax tree, parsed once a process (the walks read the same files for every definition); the
    callers only read it."""
    return ast.parse(text)


def source(context, path):
    """A repository file's text at the commit, read once a run."""
    return context.remembered(("text", path), lambda: context.repo.read_text(path))


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
    for node in ast.walk(parse(text)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            else:
                base = node.module
            names += [base] + [f"{base}.{alias.name}" for alias in node.names]
    return names


def used_names(text, package, search, files):
    """The module names a source runs code of: imported_names, less the package of a `from <package> import
    <submodule>...` whose every name is a module file (Python only needs the package to exist to load them)."""
    names = []
    for node in ast.walk(parse(text)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            else:
                base = node.module
            submodules = [f"{base}.{alias.name}" for alias in node.names]
            names += submodules
            if not all(module_files(name, search, files) for name in submodules):
                names.append(base)
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
        text = source(context, path)
        for name in imported_names(text, package_of(path, files)):
            pending += [module for module in module_files(name, folders, files) if module not in found]
    return sorted(found)


def path_value(node, path, assigned):
    """The repository folder a path expression names, statically: `pathlib.Path(__file__)` (and `.resolve()`,
    `.absolute()`), `.parent`, `.parents[k]`, `/ "literal"`, `str(...)` and module-level names bound to such an
    expression; None for anything else (a folder from argv, the working directory)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
        return path_value(node.args[0], path, assigned)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in ("resolve", "absolute") and not node.args:
            return path_value(node.func.value, path, assigned)
        if node.func.attr == "Path" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) \
                and node.args[0].id == "__file__":
            return path
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        inner = path_value(node.value, path, assigned)
        return None if inner is None else posixpath.dirname(inner)
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents" \
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
        inner = path_value(node.value.value, path, assigned)
        for _ in range(node.slice.value + 1):
            inner = None if inner is None else posixpath.dirname(inner)
        return inner
    right = assigned.get(node.right.id) if isinstance(node, ast.BinOp) and isinstance(node.right, ast.Name) else \
        getattr(node, "right", None)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(right, ast.Constant) \
            and isinstance(right.value, str):
        inner = path_value(node.left, path, assigned)
        return None if inner is None else posixpath.normpath(posixpath.join(inner, right.value)).lstrip("./")
    if isinstance(node, ast.Name) and node.id in assigned:
        return path_value(assigned.pop(node.id), path, assigned)
    return None


@functools.lru_cache(maxsize=None)
def inserted_folders(text, path):
    """The repository folders a source puts on sys.path (`sys.path.insert(i, <folder>)`, `sys.path.append(<folder>)`)
    where the folder is a path expression path_value can follow; '' is the repository's root."""
    tree = parse(text)
    assigned = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    found = []
    for node, bound in path_calls(tree, assigned):
        folder = path_value(node.args[-1], path, bound)
        if folder is not None and folder != ".." and not folder.startswith("../"):
            found.append("" if folder == "." else folder)
    return tuple(found)


def path_calls(tree, assigned):
    """Each `sys.path.insert` / `sys.path.append` call of a source with the names it may see: the module's own, and
    once per value a loop over a literal tuple or list of strings binds its name to (`for folder in ("a", "b"):`)."""
    loops = {}
    for loop in ast.walk(tree):
        if isinstance(loop, ast.For) and isinstance(loop.target, ast.Name) and isinstance(loop.iter, (ast.Tuple, ast.List)) \
                and all(isinstance(each, ast.Constant) and isinstance(each.value, str) for each in loop.iter.elts):
            for inner in ast.walk(loop):
                loops.setdefault(id(inner), []).extend((loop.target.id, each) for each in loop.iter.elts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("insert", "append") \
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "path" and node.args:
            bindings = loops.get(id(node)) or [(None, None)]
            for name, value in bindings:
                yield node, {**assigned, **({name: value} if name else {})}


def needed(context, roots, search, framework=FRAMEWORK):
    """The files of the hashed closure the roots run: followed by used_names, so a package only reached through
    `from <package> import <submodule>` is not run and ships as a marker (package_marker), and through every folder a
    reached source puts on sys.path (inserted_folders), as Python finds them when it runs; the `framework` files are
    not followed."""
    files = python_files(context)
    folders = [""] + list(search)
    found = set()
    while True:
        pending, reached, grown = list(roots), set(), False
        while pending:
            path = pending.pop()
            if path in reached or path in framework or path not in files:
                continue
            reached.add(path)
            text = source(context, path)
            for folder in inserted_folders(text, path):
                if folder not in folders:
                    folders.append(folder)
                    grown = True
            for name in used_names(text, package_of(path, files), folders, files):
                pending += [module for module in module_files(name, folders, files) if module not in reached]
        found = reached
        if not grown:
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


def package_marker(destination):
    """An empty __init__.py at `destination`, its path: a package the snapshot's files sit in but import nothing from
    by name. Its own content in score (the stage table, the framework's imports) is not the algorithm, so the
    snapshot carries the folder as a package and none of that code."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"")
    return destination


def code_hash(context, paths):
    """score's code version of a set of files: sha256 of the sorted JSON of the python version and path hashes."""
    parts = [f"python {PYTHON}"] + [f"{path} {hashlib.sha256(source(context, path).encode()).hexdigest()}"
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
    found = SPDX.search(source(context, path)[:HEAD])
    return found[1] if found and found[1] != "MIT" else None


def foreign_code(context, paths, definition):
    """{path: licence} of the code files that name a licence other than MIT and no outside requirement keeps out."""
    found = {path: foreign_licence(context, path) for path in paths if not kept_out(path, definition)}
    return {path: licence for path, licence in sorted(found.items()) if licence}


def foreign_outside(context, foreign):
    """An outside requirement for each foreign code file: what the entry needs but may not carry."""
    return [{"name": path, "version": f"score at {context.world['commit']}", "where": f"the score repository: {path}",
             "why": f"{licence}: its licence and notices must stay with its code"} for path, licence in foreign.items()]


def conftests(context, tests):
    """Every conftest.py pytest loads for the tests: the one in each folder from the repository's root down to the
    test's own."""
    files = python_files(context)
    found = set()
    for test in tests:
        folder = posixpath.dirname(test)
        while True:
            path = posixpath.join(folder, "conftest.py") if folder else "conftest.py"
            if path in files:
                found.add(path)
            if not folder:
                break
            folder = posixpath.dirname(folder)
    return sorted(found)


def test_support(context, definition, run):
    """The code files the tests and their conftest.py files run that the roots do not, the framework's included (a
    stage's module test imports the stage table and its checks): shipped beside the snapshot, or named in code.json's
    left_out when the library may not carry them."""
    tests = list(definition.get("tests", []))
    tests += conftests(context, tests)
    return [path for path in needed(context, tests, definition.get("search", []), framework=frozenset())
            if path not in run + tests]


def kept_out_outside(definition, run):
    """The definition's outside requirements whose code the roots run (not only data a preset leaves out)."""
    return [{key: each[key] for key in ("name", "version", "where", "why")} for each in definition.get("outside", [])
            if any(fnmatch.fnmatchcase(path, pattern) for path in run for pattern in each.get("paths", []))]


def cloud_outside(context, run):
    """An outside requirement for each cloud runner file the roots run: score's own code, which the library never
    carries, so the entry runs only beside score."""
    return [{"name": path, "version": f"score at {context.world['commit']}", "where": f"the score repository: {path}",
             "why": "score's cloud runner code: the library carries no provider code"}
            for path in run if path.startswith(CLOUD)]


def named_files(context, paths):
    """The repository files (data, or a script read as text) the code files name by a path expression path_value
    can follow (`REPO / "data/people/rows.json"`, `HERE / "inside/bake.py"`) or by their repository path as a
    string, and the files those JSON files name in turn (json_named), other than the code files themselves: what the
    code reads when it runs (the cloud runner's files among them, which the candidate leaves out). Never vendored code
    or a script under another licence."""
    every = context.remembered(("files",), lambda: set(context.repo.files()))
    found = set()
    for path in paths:
        tree = parse(source(context, path))
        assigned = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                names = [path_value(node, path, dict(assigned))]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names = [node.value] if node.value in every else tail_named(context, node.value, every)
            else:
                continue
            for named in names:
                if named in every and not named.startswith("vendor/") and not (
                        named.endswith(".py") and foreign_licence(context, named)):
                    found.add(named)
    pending = sorted(found)
    while pending:
        for named in json_named(context, pending.pop(), every):
            if named not in found and not named.startswith("vendor/"):
                found.add(named)
                pending.append(named)
    return sorted(found - set(paths))


TAIL = re.compile(r"^[\w.-]+(/[\w.-]+)*\.(json|toml|txt|csv|npz|npy|png|jpg|usda|usdc|usd|glb|wav|ttf|otf|py)$")
TAILS = 3  # a file name naming more repository files than this (a generic record.json) names none


def tail_named(context, text, every):
    """The repository data files a file name or tail of a path in code names (`"concept_views.json"` read from a
    folder another module names), outside vendored code; none when the name is generic (more than TAILS files)."""
    text = re.sub(r"^\{\w+\}/", "", text)  # a template's folder: "{data}/effects/flag_cloth.json"
    if not TAIL.match(text):
        return []
    tails = context.remembered(("tails",), lambda: tail_index(every))
    found = tails.get(text, [])
    return found if len(found) <= TAILS else []


def tail_index(every):
    """{tail: [repository paths ending in it]} of every file outside vendor/, for each of its path tails."""
    index = {}
    for path in every:
        if path.startswith("vendor/"):
            continue
        parts = path.split("/")
        for start in range(1, len(parts)):
            index.setdefault("/".join(parts[start:]), []).append(path)
    return index


def json_strings(value):
    """Every string inside a JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for each in value.values() for text in json_strings(each)]
    if isinstance(value, list):
        return [text for each in value for text in json_strings(each)]
    return []


def json_named(context, path, every):
    """The repository files a JSON data file names by their repository path (a row's `spec`); none for other files."""
    if not path.endswith(".json"):
        return []
    return sorted({text for text in json_strings(json.loads(source(context, path))) if text in every})


def preset_files(context, definition, named=()):
    """Repository data files the preset globs name, and the `named` ones the code reads (named_files), at the
    commit, without those of an outside requirement."""
    found = list(named)
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


def run_record(definition, outside, beside):
    """run.json: what a world needs to run the algorithm and the command it runs; `runs_beside` the outside code its
    roots run, without which it does not run from the library alone."""
    return {"python": PYTHON, "requires": definition.get("requires", []),
            "test_requires": definition.get("test_requires", []), "entry": definition["entry"],
            "bake": definition.get("bake", []), "needs": definition.get("needs", []),
            "paid": definition.get("paid", []), "contract": definition.get("contract", []),
            "outside": outside, "runs_beside": beside,
            "how": f"score-lib run algorithm/{definition['name']}@<version> --preset world1 --out <dir>"}


def candidate(context, definition):
    """One algorithm candidate."""
    name = definition["name"]
    refuse_missing(context, [*definition.get("tests", []),
                             *(reference.rsplit(":", 1)[0] for reference in definition.get("constants", []))],
                   f"algorithm {name}'s tests and constants")
    work = context.work("algorithm", name)
    hashed = closure(context, definition["roots"], definition.get("search", []))
    reached = set(needed(context, definition["roots"], definition.get("search", [])))
    run = [path for path in hashed if path in reached]
    beside = (kept_out_outside(definition, run) + foreign_outside(context, foreign_code(context, run, definition))
              + cloud_outside(context, run))
    outside = outside_record(definition) + [each for each in beside if each not in outside_record(definition)]
    run += test_support(context, definition, run)
    foreign = foreign_code(context, run, definition)
    shipped = [path for path in run
               if not path.startswith(CLOUD) and not kept_out(path, definition) and path not in foreign]
    every = python_files(context)
    tests = [*definition.get("tests", []), *conftests(context, definition.get("tests", []))]
    preset = preset_files(context, definition, named_files(context, run + tests))
    files, roles, known = {}, {}, {}
    for path in shipped:
        files[f"code/{path}"] = context.repo.export(path, work / "code" / path)
        roles[f"code/{path}"] = "code"
    for path in parents(shipped, every):
        files[f"code/{path}"] = package_marker(work / "code" / path)
        roles[f"code/{path}"] = "code"
    digest = code_hash(context, hashed)
    code = {"commit": context.world["commit"], "python": PYTHON, "code_hash": digest,
            "files": {path: hashlib.sha256(source(context, path).encode()).hexdigest() for path in shipped},
            "left_out": {path: hashlib.sha256(source(context, path).encode()).hexdigest()
                         for path in {*hashed, *run, *(path for path in preset if path.startswith(CLOUD))}
                         if path not in shipped},
            "left_out_why": "cloud runner code (paid parts), outside requirements' and other licences' code and "
                            "modules the roots do not run (a package only imported from): not shipped; the roots' "
                            "closure is hashed in code_hash, the tests' own imports beside it are not",
            "imports_only": parents(shipped, every), "reproduces": reproduces(context, definition, digest)}
    files["code.json"] = write_json(work / "code.json", code)
    files["settings.schema.json"] = write_json(work / "settings.schema.json", settings_schema(context, definition))
    files["presets/world1/seed.json"] = write_json(work / "presets/world1/seed.json", seeds(context, definition))
    for path in [path for path in preset if not path.startswith(CLOUD)]:
        files[f"presets/world1/{path}"] = context.repo.export(path, work / "presets/world1" / path)
    for path in tests:
        files[f"test/{path}"] = context.repo.export(path, work / "test" / path)
    files["run.json"] = write_json(work / "run.json", run_record(definition, outside, beside))
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
