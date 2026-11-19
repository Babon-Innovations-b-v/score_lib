"""score-lib trace and verify's contribution check: every outside entry traces to its contributor, pull request,
signed-off commit, claim and bundle.

Red when: trace misses an entry by its address, a blob's sha256, the handle, a source or the pull request; verify
passes an outside entry whose contribution record is missing or half written, or fails one waiting for its merge.
"""
from bundle_test import COMMIT, contributed

from score_lib import contribution
from score_lib import entry as entries
from score_lib.verify import check_contributed


def test_every_outside_entry_traces_and_an_untraced_one_fails(tmp_path):
    root = tmp_path / "library"
    (root / "entries").mkdir(parents=True)
    addresses = contributed(root, tmp_path / "loose")
    records = [entries.load_address(root, address) for address in addresses]
    assert check_contributed(records, pending=True) == []
    assert {fault for _, fault in check_contributed(records)} == {
        "contribution: no pull request number", "contribution: no signed-off commit", "contribution: no date",
        "contribution: no bundle hash"}

    stamp = {"pr": 7, "signed_off": COMMIT, "date": "2026-11-19", "bundle_sha256": "e" * 64}
    for record in records:
        record["contribution"].update(stamp)
        entries.write(root, record)
    assert check_contributed(records) == []
    unclaimed = {**records[0], "contribution": {**records[0]["contribution"], "claim": " "}}
    assert check_contributed([unclaimed]) == [(unclaimed["address"], "contribution: no claim of rights")]
    assert check_contributed([{**records[0], "contribution": None}]) == [
        (records[0]["address"], "contribution: an outside entry without its contribution record")]

    sha = records[1]["files"]["gltf/box.glb"]["sha256"]
    for query, found in (("model/box@1", ["model/box@1"]), (sha, ["model/box@1"]),
                         ("handle:Ada-Lovelace", sorted(addresses)), ("pr:7", sorted(addresses)),
                         ("pr:8", []), ("handle:nobody", [])):
        assert sorted(row["address"] for row in contribution.traced(root, query)) == found, query
    sources = contribution.traced(root, "model/box@1")[0]["sources"]
    assert contribution.traced(root, f"source:{sources[0]}") and contribution.traced(root, "model/box@1")[0][
        "contribution"]["claim"] == "my own work"
