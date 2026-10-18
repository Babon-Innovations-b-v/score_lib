"""The read-only store reader: the accept recipe (newest accept no later reject undid), listings and links."""
import os

from conftest import FixtureStore

from score_lib.source.store import Store, retired


def test_newest_accept_stands():
    fixture = FixtureStore(os.environ["SCORE_STORE"])
    first = fixture.put_output({"a.json": b"1"})
    second = fixture.put_output({"a.json": b"2"})
    fixture.accept("node@room", first, "2026-01-01T00:00:00+00:00")
    fixture.accept("node@room", second, "2026-01-02T00:00:00+00:00")
    assert Store(os.environ["SCORE_STORE"]).accepted("node@room") == second


def test_a_later_reject_undoes_the_accept():
    fixture = FixtureStore(os.environ["SCORE_STORE"])
    first = fixture.put_output({"a.json": b"1"})
    second = fixture.put_output({"a.json": b"2"})
    fixture.accept("node@room", first, "2026-01-01T00:00:00+00:00")
    fixture.accept("node@room", second, "2026-01-02T00:00:00+00:00")
    fixture.reject("node@room", second, "2026-01-03T00:00:00+00:00")
    assert Store(os.environ["SCORE_STORE"]).accepted("node@room") == first


def test_an_accept_after_the_reject_stands_again():
    fixture = FixtureStore(os.environ["SCORE_STORE"])
    output = fixture.put_output({"a.json": b"1"})
    fixture.accept("node@room", output, "2026-01-01T00:00:00+00:00")
    fixture.reject("node@room", output, "2026-01-02T00:00:00+00:00")
    assert Store(os.environ["SCORE_STORE"]).accepted("node@room") is None
    fixture.accept("node@room", output, "2026-01-03T00:00:00+00:00")
    assert Store(os.environ["SCORE_STORE"]).accepted("node@room") == output


def test_listing_json_and_links(tmp_path):
    fixture = FixtureStore(os.environ["SCORE_STORE"])
    output = fixture.put_output({"folder/a.json": b'{"x": 1}'})
    store = Store(os.environ["SCORE_STORE"])
    assert store.read_json(output, "folder/a.json") == {"x": 1}
    linked = store.link_tree(output, tmp_path / "tree")
    assert (linked / "folder/a.json").is_symlink()
    assert (linked / "folder/a.json").read_bytes() == b'{"x": 1}'


def test_retired_rows_are_skipped():
    assert retired("effects@camp") and retired("split-lab-inputs") and retired("person-clips")
    assert not retired("effects-layer@camp") and not retired("ship_kit@cockpit")
