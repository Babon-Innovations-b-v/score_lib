"""Quality scores: the ramp and the score's mean, the parts made from gates and held entries, the quality fields
left out of an entry's digest and the export's comparison, and the free mesh measures on a closed and an open box."""
import numpy as np
from pxr import Usd, UsdGeom

from score_lib import entry as entries
from score_lib.export import run as export_run
from score_lib.quality import mesh, parts


def test_ramp_marks_100_50_0_both_ways():
    assert parts.ramp(0.0, 0.0, 0.001, 0.01) == 100
    assert parts.ramp(0.001, 0.0, 0.001, 0.01) == 50
    assert parts.ramp(0.02, 0.0, 0.001, 0.01) == 0
    assert parts.ramp(0.8, 0.8, 0.5, 0.3) == 100
    assert parts.ramp(0.5, 0.8, 0.5, 0.3) == 50
    assert parts.ramp(0.4, 0.8, 0.5, 0.3) == 25


def test_total_weights_measured_parts_and_leaves_out_the_rest():
    found = [parts.scored("a", 100, 1, "x", weight=3.0), parts.scored("b", 0, 1, "x"),
             parts.unmeasured("c", "needs a judge", parts.NEEDS_PAID_JUDGE)]
    assert parts.total(found) == 75
    assert parts.total([parts.unmeasured("c", "none")]) is None


def test_gates_count_unknown_as_not_passing():
    verdicts = {"one": {"answer": "pass", "reason": ""}, "two": {"answer": "unknown", "reason": ""}}
    assert parts.gates("gates", verdicts, "x@y")["value"] == 50
    assert parts.gates("gates", {}, "x@y")["value"] is None


def test_held_is_the_mean_of_scored_entries():
    found = parts.held("pieces", {"model/a@1": 80, "model/b@1": 40, "model/c@1": None})
    assert found["value"] == 60 and "1 under 50" in found["how"]


def test_quality_fields_leave_digest_and_comparison_alone():
    record = {"schema": entries.SCHEMA, "address": "model/a@1", "kind": "model", "id": "a", "version": 1,
              "state": "draft", "files": {}}
    scored = dict(record, score=40, score_parts=[], refine_options=[], score_made={"at": "2026-10-18", "by": "t"})
    assert entries.digest(record) == entries.digest(scored)
    assert export_run.comparable(record) == export_run.comparable(scored)


def write_box(path, open_top):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    box = UsdGeom.Mesh.Define(stage, "/box")
    corners = [(x_side, y_side, z_side) for x_side in (0, 1) for y_side in (0, 1) for z_side in (0, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    if open_top:
        faces.remove((2, 3, 7, 6))
    box.CreatePointsAttr(corners)
    box.CreateFaceVertexCountsAttr([4] * len(faces))
    box.CreateFaceVertexIndicesAttr([corner for face in faces for corner in face])
    stage.GetRootLayer().Save()
    return path


def test_closed_box_has_no_open_or_crowded_edges(tmp_path):
    model = mesh.read(write_box(tmp_path / "box.usda", open_top=False))
    assert len(model.triangles) == 12
    assert mesh.open_edges(model)[0] == 0.0
    assert mesh.crowded_edges(model)[0] == 0.0
    assert np.allclose(mesh.size_of(model), [1, 1, 1])


def test_open_box_shows_its_open_edges(tmp_path):
    model = mesh.read(write_box(tmp_path / "box.usda", open_top=True))
    share, flagged = mesh.open_edges(model)
    assert share > 0.2 and flagged.sum() == 4  # one triangle of each open quad side
