"""Model-directed native lung views and revisable regional histology labels."""
from collections import deque
from pathlib import Path
import argparse
import numpy as np
from PIL import Image, ImageDraw
from skimage.segmentation import slic,find_boundaries
from skimage.measure import regionprops
from .codex_session import run
from .composition import bounds
from .composition_bench import Bench
from .composition_view import encode
from .packets import dump,read,sha
from .rounds import object_schema

CLASSES=('other','cribriform','micropapillary','solid','papillary','acinar','lepidic')
COLORS=np.array([[0,0,0],[182,101,242],[255,128,54],[245,77,103],[245,210,70],[32,208,211],[117,223,97]])
TOOLS=('baseline','dense','tissue','empty')
PROMPT="""Use exclusively the two provided dynamic tools workbench_open and workbench_submit until COMPLETE. All source images, crops, IDs and previews arrive inline. Use workbench_submit for all calculations, zooms and decisions. Shell, files and other tool calls invalidate the blind session. Assess the extent and subtypes of six
lung growth patterns from visible histology. Other includes non-tumor tissue. Overview, current type map
and SLIC region IDs are shown. You may use zoom with an arbitrary [x,y,width,height] in original image
pixels, then inspect the native crop, including surrounding architecture. Whole proposes assigning a
pattern to a chosen candidate extent; paint assigns one named pattern (including other) to selected
SLIC region IDs. Overlapping paint proposals are applied to the latest accepted state. Check the preview,
then accept, accept_continue or undo. Keep or mark unresolved as appropriate. Do not assume tumor exists.
Independent neighboring regions may have different growth patterns. Supply unused lists as []."""

def definitions():
    string={'type':'string'}
    ints={'type':'array','items':{'type':'integer'}}
    row=object_schema(dict(field=string,reason=string,
        action={'type':'string','enum':['zoom','whole','paint','accept','accept_continue','undo','keep','unresolved']},
        roi=ints,ids=ints,donor={'type':'string','enum':list(TOOLS)},pattern={'type':'string','enum':list(CLASSES)}))
    return [dict(type='function',name='workbench_'+k,deferLoading=False,description=msg,inputSchema=s)
            for k,msg,s in [('open','Open the current lung views',object_schema({})),('submit','Review or edit selected tissue regions',
                object_schema(dict(ticket=string,decisions={'type':'array','items':row})))]]


def semantic_view(rgb,lab):
    overlay=rgb.copy()
    active=lab>0
    overlay[active]=np.uint8(.62*rgb[active]+.38*COLORS[lab[active]])
    edge=find_boundaries(lab,mode='inner') & active
    overlay[edge]=COLORS[lab[edge]]
    return Image.fromarray(overlay)

def montage(tiles):
    panels=[]
    for name,image in tiles:
        thumb=image.copy()
        thumb.thumbnail((420,420))
        p=Image.new('RGB',(thumb.width,thumb.height+23),'white')
        p.paste(thumb,(0,23))
        ImageDraw.Draw(p).text((3,3),name,fill='black')
        panels.append(p)
    w=max(v.width for v in panels)
    h=max(v.height for v in panels)
    out=Image.new('RGB',(min(3,len(panels))*w,((len(panels)+2)//3)*h),'white')
    for i,p in enumerate(panels):
        out.paste(p,((i%3)*w,(i//3)*h))
    return out


class RegionField:
    def __init__(self,path,native):
        info=read(path/'field.json')
        if sha(native)!=info['source_key'] or any(sha(path/n)!=d for n,d in info['files'].items()):
            raise ValueError('Native image or overview differs from packet')
        self.native=Image.open(native).convert('RGB')
        self.rgb=np.asarray(Image.open(path/'source.png').convert('RGB'))
        self.bank={n:np.load(path/(n+'.npz'))['labels'] for n in TOOLS}
        self.slic=slic(self.rgb,n_segments=64,compactness=12,start_label=1).astype('int32')
        self.current=np.uint8((self.bank['baseline']>0)*CLASSES.index('acinar'))
        self.pending=None
        self.source=sha(path/'field.json')
        self.detector=info['source_key']
        self.finished=False
        self.spent=0
        self.roi=[]
        self.message='Overview. Native WxH '+str(self.native.size)
        self.view='overview'

    def render(self):
        raw=Image.fromarray(self.rgb)
        if self.roi:
            x,y,w,h=self.roi
            box=(x,y,x+w,y+h)
            view=self.native.crop(box)
            def crop(labels):
                return np.array(Image.fromarray(labels).resize(self.native.size,Image.Resampling.NEAREST).crop(box))
            before=crop(self.current)
            ids=crop(self.slic)
        else:
            view=raw
            ids=self.slic
            before=self.current
        source=np.asarray(view)
        labeled=source.copy()
        labeled[find_boundaries(ids,mode='inner')]=(255,217,69)
        id_image=Image.fromarray(labeled)
        # Draw after scaling so small text remains readable.
        id_image.thumbnail((420,420))
        draw=ImageDraw.Draw(id_image)
        for obj in regionprops(ids):
            yy,xx=obj.centroid
            draw.text((xx*id_image.width/ids.shape[1],yy*id_image.height/ids.shape[0]),str(obj.label),
                      anchor='mm',fill='white',stroke_width=1,stroke_fill='black')
        tiles=[('source (native crop)' if self.roi else 'source',view),
               ('current types',semantic_view(source,before)),('SLIC IDs',id_image)]
        if self.pending is not None:
            tiles.append(('proposed types',semantic_view(source,crop(self.pending) if self.roi else self.pending)))
            if self.roi:
                tiles.append(('full proposal',semantic_view(self.rgb,self.pending)))
        elif not self.roi:
            for n in TOOLS:
                if n!='empty':
                    tiles.append((n,semantic_view(self.rgb,np.uint8(5*(self.bank[n]>0)))))
        if self.roi and max(view.size)>420:
            cw,ch=min(view.width,420),min(view.height,420)
            left,top=(view.width-cw)//2,(view.height-ch)//2
            center_box=(left,top,left+cw,top+ch)
            tiles.append((f'1:1 native center at ({x+left},{y+top})',view.crop(center_box)))
        return montage(tiles)

    def apply(self,r,local,limit):
        a=r['action']
        if not r['reason'].strip():
            raise ValueError('Visual reason required')
        if self.pending is not None:
            if a not in ('accept','accept_continue','undo'):
                raise ValueError('Confirm or undo pending map first')
            if a!='undo':
                self.current=self.pending
            self.pending=None
            self.finished=a=='accept'
            self.roi=[]
            self.message='Preview '+a+'; native WxH '+str(self.native.size)
            return
        if a in ('unresolved','keep'):
            self.finished=True
            return
        if self.spent>=limit or a not in ('zoom','whole','paint'):
            raise ValueError('Use keep/unresolved, or a permitted operation with remaining budget')
        roi=r['roi']
        bounds(roi,self.native.size[::-1])
        self.spent+=1
        if a=='zoom':
            self.roi=roi
            self.message='Native ROI '+str(roi)+' within native WxH '+str(self.native.size)
            return
        value=CLASSES.index(r['pattern'])
        if a=='whole':
            out=np.uint8((self.bank[r['donor']]>0)*value)
            self.roi=[]
        else:
            ids=r['ids']
            if not ids or not set(ids)<=set(np.unique(self.slic)):
                raise ValueError('Select valid SLIC IDs')
            mask=np.isin(self.slic,ids)
            out=self.current.copy()
            out[mask]=value
            assert np.array_equal(out[~mask],self.current[~mask])
            self.roi=roi
        self.pending=out
        self.message=f'Changed overview pixels: {np.count_nonzero(out!=self.current)}. Native WxH {self.native.size}'


class RegionBench(Bench):
    def __init__(self,paths,output,policy,classifier=None,*,schedule=None):
        self.output=Path(output)
        self.fields={p.name:RegionField(p,Path(policy['native'])/(p.name+'.jpg')) for p in paths}
        self.local=True
        self.limit=policy['budget']
        self.sequential=policy['individual']
        size=1 if self.sequential else policy['batch']
        keys=list(self.fields)
        self.queue=deque(keys[i:i+size] for i in range(0,len(keys),size))
        self.completed=self.errors=0
        self.history=[]
        dump(self.output/'inputs.json',dict(policy=policy,fields={k:[f.source,f.detector] for k,f in self.fields.items()}))

    def handle(self,name,args,call_id):
        # The parent transport handles the queue and sealing. Each region view already
        # includes its full proposal, so use the regional display for every native ROI.
        parts=super().handle(name,args,call_id)
        if not self.done:
            for p in parts:
                if p['type']=='inputText' and 'Field WxH' in p['text']:
                    p['text']=p['text'].replace('Field WxH','Overview WxH')
            parts.insert(1,dict(type='inputText',text='Type colors: cribriform purple, micropapillary orange, solid red, papillary yellow, acinar cyan, lepidic green, other unfilled. ROI coordinates always use native WxH.'))
        return parts


def cli(policy,individual,argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('fields',nargs='+',type=Path)
    p.add_argument('--native-dir',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--session',required=True,type=Path)
    p.add_argument('--budget',type=int,default=6)
    a=p.parse_args(argv)
    if a.budget<=0:
        raise ValueError('Budget must be positive')
    cfg={**policy,'individual':individual,'budget':a.budget,'native':str(a.native_dir.resolve())}
    print(run(a.fields,a.output,cfg,a.session,bench_factory=RegionBench,definitions=definitions,prompt=PROMPT))
