"""The blob store: one file's bytes named by its sha256, behind one small interface, picked from the environment.

    local:          SCORE_LIB_BLOBS_DIR=<folder>
    S3-compatible:  SCORE_LIB_S3_ENDPOINT, SCORE_LIB_S3_BUCKET, SCORE_LIB_S3_PREFIX (optional), SCORE_LIB_S3_REGION
                    (optional), AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, SCORE_LIB_PUBLIC_URL (optional)
    Hugging Face:   SCORE_LIB_HUB_REPO (a dataset repository), SCORE_LIB_HUB_REVISION (optional), SCORE_LIB_HUB_PREFIX
                    (optional); read with no key, written with the standard Hugging Face login's token
"""
import hashlib
import os
import pathlib
from typing import Protocol

CHUNK = 1 << 20
VARIABLES = ("SCORE_LIB_BLOBS_DIR", "SCORE_LIB_S3_ENDPOINT", "SCORE_LIB_S3_BUCKET", "SCORE_LIB_S3_PREFIX",
             "SCORE_LIB_S3_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SCORE_LIB_PUBLIC_URL",
             "SCORE_LIB_HUB_REPO", "SCORE_LIB_HUB_REVISION", "SCORE_LIB_HUB_PREFIX")


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


def from_environment(environ=None):
    """The blob store the environment configures; NoBlobStore naming the variables when none is."""
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
    raise NoBlobStore("no blob store configured: set SCORE_LIB_BLOBS_DIR for a local folder, SCORE_LIB_S3_ENDPOINT "
                      "and SCORE_LIB_S3_BUCKET (with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY) for an "
                      "S3-compatible store, or SCORE_LIB_HUB_REPO for a Hugging Face dataset repository; see "
                      "config/blobstore.env.template")
