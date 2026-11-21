"""Regression tests for source alignment, host-specific routing, and evidence-bound edits."""
import numpy as np
import pytest
from PIL import Image
pytest.importorskip("pathology_tools")
from pathology_tools.evidence import unique_alternatives
from pathology_tools.inspection import steps
from pathology_tools.native_workbench import LiveWorkbench
from pathology_tools.nuclei import LOCAL_TOOL, segment, segmentation_qc
from pathology_tools.packets import dump, prepare, read
from pathology_tools.source import align_rgb
from sixth_magnitude import toolbox
sol = pytest.importorskip("sols_hammer.cli")


def sample():
    rgb = np.full((63, 83, 3), [234, 211, 217], np.uint8)
    for y, x in [(12, 7), (28, 40), (36, 65)]:
        rgb[y:y+9, x:x+10] = [52, 35, 89]
    return rgb


def model_file(path):
    dump(path, {"classes":[1,2,3,4], "mean":[0]*10, "scale":[1]*10,
                "intercept":[0,1,0,0], "coef":[[0]*10 for _ in range(4)]})
    return path


def submit(bench, choices, edits=None):
    rows = [{"field":k, "reason":"Check the boundaries at original scale.", "choice":choices[k],
             "edits":(edits or {}).get(k, [])} for k in bench.ids]
    return bench.handle("workbench_submit", {"ticket":bench.ticket, "decisions":rows}, "submit")


def test_color_reference_and_untyped_contract(tmp_path):
    ref = sample()
    path = tmp_path/"reference.png"
    Image.fromarray(ref).save(path)
    crop = ref[4:40, 3:52]
    for incoming, status in [(crop, "reference_rgb"), (crop[:, :, ::-1], "reference_rb_swapped")]:
        pixels, record = align_rgb(incoming, (3,4), path)
        assert record["status"] == status and np.array_equal(pixels, crop)
    pixels, info = align_rgb(crop[:, :, ::-1], (3,4))
    assert info["status"] == "unverified" and np.array_equal(pixels, crop[:, :, ::-1])
    with pytest.raises(ValueError, match="Reference pixels disagree"):
        prepare(crop, (4,4), "a", tmp_path/"mismatch", reference=path)
    assert not (tmp_path/"mismatch").exists()
    gray = np.full((8,9,3), 180, np.uint8)
    Image.fromarray(gray).save(path)
    assert align_rgb(gray,(0,0),path)[1]["status"] == "reference_indistinguishable"
    weights = model_file(tmp_path/"model.json")
    dump(weights,{**read(weights),"color_basis":"reference_rgb"})
    with pytest.raises(ValueError, match="requires native"):
        prepare(ref,(0,0),"a",tmp_path/"invalid",weights)
    field = prepare(gray,(0,0),"a",tmp_path/"valid",weights,reference=path)
    with np.load(field/"baseline.npz") as packet:
        assert packet["logits"].shape == (1,4) and not packet["labels"].any()


def test_local_candidate_and_qc_do_not_need_labels():
    rgb = sample()
    assert np.array_equal(segment(rgb, LOCAL_TOOL), segment(rgb[:, :, ::-1], LOCAL_TOOL))
    flat = np.full((40,50,3),190,np.uint8)
    assert not segment(flat, LOCAL_TOOL).any()
    assert not segmentation_qc(flat, np.zeros(flat.shape[:2], int))["flags"]
    # Use a larger field so the known small islands occupy under 2% of its area.
    large = np.tile(rgb,(2,2,1))
    qc = segmentation_qc(large, np.zeros(large.shape[:2], int))
    assert "uncovered_dark_structures" in qc["flags"]


def test_sol_batches_only_nominations_and_sesta_checks_every_field(tmp_path):
    flat = np.full((32,40,3),220,np.uint8)
    paths = [prepare(flat, (0,0), k, tmp_path/k) for k in ("A","B","C")]
    for module, expected in [(sol, [("select", ["A","B","C"]), ("review", ["B"])]),
                              (toolbox, [(s,[k]) for k in "ABC" for s in ("select","review")])]:
        bench = LiveWorkbench(paths, tmp_path/module.__name__, module.POLICY, schedule=module.schedule)
        bench.handle("workbench_open", {}, "open")
        visited = []
        while not bench.done:
            visited.append((bench.stage, bench.ids))
            if bench.stage == "select":
                submit(bench,{k:"inspect_baseline" if k=="B" else "baseline" for k in bench.ids})
            else:
                submit(bench,{k:"A" for k in bench.ids})
        assert visited == expected
        if module is sol:
            assert bench.decisions["A"]["status"] == "triaged"


def test_neither_can_inspect_only_distinct_unused_candidates(tmp_path):
    rgb = sample()
    f = prepare(rgb,(11,17),"field",tmp_path/"field")
    bench = LiveWorkbench([f], tmp_path/"result", toolbox.POLICY, schedule=toolbox.schedule)
    bench.handle("workbench_open",{}, "open")
    submit(bench, {"field":LOCAL_TOOL})
    submit(bench, {"field":"neither"})
    candidates = unique_alternatives(f, ["baseline", LOCAL_TOOL])
    if candidates:
        assert bench.stage == "alternatives" and list(bench.facts["field"]) == candidates
        with pytest.raises(ValueError, match="displayed alternative"):
            submit(bench, {"field":"baseline"})
        submit(bench, {"field":candidates[0]})
        assert bench.stage == "rescue"
        submit(bench, {"field":"neither"})
    assert bench.done and bench.decisions["field"]["status"] == "unresolved"
    with np.load(f/"baseline.npz") as base, np.load(bench.output/"field.npz") as final:
        assert np.array_equal(base["labels"], final["labels"])
    labels = np.array([[0,1,1,0,2]], dtype=int)
    for name, a in [("baseline",labels),("one",np.array([[0,2,2,0,1]])),("two",np.array([[0,1,2,0,3]]))]:
        np.savez(tmp_path/(name+".npz"), labels=a)
    dump(tmp_path/"field.json", {"tools":{"baseline":{}, "one":{}, "two":{}}})
    assert unique_alternatives(tmp_path, ["baseline"]) == ["two"]


def test_nomination_confirmation_replay_and_corruption(tmp_path):
    classifier = model_file(tmp_path/"weights.json")
    f = prepare(sample(),(25,31),"F",tmp_path/"F",classifier,mpp=[0.5,0.5])
    out = tmp_path/"run"
    b = LiveWorkbench([f],out,toolbox.POLICY,classifier,schedule=toolbox.schedule)
    b.handle("workbench_open",{},"open")
    submit(b,{"F":"inspect_baseline"})
    edits = [{"id":i,"cell_type":"neutrophil"} for i in (1,2)]
    submit(b,{"F":"A"},{"F":edits})
    assert b.stage == "confirm" and not (out/"F.npz").exists()
    info = read(b.current/"request.json")["evidence"]["F"]
    assert info["1"]["source_xy"][0] >= 25 and info["1"]["area_um2"] == info["1"]["area_px"]*0.25
    assert info["1"]["original_peer_id"] == 3 and info["1"]["peer_id"] is None
    verdicts = [{"id":i,"decision":"apply","boundary_clear":True,"feature":feature,"reason":"Observed in the clean crop."}
                for i,feature in [(1,"surface_appearance"),(2,"internal_structure")]]
    def confirm(rows):
        b.handle("workbench_confirm",{"ticket":b.ticket, "decisions":[{"field":"F","verdicts":rows}]},"confirm")
    with pytest.raises(ValueError,match="exactly the nominated"):
        confirm(verdicts[:1])
    confirm(verdicts)
    d = b.decisions["F"]
    assert d["edits"] == [edits[1]] and d["adjudications"][0]["outcome"] == "held_insufficient_evidence"
    assert steps([f],out,toolbox.POLICY,True,schedule=toolbox.schedule) == b.decisions
    img = out/"step-03-confirm"/"F.png"
    img.write_bytes(b"changed")
    with pytest.raises(ValueError,match="Recorded attachment"):
        steps([f],out,toolbox.POLICY,True,schedule=toolbox.schedule)
    assert img.read_bytes() == b"changed"


def test_unreferenced_competitor_and_unreliable_boundaries_are_held(tmp_path):
    weights = model_file(tmp_path/"model.json")
    field = prepare(sample(),(0,0),"F",tmp_path/"F",weights)
    b = LiveWorkbench([field],tmp_path/"run",sol.POLICY,weights,schedule=sol.schedule)
    b.handle("workbench_open",{},"open")
    submit(b,{"F":"inspect_baseline"})
    submit(b,{"F":"B"},{"F":[{"id":1,"cell_type":"neutrophil"},{"id":2,"cell_type":"macrophage"}]})
    verdicts = [{"id":i,"decision":"apply","feature":feat,"boundary_clear":clear,"reason":"Check the clean profile."}
                for i,feat,clear in [(1,"relative_size",True),(2,"internal_structure",False)]]
    b.handle("workbench_confirm",{"ticket":b.ticket,"decisions":[{"field":"F","verdicts":verdicts}]},"ok")
    assert not b.decisions["F"]["edits"]
    assert [v["outcome"] for v in b.decisions["F"]["adjudications"]] == ["held_no_size_comparator","held_uncertain_boundary"]

def test_one_shot_host_uses_same_stages_and_binds_raw_answers(tmp_path, monkeypatch):
    field = prepare(np.full((24,32,3),230,np.uint8), (0,0), "F", tmp_path/"F")
    calls = []
    def answer(folder, prompt, images, schema, policy):
        stage = read(folder/"request.json")["stage"]
        calls.append(stage)
        assert images[0].is_file() and "decisions" in schema["properties"]
        attempt = folder/"attempt-01"
        attempt.mkdir()
        path = attempt/"answer.json"
        dump(path, {"decisions":[{"field":"F","choice":"baseline" if stage == "select" else "A",
                                  "reason":"Uniform field.","edits":[]}]})
        return attempt, path
    monkeypatch.setattr("pathology_tools.inspection.execute_model", answer)
    out = tmp_path/"output"
    expected = steps([field],out,toolbox.POLICY,schedule=toolbox.schedule)
    assert calls == ["select","review"]
    assert steps([field],out,toolbox.POLICY,True,schedule=toolbox.schedule) == expected
    dump(out/"step-01-select"/"attempt-01"/"answer.json",{"decisions":[]})
    with pytest.raises(ValueError,match="original attempt"):
        steps([field],out,toolbox.POLICY,True,schedule=toolbox.schedule)
    assert calls == ["select","review"]


def test_verified_training_uses_aligned_training_pixels(tmp_path, monkeypatch):
    import importlib
    from io import BytesIO
    from pathlib import Path
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pytest.importorskip("sklearn")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    pilot = importlib.import_module("monusac_pilot")
    def png(arr):
        stream = BytesIO()
        Image.fromarray(arr).save(stream, format="PNG")
        return {"bytes":stream.getvalue()}
    ref = sample()
    path = tmp_path/"original.png"
    Image.fromarray(ref).save(path)
    labels = segment(ref,"baseline").astype(np.uint16)
    assert int(labels.max()) == 3
    data = tmp_path/"train.parquet"
    pq.write_table(pa.Table.from_pylist([{"image":png(ref[:,:,::-1]), "inst_map":png(labels),
                                          "type_map":png(labels.astype(np.uint8)), "patient":"P"}]), data)
    manifest = tmp_path/"references.json"
    dump(manifest,[{"row":0, "patient":"P", "path":path.name}])
    target = tmp_path/"classifier.json"
    pilot.train(data,target,manifest)
    weights = read(target)
    assert weights["color_basis"] == "reference_rgb"
    assert weights["verified_training_fields"][0]["status"] == "reference_rb_swapped"
    dump(manifest,[{"row":0, "patient":"different", "path":path.name}])
    with pytest.raises(ValueError,match="misassigned"):
        pilot.train(data,tmp_path/"invalid.json",manifest)
    assert not (tmp_path/"invalid.json").exists()
