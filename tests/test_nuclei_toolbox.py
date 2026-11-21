"""Extra toolbox checks; base-package tests remain independent of optional packages."""
import argparse
from pathlib import Path
import importlib.util
import numpy as np
import pytest
from PIL import Image
pytest.importorskip("pathology_tools")
from pathology_tools.nuclei import segment, TOOLS
from pathology_tools.packets import prepare, read, sheet
from pathology_tools.rounds import validate, invoke
from sixth_magnitude.cli import initialize
from sixth_magnitude.toolbox import command

def test_source_space_and_standalone_parity(tmp_path):
    rgb = np.full((62,86,3),225,np.uint8)
    rgb[12:30,14:35] = (90,63,120)
    image = tmp_path/"sample.png"; Image.fromarray(rgb).save(image)
    run = tmp_path/"run"; initialize(run,[{"asset_id":"case","path":image}])
    field=tmp_path/"field"
    args=argparse.Namespace(operation="prepare",run=run,asset_id="case",roi=[7,5,57,41],field=field,classifier=None)
    command(args)
    other=prepare(rgb[5:46,7:64],(0,0),"tile",tmp_path/"other")
    assert read(field/"field.json")["origin_xy"]==[7,5]
    assert read(field/"field.json")["files"]==read(other/"field.json")["files"]
    assert sheet(field,"case","split_touching").size==(57*2*3,41*2+26)
    with pytest.raises(ValueError,match="unused"): command(args)
    args.roi=[80,5,57,41]
    with pytest.raises(ValueError,match="inside"): command(args)

def test_uniform_and_small_nucleus():
    im = np.full((40,50,3),255,np.uint8)
    assert all(not segment(im,t).any() for t in TOOLS)
    im[13:24,17:30] = [94,60,116]
    assert segment(im,"baseline").max()==1

def test_decision_ids_are_panel_bound(tmp_path):
    np.savez(tmp_path/"baseline.npz",labels=np.array([[0,1,1]]))
    row={"field":"F","choice":"A","reason":"edge","edits":[{"id":2,"cell_type":"lymphocyte"}]}
    with pytest.raises(ValueError,match="missing ID"):
        validate([row],["F"],"review",{"F":tmp_path},{"F":"split_touching"})
    row["edits"][0]["id"]=1
    validate([row],["F"],"review",{"F":tmp_path},{"F":"split_touching"})

def test_score_instance_threshold_and_ambiguous():
    pytest.importorskip("sklearn")
    path=Path(__file__).resolve().parents[1]/"scripts/monusac_pilot.py"
    spec=importlib.util.spec_from_file_location("pilot",path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    gold=np.array([[1,1,2,2,3,3]],dtype=int)
    gtclass=np.array([[1,1,2,2,5,5]],dtype=int)
    pred=np.array([[1,1,2,0,3,3]],dtype=int)
    score=mod.score_masks(gold,gtclass,pred,np.array([0,1,2,1]))
    assert (score["tp"],score["fn"],score["fp"],score["correct_type"])==(1,1,1,1)
    assert score["matched_mean_iou"]==1


def test_offline_replay_checks_original_attachment_and_answer(tmp_path, monkeypatch):
    from pathology_tools.packets import dump, sha
    from sixth_magnitude.toolbox import POLICY
    f=prepare(np.full((24,29,3),240,np.uint8),(0,0),"F",tmp_path/"F")
    root=tmp_path/"round"
    def forbidden(*a, **k):
        raise AssertionError("Offline replay launched a process")
    monkeypatch.setattr("pathology_tools.rounds.subprocess.run",forbidden)
    args=(root,["F"],{"F":f},"select",{},POLICY)
    with pytest.raises(ValueError,match="No sealed decision"):
        invoke(*args,replay_only=True)
    raw=root/"attempt-01"/"answer.json";raw.parent.mkdir()
    rows=[{"field":"F","choice":"baseline","reason":"Uniform field"}]
    dump(raw,{"decisions":rows})
    dump(root/"sealed.json",{"request_sha256":sha(root/"request.json"),
         "answer_sha256":sha(raw),"rows":rows,"attempt":"attempt-01"})
    assert invoke(*args,replay_only=True)==rows
    dump(raw,{"decisions":[]})
    with pytest.raises(ValueError,match="original model answer"):
        invoke(*args,replay_only=True)
    attachment=root/"F.png";attachment.write_bytes(b"changed")
    with pytest.raises(ValueError,match="Recorded attachment"):
        invoke(*args,replay_only=True)
    assert attachment.read_bytes()==b"changed"
