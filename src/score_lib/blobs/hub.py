"""The Hugging Face Hub blob store: a dataset repository holding blobs/<sha[:2]>/<sha>, configured only by environment
variables (the repository's name is a setting, never in the code).

Anyone reads a public repository with no key; a write uses the token of the standard Hugging Face login (`hf auth
login`, or HF_TOKEN), which huggingface_hub finds on its own. An entry's files go up together (put_many): one commit
of at most COMMIT_FILES files, since the hub allows 128 commits a repository an hour (2026-11-20: a commit a file
spent the hour on one place, and every other take-in failed with 429); a commit refused for that rate is tried again
after the wait the hub names (Retry-After), up to RATE_TRIES times.

A read is one plain GET of the blob's resolve link (`url`), with the login's token only when there is one: the hub's
own download asks for a read token before each file, and with no login it refuses after a few (2026-11-22: HTTP 429
with 215 s waits, so the README's `score-lib fetch` failed for every visitor), while the resolve links fetched 386 files
in 77 s. A read refused for rate or a passing server fault is tried again after the wait it names, up to RATE_TRIES.

Which blobs the repository holds comes from one listing of its tree (`held`), made once for the store's life and kept
up to date with what the store commits: a request a blob (`file_exists`) was refused for rate after a few minutes of
`score-lib verify` (2026-11-22, HTTP 429), and the listing also carries each large file's size and recorded sha256. A
blob another writer commits after the listing reads as missing; a put of it commits nothing new.
"""
import hashlib
import pathlib
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

from score_lib.blobs import MissingBlob, check_sha, key_of

REPO_TYPE = "dataset"
COMMIT_FILES = 500  # files in one commit
RATE_TRIES = 30  # commits refused for the hub's commit rate tried again, each after the wait it names
RATE_WAIT = 120  # seconds to wait when the hub names none
READ_RETRY = (429, 500, 502, 503, 504)  # a read refused for these is tried again
READ_TIMEOUT = 600  # seconds one read may take (a large model over a slow link)
EMPTY = hashlib.sha256(b"").hexdigest()  # the sha256 of no bytes


class HubBlobStore:
    deep_always = False

    def __init__(self, api, repo_id, revision="main", prefix=""):
        self.api = api
        self.repo_id = repo_id
        self.revision = revision or "main"
        self.prefix = prefix.strip("/")
        self.listing = None

    @classmethod
    def from_environment(cls, environ):
        """A store from SCORE_LIB_HUB_REPO, SCORE_LIB_HUB_REVISION and SCORE_LIB_HUB_PREFIX (both optional)."""
        from huggingface_hub import HfApi
        return cls(HfApi(), environ["SCORE_LIB_HUB_REPO"], environ.get("SCORE_LIB_HUB_REVISION") or "main",
                   environ.get("SCORE_LIB_HUB_PREFIX", ""))

    def key(self, sha256):
        """The path of a blob in the repository, under the optional prefix."""
        return f"{self.prefix}/{key_of(sha256)}" if self.prefix else key_of(sha256)

    def held(self):
        """{sha256: (size, recorded sha256 or None)} of every blob the repository holds, listed on first use."""
        if self.listing is None:
            self.listing = hub_held(self.api, self.repo_id, self.revision, self.prefix)
        return self.listing

    def has(self, sha256):
        return sha256 in self.held()

    def put(self, sha256, source):
        """Commit a file under its sha256 after checking it; nothing when already there."""
        self.put_many([(sha256, source)])

    def put_many(self, pairs):
        """Commit files [(sha256, path)] after checking each, those already there left out, in commits of at most
        COMMIT_FILES; the count committed."""
        from huggingface_hub import CommitOperationAdd
        wanted = {}
        for sha256, source in pairs:
            check_sha(source, sha256)
            if sha256 not in wanted and not self.has(sha256):
                wanted[sha256] = source
        shas = sorted(wanted)
        for start in range(0, len(shas), COMMIT_FILES):
            batch = shas[start:start + COMMIT_FILES]
            operations = [CommitOperationAdd(path_in_repo=self.key(sha), path_or_fileobj=str(wanted[sha]))
                          for sha in batch]
            self.committed(operations, f"blobs: {len(batch)} files")
            self.held().update({sha: (pathlib.Path(wanted[sha]).stat().st_size, sha) for sha in batch})
        return len(shas)

    def committed(self, operations, message):
        """One commit, tried again after the hub's named wait while it refuses for its commit rate (HTTP 429)."""
        from huggingface_hub.errors import HfHubHTTPError
        for attempt in range(RATE_TRIES):
            try:
                return self.api.create_commit(self.repo_id, operations, commit_message=message, repo_type=REPO_TYPE,
                                              revision=self.revision)
            except HfHubHTTPError as error:
                response = getattr(error, "response", None)
                if response is None or response.status_code != 429 or attempt == RATE_TRIES - 1:
                    raise
                wait = float(response.headers.get("Retry-After") or RATE_WAIT)
                print(f"hub: commit rate reached, trying again in {wait:.0f} s ({attempt + 1}/{RATE_TRIES})",
                      file=sys.stderr, flush=True)
                time.sleep(wait)

    def get(self, sha256, destination):
        """Download a blob to a path by its resolve link and check its bytes; a partial download never stays."""
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".hub-") as folder:
            part = pathlib.Path(folder) / "blob"
            read_link(self.url(sha256), part, sha256)
            shutil.move(part, destination)
        check_sha(destination, sha256)

    def url(self, sha256):
        """The public read link of the blob."""
        from huggingface_hub import hf_hub_url
        return hf_hub_url(self.repo_id, self.key(sha256), repo_type=REPO_TYPE, revision=self.revision)


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


def read_link(url, target, sha256):
    """One public link's bytes into `target`, with the login's token when there is one; MissingBlob for a 404, tried
    again after the named wait for a refusal in READ_RETRY, up to RATE_TRIES times."""
    from huggingface_hub import get_token
    token = get_token()
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    for attempt in range(RATE_TRIES):
        try:
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as answer, open(target, "wb") as out:
                shutil.copyfileobj(answer, out)
            return
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise MissingBlob(f"the blob store holds no {sha256}") from error
            if error.code not in READ_RETRY or attempt == RATE_TRIES - 1:
                raise
            wait = float(error.headers.get("Retry-After") or RATE_WAIT) if error.headers else RATE_WAIT
            print(f"hub: {url} refused ({error.code}); trying again in {wait:.0f} s", file=sys.stderr, flush=True)
            time.sleep(wait)
