from copy import deepcopy
import json
from pathlib import Path
import uuid
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest

from sixth_magnitude import schema
from sixth_magnitude.exports import export_run
from sixth_magnitude.imaging import crop, source_key, geometry_mask
from sixth_magnitude.store import Conflict, Store, atomic_json, validate_geometry
from sixth_magnitude.telemetry import bind, report


@pytest.fixture
def setup(tmp_path):
    source={"source":"example.isyntax","source_asset_id":"asset-a","slide_token":"S1","width":4000,"height":3000,
            "mpp_x":.25,"mpp_y":.5,"size_bytes":100,"mtime_ns":2,"header_64k_sha256":"abc","downsamples":[1,2,4]}
    source["source_key"]=source_key(source)
    atomic_json(tmp_path/"run_manifest.json",{"run_id":"test","purpose":"annotation","task_mode":"fixed_roi",
        "model_requested":"gpt-6-astra","effort_requested":"low","cases":[{"case_id":"C1","source":source,"planned_rois":[]}]})
    store=Store(tmp_path)
    geom={"type":"rectangle","role":"sampling_window","bbox_level0":[100,200,612,456],"context_side_um":512,"points":None}
    observation=schema.defaults();observation["label"]="工程樣本"
    observation["focus"].update(tumour_evidence="indeterminate",note="Test only")
    actor={"role":"ai","id":"gpt-6-astra-low"}
    def request(action="save",**extra):
        return {"action":action,"operation_id":uuid.uuid4().hex,"base_revision":store.state("C1")["revision"],
            "source_key":source["source_key"],"annotation_id":"a1","geometry":deepcopy(geom),"observation":deepcopy(observation),**extra}
    def evidence(g=geom):
        e={"source_key":source["source_key"],"geometry":deepcopy(g),"evidence_id":"e1"}
        atomic_json(tmp_path/"evidence/e1/metadata.json",e)
        return e
    return store,source,geom,observation,actor,request,evidence


def test_schema_distinguishes_unassessed_and_absent(setup):
    *_,obs,actor,request,evidence=setup
    schema.validate(obs)
    obs["focus"]["patterns_present"]=["acinar"]
    with pytest.raises(ValueError,match="評估狀態"):
        schema.validate(obs)
    obs["focus"]["pattern_assessment_state"]="indeterminate"
    schema.validate(obs)
    obs["invented_diagnosis"]="positive"
    with pytest.raises(ValueError,match="Additional properties"):
        schema.validate(obs)


def test_idempotent_retry_survives_restart_and_conflicts(setup):
    store,source,g,obs,actor,request,evidence=setup
    req=request()
    first=store.save("C1",req,actor,evidence())
    reopened=Store(store.root)
    retried=reopened.save("C1",req,actor,evidence())
    assert first["revision"]==retried["revision"]==1 and retried["replayed"]
    changed=deepcopy(req);changed["observation"]["label"]="changed"
    with pytest.raises(Conflict,match="Operation ID"):
        store.save("C1",changed,actor,evidence())
    stale=request();stale["base_revision"]=0
    with pytest.raises(Conflict,match="Revision conflict"):
        store.save("C1",stale,actor,evidence())
    assert len(store.revisions("C1"))==1


def test_source_mismatch_never_commits(setup):
    store,source,g,obs,actor,request,evidence=setup
    req=request();req["source_key"]="wrong-case"
    with pytest.raises(Conflict,match="Source"):
        store.save("C1",req,actor,evidence())
    assert store.state("C1")["revision"]==0


def approve(store,request,actor):
    a=store.state("C1")["annotations"]["a1"]
    store.action("C1",request("visual",content_hash=a["content_hash"],evidence_id=a["evidence_id"],state="matched"),actor)
    store.action("C1",request("review",content_hash=a["content_hash"],decision="accept"),{"role":"physician","id":"TEST-PHYSICIAN"})


@pytest.mark.parametrize("field",["geometry","observation"])
def test_edit_invalidates_only_changed_annotation_approval(setup,field):
    store,source,g,obs,actor,request,evidence=setup
    store.save("C1",request(),actor,evidence())
    approve(store,request,actor)
    old=store.state("C1")["annotations"]["a1"]
    req=request()
    if field=="geometry":req["geometry"]["bbox_level0"][2]+=100
    else:req["observation"]["focus"]["note"]="corrected"
    store.save("C1",req,actor,evidence(req["geometry"]))
    new=store.state("C1")["annotations"]["a1"]
    assert new["visual"]["state"]=="pending" and new["review"]["state"]=="not_reviewed"
    assert new["version"]==old["version"]+1
    assert new["geometry_revision"]==old["geometry_revision"]+int(field=="geometry")
    assert new["observation_revision"]==old["observation_revision"]+int(field=="observation")
    assert store.revisions("C1")[2].exists()  # Prior accepted version remains present.


def test_ai_cannot_mark_physician_review_and_stale_visual_rejected(setup):
    store,source,g,obs,actor,request,evidence=setup
    store.save("C1",request(),actor,evidence())
    a=store.state("C1")["annotations"]["a1"]
    with pytest.raises(ValueError,match="Only the physician"):
        store.action("C1",request("review",content_hash=a["content_hash"],decision="accept"),actor)
    with pytest.raises(Conflict,match="Annotation changed"):
        store.action("C1",request("visual",content_hash="stale",evidence_id="e1",state="matched"),actor)


def test_export_has_no_automatic_visual_or_research_labels(setup):
    store,source,g,obs,actor,request,evidence=setup
    store.save("C1",request(),actor,evidence())
    out=export_run(store)
    assert out["regions"]==1 and out["observations"]==2 and out["research_labels"]==0
    rows=pq.read_table(Path(out["directory"])/"observations.parquet").to_pylist()
    assert {r["scope"] for r in rows}=={"focus","context"}
    assert all(r["visual_state"]=="pending" for r in rows)
    assert rows[1]["support"]=="context_window_minus_focus_geometry"
    approve(store,request,actor)
    assert export_run(store)["research_labels"]==2
    store.manifest["purpose"]="engineering"
    assert export_run(store)["research_labels"]==0


def test_no_five_roi_cap_and_tombstone_retains_history(setup):
    store,source,g,obs,actor,request,evidence=setup
    for i in range(6):store.save("C1",request(annotation_id=f"a{i}"),actor,evidence())
    assert store.queue()[0]["count"]==6
    a=store.state("C1")["annotations"]["a1"]
    store.action("C1",request("delete",content_hash=a["content_hash"]),actor)
    assert store.queue()[0]["count"]==5
    assert store.state("C1")["annotations"]["a1"]["deleted"]


def test_geometry_checks_units_and_bounds(setup):
    _,source,g,*_=setup
    valid=validate_geometry(g,source)
    assert (valid["bbox_level0"][2]-valid["bbox_level0"][0])*source["mpp_x"]==128
    assert (valid["bbox_level0"][3]-valid["bbox_level0"][1])*source["mpp_y"]==128
    for bbox in ([0,0,5000,5],[0,0,float("nan"),5],[0,0,0,5]):
        bad=deepcopy(g);bad["bbox_level0"]=bbox
        with pytest.raises(ValueError):validate_geometry(bad,source)
    bad=deepcopy(g);bad["context_side_um"]=128
    with pytest.raises(ValueError,match="extend beyond"):validate_geometry(bad,source)


class GradientReader:
    dimensions=(100,100);level_dimensions=[(100,100)];level_downsamples=[1];mpp_x=.25;mpp_y=.5
    def read_rgba(self,level,x,y,w,h):
        data=np.full((h,w,4),255,dtype=np.uint8)
        for iy in range(h):
            for ix in range(w):
                if 0<=x+ix<100 and 0<=y+iy<100:data[iy,ix,:3]=[x+ix,y+iy,10]
        return data


def test_anisotropic_crop_affine_and_padding_mask():
    image,mask,meta=crop(GradientReader(),[-10,0,70,40],100)
    assert image.size==(100,100)  # 20 x 20 physical microns.
    assert meta["output_mpp_x"]==meta["output_mpp_y"]==.2
    assert meta["image_to_level0_affine"]==[.8,0,-10,0,.4,0]
    valid=np.array(mask)
    assert not valid[:,:12].any() and valid[:,13:].all()
    assert meta["boundary_padded"]


def test_telemetry_counter_boundaries_and_closed_segment(setup,tmp_path):
    store,*_=setup
    log=tmp_path/"session.jsonl"
    def event(input,cache,out):return {"timestamp":"2026-09-11T01:00:00+00:00","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":input,"cached_input_tokens":cache,"output_tokens":out,"reasoning_output_tokens":2,"total_tokens":input+out}}}}
    log.write_text(json.dumps({"type":"turn_context","payload":{"model":"gpt-6-astra","effort":"low"}})+"\n"+json.dumps(event(100,50,10))+"\n")
    bind(store,log)
    with log.open("a") as f:f.write(json.dumps(event(150,90,20))+"\n")
    result=report(store,close=True)["segments"][0]
    assert result["tokens"]["input_tokens"]==50 and result["tokens"]["uncached_input_tokens"]==10
    assert result["configuration_latest"]=={"model":"gpt-6-astra","effort":"low"}
    assert report(store)["segments"][0]==result


def test_fixed_plan_completion_requires_each_position(setup):
    store,source,g,obs,actor,request,evidence=setup
    store.cases["C1"]["planned_rois"]=[{"id":"p1"},{"id":"p2"}]
    store.save("C1",request(planned_roi_id="p1"),actor,evidence())
    approve(store,request,actor)
    with pytest.raises(ValueError,match="預定位置"):
        store.action("C1",request("case_status",status="completed_planned_sampling"),actor)
    store.action("C1",request("case_status",status="deferred"),actor)
    assert store.state("C1")["status"]=="deferred"


def test_mask_is_half_open_at_pixel_centers():
    geom={"type":"rectangle","bbox_level0":[1,1,3,3]}
    mask=geometry_mask(geom,[0,0,4,4],(4,4))
    assert mask.sum()==4 and mask[1:3,1:3].all()
    assert not mask[3,:].any() and not mask[:,3].any()


def test_self_crossing_polygon_rejected(setup):
    _,source,g,*_=setup
    bad={**g,"type":"polygon","role":"tissue_region","bbox_level0":[100,100,300,300],
         "points":[[100,100],[300,300],[100,300],[250,150]]}
    with pytest.raises(ValueError,match="Self-intersecting"):
        validate_geometry(bad,source)


def test_schema_drift_requires_migration(setup):
    store,*_=setup
    atomic_json(store.root/"observation.schema.json",{"type":"string"})
    with pytest.raises(ValueError,match="schema differs"):
        Store(store.root)


def test_saved_content_corruption_detected_on_reopen(setup):
    store,source,g,obs,actor,request,evidence=setup
    store.save("C1",request(),actor,evidence())
    file=store.revisions("C1")[-1]
    data=json.loads(file.read_text(encoding="utf-8"));data["annotations"]["a1"]["observation"]["label"]="altered outside tool"
    atomic_json(file,data)
    with pytest.raises(ValueError,match="checksum"):
        Store(store.root).state("C1")


def test_reported_visual_mismatch_removes_prior_research_acceptance(setup):
    store,source,g,obs,actor,request,evidence=setup
    store.save("C1",request(),actor,evidence())
    approve(store,request,actor)
    a=store.state("C1")["annotations"]["a1"]
    store.action("C1",request("visual",content_hash=a["content_hash"],evidence_id="e1",state="mismatch"),actor)
    assert store.state("C1")["annotations"]["a1"]["review"]["state"]=="not_reviewed"
    assert export_run(store)["research_labels"]==0


def test_missing_usage_counters_are_not_assumed_zero(setup,tmp_path):
    store,*_=setup
    log=tmp_path/"missing-counter.jsonl"
    def event(counters):return {"timestamp":"2026-09-11T01:00:00+00:00","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":counters}}}
    log.write_text(json.dumps(event({"input_tokens":100}))+"\n")
    bind(store,log)
    with log.open("a") as f:f.write(json.dumps(event({"input_tokens":150,"cached_input_tokens":90}))+"\n")
    result=report(store)["segments"][0]["tokens"]
    assert result["input_tokens"]==50 and result["cached_input_tokens"] is None
    assert "uncached_input_tokens" not in result
