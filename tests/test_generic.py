from copy import deepcopy
from pathlib import Path
import json
import uuid
import pytest
from PIL import Image
import numpy as np

from sixth_magnitude.cli import initialize
from sixth_magnitude.store import Store, Conflict, atomic_json, read
from sixth_magnitude.imaging import Images
from sixth_magnitude.profiles import generic
from sixth_magnitude.exports import export_run
from sixth_magnitude.readers import calibration

@pytest.fixture
def generic_run(tmp_path):
    image=tmp_path/'input.png';Image.new('RGB',(600,400),'#e0d0d0').save(image)
    root=tmp_path/'run';initialize(root,[{'asset_id':'surface','path':str(image)}])
    store=Store(root);images=Images(store)
    geom={'type':'rectangle','role':'sampling_window','bbox_level0':[10,15,110,85],
          'context_side_px':250,'points':None}
    obs=generic.defaults();obs['label']='Observed mark'
    obs['focus'].update(assessment_state='assessed',findings=['line'])
    actor={'role':'ai','id':'test-ai'}
    def request(action='save',**kw):
        return {'action':action,'operation_id':uuid.uuid4().hex,'base_revision':store.state('surface')['revision'],
            'source_key':store.source('surface')['source_key'],'annotation_id':'a','geometry':deepcopy(geom),
            'observation':deepcopy(obs),**kw}
    yield store,images,geom,obs,actor,request
    images.close()

@pytest.mark.parametrize('effort',['low','max','custom'])
def test_requested_configuration_is_not_observed(tmp_path,effort):
    image=tmp_path/'image.png';Image.new('RGB',(40,30)).save(image)
    initialize(tmp_path/'run',[{'asset_id':'example','path':str(image)}],effort=effort)
    s=Store(tmp_path/'run')
    assert s.manifest['effort_requested']==effort
    assert s.manifest['model_observed'] is None and s.manifest['effort_observed'] is None
    assert s.source('example')['mpp_x'] is None and s.source('example')['coordinate_unit']=='pixel'

def test_focus_context_pixel_support_and_padding(generic_run):
    s,images,g,obs,actor,req=generic_run;e=images.prepare('surface',g)
    assert e['views']['focus']['output_mpp_x'] is None
    assert e['views']['context']['boundary_padded']
    assert e['views']['focus']['image_to_level0_affine'][2]==10
    p=s.root/'evidence'/e['evidence_id']
    assert np.asarray(Image.open(p/'focus_support_mask.png')).max()==255
    context=np.asarray(Image.open(p/'context_support_mask.png'))
    assert context[context.shape[0]//2,context.shape[1]//2]==0
    assert images.prepare('surface',g)['evidence_id']==e['evidence_id']

def test_generic_save_visual_review_correction_and_export(generic_run):
    s,images,g,obs,actor,req=generic_run
    s.save('surface',req(),actor,images.prepare('surface',g))
    a=s.state('surface')['annotations']['a']
    assert a['visual']['state']=='pending'
    assert export_run(s)['accepted_observations']==0
    with pytest.raises(ValueError,match='reviewer'):
        s.action('surface',req('review',content_hash=a['content_hash'],decision='accept'),actor)
    s.action('surface',req('visual',content_hash=a['content_hash'],evidence_id=a['evidence_id'],state='matched'),actor)
    s.action('surface',req('review',content_hash=a['content_hash'],decision='accept'),{'role':'reviewer','id':'human-test'})
    out=export_run(s);assert out['accepted_observations']==2
    snapshot=read(Path(out['directory'])/'canonical_snapshot.json')
    assert snapshot['cases']['surface']['annotations']['a']['review']['state']=='reviewer_accepted'
    change=req();change['observation']['label']='Revised mark'
    s.save('surface',change,actor,images.prepare('surface',g))
    assert s.state('surface')['annotations']['a']['review']['state']=='not_reviewed'
    assert export_run(s)['accepted_observations']==0
    with pytest.raises(Conflict):
        s.action('surface',req('visual',content_hash=a['content_hash'],evidence_id=a['evidence_id'],state='matched'),actor)

def test_generic_engineering_excluded_and_retry_idempotent(generic_run):
    s,images,g,obs,actor,req=generic_run;request=req()
    s.save('surface',request,{'role':'engineering','id':'test'},images.prepare('surface',g))
    result=Store(s.root).save('surface',request,{'role':'engineering','id':'test'},images.prepare('surface',g))
    assert result['replayed'] and result['revision']==1
    a=s.state('surface')['annotations']['a']
    s.action('surface',req('visual',content_hash=a['content_hash'],evidence_id=a['evidence_id'],state='matched'),actor)
    s.action('surface',req('review',content_hash=a['content_hash'],decision='accept'),{'role':'reviewer','id':'human-test'})
    assert export_run(s)['accepted_observations']==0

def test_atomic_batch_does_not_commit_partial_findings(generic_run):
    s,images,g,obs,actor,req=generic_run
    good=req();bad=req(annotation_id='b');bad['observation']['focus']['assessment_state']='not_assessed'
    evidence=images.prepare('surface',g)
    with pytest.raises(ValueError,match='findings'):
        s.batch_save('surface',req('batch_save'),actor,[(good,evidence),(bad,evidence)])
    assert s.state('surface')['revision']==0

def test_generic_rejects_medical_measurements(generic_run):
    s,images,g,obs,actor,req=generic_run
    with pytest.raises(ValueError,match='Morphometry'):
        s.save('surface',req(measurement={'code':'S'}),actor,images.prepare('surface',g))
    assert s.state('surface')['revision']==0

def test_schema_drift_source_drift_and_no_reset(generic_run):
    s,images,g,obs,actor,req=generic_run
    with pytest.raises(ValueError,match='empty'):
        initialize(s.root,[{'asset_id':'new','path':s.source('surface')['source']}])
    contract=read(s.root/'observation.schema.json');contract['title']='Changed'
    atomic_json(s.root/'observation.schema.json',contract)
    with pytest.raises(ValueError,match='schema'):Store(s.root)
    Image.new('RGB',(610,410),'black').save(s.source('surface')['source'])
    with pytest.raises(ValueError,match='changed'):images.reader('surface')

@pytest.mark.parametrize('values',[(0,1),(1,None),(True,1),(float('nan'),1),(-1,1)])
def test_invalid_calibration(values):
    with pytest.raises(ValueError):calibration(*values)

def test_pathology_requires_explicit_calibration(tmp_path):
    image=tmp_path/'image.png';Image.new('RGB',(80,60)).save(image)
    with pytest.raises(ValueError,match='calibration'):
        initialize(tmp_path/'run',[{'asset_id':'example','path':str(image)}],profile='lung-pathology')
    initialize(tmp_path/'run',[{'asset_id':'example','path':str(image),'mpp_x':.25,'mpp_y':.5}],profile='lung-pathology')
    source=Store(tmp_path/'run').source('example')
    assert source['mpp_y']==.5 and source['calibration_origin']=='user-provided'

@pytest.mark.parametrize('bad_id',['../outside','a/b','.', 'space id'])
def test_source_ids_cannot_escape_run(tmp_path,bad_id):
    with pytest.raises(ValueError,match='identifier'):
        initialize(tmp_path/'run',[{'asset_id':bad_id,'path':'unused.png'}])

def test_duplicate_ids_rejected_before_manifest(tmp_path):
    image=tmp_path/'image.png';Image.new('RGB',(40,30)).save(image)
    row={'asset_id':'same','path':str(image)}
    with pytest.raises(ValueError,match='Duplicate'):
        initialize(tmp_path/'run',[row,row])
    assert not (tmp_path/'run/run_manifest.json').exists()

def test_contract_unknown_is_not_negative():
    obs=generic.defaults();obs['label']='Example'
    generic.validate(obs)
    assert obs['focus']['assessment_state']=='not_assessed'
    obs['focus']['assessment_state']='no_evidence_in_view';obs['focus']['findings']=['mark']
    with pytest.raises(ValueError):generic.validate(obs)

def test_review_timer_never_double_counts_open_intervals(generic_run):
    s,images,g,obs,actor,req=generic_run
    human={'role':'reviewer','id':'reviewer-test'}
    s.action('surface',req('review_start',review_session_id='first'),human)
    with pytest.raises(ValueError,match='existing interval'):
        s.action('surface',req('review_start',review_session_id='second'),human)
    assert s.state('surface')['review_sessions']['first']['seconds'] is None
    s.action('surface',req('review_stop',review_session_id='first'),human)
    assert s.state('surface')['review_sessions']['first']['seconds']>=0

def test_http_rejects_malformed_requests_without_losing_service(generic_run):
    from http.client import HTTPConnection
    from threading import Thread
    from sixth_magnitude.server import Server
    s,images,g,obs,actor,req=generic_run
    with Server(s.root,0) as server:
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        client=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            for body in ([], None, {'action':'review_start','case_id':'surface',
                    'actor':{'role':'reviewer','id':3},'operation_id':'bad-actor',
                    'source_key':s.source('surface')['source_key'],'base_revision':0}):
                client.request('POST','/api/action',json.dumps(body),{'Content-Type':'application/json'})
                response=client.getresponse();payload=json.loads(response.read())
                assert response.status==400 and 'error' in payload
            client.request('GET','/api/health')
            response=client.getresponse()
            assert response.status==200 and json.loads(response.read())['status']=='ok'
            assert s.state('surface')['revision']==0
        finally:
            client.close();server.shutdown();worker.join(timeout=5)
