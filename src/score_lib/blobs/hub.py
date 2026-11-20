"""The Hugging Face Hub blob store: a dataset repository holding blobs/<sha[:2]>/<sha>, configured only by environment
variables (the repository's name is a setting, never in the code).

Anyone reads a public repository with no key; a write uses the token of the standard Hugging Face login (`hf auth
login`, or HF_TOKEN), which huggingface_hub finds on its own. An entry's files go up together (put_many): one commit
of at most COMMIT_FILES files, since the hub allows 128 commits a repository an hour (2026-11-20: a commit a file
spent the hour on one place, and every other take-in failed with 429); a commit refused for that rate is tried again
after the wait the hub names (Retry-After), up to RATE_TRIES times.
"""
import pathlib
import shutil
import sys
import tempfile
import time

from score_lib.blobs import MissingBlob, check_sha, key_of

REPO_TYPE = "dataset"
COMMIT_FILES = 500  # files in one commit
RATE_TRIES = 30  # commits refused for the hub's commit rate tried again, each after the wait it names
RATE_WAIT = 120  # seconds to wait when the hub names none


class HubBlobStore:
    deep_always = False

    def __init__(self, api, repo_id, revision="main", prefix=""):
        self.api = api
        self.repo_id = repo_id
        self.revision = revision or "main"
        self.prefix = prefix.strip("/")

    @classmethod
    def from_environment(cls, environ):
        """A store from SCORE_LIB_HUB_REPO, SCORE_LIB_HUB_REVISION and SCORE_LIB_HUB_PREFIX (both optional)."""
        from huggingface_hub import HfApi
        return cls(HfApi(), environ["SCORE_LIB_HUB_REPO"], environ.get("SCORE_LIB_HUB_REVISION") or "main",
                   environ.get("SCORE_LIB_HUB_PREFIX", ""))

    def key(self, sha256):
        """The path of a blob in the repository, under the optional prefix."""
        return f"{self.prefix}/{key_of(sha256)}" if self.prefix else key_of(sha256)

    def has(self, sha256):
        return self.api.file_exists(self.repo_id, self.key(sha256), repo_type=REPO_TYPE, revision=self.revision)

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
        """Download a blob to a path and check its bytes; the download's own folder is removed after."""
        from huggingface_hub.errors import EntryNotFoundError
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".hub-") as folder:
            try:
                found = self.api.hf_hub_download(self.repo_id, self.key(sha256), repo_type=REPO_TYPE,
                                                 revision=self.revision, local_dir=folder)
            except EntryNotFoundError as error:
                raise MissingBlob(f"the blob store holds no {sha256}") from error
            shutil.move(found, destination)
        check_sha(destination, sha256)

    def url(self, sha256):
        """The public read link of the blob."""
        from huggingface_hub import hf_hub_url
        return hf_hub_url(self.repo_id, self.key(sha256), repo_type=REPO_TYPE, revision=self.revision)
