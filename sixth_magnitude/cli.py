"""Portable CLI. The viewer never selects, launches or impersonates a model."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import tempfile
import uuid
from . import __version__, telemetry
from .profiles import load
from .readers import calibration
from .imaging import normalize_source
from .store import Store,atomic_json,identifier,utc

def initialize(run,rows,profile='generic',purpose='annotation',model='gpt-6-astra',effort='low',copy_assets=False):
    root=Path(run).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Choose an empty run directory; existing runs are never reset')
    if not rows: raise ValueError('At least one source is required')
    contract=load(profile); cases=[]; seen=set()
    for row in rows:
        asset=identifier(row['asset_id'])
        if asset in seen: raise ValueError('Duplicate asset ID')
        seen.add(asset)
        path=Path(row['path']).resolve()
        backend=row.get('reader') or ('raster' if path.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp') else 'isyntax' if path.suffix.lower()=='.isyntax' else 'openslide')
        mx,my=calibration(row.get('mpp_x'),row.get('mpp_y'))
        source=normalize_source({'source':str(path),'source_asset_id':asset,'slide_token':asset,
            'reader':backend,'mpp_x':mx,'mpp_y':my})
        if profile=='lung-pathology' and source['mpp_x'] is None:
            raise ValueError('The pathology profile requires verified physical calibration; supply mpp_x and mpp_y when absent')
        cases.append({'case_id':asset,'source':source,'planned_rois':[]})
    root.mkdir(parents=True,exist_ok=True)
    if copy_assets:
        (root/'assets').mkdir()
        for case in cases:
            s=case['source'];target=root/'assets'/(case['case_id']+Path(s['source']).suffix)
            shutil.copy2(s['source'],target)
            s['source']=str(target)
    manifest={'run_id':root.name,'workspace_id':uuid.uuid4().hex,'framework':'Sixth Magnitude','version':__version__,
        'created_utc':utc(),'profile':profile,'purpose':purpose,'task_mode':'slide_discovery',
        'coordinate_frame':'reader_level0_px','model_requested':model,'effort_requested':effort,
        'model_observed':None,'effort_observed':None,'cases':cases}
    atomic_json(root/'observation.schema.json',contract.SCHEMA,exclusive=True)
    atomic_json(root/'run_manifest.json',manifest,exclusive=True)
    return {'run':str(root),'profile':profile,'assets':len(cases),'purpose':purpose}

def manifest_rows(path):
    path=Path(path).resolve()
    with path.open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    for row in rows:
        p=Path(row['path'])
        row['path']=str((path.parent/p).resolve()) if not p.is_absolute() else str(p)
        for k in ('mpp_x','mpp_y'): row[k]=float(row[k]) if row.get(k) else None
    return rows

def main(argv=None):
    parser=argparse.ArgumentParser(prog='sixth-magnitude',description=__doc__)
    parser.add_argument('--version',action='version',version=__version__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('demo','init'):
        p=sub.add_parser(name);p.add_argument('--run',required=True)
        p.add_argument('--profile',choices=['generic','lung-pathology'],default='generic')
        p.add_argument('--model',default='gpt-6-astra');p.add_argument('--effort',default='low')
        if name=='init':
            p.add_argument('--manifest',required=True)
            p.add_argument('--purpose',choices=['annotation','engineering'],default='annotation')
    for name in ('serve','status','brief','export','prompt','bind-session','usage'):
        p=sub.add_parser(name);p.add_argument('--run',required=True)
        if name=='serve': p.add_argument('--port',type=int,default=8790)
        if name=='brief': p.add_argument('--asset-id',required=True)
        if name=='bind-session': p.add_argument('--session-log',required=True)
        if name=='usage': p.add_argument('--close',action='store_true')
    for name in ('schema','template'):
        p=sub.add_parser(name);p.add_argument('--profile',choices=['generic','lung-pathology'],default='generic')
    args=parser.parse_args(argv)
    try:
        if args.command in ('schema','template'):
            contract=load(args.profile);result=contract.SCHEMA if args.command=='schema' else contract.defaults()
        elif args.command=='demo':
            from .demo import create_fixture
            with tempfile.TemporaryDirectory() as folder:
                image=Path(folder)/'fixture.png';create_fixture(image,args.profile)
                mpp=4.0 if args.profile=='lung-pathology' else None
                result=initialize(args.run,[{'asset_id':'demo','path':str(image),'mpp_x':mpp,'mpp_y':mpp}],
                    args.profile,'engineering',args.model,args.effort,copy_assets=True)
        elif args.command=='init':
            result=initialize(args.run,manifest_rows(args.manifest),args.profile,args.purpose,args.model,args.effort)
        elif args.command=='serve':
            from .server import serve
            serve(args.run,args.port);return
        else:
            store=Store(args.run)
            if args.command=='status': result={'profile':store.profile,'queue':store.queue(),'usage':telemetry.report(store)}
            elif args.command=='brief':
                case=identifier(args.asset_id);state=store.state(case)
                result={'asset_id':case,'revision':state['revision'],'profile':store.profile,
                    'regions':[{'id':a['id'],'label':a['observation']['label'],'visual':a['visual']['state'],'review':a['review']['state']}
                       for a in state['annotations'].values() if not a['deleted']]}
            elif args.command=='export':
                from .exports import export_run
                result=export_run(store)
            elif args.command=='bind-session': result=telemetry.bind(store,args.session_log)
            elif args.command=='usage': result=telemetry.report(store,args.close)
            else:
                skill=Path(__file__).parent/'skill/SKILL.md'
                print(f'Read {skill}. Run: {store.root}. Profile: {store.profile}. Requested model: {store.manifest["model_requested"]}; effort: {store.manifest["effort_requested"]}. Verify actual configuration separately.');return
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError) as exc:
        parser.exit(2,f'{exc}\n')

if __name__=='__main__': main()
