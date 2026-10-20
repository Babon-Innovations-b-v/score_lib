"""The handover's copy hook (handover.COPY_HOOK): one entry's web 3D copy, made when it has none or it went stale."""
import os
import pathlib

from score_lib.web3d import run as web3d

LIBRARIES = {}  # library root -> its surface library, read once a process


def cache_folder():
    return pathlib.Path(os.environ.get("SCORE_LIB_CACHE") or pathlib.Path.home() / ".cache/score-lib") / "web3d"


def web_copy(root, blobs, record):
    """{sha256, light_sha256, role} of an entry's web copy (made now when it has none or it went stale), or None for
    a kind the copies do not cover or a locked entry without one."""
    if record["kind"] not in web3d.KINDS:
        return None
    if web3d.stale_reason(root, blobs, record) is not None:
        if record["state"] != "draft":
            return None
        if str(root) not in LIBRARIES:
            LIBRARIES[str(root)] = web3d.Library(root, blobs)
        record = web3d.make_one(root, blobs, LIBRARIES[str(root)], record, cache_folder())
    made = record["web3d"]
    return {"sha256": made["full_sha256"], "light_sha256": made["light_sha256"], "role": "web3d"}
