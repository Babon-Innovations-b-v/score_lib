"""The Hugging Face Hub blob store: a dataset repository holding blobs/<sha[:2]>/<sha>, configured only by environment
variables (the repository's name is a setting, never in the code).

Anyone reads a public repository with no key; a write uses the token of the standard Hugging Face login (`hf auth
login`, or HF_TOKEN), which huggingface_hub finds on its own. Each put is one commit of one file.
"""
import pathlib
import shutil
import tempfile

from score_lib.blobs import MissingBlob, check_sha, key_of

REPO_TYPE = "dataset"


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
        check_sha(source, sha256)
        if self.has(sha256):
            return
        self.api.upload_file(path_or_fileobj=str(source), path_in_repo=self.key(sha256), repo_id=self.repo_id,
                             repo_type=REPO_TYPE, revision=self.revision, commit_message=f"blob {sha256}")

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
