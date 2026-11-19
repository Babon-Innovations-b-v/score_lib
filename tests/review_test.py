"""score-lib review: an outside pull request may only add entries, algorithm definitions, source rows and the index
lines built for them, each commit signed off, and its bundle drawn on a contact sheet.

Red when: a pull request that only adds a contributor's entries is faulted or gets no contact sheet; a pull request
that changes our code, an existing source row, an existing entry, the locks, or holds an unsigned commit, an entry
not taken in as its author's or an index the entries do not build passes; or review runs outside the review
environment.
"""
import subprocess

import pytest
from bundle_test import contributed

from score_lib import bundle, index, review
from score_lib.blobs.local import LocalBlobStore

SIGNED = "\n\nSigned-off-by: Ada Lovelace <ada@example.org>"


def git(root, *arguments):
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def commit(root, message, signed=True):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message + (SIGNED if signed else ""))


def base_library(root):
    """A library repository at its base: our code, one source row, one entry of ours and the index."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "code.py").write_text("x = 1\n")
    (root / "config").mkdir()
    (root / "config" / "sources.toml").write_text('[cc0]\nid = "cc0"\noutput = "mit"\n')
    (root / "entries").mkdir()
    index.rebuild(root)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.org")
    git(root, "config", "user.name", "test")
    commit(root, "base")
    git(root, "branch", "base")


def test_only_new_entries_pass_and_every_change_to_ours_fails(tmp_path, monkeypatch):
    root = tmp_path / "library"
    base_library(root)
    addresses = contributed(root, tmp_path / "loose")
    with open(root / "config" / "sources.toml", "a") as sources:
        sources.write('\n[my_source]\nid = "my_source"\noutput = "mit"\n')
    commit(root, "entries: two of mine")
    packed = bundle.pack(root, LocalBlobStore(tmp_path / "contributor_blobs"), addresses, tmp_path / "mine.tgz")

    monkeypatch.delenv(bundle.REVIEW_SETTING, raising=False)
    with pytest.raises(bundle.Refused, match="review environment"):
        review.review(root, "base", "ada-lovelace")
    monkeypatch.setenv(bundle.REVIEW_SETTING, "a test")
    faults, tiles = review.review(root, "base", "ada-lovelace", [packed["path"]], tmp_path / "sheet.png")
    assert faults == [] and tiles == 2 and (tmp_path / "sheet.png").is_file()
    faults, _ = review.review(root, "base", "someone-else")
    assert any("not taken in as someone-else's" in fault for fault in faults), faults

    git(root, "tag", "good")
    breaks = {
        "src/code.py": lambda: (root / "src" / "code.py").write_text("x = 2\n"),
        "config/sources.toml": lambda: (root / "config" / "sources.toml").write_text(
            (root / "config" / "sources.toml").read_text().replace('[cc0]\nid = "cc0"\noutput = "mit"',
                                                                    '[cc0]\nid = "cc0"\noutput = "any"')),
        "index/locks.json": lambda: (root / "index" / "locks.json").write_text("[{}]\n"),
        "index/sound.json": lambda: (root / "index" / "sound.json").write_text("{}\n"),
        "README.md": lambda: (root / "README.md").write_text("mine\n"),
        "no Signed-off-by": lambda: (root / "definitions" / "algorithms").mkdir(parents=True),
    }
    for expected, broken in breaks.items():
        git(root, "reset", "-q", "--hard", "good")
        broken()
        if expected == "no Signed-off-by":
            (root / "definitions" / "algorithms" / "mine.toml").write_text('name = "mine"\n')
        commit(root, "a change", signed=expected != "no Signed-off-by")
        faults, _ = review.review(root, "base", "ada-lovelace")
        assert any(expected in fault for fault in faults), (expected, faults)

    git(root, "reset", "-q", "--hard", "good")
    git(root, "tag", "after")
    (root / "entries" / "sound" / "hum" / "1" / "extra.json").write_text("{}")
    commit(root, "adds to an entry already merged")
    faults, _ = review.review(root, "after", "ada-lovelace")
    assert any("not a new entry" in fault for fault in faults), faults
