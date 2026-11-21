"""Live, reviewable local-edit stage shared by independent host policies."""
from collections import deque
from pathlib import Path
import json
import numpy as np
from PIL import Image
from .composition import bounds, compose, seeded, centers, walker, delta
from .composition_view import sheet, encode
from .nuclei import TOOLS, LOCAL_TOOL
from .packets import read, dump, sha
from .source import pixel_hash
from .rounds import object_schema

MENU = [*TOOLS, LOCAL_TOOL]
ACTIONS = ['keep','unresolved','zoom','whole','splice','watershed','random_walker','accept','accept_continue','undo']
PROMPT = """Call workbench_open. Inspect each raw field and the candidate contours; choose independently.
Only use workbench tools. Keep a satisfactory mask or mark unresolved. You may zoom an arbitrary field ROI
and named donor to see IDs on current and donor maps. All points and rectangles use FIELD pixels [x,y],
origin top left, even in zoom. roi=[x,y,width,height] or [] for entire field. A whole proposal replaces the
field. In local-enabled arms splice removes named current parent IDs and inserts complete donor child IDs
(add/delete allowed). Watershed splits one named parent using your points plus interior centers of donor
child IDs. Random_walker uses foreground points and background points within an ROI. Proposals display a
preview and require accept, accept_continue (commit then edit more), or undo before further action.
Zoom does not modify a mask. Supply unused arrays as [], donor as baseline if unused. On conflict inspect,
expand the scope or keep. The visual budget is shared across tools. Decide from morphology, not coverage
alone. Give a short reason per field. Continue until COMPLETE."""

def definitions():
    string={'type':'string'}
    ints={'type':'array','items':{'type':'integer'}}
    points={'type':'array','items':ints}
    row=object_schema(dict(field=string,action={'type':'string','enum':ACTIONS},donor=string,
                           roi=ints,parents=ints,children=ints,points=points,background=points,reason=string))
    return [dict(type='function',name='workbench_'+name,deferLoading=False,description=desc,inputSchema=schema)
            for name,desc,schema in [('open','Show current evidence.',object_schema({})),
                ('submit','Inspect, propose, review, or finish each field on this ticket.',
                 object_schema({'ticket':string,'decisions':{'type':'array','items':row}}))]]


class Field:
    def __init__(self, path, detector):
        info=read(path/'field.json')
        files=['source.png',*(name+'.npz' for name in MENU)]
        if any(sha(path/name)!=info['files'][name] for name in files):
            raise ValueError('Input differs from packet manifest')
        self.rgb=np.array(Image.open(path/'source.png').convert('RGB'))
        self.bank={name:np.load(path/(name+'.npz'))['labels'] for name in MENU}
        self.source=sha(path/'field.json')
        self.detector=None
        if detector:
            file=Path(detector)/(path.name+'.npz')
            meta=read(file.with_suffix('.json'))
            if meta['source_sha256']!=info['files']['source.png'] or sha(file)!=meta['output_sha256']:
                raise ValueError('Detector candidate disagrees with source or file hash')
            self.bank['stardist']=np.load(file)['labels']
            self.detector=sha(file)
        for lab in self.bank.values():
            if lab.shape!=self.rgb.shape[:2] or lab.dtype.kind not in 'iu' or lab.min()<0:
                raise ValueError('Invalid instance map')
        self.current=self.bank['baseline']
        self.pending=None
        self.finished=False
        self.spent=0
        self.roi=[]
        self.donor='baseline'
        self.message='First look. Raw and all available candidate contours.'
        self.view='gallery'

    def render(self):
        layers=[('raw',None)]
        if self.pending is not None:
            layers.extend([('before',self.current),('proposal',self.pending)])
        elif self.view=='zoom':
            layers.extend([('current IDs',self.current),(self.donor+' IDs',self.bank[self.donor])])
        else:
            layers.extend(self.bank.items())
            if self.spent:
                layers.append(('current',self.current))
        return sheet(self.rgb,layers,self.roi,ids=self.view=='zoom' and self.pending is None)

    def apply(self, r, local, limit):
        action=r['action']
        if self.finished or action not in ACTIONS:
            raise ValueError('Field finished or invalid action')
        if not r['reason'].strip():
            raise ValueError('Supply visual reason')
        if self.pending is not None:
            if action not in ('accept','accept_continue','undo'):
                raise ValueError('Preview pending. Accept, accept_continue, or undo.')
            if action!='undo':
                self.current=self.pending
            self.pending=None
            self.finished=action=='accept'
            self.view,self.roi='gallery',[]
            self.message='Preview '+action
            return
        if action in ('keep','unresolved'):
            self.finished=True
            return
        if action in ('accept','accept_continue','undo'):
            raise ValueError('No preview pending')
        if action not in ('zoom','whole') and not local:
            raise ValueError('This arm enables whole-field proposals and zoom only')
        if self.spent>=limit:
            raise ValueError('Review budget used. Select keep or unresolved.')
        donor=r['donor']
        if donor not in self.bank:
            raise ValueError('Unknown donor, available: '+', '.join(self.bank))
        roi=r['roi']
        bounds(roi,self.current.shape)
        self.spent+=1
        if action=='zoom':
            self.view,self.roi,self.donor='zoom',roi,donor
            self.message='ROI, current and donor IDs. Points use field coordinates.'
            return
        source=self.bank[donor]
        if action=='whole':
            out=source
            scope=np.ones_like(out,dtype=bool)
        elif action=='splice':
            out,scope=compose(self.current,source,r['parents'],r['children'],roi)
        elif action=='watershed':
            if len(r['parents'])!=1:
                raise ValueError('Specify one parent')
            points=r['points']+centers(source,r['children'])
            out,scope=seeded(self.rgb,self.current,r['parents'][0],points,roi)
        else:
            out,scope=walker(self.rgb,self.current,r['parents'],r['points'],r['background'],roi)
        self.pending=out
        self.view='preview'
        self.roi=[] if action=='whole' else roi
        self.message='Full field geometry delta '+json.dumps(delta(self.current,out,scope))
        if action!='whole' and not self.roi and scope.any():
            yy,xx=np.nonzero(scope)
            x,y=max(0,int(xx.min())-10),max(0,int(yy.min())-10)
            self.roi=[x,y,min(out.shape[1],int(xx.max())+11)-x,min(out.shape[0],int(yy.max())+11)-y]


class Bench:
    def __init__(self,paths,output,policy,classifier=None,*,schedule=None):
        self.output=Path(output)
        self.policy=policy
        self.fields={p.name:Field(p,policy.get('detector')) for p in paths}
        self.local=policy['local']
        self.limit=policy['budget']
        self.sequential=policy['individual']
        size=1 if self.sequential else policy['batch']
        keys=list(self.fields)
        self.queue=deque(keys[i:i+size] for i in range(0,len(keys),size))
        self.completed=self.errors=0
        self.history=[]
        dump(self.output/'inputs.json',dict(policy=policy,fields={k:[f.source,f.detector] for k,f in self.fields.items()}))

    @property
    def done(self):
        return not self.queue

    def handle(self,name,args,call_id):
        if self.done:
            raise ValueError('Session complete')
        keys=self.queue[0]
        ticket=f'R{self.completed+1}'
        if name=='workbench_submit':
            rows=args['decisions']
            if args['ticket']!=ticket or sorted(r['field'] for r in rows)!=sorted(keys):
                raise ValueError('Submit once for every field on current ticket '+ticket)
            self.queue.popleft()
            for row in rows:
                key=row['field']
                f=self.fields[key]
                msg=None
                try:
                    f.apply(row,self.local,self.limit)
                except ValueError as exc:
                    msg=str(exc)
                    f.message=msg
                if f.finished:
                    np.savez_compressed(self.output/(key+'.npz'),labels=f.current)
                self.history.append(dict(ticket=ticket,field=key,request=row,error=msg,current=pixel_hash(f.current),
                    preview=pixel_hash(f.pending) if f.pending is not None else None,detail=f.message))
            remaining=[k for k in keys if not self.fields[k].finished]
            if remaining:
                if self.sequential:
                    self.queue.appendleft(remaining)
                else:
                    self.queue.append(remaining)
            self.completed+=1
            dump(self.output/'history.json',self.history)
        elif name!='workbench_open':
            raise ValueError('Unexpected tool')
        if self.done:
            return [dict(type='inputText',text='COMPLETE')]
        parts=[dict(type='inputText',text=f'Ticket R{self.completed+1}. Local operations enabled: {self.local}.')]
        for k in self.queue[0]:
            f=self.fields[k]
            parts.append(dict(type='inputText',text=f'{k}: {f.message} Field WxH {f.rgb.shape[1]}x{f.rgb.shape[0]}, ROI {f.roi or "full"}, {self.limit-f.spent} previews/zooms left. Tools '+', '.join(f.bank)))
            parts.append(encode(f.render()))
            if f.pending is not None and f.roi:
                parts.append(encode(sheet(f.rgb,[('before (full)',f.current),('proposal (full)',f.pending)])))
        return parts
