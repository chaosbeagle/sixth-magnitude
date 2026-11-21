"""Run a blind interactive session with either host's editing policy."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import numpy as np
from .codex_session import Rpc
from .free_edit import DEFAULT_CLASSES, Review
from .packets import dump,sha
from .rounds import object_schema

SETTINGS={'astra':('gpt-6.2-astra','low','priority'),'sol':('gpt-6.2-sol','xhigh','ultrafast')}
def prompt(mode,individual,classes,python):
    guide="""You are the blinded image operator in a paired pathology benchmark. Use the visible H&E to segment separate nuclei and assign cell types. Work only with supplied images in your current workspace. No other cases, project sources, reference masks, network, external models, assistants or other sessions. The coordinator keeps the progress record separately. Field coordinates are native pixels [x,y] from upper left. Inspect visual evidence and determine when the result is ready; there is no edit quota. Use the native review_fields tool: decisions_json is a JSON array of actions; [] opens the current case(s). Each action has field and action. Each proposal displays its overlay and IDs, then accept commits for further editing, undo cancels, or seal commits and finishes after inspection. view with optional roi [x,y,width,height] zooms; view with gallery true shows candidates when available. Assign every final ID a class at seal using default (class integer) plus optional groups [{"class":integer,"ids":[...]}]. An empty segmentation is allowed. Object IDs need not be contiguous. Supply a brief reason on each edit/seal. Seal every field, then finish. """
    if mode!='raw':
        guide+="Tools: whole with donor name from gallery; splice with donor, parents (remove current IDs), children (insert donor IDs); watershed with parents:[ID] and points or donor children; random_walker with parents plus points and background seeds. view with donor shows donor IDs. Baseline is initially current. "
    if mode!='tools':
        guide+=f"""Direct image editing: paint with strokes [{{"id":positive instance ID or 0 for erasing,"points":[[x,y],...]}}], or pixels with file (local .npy basename) and optional roi. Local arrays must exactly fill the rectangle; IDs are literal. You may freely write and run your own general image processing or drawing code in this directory with {python} (NumPy, SciPy, skimage, Pillow). Raw images FIELD.png and committed FIELD-current.npy available. No prescribed algorithm. Output masks are submitted via review_fields, including model-generated arrays. """
    else:guide+="Use only review_fields for image decisions, including counting, geometry and typing. "
    if mode=='hybrid':guide+="You choose when to use tools, to work directly from pixels, or to combine both in any order. "
    if mode=='raw':guide+="Start from blank maps and rely on the original images. "
    guide+=("Review fields individually in order. " if individual else "You may batch independent decisions across fields. ")
    return guide+'Class IDs: '+', '.join(f'{i+1}={v}' for i,v in enumerate(classes))+'.'
def run(host,individual,a):
    out=a.output.resolve();out.mkdir(exist_ok=False,parents=True)
    model,effort,tier=SETTINGS[host]
    receipt=dict(host=host,mode=a.mode,model=model,effort=effort,tier=tier,status='started',time_utc=datetime.now(timezone.utc).isoformat())
    start=time.monotonic()
    with TemporaryDirectory(prefix='pathology-blind-') as tmp,(out/'stderr.log').open('w') as err,(out/'events.jsonl').open('w') as journal:
        work=Path(tmp).resolve()
        bench=Review(a.fields,a.mode,a.detector,work,out,a.classes,individual)
        receipt['work']=str(work)
        receipt['sources']={f.name:sha(f/'source.png') for f in a.fields}
        instruction=prompt(a.mode,individual,a.classes,a.python)
        receipt['prompt']=instruction
        proc=subprocess.Popen(['codex','app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
        rpc=Rpc(proc)
        def log(record):
            journal.write(json.dumps(record)+'\n');journal.flush()
        try:
            rpc.call('initialize',{'clientInfo':{'name':'native_hybrid_workbench','version':'1.0'},'capabilities':{'experimentalApi':True}})
            rpc.send({'method':'initialized'})
            definition=dict(type='function',name='review_fields',description='Inspect images, propose tool or pixel edits, review, type and seal. Pass [] to open.',deferLoading=False,inputSchema=object_schema({'decisions_json':{'type':'string'}}))
            res=rpc.call('thread/start',dict(ephemeral=True,allowProviderModelFallback=False,model=model,serviceTier=tier,config={'model_reasoning_effort':effort},cwd=str(work),sandbox='read-only' if a.mode=='tools' else 'workspace-write',approvalPolicy='never',dynamicTools=[definition]))
            receipt['accepted']={k:res.get(k) for k in ('model','reasoningEffort','serviceTier','modelProvider')}
            if tuple(receipt['accepted'].values())[:3]!=(model,effort,tier):raise RuntimeError(str(receipt['accepted']))
            tid=receipt['thread_id']=res['thread']['id'];dump(out/'receipt.json',receipt)
            follow=instruction
            receipt['turns']=[]
            for attempt in range(2):
                t0=time.monotonic()
                turn_id=rpc.call('turn/start',dict(threadId=tid,model=model,effort=effort,serviceTier=tier,input=[{'type':'text','text':follow}]))['turn']['id']
                while True:
                    msg=rpc.pending.pop(0) if rpc.pending else rpc.recv(timeout=240)
                    method=msg.get('method','');p=msg.get('params',{})
                    if method=='item/tool/call' and 'id' in msg:
                        if (p['threadId'],p['turnId'],p['tool'])!=(tid,turn_id,'review_fields'):raise RuntimeError('Unexpected tool request')
                        try:
                            decisions=json.loads(p['arguments']['decisions_json'])
                            if not isinstance(decisions,list) or any(not isinstance(d,dict) for d in decisions):raise ValueError('Use an array of objects')
                            parts=bench.handle(decisions)
                        except (ValueError,KeyError,TypeError) as e:parts=[{'type':'inputText','text':str(e)}]
                        rpc.send({'id':msg['id'],'result':{'success':True,'contentItems':parts}})
                        log(dict(call=p['callId'],arguments=p['arguments'],text=[d['text'] for d in parts if d['type']=='inputText'],images=sum(d['type']=='inputImage' for d in parts)))
                    elif 'id' in msg and method:raise RuntimeError('Unexpected native request '+method)
                    elif p.get('threadId')!=tid:continue
                    elif method=='item/completed':
                        item=p['item']
                        log({k:v for k,v in item.items() if k in ('type','id','command','cwd','path','tool','status','exitCode','text','changes')})
                        if a.mode=='tools' and item['type'] in ('commandExecution','fileChange','mcpToolCall','webSearch'):raise RuntimeError('Tool-only condition used '+item['type'])
                    elif method=='thread/tokenUsage/updated':
                        receipt['usage']=p['tokenUsage']
                    elif 'rerout' in method.lower():raise RuntimeError('Rerouted')
                    elif method=='turn/completed' and p['turn']['id']==turn_id:
                        s=p['turn'];receipt['turns'].append(dict(id=turn_id,seconds=time.monotonic()-t0,status=s['status'],server_ms=s.get('durationMs')))
                        if s['status']!='completed':raise RuntimeError(str(s.get('error')))
                        break
                if not bench.active():break
                follow='There are still unsealed fields: '+str(bench.active())+'. Open with [] and complete them; no scores are available.'
            if bench.active() or 'usage' not in receipt:raise RuntimeError('Incomplete field or missing native usage')
            receipt['status']='completed'
        except BaseException as e:
            receipt.update(status='failed',error=str(e));raise
        finally:
            receipt['seconds']=time.monotonic()-start
            receipt['outputs']={f.name:sha(f) for f in sorted(out.glob('*.npz'))}
            dump(out/'actions.json',{k:f.events for k,f in bench.fields.items()})
            dump(out/'receipt.json',receipt)
            proc.stdin.close()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=8)
            scratch=out/'authored';scratch.mkdir()
            for f in work.iterdir():
                if f.is_file() and (f.suffix=='.py' or f.suffix=='.sh'):shutil.copyfile(f,scratch/f.name)
def cli(host,individual,argv):
    p=argparse.ArgumentParser(description='Inspect source images, choose candidate or native-pixel edits, and seal typed instance maps.')
    p.add_argument('--mode',choices=['hybrid','tools','raw'],default='hybrid');p.add_argument('--detector',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fields',type=Path,nargs='+',required=True)
    p.add_argument('--classes',nargs='+',default=DEFAULT_CLASSES);p.add_argument('--python',default='python3',help='Python runtime for model-authored image operations')
    run(host,individual,p.parse_args(argv))
