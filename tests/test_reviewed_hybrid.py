import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from pathology_tools.free_edit import Review, DEFAULT_CLASSES
from pathology_tools.packets import prepare
from pathology_tools.reviewed_hybrid import plan_fields, operator_prompt, validate_routes
from pathology_tools.source import pixel_hash


def test_three_review_routes_drive_real_masks(tmp_path):
    work = tmp_path / "operator"
    out = tmp_path / "result"
    work.mkdir()
    out.mkdir()
    paths = [tmp_path / name for name in ("F1", "F2", "F3")]
    rgb = np.full((48, 48, 3), 235, np.uint8)
    rgb[9:25, 8:26] = [70, 25, 90]
    for path in paths:
        prepare(rgb, [0, 0], path.name, path, native=True)
    bench = Review(paths, "hybrid", None, work, out, DEFAULT_CLASSES, False)
    routes = ["tool-guided", "model-guided", "combined"]
    rows = [dict(field=p.name, route=r, candidate="local_gradient", roi=[4, 4, 25, 25],
                 reason="Compare the dark central object with the proposed boundary.") for p, r in zip(paths, routes)]

    def turn(parts):
        assert sum(p["type"] == "localImage" for p in parts) == 3
        assert sum(p["type"] == "image" for p in parts) == 3
        return json.dumps({"decisions": rows})

    selected = plan_fields(SimpleNamespace(turn=turn, id="reviewer"), bench, work, out)
    for key, row in selected.items():
        field = bench.fields[key]
        expected = np.zeros_like(field.current) if row["route"] == "model-guided" else field.bank[row["candidate"]]
        if row["route"] != "model-guided":
            assert expected.max() > 0
        assert np.array_equal(field.current, expected)
        assert np.array_equal(np.load(work / (key + "-current.npy")), expected)
        assert row["initial_mask_sha256"] == pixel_hash(field.current)
    handoff = operator_prompt(True, DEFAULT_CLASSES, "python3", selected)
    assert "Baseline is initially current" not in handoff
    assert [json.loads(handoff.splitlines()[-1])[p.name]["route"] for p in paths] == routes
    assert json.loads((out / "route-review.json").read_text())["reviewer_thread_id"] == "reviewer"


@pytest.mark.parametrize("change", [
    {"field": "unknown"}, {"route": "raw?"}, {"candidate": "missing"},
    {"roi": [20, 20, 40, 40]}, {"reason": "  "}, {"roi": "all"}
])
def test_reviewer_must_ground_advice_in_the_field(change):
    field = SimpleNamespace(bank={"baseline": None}, rgb=np.zeros((32, 32, 3)))
    row = dict(field="F", route="tool-guided", candidate="baseline", roi=[], reason="Visible contour.")
    with pytest.raises(ValueError):
        validate_routes([{**row, **change}], {"F": field})
    with pytest.raises(ValueError):
        validate_routes([], {"F": field})
    with pytest.raises(ValueError):
        validate_routes([row, row], {"F": field})


def test_partial_usage_keeps_consumed_tokens():
    from pathology_tools.reviewed_hybrid import usage_summary
    reviewer = {"usage": {"total": {"totalTokens": 25, "inputTokens": 18}}}
    assert usage_summary({"reviewer": reviewer, "operator": {}}) == {
        "total": {"totalTokens": 25, "inputTokens": 18}, "missing_roles": ["operator"]}
    assert usage_summary({"reviewer": {}, "operator": {}})["total"] is None
