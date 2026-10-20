"""Quality scores: every entry scored 0 to 100 from free, local measures, by a scoring system of its own kind's, with
each part, how it was measured and a picture of the worst spot where it applies, and what refining it would do.

    score-lib quality [--kind k] [--address a] [--threads 4] [--dry-run --report file]

Each kind's module (`quality/<kind>.py`) holds its parts and their marks; `parts.py` the ramp they share; 50 is a
gate's own line wherever the framework has a gate for a measure. Parts that need a paid judge are named with no value
and left out. The run reads the SCORE store (`SCORE_STORE`) and the score repository (`SCORE_REPO`) and never writes
to either; it writes the four fields below into each entry and puts its worst-spot pictures into the blob store.

    score           0..100, the weighted mean of the measured parts (None when nothing could be measured)
    score_parts     [{name, value, weight, measured, unit, how, picture?} or {name, value: None, status, how}]
    refine_options  [{action, does, cost: "free" | "paid", price?, basket?}]
    score_made      {at, by, score_commit}

These fields are an assessment, not content: they are left out of an entry's digest and of the export's comparison,
so a locked entry may be scored again and a re-export of unchanged content keeps them.
"""
import datetime
import multiprocessing
import os
import pathlib
import subprocess
import tempfile

from score_lib import entry as entries

# Kinds in the order they are scored: a kit, a layer, a place, a ground cut and a dress read the scores of the
# entries they hold, so those come first.
ORDER = ("algorithm", "surface", "model", "light", "sound", "effect", "character", "ground", "kit", "layer", "place")
# Kinds whose entries are measured in worker processes (the rest are light and run in one).
HEAVY = ("model", "sound", "effect", "surface")

WORKER = {}


def kind_module(kind):
    import importlib
    return importlib.import_module(f"score_lib.quality.{kind}")


def make_context(root, blobs, store, repo, work, scores):
    from score_lib.quality.context import Context
    context = Context(root, blobs, store, repo, work)
    context.scores = scores
    return context


def start_worker(root, repo, work, scores):
    """A worker process's own context (its blob store and store reader from the environment)."""
    from score_lib.blobs import from_environment
    from score_lib.source.store import Store
    folder = pathlib.Path(work) / f"worker-{os.getpid()}"
    WORKER["context"] = make_context(root, from_environment(), Store(os.environ["SCORE_STORE"]), repo, folder,
                                     scores)


def score_one(address):
    """(address, parts, refine options) of one entry, in a worker."""
    context = WORKER["context"]
    record = context.records[address]
    return judged(record, context)


def judged(record, context):
    """(address, parts, refine options) of one entry."""
    from score_lib.quality import parts, refine
    found = kind_module(record["kind"]).run(record, context)
    return record["address"], found, refine.options(record, found, parts.total(found), context)


def score_commit(repo):
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() or None


def stamped(record, found, options, made):
    """The record with its quality fields."""
    from score_lib.quality import parts
    record = dict(record)
    record["score"] = parts.total(found)
    record["score_parts"] = found
    record["refine_options"] = options
    record["score_made"] = made
    return record


def chosen(context, kinds, addresses):
    """The records to score, by kind in ORDER."""
    records = sorted(context.records.values(), key=lambda record: (ORDER.index(record["kind"]), record["address"]))
    return [record for record in records
            if (not kinds or record["kind"] in kinds) and (not addresses or record["address"] in addresses)
            and not (record["kind"] == "model" and record.get("usd") is None)]  # a taken-in glTF: no OpenUSD to read


def scored_kind(kind, records, context, threads, root, repo, work):
    """[(address, parts, options)] of one kind's records, in workers for a heavy kind."""
    if kind not in HEAVY or threads <= 1 or len(records) < 2:
        return [judged(record, context) for record in records]
    spawn = multiprocessing.get_context("spawn")
    with spawn.Pool(threads, initializer=start_worker, initargs=(root, repo, work, context.scores)) as pool:
        return pool.map(score_one, [record["address"] for record in records], chunksize=4)


def score_of(root, blobs, record):
    """The handover's quality hook (contract library-handover v1): the entry's recorded score as {score 0..1,
    measures {part: value 0..100}}, or None while it has none (a `score-lib quality` run scores it)."""
    if record.get("score") is None:
        return None
    measures = {part["name"]: part["value"] for part in record.get("score_parts") or [] if part.get("value") is not None}
    return {"score": record["score"] / 100, "measures": measures}


def run(root, blobs, store, repo, kinds=(), addresses=(), threads=4, dry_run=False):
    """Score the chosen entries kind by kind; write them unless dry_run. {address: (score, parts, options)}."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="score-lib-quality-"))
    scores = {address: record.get("score") for address, record in
              make_context(root, blobs, store, repo, work, {}).records.items()}
    context = make_context(root, blobs, store, repo, work / "main", scores)
    made = {"at": datetime.datetime.now(datetime.timezone.utc).date().isoformat(), "by": "score-lib quality",
            "score_commit": score_commit(repo)}
    results = {}
    records = chosen(context, kinds, addresses)
    for kind in ORDER:
        of_kind = [record for record in records if record["kind"] == kind]
        if not of_kind:
            continue
        for address, found, options in scored_kind(kind, of_kind, context, threads, root, repo, work):
            record = stamped(context.records[address], found, options, made)
            context.records[address] = record
            scores[address] = record["score"]
            results[address] = (record["score"], found, options)
            if not dry_run:
                entries.write(root, record)
    return results
