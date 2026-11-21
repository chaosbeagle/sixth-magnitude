"""Native-coordinate pixel editing and structural proposals for visually reviewed masks."""
from base64 import b64encode
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .composition import bounds, centers, compose, seeded, walker
from .composition_bench import Field
from .composition_view import sheet
from .packets import dump

DEFAULT_CLASSES = ['epithelial/tumor','lymphocyte','macrophage','neutrophil','stromal','plasma cell','eosinophil']
STRUCTURAL = ('whole','splice','watershed','random_walker')
DIRECT = ('paint','pixels')
def checked_map(mask, shape):
    if mask.shape!=shape or mask.dtype.kind not in 'iu' or mask.min()<0 or mask.max()>2**31-1:
        raise ValueError('Expected aligned nonnegative int32-compatible 2-D instance IDs')
    return mask.astype(np.int32)

def pixel_edit(current, roi, array=None, strokes=None):
    x,y,w,h=bounds(roi,current.shape)
    out=current.copy()
    if array is not None:
        out[y:y+h,x:x+w]=checked_map(array,(h,w))
    else:
        if not strokes:raise ValueError('Supply polygons with field coordinates')
        patch=Image.fromarray(out[y:y+h,x:x+w].astype(np.int32))
        draw=ImageDraw.Draw(patch)
        for s in strokes:
            label=s['id'];points=s['points']
            if type(label)!=int or not 0<=label<=2**31-1 or len(points)<3:raise ValueError('Invalid polygon ID or vertices')
            if any(len(pt)!=2 or any(type(z)!=int for z in pt) or not (x<=pt[0]<x+w and y<=pt[1]<y+h) for pt in points):
                raise ValueError('Polygon outside ROI')
            draw.polygon([(px-x,py-y) for px,py in points],fill=label)
        out[y:y+h,x:x+w]=np.asarray(patch)
    return out

def picture(rgb,layers,roi=(),ids=False):
    canvas=sheet(rgb,layers,roi,ids=ids)
    buf=BytesIO();canvas.save(buf,format='JPEG',quality=90,subsampling=0)
    return {'type':'inputImage','imageUrl':'data:image/jpeg;base64,'+b64encode(buf.getvalue()).decode()}

def message(s):return {'type':'inputText','text':s}
class Canvas:
    def __init__(self,path,mode,detector,work,output,classes):
        self.key=path.name; self.mode=mode; self.work=work; self.output=output;self.classes=classes
        self.rgb=np.array(Image.open(path/'source.png').convert('RGB'))
        self.bank={} if mode=='raw' else Field(path,detector).bank
        self.current=(self.bank['baseline'].astype(np.int32) if self.bank else np.zeros(self.rgb.shape[:2],np.int32))
        self.pending=None;self.finished=False;self.events=[];self.opened=False
        Image.fromarray(self.rgb).save(work/(self.key+'.png'));self.sync()
    def sync(self):
        np.save(self.work/(self.key+'-current.npy'),self.current,allow_pickle=False)
    def view(self, gallery=False, roi=(), donor=None):
        self.opened=True
        labels=self.pending if self.pending is not None else self.current
        layers=[('H&E',None),('pending IDs' if self.pending is not None else 'current IDs',labels)]
        if donor:layers.append((donor,self.bank[donor]))
        parts=[message(f'{self.key} field pixels WxH={self.rgb.shape[1]}x{self.rgb.shape[0]}. Visible ID map saved as {self.key}-current.npy for committed state.'),
               picture(self.rgb,layers,roi,True)]
        if gallery and self.bank:parts.append(picture(self.rgb,list(self.bank.items()),roi))
        return parts
    def apply(self,r):
        a=r['action'];roi=r.get('roi',[]);bounds(roi,self.current.shape)
        if self.finished:raise ValueError('Already sealed')
        if a=='view':return self.view(r.get('gallery',False),roi,r.get('donor'))
        if not self.opened:raise ValueError('View the source before editing')
        if a=='seal':
            mask=self.pending if self.pending is not None else self.current
            ids=set(np.unique(mask).tolist())-{0}
            default=r.get('default',0)
            mapping={i:default for i in ids};used=set()
            for group in r.get('groups',[]):
                for i in group['ids']:
                    if i not in ids or i in used:raise ValueError('Nonexistent or duplicate instance ID '+str(i))
                    mapping[i]=group['class'];used.add(i)
            if any(type(t)!=int or not 1<=t<=len(self.classes) for t in mapping.values()):
                raise ValueError('Assign all IDs a valid integer type')
            keys=np.array(sorted(ids),np.int32)
            np.savez_compressed(self.output/(self.key+'.npz'),labels=mask,ids=keys,types=np.array([mapping[i] for i in keys],np.int32))
            self.current=mask;self.pending=None;self.finished=True
            return [message(self.key+' sealed')]
        if a in ('accept','undo'):
            if self.pending is None:raise ValueError('No preview pending')
            if a=='accept':self.current=self.pending;self.sync()
            self.pending=None
            return self.view()
        if self.pending is not None:raise ValueError('Accept, undo or seal the preview first')
        if a in STRUCTURAL and self.bank:
            d=r.get('donor','baseline');donor=self.bank[d]
            if a=='whole':out=donor.copy()
            elif a=='splice':out,_=compose(self.current,donor,r.get('parents',[]),r.get('children',[]),roi)
            elif a=='watershed':
                points=r.get('points',[])+centers(donor,r.get('children',[]))
                out,_=seeded(self.rgb,self.current,r['parents'][0],points,roi)
            else:out,_=walker(self.rgb,self.current,r.get('parents',[]),r['points'],r['background'],roi)
        elif a in DIRECT and self.mode!='tools':
            if a=='paint':out=pixel_edit(self.current,roi,strokes=r['strokes'])
            else:
                name=r['file']
                if Path(name).name!=name or not name.endswith('.npy'):raise ValueError('Supply a local .npy basename')
                src=(self.work/name).resolve()
                if src.parent!=self.work.resolve():raise ValueError('Use an array inside the blind workspace')
                arr=np.load(src,allow_pickle=False)
                out=pixel_edit(self.current,roi,array=arr)
                ref=self.key+f'-edit-{len(self.events):03}.npy'
                np.save(self.output/ref,arr,allow_pickle=False)
                r['archived_array']=ref
        else:raise ValueError('Action unavailable in '+self.mode)
        self.pending=checked_map(out,self.current.shape)
        return self.view(roi=roi)

class Review:
    def __init__(self,paths,mode,detector,work,output,classes,individual):
        self.fields={p.name:Canvas(p,mode,detector,work,output,classes) for p in paths}
        self.individual=individual
    def active(self):return [k for k,f in self.fields.items() if not f.finished]
    def handle(self,decisions):
        parts=[]
        if not decisions:
            keys=self.active()
            for k in keys[:1] if self.individual else keys:
                parts.extend(self.fields[k].view(gallery=True))
            return parts or [message('COMPLETE')]
        for r in decisions:
            try:
                k=r['field']
                if self.individual and self.active()[:1]!=[k]:raise ValueError('Review current field first')
                f=self.fields[k];parts.extend(f.apply(r));f.events.append(r.copy())
            except (ValueError,KeyError,TypeError,IndexError,OSError) as e:
                parts.append(message('Field action failed: '+str(e)))
        keys=self.active()
        parts.append(message(('Remaining: '+', '.join(keys)+'. To open next views submit [].') if keys else 'COMPLETE'))
        return parts
