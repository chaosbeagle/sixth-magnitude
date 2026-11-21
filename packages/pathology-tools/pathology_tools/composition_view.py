"""Native field coordinates stay fixed across contact sheets and zooms."""
from base64 import b64encode
from io import BytesIO
import math
import numpy as np
from PIL import Image, ImageDraw
from skimage.segmentation import find_boundaries
from skimage.measure import regionprops
from .composition import bounds


def panel(rgb, labels, roi, name, ids=False):
    x,y,w,h = bounds(roi, rgb.shape[:2])
    raw = rgb[y:y+h,x:x+w].copy()
    if labels is not None:
        edge = find_boundaries(labels, mode='inner')[y:y+h,x:x+w]
        raw[edge] = (15,235,225)
    scale = min(3, 256/max(w,h))
    w2,h2 = max(1,round(w*scale)),max(1,round(h*scale))
    tile = Image.new('RGB',(w2,h2+21),'white')
    tile.paste(Image.fromarray(raw).resize((w2,h2),Image.Resampling.NEAREST),(0,21))
    draw = ImageDraw.Draw(tile)
    draw.text((3,3),name,fill='black')
    if ids and labels is not None:
        for obj in regionprops(labels[y:y+h,x:x+w]):
            yy,xx = obj.centroid
            draw.text((xx*scale,21+yy*scale),str(obj.label),anchor='mm',fill='white',stroke_width=1,stroke_fill='black')
    return tile


def sheet(rgb, layers, roi=(),ids=False):
    panels=[panel(rgb,lab,roi,name,ids) for name,lab in layers]
    w,h=panels[0].size
    cols=min(3,len(panels))
    canvas=Image.new('RGB',(w*cols,h*math.ceil(len(panels)/cols)),'#e3e3e3')
    for i,p in enumerate(panels):
        canvas.paste(p,((i%cols)*w,(i//cols)*h))
    return canvas


def encode(image):
    b=BytesIO()
    image.save(b,format='PNG')
    return {'type':'inputImage','imageUrl':'data:image/png;base64,'+b64encode(b.getvalue()).decode()}
