"""The catalogue site builds, links every entry picture, and stays outside the repository."""
import os
import re

import pytest

from score_lib import entry as entries
from score_lib.blobs.local import LocalBlobStore
from score_lib.page.build import OutsideRepo, build


def test_page_links_every_picture(world, tmp_path):
    world.export()
    out = tmp_path / "site"
    build(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), out, drafts=True)
    front = (out / "index.html").read_text()
    for record in entries.all_entries(world.root):
        page = out / record["kind"] / record["id"] / f"{record['version']}.html"
        assert page.is_file()
        assert f'href="{record["kind"]}/{record["id"]}/{record["version"]}.html"' in front
        if record["picture"]:
            sha = record["files"][record["picture"]["file"]]["sha256"]
            assert sha in front
    for source in re.findall(r'src="([^"]+)"', front):
        assert (out / source).is_file()
    for page in out.glob("*/*/*.html"):
        for link in re.findall(r'(?:src|href)="([^"#]+)"', page.read_text()):
            if not link.startswith("http"):
                assert (page.parent / link).resolve().is_file(), f"{page.relative_to(out)}: {link}"
    assert "<audio" in (out / "sound/hum/1.html").read_text()


def test_drafts_hidden_without_the_flag(world, tmp_path):
    world.export()
    pages = build(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), tmp_path / "site")
    assert pages == 1


def test_site_refuses_the_repository(world):
    world.export()
    with pytest.raises(OutsideRepo):
        build(world.root, LocalBlobStore(os.environ["SCORE_LIB_BLOBS_DIR"]), world.root / "site", drafts=True)
