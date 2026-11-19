"""The blob store: one file's bytes named by its sha256, behind one small interface, picked from the environment.

    local:          SCORE_LIB_BLOBS_DIR=<folder>
    S3-compatible:  SCORE_LIB_S3_ENDPOINT, SCORE_LIB_S3_BUCKET, SCORE_LIB_S3_PREFIX (optional), SCORE_LIB_S3_REGION
                    (optional), AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, SCORE_LIB_PUBLIC_URL (optional)
    Hugging Face:   SCORE_LIB_HUB_REPO (a dataset repository), SCORE_LIB_HUB_REVISION (optional), SCORE_LIB_HUB_PREFIX
                    (optional); read with no key, written with the standard Hugging Face login's token

With none of them set, a command that only reads uses the library's public copy: the Hugging Face dataset repository
that config/public_store.toml names (`hub_repo`), read with no key. A command that writes still needs one set.
"""
import hashlib
import os
import pathlib
import tomllib
from typing import Protocol

CHUNK = 1 << 20
VARIABLES = ("SCORE_LIB_BLOBS_DIR", "SCORE_LIB_S3_ENDPOINT", "SCORE_LIB_S3_BUCKET", "SCORE_LIB_S3_PREFIX",
             "SCORE_LIB_S3_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SCORE_LIB_PUBLIC_URL",
             "SCORE_LIB_HUB_REPO", "SCORE_LIB_HUB_REVISION", "SCORE_LIB_HUB_PREFIX")
# The public copy read when the environment names no store (the repository is a setting, never in code).
PUBLIC_STORE = pathlib.Path(__file__).resolve().parents[3] / "config/public_store.toml"


class BlobStore(Protocol):
    def has(self, sha256: str) -> bool: ...

    def put(self, sha256: str, source: pathlib.Path) -> None: ...

    def get(self, sha256: str, destination: pathlib.Path) -> None: ...

    def url(self, sha256: str) -> str | None: ...


class NoBlobStore(RuntimeError):
    """No blob store is configured in the environment."""


class MissingBlob(LookupError):
    """The blob store does not hold a blob asked for."""


class BadBlob(RuntimeError):
    """A file's bytes do not hash to the name it is put or got under."""


def file_sha256(path):
    """The sha256 of one file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def key_of(sha256):
    """The store key of a blob: blobs/<sha[:2]>/<sha>."""
    return f"blobs/{sha256[:2]}/{sha256}"


def check_sha(path, sha256):
    """Raise BadBlob when a file's bytes do not hash to sha256."""
    found = file_sha256(path)
    if found != sha256:
        raise BadBlob(f"{path} hashes to {found}, not {sha256}")


def public_repository(path=PUBLIC_STORE):
    """The Hugging Face dataset repository of the library's public copy, or None when no such setting is there."""
    if not pathlib.Path(path).is_file():
        return None
    with open(path, "rb") as source:
        return tomllib.load(source).get("hub_repo") or None


def from_environment(environ=None, public=None):
    """The blob store the environment configures, else the public copy the setting file `public` names (a reader's
    default, PUBLIC_STORE); NoBlobStore naming the variables when there is neither."""
    environ = os.environ if environ is None else environ
    if environ.get("SCORE_LIB_BLOBS_DIR"):
        from score_lib.blobs.local import LocalBlobStore
        return LocalBlobStore(pathlib.Path(environ["SCORE_LIB_BLOBS_DIR"]).expanduser())
    if environ.get("SCORE_LIB_S3_ENDPOINT") and environ.get("SCORE_LIB_S3_BUCKET"):
        from score_lib.blobs.s3 import S3BlobStore
        return S3BlobStore.from_environment(environ)
    if environ.get("SCORE_LIB_HUB_REPO"):
        from score_lib.blobs.hub import HubBlobStore
        return HubBlobStore.from_environment(environ)
    repository = public_repository(public) if public else None
    if repository:
        from score_lib.blobs.hub import HubBlobStore
        return HubBlobStore.from_environment({"SCORE_LIB_HUB_REPO": repository})
    raise NoBlobStore("no blob store configured: set SCORE_LIB_BLOBS_DIR for a local folder, SCORE_LIB_S3_ENDPOINT "
                      "and SCORE_LIB_S3_BUCKET (with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY) for an "
                      "S3-compatible store, or SCORE_LIB_HUB_REPO for a Hugging Face dataset repository; see "
                      "config/blobstore.env.template")
