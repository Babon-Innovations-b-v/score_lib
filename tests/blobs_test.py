"""Blob stores: the local folder, and the S3-compatible backend against a stub client (no network)."""
import hashlib
import pathlib

import pytest

from score_lib.blobs import BadBlob, NoBlobStore, from_environment, key_of
from score_lib.blobs.local import LocalBlobStore
from score_lib.blobs.s3 import S3BlobStore


def a_file(folder, data=b"bytes"):
    path = pathlib.Path(folder) / "file.bin"
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def test_local_put_get_has(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs")
    path, sha = a_file(tmp_path)
    assert not store.has(sha)
    store.put(sha, path)
    assert store.has(sha)
    assert (tmp_path / "blobs" / key_of(sha)).is_file()
    store.get(sha, tmp_path / "out" / "copy.bin")
    assert (tmp_path / "out" / "copy.bin").read_bytes() == b"bytes"
    store.put(sha, path)  # a second put is a no-op


def test_local_put_refuses_wrong_sha(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs")
    path, _ = a_file(tmp_path)
    with pytest.raises(BadBlob):
        store.put("0" * 64, path)


def test_environment_picks_local_and_names_the_variables(tmp_path):
    assert isinstance(from_environment({"SCORE_LIB_BLOBS_DIR": str(tmp_path)}), LocalBlobStore)
    with pytest.raises(NoBlobStore, match="SCORE_LIB_BLOBS_DIR"):
        from_environment({})


class Missing(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"}}


class StubClient:
    """The few S3 calls the backend makes, kept in a dict."""

    def __init__(self):
        self.objects = {}
        self.metadata = {}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise Missing()
        return {}

    def upload_file(self, source, bucket, key, ExtraArgs=None):
        self.objects[(bucket, key)] = pathlib.Path(source).read_bytes()
        self.metadata[(bucket, key)] = (ExtraArgs or {}).get("Metadata", {})

    def download_file(self, bucket, key, destination):
        pathlib.Path(destination).write_bytes(self.objects[(bucket, key)])


def test_s3_backend_with_a_stub_client(tmp_path):
    client = StubClient()
    store = S3BlobStore(client, "bucket", prefix="lib", public_url="https://example.org/files/")
    path, sha = a_file(tmp_path)
    assert not store.has(sha)
    store.put(sha, path)
    assert store.has(sha)
    assert client.metadata[("bucket", f"lib/{key_of(sha)}")] == {"sha256": sha}
    store.get(sha, tmp_path / "copy.bin")
    assert (tmp_path / "copy.bin").read_bytes() == b"bytes"
    assert store.url(sha) == f"https://example.org/files/lib/{key_of(sha)}"


def test_s3_get_checks_the_bytes(tmp_path):
    client = StubClient()
    store = S3BlobStore(client, "bucket")
    path, sha = a_file(tmp_path)
    store.put(sha, path)
    client.objects[("bucket", key_of(sha))] = b"changed"
    with pytest.raises(BadBlob):
        store.get(sha, tmp_path / "copy.bin")
