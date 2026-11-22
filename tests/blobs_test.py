"""Blob stores: the local folder, and the S3-compatible backend against a stub client (no network)."""
import hashlib
from types import SimpleNamespace
import pathlib

import pytest

from score_lib.blobs import BadBlob, NoBlobStore, from_environment, key_of
from score_lib.blobs.hub import HubBlobStore
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


class NoBlobs:
    def get(self, sha256, destination):
        raise AssertionError(f"asked the store for {sha256}")


def test_environment_picks_local_and_names_the_variables(tmp_path):
    """Break: from_environment ignoring `public` (a reader with no store set gets none, not the public copy), or
    fetch.place_file's 'if sha == EMPTY' branch removed (a store not holding the empty blob, as the public copy, fails
    every entry with an empty __init__.py)."""
    from score_lib import fetch
    target = tmp_path / "pkg" / "__init__.py"
    fetch.place_file(NoBlobs(), hashlib.sha256(b"").hexdigest(), target, link=False)
    assert target.read_bytes() == b""
    assert isinstance(from_environment({"SCORE_LIB_BLOBS_DIR": str(tmp_path)}), LocalBlobStore)
    setting = tmp_path / "public_store.toml"
    setting.write_text('hub_repo = "owner/lib"\n')
    public = from_environment({}, public=setting)
    assert isinstance(public, HubBlobStore) and public.repo_id == "owner/lib"
    with pytest.raises(NoBlobStore, match="SCORE_LIB_BLOBS_DIR"):
        from_environment({}, public=tmp_path / "none.toml")


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


class FakeHub:
    """The few huggingface_hub calls the hub store and the mirror make, kept in a dict; blobs/ files are large files,
    so the listing records their sha256 (as the repository's .gitattributes makes them)."""

    def __init__(self, refusals=0):
        self.files, self.pending, self.commits, self.made, self.refusals = {}, {}, [], [], refusals

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        self.made.append((repo_id, repo_type, private))

    def file_exists(self, repo_id, filename, repo_type, revision):
        return filename in self.files

    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, repo_type, revision, commit_message):
        self.files[path_in_repo] = pathlib.Path(path_or_fileobj).read_bytes()
        self.commits.append([path_in_repo])

    def preupload_lfs_files(self, repo_id, additions, repo_type, revision):
        for operation in additions:
            self.pending[operation.path_in_repo] = operation.path_or_fileobj

    def create_commit(self, repo_id, operations, commit_message, repo_type, revision):
        if self.refusals:  # the hub's commit rate, 128 an hour: refused, naming the wait
            from huggingface_hub.errors import HfHubHTTPError
            self.refusals -= 1
            raise HfHubHTTPError("429 Too Many Requests", response=SimpleNamespace(status_code=429, request=None,
                                                                                   headers={"Retry-After": "7"}))
        for operation in operations:
            given = self.pending.pop(operation.path_in_repo, operation.path_or_fileobj)
            self.files[operation.path_in_repo] = pathlib.Path(given).read_bytes() if isinstance(given, str) else given
        self.commits.append([operation.path_in_repo for operation in operations])

    def list_repo_tree(self, repo_id, path_in_repo, recursive, repo_type, revision):
        return [SimpleNamespace(path=path, size=len(data), lfs=SimpleNamespace(sha256=hashlib.sha256(data).hexdigest())
                                if data else None)  # as the hub: an empty file is no large file
                for path, data in self.files.items() if path.startswith(path_in_repo + "/")]


class ListingClient(StubClient):
    """The stub S3 client with a paged listing (two keys a page) and get_object."""

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(key for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix))
        at = int(ContinuationToken or 0)
        page = {"Contents": [{"Key": key, "Size": len(self.objects[(Bucket, key)])} for key in keys[at:at + 2]],
                "IsTruncated": at + 2 < len(keys)}
        return {**page, "NextContinuationToken": str(at + 2)} if page["IsTruncated"] else page

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def read_from(hub, monkeypatch, hub_module, refusals=0):
    """Plain link reads answered from the fake hub's files (a 404 for a missing one, a 429 for the first `refusals`
    reads of a held one); the reads' keys, in order, "refused" for each refusal."""
    import io
    import urllib.error
    reads, left = [], [refusals]
    monkeypatch.setattr(hub_module.time, "sleep", lambda seconds: None)

    def urlopen(request, timeout):
        key = request.full_url.split("/resolve/main/", 1)[1]
        if key not in hub.files:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        if left[0]:
            left[0] -= 1
            reads.append("refused")
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "3"}, None)
        reads.append(key)
        return io.BytesIO(hub.files[key])
    monkeypatch.setattr(hub_module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("huggingface_hub.get_token", lambda: None)
    return reads


def test_hub_store_reads_without_a_key_and_the_mirror_copies_resumes_and_checks(tmp_path, monkeypatch):
    """Red when: the environment does not pick the hub store from SCORE_LIB_HUB_REPO; a hub put, get or has loses or
    changes bytes or a missing blob is not MissingBlob; the url is not the public resolve link; the mirror leaves a
    named blob behind, copies one no entry names, re-uploads what the repository holds, or its check misses a blob
    whose bytes differ, or an empty blob (no sha256 recorded) is told a failure; a read asks the hub for anything but
    the blob's resolve link, or is not tried again after a 429; an entry's files go up one commit a file,
    or a commit refused for the hub's rate is not tried again after its named wait."""
    import json
    from score_lib.blobs import MissingBlob, mirror
    hub = FakeHub()
    store = HubBlobStore(hub, "owner/lib")
    path, sha = a_file(tmp_path)
    from score_lib.blobs import hub as hub_module
    reads = read_from(hub, monkeypatch, hub_module, refusals=1)
    assert not store.has(sha)
    with pytest.raises(MissingBlob):
        store.get(sha, tmp_path / "missing.bin")
    store.put(sha, path)
    store.put(sha, path)  # a second put is a no-op
    assert store.has(sha) and hub.commits == [[key_of(sha)]]
    store.get(sha, tmp_path / "out" / "copy.bin")
    assert (tmp_path / "out" / "copy.bin").read_bytes() == b"bytes"
    assert sorted(item.name for item in (tmp_path / "out").iterdir()) == ["copy.bin"]  # the download folder is gone
    assert reads == ["refused", key_of(sha)]  # a plain read of the resolve link, tried again after a 429
    assert store.url(sha) == f"https://huggingface.co/datasets/owner/lib/resolve/main/{key_of(sha)}"
    # an entry's files go up in one commit, one held already left out, after the waits the hub's rate names
    from score_lib.blobs import put_all
    waits = []
    monkeypatch.setattr(hub_module.time, "sleep", waits.append)
    rated = FakeHub(refusals=2)
    rated.files[key_of(sha)] = b"bytes"
    pairs = [(sha, path)]
    for number in range(3):
        (tmp_path / f"part{number}.bin").write_bytes(b"part %d" % number)
        pairs.append((hashlib.sha256(b"part %d" % number).hexdigest(), tmp_path / f"part{number}.bin"))
    put_all(HubBlobStore(rated, "owner/lib"), pairs)
    assert rated.commits == [sorted(key_of(blob) for blob, _ in pairs[1:])] and waits == [7.0, 7.0], (rated.commits,
                                                                                                    waits)
    picked = from_environment({"SCORE_LIB_HUB_REPO": "owner/lib", "SCORE_LIB_HUB_PREFIX": "lib"})
    assert isinstance(picked, HubBlobStore) and picked.key(sha) == f"lib/{key_of(sha)}"

    root = tmp_path / "library"
    (root / "config").mkdir(parents=True)
    (root / "config/sources.toml").write_text('[[source]]\nid = "score"\nname = "made by score"\ncopy = true\n'
                                              'why = "our own work"\n')
    datas = [b"one", b"", b"three", b"four", b"five"]  # the second empty: the hub records no sha256 for it
    shas = [hashlib.sha256(data).hexdigest() for data in datas]
    files = {f"file{number}": {"sha256": shas[number]} for number in range(4)}  # the fifth no entry names
    entry_path = root / "entries/model/thing/1/entry.json"
    entry_path.parent.mkdir(parents=True)
    entry_path.write_text(json.dumps({"files": files}))
    client = ListingClient()
    for data, blob in zip(datas, shas):
        client.objects[("bucket", f"lib/{key_of(blob)}")] = data
    source = S3BlobStore(client, "bucket", prefix="lib")
    target = FakeHub()
    read_from(target, monkeypatch, hub_module)  # the mirror's check reads the target hub's links
    monkeypatch.setattr(mirror, "BATCH_FILES", 3)
    first = mirror.mirror(root, source, target, "owner/lib", deep=4, records="https://example.org/records",
                          say=lambda line: None)
    assert target.made == [("owner/lib", "dataset", True)]
    assert sorted(target.files) == sorted([".gitattributes", "README.md"] + [key_of(blob) for blob in shas[:4]])
    assert "license: mit" in target.files["README.md"].decode() and "https://example.org/records" in \
        target.files["README.md"].decode()
    assert (first["copied"], first["source_not_named"], first["failures"]) == (4, 1, [])
    assert [len(commit) for commit in target.commits] == [2, 3, 1]  # the card, then the blobs in commits of three
    target.files.pop(key_of(shas[3]))  # a run stopped before its last batch: the next one copies only that blob
    again = mirror.mirror(root, source, target, "owner/lib", records="x", say=lambda line: None)
    assert again["copied"] == 1 and again["failures"] == [] and target.commits[-1] == [key_of(shas[3])]
    target.files[key_of(shas[0])] = b"other"
    checked = mirror.mirror(root, source, target, "owner/lib", check_only=True, say=lambda line: None)
    assert checked["copied"] == 0 and len(checked["failures"]) == 1 and shas[0] in checked["failures"][0]

