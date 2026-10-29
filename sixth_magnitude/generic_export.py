"""General inspection exports preserve candidate, visual and review states."""
import hashlib
from .store import atomic_json, read, utc
from .exports import table, flat

def export_generic(store):
    with store.writing():
        states={case:store.state(case) for case in store.cases}
    directory=store.root/'exports'/utc().replace(':','').replace('.','-')
    directory.mkdir(parents=True,exist_ok=False)
    rows=[]; observations=[]; accepted=[]; features=[]; evidence=[]; events=[]; sessions=[]
    for case,state in states.items():
        for path in store.revisions(case):
            rev=read(path)
            if rev['revision']<=state['revision']:
                events.append({'case_id':case,'revision':rev['revision'],**rev['last_event']})
        for sid,session in state['review_sessions'].items():
            sessions.append({'case_id':case,'session_id':sid,**flat(session)})
        for a in state['annotations'].values():
            if a['deleted']: continue
            source=store.source(case); g=a['geometry']; bbox=g['bbox_level0']
            row={'case_id':case,'case_revision':state['revision'],'annotation_id':a['id'],
                'version':a['version'],'source_key':source['source_key'],'content_hash':a['content_hash'],
                'evidence_id':a['evidence_id'],'label':a['observation']['label'],
                'coordinate_frame':'reader_level0_px','coordinate_unit':source['coordinate_unit'],
                'mpp_x':source['mpp_x'],'mpp_y':source['mpp_y'],
                'width_px':bbox[2]-bbox[0],'height_px':bbox[3]-bbox[1],
                'visual_state':a['visual']['state'],'review_state':a['review']['state'],
                'origin':a['origin'],'geometry':g}
            rows.append(row)
            for scope in ('focus','context'):
                obs={**{k:v for k,v in row.items() if k!='geometry'},'scope':scope,
                    'support':'focus_geometry' if scope=='focus' else 'context_window_minus_focus_geometry',
                    **a['observation'][scope]}
                observations.append(obs)
                if (store.manifest['purpose']=='annotation' and a['origin']!='engineering'
                    and a['visual']['state']=='matched' and a['visual'].get('content_hash')==a['content_hash']
                    and a['review']['state']=='reviewer_accepted' and a['review'].get('content_hash')==a['content_hash']):
                    accepted.append({**obs,'reviewer_id':a['review']['actor']['id']})
            x0,y0,x1,y1=bbox
            pts=g['points'] or [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
            features.append({'type':'Feature','id':a['id'],'geometry':{'type':'Polygon','coordinates':[pts+[pts[0]]]},
                'properties':{**row,'observation':a['observation']}})
            evidence.append({'case_id':case,'annotation_id':a['id'],'metadata':read(store.root/'evidence'/a['evidence_id']/'metadata.json')})
    table(directory,'regions',rows,['case_id','annotation_id','version','coordinate_unit'])
    table(directory,'observations',observations,['case_id','annotation_id','scope','assessment_state'])
    table(directory,'accepted_observations',accepted,['case_id','annotation_id','scope','reviewer_id'])
    table(directory,'review_sessions',sessions,['case_id','session_id','start_utc','end_utc','seconds'])
    atomic_json(directory/'canonical_snapshot.json',{'run':store.manifest,'cases':states})
    atomic_json(directory/'annotations.geojson',{'type':'FeatureCollection','features':features})
    atomic_json(directory/'evidence_manifest.json',evidence)
    atomic_json(directory/'events.json',events)
    summary={'profile':'generic','regions':len(rows),'observations':len(observations),'accepted_observations':len(accepted),
        'purpose':store.manifest['purpose'],'case_revisions':{c:s['revision'] for c,s in states.items()},
        'whole_image_coverage_established':False,
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}}
    atomic_json(directory/'export_manifest.json',summary)
    return {'directory':str(directory),**{k:summary[k] for k in ('regions','observations','accepted_observations')}}
