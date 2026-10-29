"""Geometry, atomic batches and scientific denominator tests; labels are synthetic."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import numpy as np
from PIL import Image
import pyarrow.parquet as pq
import pytest

from sixth_magnitude import schema
from sixth_magnitude.exports import export_run
from sixth_magnitude.morphometry import measure, validate_measurement, CODES
from sixth_magnitude.store import Store, Conflict, atomic_json, digest
from sixth_magnitude.workbench import Workbench


def territory(code, bbox, uid=None, complete=True, approved=True, points=None):
    m=dict(version='1.0.0',code=code,section_complete=complete if code=='T' else False,
           boundary_assessable=code=='T',applicability='nonmucinous_luad' if code=='T' else 'not_assessed')
    return dict(id=uid or uuid.uuid4().hex,deleted=False,content_hash=uuid.uuid4().hex,measurement=m,
                geometry=dict(type='polygon' if points else 'rectangle',role='tissue_region',bbox_level0=bbox,points=points,context_side_um=100),
                origin='ai_candidate',visual={'state':'pending'},review={'state':'not_reviewed'})


def state(*annotations):
    for a in annotations:
        a['visual']={'state':'matched','content_hash':a['content_hash']}
        a['review']={'state':'physician_reviewed','content_hash':a['content_hash']}
    return dict(case_id='C1',revision=1,annotations={a['id']:a for a in annotations})


SOURCE=dict(source_key='source1',mpp_x=1,mpp_y=1,width=16,height=16)


def calculate(s, **kw):
    return measure(s, SOURCE, grid_um=1, edge_um=1, **kw)


def test_known_four_measurements_and_composition_expectation():
    s=state(territory('T',[0,0,4,4]),territory('A',[0,0,4,4]),territory('S',[0,0,2,4]))
    m=calculate(s)
    assert m['status']=='ok' and m['research_eligible']
    assert m['coverage_pct']==100 and m['high_grade_area_pct']==50
    assert m['largest_high_grade_component_pct']==100
    assert m['edge_enrichment_pp']==0
    assert m['pattern_mixing_pct']==pytest.approx(100*4/24)
    assert m['mixing_random_composition_pct']==pytest.approx(100*8/15)
    assert m['assessable_area_mm2']==16/1e6


def test_four_neighbor_connectivity_does_not_join_diagonals():
    s=state(territory('T',[0,0,2,2]),territory('A',[0,0,2,2]),
            territory('S',[0,0,1,1]),territory('M',[1,1,2,2]))
    m=calculate(s)
    assert m['largest_high_grade_component_pct']==50
    assert m['pattern_mixing_pct']==100


def test_exclusion_holes_do_not_become_tumour_edges():
    s=state(territory('T',[0,0,7,7]),territory('S',[0,0,7,7]),territory('X',[3,3,4,4]))
    m=calculate(s)
    assert m['assessable_area_mm2']==48/1e6
    assert m['edge_enrichment_pp']==0 and m['centre_high_grade_pct']==100


def test_boundary_unclear_and_no_high_grade_are_na_not_zero():
    s=state(territory('T',[0,0,4,4]),territory('A',[0,0,4,4]),territory('B',[0,0,1,1]))
    m=calculate(s)
    assert m['high_grade_area_pct']==0
    assert m['largest_high_grade_component_pct'] is None
    assert m['edge_enrichment_pp'] is None
    assert m['metric_reasons']['edge_enrichment_pp']=='boundary_unassessable'


@pytest.mark.parametrize('kind',['no_envelope','not_applicable','partial','incomplete'])
def test_incomplete_or_inapplicable_sections_do_not_yield_whole_section_metrics(kind):
    t=territory('T',[0,0,4,4]);a=territory('S',[0,0,4,4])
    if kind=='not_applicable':t['measurement']['applicability']='not_applicable'
    if kind=='partial':t['measurement']['section_complete']=False
    if kind=='incomplete':a['geometry']['bbox_level0']=[0,0,2,4]
    m=calculate(state(a) if kind=='no_envelope' else state(t,a))
    assert m['high_grade_area_pct'] is None and not m['research_eligible']
    if kind=='incomplete':assert m['coverage_pct']==50 and m['next_unresolved_bbox_level0'] is not None


def test_equal_size_conflicts_are_order_independent_and_fine_unknown_blocks_coarse():
    t=territory('T',[0,0,4,4]);a=territory('A',[0,0,4,4]);b=territory('S',[0,0,4,4])
    for regions in [(t,a,b),(t,b,a)]:
        m=calculate(state(*regions));assert m['coverage_pct']==0 and m['conflict_area_mm2']==16/1e6
    m=calculate(state(t,a,territory('?',[0,0,2,2])))
    assert m['coverage_pct']==75 and m['high_grade_area_pct'] is None


def test_pending_refinement_blocks_research_export_instead_of_using_approved_coarse_map():
    s=state(territory('T',[0,0,4,4]),territory('A',[0,0,4,4]),territory('S',[0,0,2,2]))
    list(s['annotations'].values())[-1]['review']={'state':'not_reviewed'}
    m=calculate(s)
    assert m['high_grade_area_pct']==25 and not m['research_eligible']


def test_anisotropic_mpp_and_polygon_area():
    points=[[0,0],[8,0],[0,4]]
    s=state(territory('T',[0,0,8,4],points=points),territory('S',[0,0,8,4]))
    source={**SOURCE,'mpp_x':.5,'mpp_y':1}
    m=measure(s,source,grid_um=1,edge_um=1)
    assert m['envelope_area_mm2']==6/1e6  # strict pixel-centre triangle membership
    assert m['high_grade_area_pct']==100


def test_sampling_window_cannot_be_used_as_measurement_territory():
    a=territory('A',[0,0,4,4]);a['geometry']['role']='sampling_window'
    with pytest.raises(ValueError,match='sampling window'):validate_measurement(a['measurement'],a['geometry'])


@pytest.fixture
def workbench(tmp_path,monkeypatch):
    source={**SOURCE,'width':4096,'height':4096,'slide_token':'synthetic','source_asset_id':'synthetic','downsamples':[1,2,4]}
    atomic_json(tmp_path/'run_manifest.json',dict(run_id='test',purpose='engineering',task_mode='slide_discovery',model_requested='gpt-6-astra',effort_requested='low',cases=[dict(case_id='C1',source=source)]))
    store=Store(tmp_path)
    def crop(reader,bbox,long_edge=1024):
        x,y,u,v=bbox;w=h=64
        return Image.new('RGB',(w,h),'white'),Image.new('L',(w,h),255),dict(output_dimensions=[w,h],image_to_level0_affine=[(u-x)/w,0,x,0,(v-y)/h,y],output_mpp_x=(u-x)/w,output_mpp_y=(v-y)/h)
    monkeypatch.setattr('sixth_magnitude.workbench.crop',crop)
    wb=Workbench(store,SimpleNamespace(reader=lambda case:None))
    packet=wb.packet('C1')
    def request(**kw):
        return dict(action='batch_save',operation_id=uuid.uuid4().hex,source_key=source['source_key'],base_revision=store.state('C1')['revision'],packet_id=packet['packet_id'],**kw)
    return wb,packet,request,dict(role='engineering',id='synthetic-test')


def test_compact_batch_is_atomic_idempotent_and_source_bound(workbench):
    wb,p,req,actor=workbench
    r=req(labels='A:A1,A2; S:B1; ?:C3')
    first=wb.submit('C1',r,actor)
    again=wb.submit('C1',r,actor)
    assert first['revision']==again['revision']==1 and again['replayed']
    assert len(wb.store.state('C1')['annotations'])==4
    assert all(a['visual']['state']=='pending' for a in wb.store.state('C1')['annotations'].values())
    with pytest.raises(ValueError,match='Duplicate'):wb.submit('C1',req(labels='A:A1; S:A1'),actor)
    assert wb.store.state('C1')['revision']==1
    bad=req(labels='A:A3');bad['source_key']='wrong'
    with pytest.raises(Conflict,match='Source'):wb.submit('C1',bad,actor)


def test_packet_image_corruption_is_detected(workbench):
    wb,p,req,actor=workbench
    (wb.store.root/'work_packets'/p['packet_id']/'image.png').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):wb.submit('C1',req(labels='A:A1'),actor)


def test_packet_coordinate_corruption_is_detected(workbench):
    wb,p,req,actor=workbench
    path=wb.store.root/'work_packets'/p['packet_id']/'metadata.json'
    p['cells']['A1'][0]=10;atomic_json(path,p)
    with pytest.raises(ValueError,match='geometry checksum'):wb.submit('C1',req(labels='A:A1'),actor)


def test_batch_visual_is_atomic_and_ai_cannot_physician_accept(workbench):
    wb,p,req,actor=workbench;wb.submit('C1',req(labels='A:A1,A2'),actor)
    aa=list(wb.store.state('C1')['annotations'].values())
    items=[dict(annotation_id=a['id'],content_hash=a['content_hash'],evidence_id=a['evidence_id'],state='matched') for a in aa]
    bad=deepcopy(items);bad[-1]['content_hash']='stale'
    with pytest.raises(Conflict):wb.store.action('C1',req(action_unused=True)|{'action':'batch_visual','items':bad},actor)
    assert all(a['visual']['state']=='pending' for a in wb.store.state('C1')['annotations'].values())
    wb.store.action('C1',req()|{'action':'batch_visual','items':items},actor)
    with pytest.raises(ValueError,match='physician'):wb.store.action('C1',req()|{'action':'batch_review','items':[dict(i,decision='accept') for i in items]},dict(role='ai',id='test-ai'))


def test_measurement_semantics_edit_revokes_approval_and_checksum(workbench):
    wb,p,req,actor=workbench
    wb.submit('C1',req(labels='T:A1',section_complete=False,applicability='nonmucinous_luad'),actor)
    a=next(iter(wb.store.state('C1')['annotations'].values()));oldhash=a['content_hash']
    wb.submit('C1',req(labels='T:A1',section_complete=True,applicability='nonmucinous_luad'),actor)
    a=next(iter(wb.store.state('C1')['annotations'].values()))
    assert a['measurement_revision']==2 and a['content_hash']!=oldhash and a['visual']['state']=='pending'
    path=wb.store.revisions('C1')[-1];saved=json.loads(path.read_text('utf8'));saved['annotations'][a['id']]['measurement']['section_complete']=False;atomic_json(path,saved)
    with pytest.raises(ValueError,match='checksum'):wb.store.state('C1')


def test_polygon_correction_updates_original_and_does_not_leave_ghost_region(workbench):
    wb,p,req,actor=workbench
    wb.submit('C1',req(labels='A:A1'),actor)
    a=next(iter(wb.store.state('C1')['annotations'].values()))
    wb.submit('C1',req(polygon=[[0,0],[16,0],[16,16],[0,16]],code='S',replace_annotation_id=a['id'],expected_content_hash=a['content_hash']),actor)
    saved=wb.store.state('C1')['annotations'];assert len(saved)==1 and saved[a['id']]['measurement']['code']=='S' and saved[a['id']]['version']==2


def test_exports_include_quantitative_candidates_but_not_engineering_research(workbench):
    wb,p,req,actor=workbench
    wb.submit('C1',req(labels='T:A1',section_complete=True,boundary_assessable=True,applicability='nonmucinous_luad'),actor)
    wb.submit('C1',req(labels='S:A1'),actor)
    result=export_run(wb.store)
    d=Path(result['directory'])
    values=pq.read_table(d/'morphometry_candidates.parquet').to_pylist()
    assert values[0]['high_grade_area_pct']==100
    assert result['research_morphometry_cases']==0
    assert pq.read_table(d/'morphometry_research.parquet').num_rows==0
    assert len(json.loads((d/'annotations.geojson').read_text('utf8'))['features'])==2


def test_sixteen_cell_payload_avoids_repeating_full_observation(workbench):
    wb,p,req,actor=workbench
    compact=req(labels='A:'+','.join(p['cells']))
    wb.submit('C1',compact,actor)
    full=[dict(geometry=a['geometry'],observation=a['observation'],measurement=a['measurement']) for a in wb.store.state('C1')['annotations'].values()]
    assert len(json.dumps(compact).encode()) < len(json.dumps(full).encode())/10


def test_http_workbench_and_lazy_native_evidence_keep_saved_binding(workbench,monkeypatch):
    import http.client
    from threading import Thread
    from sixth_magnitude.server import Server
    wb,p,req,actor=workbench
    wb.submit('C1',req(labels='A:A1'),actor)
    a=next(iter(wb.store.state('C1')['annotations'].values()))
    metadata=json.loads((wb.store.root/'evidence'/a['evidence_id']/'metadata.json').read_text('utf8'))
    native_id=metadata['native_detail_evidence_id']
    images=SimpleNamespace(reader=lambda case:None,close=lambda:None,prepare=lambda case,g:{'evidence_id':native_id,'geometry':g,'files':{},'views':{}})
    monkeypatch.setattr('sixth_magnitude.server.Images',lambda s:images)
    with Server(wb.store.root,0) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('GET','/');r=conn.getresponse();page=r.read()
            assert r.status==200 and b'Sesta Workbench' in page and b'id="save-cells"' in page and b'id="visual"' in page
            conn.request('GET','/api/evidence/'+a['evidence_id']);r=conn.getresponse();body=json.loads(r.read())
            assert r.status==200 and body['evidence_id']==a['evidence_id'] and body['render_evidence_id']==native_id
            payload=dict(req(labels='S:A2'),case_id='C1',actor=actor)
            conn.request('POST','/api/territories',json.dumps(payload),{'Content-Type':'application/json'})
            r=conn.getresponse();assert r.status==200;assert json.loads(r.read())['revision']==2
            payload['source_key']='wrong';payload['operation_id']=uuid.uuid4().hex
            conn.request('POST','/api/territories',json.dumps(payload),{'Content-Type':'application/json'})
            r=conn.getresponse();assert r.status==409;r.read()
            conn.request('GET','/api/health',headers={'Host':'outside.example'})
            r=conn.getresponse();assert r.status==403;r.read();conn.close()
        finally:
            server.shutdown();thread.join(2)
