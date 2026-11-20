"""`score-lib hub-mirror`: copy the blobs the library's entries name from the S3-compatible store into a Hugging Face
dataset repository, straight from store to repository, and check the copy.

Each blob is read from the source into memory, hashed against its name, uploaded and let go; nothing is staged on
the local disk, and at most WORKERS blobs are held at once. Blobs go up in commits of BATCH_FILES files, so a run that
stops loses at most its open batch: a second run lists what the repository already holds and copies only the rest.
The first commit writes the dataset card (README.md, made by `card`) and .gitattributes (every blob a large file, so
the repository records each one's sha256 and size). The check compares the repository's listing with the source:
every named blob present, its size and its recorded sha256 equal to its name; `deep` blobs drawn at random are also
downloaded and hashed. Only blobs some entry names are copied; a source blob no entry names stays behind.
"""
import concurrent.futures
import hashlib
import random
import subprocess
import tempfile
import tomllib

from score_lib import entry as entries
from score_lib.blobs import BadBlob, key_of

REPO_TYPE = "dataset"
BATCH_FILES = 100
WORKERS = 8
EMPTY = hashlib.sha256(b"").hexdigest()  # the sha256 of no bytes
ATTRIBUTES = "blobs/** filter=lfs diff=lfs merge=lfs -text\n"


def named_blobs(root):
    """Every sha256 an entry's files name, in any state."""
    return {described["sha256"] for record in entries.all_entries(root) for described in record["files"].values()}


def source_sizes(source):
    """{sha256: size} of every blob the S3-compatible source holds, from its listing."""
    prefix = f"{source.prefix}/blobs/" if source.prefix else "blobs/"
    sizes, token = {}, None
    while True:
        asked = {"Bucket": source.bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        page = source.client.list_objects_v2(**asked)
        sizes.update({item["Key"].rsplit("/", 1)[1]: item["Size"] for item in page.get("Contents", [])})
        if not page.get("IsTruncated"):
            return sizes
        token = page["NextContinuationToken"]


def hub_held(api, repo_id, revision, prefix):
    """{sha256: (size, recorded sha256 or None)} of every blob the repository holds; empty when it holds none. An empty
    file is kept outside large-file storage, so nothing records its sha256: its size, 0, is its bytes, and its sha256
    is EMPTY's (the library's one empty blob read as a failure, 2026-11-20)."""
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError
    folder = f"{prefix}/blobs" if prefix else "blobs"
    try:
        listing = list(api.list_repo_tree(repo_id, folder, recursive=True, repo_type=REPO_TYPE, revision=revision))
    except (EntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError):
        return {}
    return {item.path.rsplit("/", 1)[1]: (item.size, item.lfs.sha256 if item.lfs else EMPTY if item.size == 0 else None)
            for item in listing if hasattr(item, "lfs")}


def read_blob(source, sha256):
    """A blob's bytes from the source, checked against its name."""
    data = source.client.get_object(Bucket=source.bucket, Key=source.key(sha256))["Body"].read()
    found = hashlib.sha256(data).hexdigest()
    if found != sha256:
        raise BadBlob(f"the source's {sha256} hashes to {found}")
    return data


def staged(source, api, repo_id, revision, path, sha256):
    """One blob read from the source and uploaded ahead of its commit; its bytes are let go after the upload."""
    from huggingface_hub import CommitOperationAdd
    operation = CommitOperationAdd(path_in_repo=path, path_or_fileobj=read_blob(source, sha256))
    api.preupload_lfs_files(repo_id, [operation], repo_type=REPO_TYPE, revision=revision)
    return operation


def records_url(root):
    """Where the library's records are: the checkout's origin, or None."""
    found = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
    if found.returncode != 0:
        return None
    return found.stdout.strip().removesuffix(".git") or None


def card(root, repo_id, count, total, records=None):
    """The dataset card: what the repository holds, how to read it, its licence and every source's terms."""
    sources = tomllib.loads((root / "config/sources.toml").read_text())["source"]
    cell = lambda text: text.replace("|", "\\|")  # noqa: E731
    rows = "\n".join(f"| {cell(row['name'])} | {'yes' if row.get('copy') else 'no, only what it made'} | "
                     f"{cell(row['why'])} |" for row in sources)
    where = f"[{records}]({records})" if records else "the library's records repository"
    return f"""---
license: mit
pretty_name: score_lib
tags:
- openusd
- 3d
- procedural
- simulation
---

# score_lib: the blob store

The bytes of score_lib, a library of locked, reusable parts of SCORE worlds (models, surfaces, effects, lights,
sounds, characters, kits, algorithms, layers and places, in OpenUSD). Each file is stored once under its own sha256,
at `blobs/<sha[:2]>/<sha>`; this repository holds {count} of them, {total / 2**30:.2f} GiB.

The records, which say which entry holds which file under which name and where each came from, are in git at
{where} (`entries/<kind>/<id>/<version>/entry.json`).

## Reading it

No key is needed. With the records checked out and score-lib installed:

```sh
SCORE_LIB_HUB_REPO={repo_id} score-lib fetch <kind>/<id>@<version> --into <folder>
```

One file by hand: `https://huggingface.co/datasets/{repo_id}/resolve/main/blobs/<sha[:2]>/<sha>`; its bytes hash
to `<sha>`.

## Licence

Everything here is released under the MIT licence of the library (its `LICENSE`). A source is let in only when its
terms allow that: what it made may go out under MIT, and its own bytes are copied in only where its terms allow
relicensing. Each entry names its sources in `provenance.made_with`.

| source | own bytes copied in | why it may go out under MIT |
|---|---|---|
{rows}
"""


def start(api, repo_id, revision, card_text, public):
    """The repository made when missing (private unless public) and the card and .gitattributes written in one
    commit."""
    from huggingface_hub import CommitOperationAdd
    api.create_repo(repo_id, repo_type=REPO_TYPE, private=not public, exist_ok=True)
    api.create_commit(repo_id, [CommitOperationAdd("README.md", card_text.encode()),
                                CommitOperationAdd(".gitattributes", ATTRIBUTES.encode())],
                      commit_message="dataset card and attributes", repo_type=REPO_TYPE, revision=revision)


def copy(source, api, repo_id, revision, prefix, shas, say=print):
    """Upload shas in commits of BATCH_FILES, WORKERS blobs at a time; the count committed."""
    root_path = f"{prefix}/" if prefix else ""
    done = 0
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for start_at in range(0, len(shas), BATCH_FILES):
            batch = shas[start_at:start_at + BATCH_FILES]
            operations = list(pool.map(lambda sha: staged(source, api, repo_id, revision, root_path + key_of(sha),
                                                          sha), batch))
            api.create_commit(repo_id, operations, commit_message=f"blobs: {len(batch)} files", repo_type=REPO_TYPE,
                              revision=revision)
            done += len(batch)
            say(f"committed {done}/{len(shas)}")
    return done


def check(api, repo_id, revision, prefix, wanted, deep=0):
    """The copy's failures against wanted ({sha256: size}): missing, other size, other recorded sha256, and with
    deep that many random blobs downloaded and hashed."""
    from score_lib.blobs.hub import HubBlobStore
    held = hub_held(api, repo_id, revision, prefix)
    failures = []
    for sha, size in sorted(wanted.items()):
        if sha not in held:
            failures.append(f"missing {sha}")
        elif held[sha][0] != size:
            failures.append(f"size {sha}: {held[sha][0]} in the repository, {size} in the source")
        elif held[sha][1] != sha:
            failures.append(f"hash {sha}: the repository records {held[sha][1]}")
    store = HubBlobStore(api, repo_id, revision, prefix)
    present = sorted(sha for sha in wanted if sha in held)
    for sha in random.sample(present, min(deep, len(present))):
        with tempfile.TemporaryDirectory() as folder:
            try:
                store.get(sha, f"{folder}/{sha}")
            except BadBlob as error:
                failures.append(f"bytes {sha}: {error}")
    return failures


def mirror(root, source, api, repo_id, revision="main", prefix="", public=False, deep=0, records=None,
           check_only=False, say=print):
    """Copy what is missing, then check; the report (counts, bytes, failures)."""
    sizes = source_sizes(source)
    named = named_blobs(root)
    wanted = {sha: sizes[sha] for sha in named if sha in sizes}
    copied = 0
    if not check_only:
        start(api, repo_id, revision, card(root, repo_id, len(wanted), sum(wanted.values()),
                                           records or records_url(root)), public)
        held = hub_held(api, repo_id, revision, prefix)
        copied = copy(source, api, repo_id, revision, prefix, sorted(sha for sha in wanted if sha not in held), say)
    failures = check(api, repo_id, revision, prefix, wanted, deep)
    return {"repo": repo_id, "named": len(named), "wanted": len(wanted), "bytes": sum(wanted.values()),
            "copied": copied, "named_not_in_source": sorted(named - set(sizes)),
            "source_not_named": len(set(sizes) - named), "failures": failures}


def report_line(report):
    """The one summary line of a run."""
    return (f"{report['repo']}: {report['wanted']} blobs, {report['bytes'] / 2**30:.2f} GiB; copied {report['copied']}; "
            f"{len(report['failures'])} failures; {len(report['named_not_in_source'])} named but not in the source; "
            f"{report['source_not_named']} in the source and named by no entry (left behind)")
