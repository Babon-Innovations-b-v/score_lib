"""Opt-in, read-only: a dry run against a real store. Runs only when SCORE_LIB_REAL_STORE names a store's objects
folder and SCORE_LIB_REAL_REPO the score repository; writes nothing (dry run, scratch in a temp folder)."""
import os
import pathlib
import shutil

import pytest

LIBRARY = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not (os.environ.get("SCORE_LIB_REAL_STORE") and os.environ.get("SCORE_LIB_REAL_REPO")),
                    reason="SCORE_LIB_REAL_STORE and SCORE_LIB_REAL_REPO not set")
def test_dry_run_against_the_real_store(monkeypatch, tmp_path):
    from score_lib.export import run
    monkeypatch.setenv("SCORE_STORE", os.environ["SCORE_LIB_REAL_STORE"])
    monkeypatch.setenv("SCORE_REPO", os.environ["SCORE_LIB_REAL_REPO"])
    context = run.context_from_environment(LIBRARY, "world1", places=["hub"])
    context.scratch = tmp_path / "scratch"
    try:
        report = run.export(context, ["algorithm", "light", "kit", "place"], dry_run=True)
    finally:
        shutil.rmtree(context.scratch, ignore_errors=True)
    assert report["dry_run"] and report["kinds"]["kit"]["counts"]["refused"] == 0
