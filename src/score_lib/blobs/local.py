"""The local blob store: a folder holding blobs/<sha[:2]>/<sha>, each written once and read-only."""
import os
import pathlib
import shutil

from score_lib.blobs import check_sha, key_of


class LocalBlobStore:
    deep_always = True  # verify always hashes a local blob's bytes

    def __init__(self, folder):
        self.folder = pathlib.Path(folder)

    def path(self, sha256):
        """Where a blob lives on disk."""
        return self.folder / key_of(sha256)

    def has(self, sha256):
        return self.path(sha256).is_file()

    def put(self, sha256, source):
        """Store a file under its sha256 after checking it; nothing when the blob is already there."""
        check_sha(source, sha256)
        if self.has(sha256):
            return
        target = self.path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.partial-{os.getpid()}")
        shutil.copyfile(source, partial)
        partial.chmod(0o444)
        partial.replace(target)

    def get(self, sha256, destination):
        """Copy a blob to a path and check its bytes."""
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path(sha256), destination)
        check_sha(destination, sha256)

    def url(self, sha256):
        """A local store has no public read link."""
        return None
