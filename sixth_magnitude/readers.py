"""Bounded raster and optional native pyramid readers. No inferred calibration."""
import math
from pathlib import Path
import numpy as np
from PIL import Image

def calibration(x, y):
    if x is None and y is None:
        return None, None
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in (x,y)):
        raise ValueError('Calibration requires two positive finite micrometres-per-pixel values')
    return float(x), float(y)

def clipped(dimensions, x, y, width, height, decoder):
    if width<=0 or height<=0 or width*height>25_000_000:
        raise ValueError('Decode request exceeds the bounded image size')
    canvas=np.full((height,width,4),255,dtype=np.uint8)
    x0,y0=max(0,x),max(0,y)
    x1,y1=min(dimensions[0],x+width),min(dimensions[1],y+height)
    if x1>x0 and y1>y0:
        pixels=np.asarray(decoder(x0,y0,x1-x0,y1-y0),dtype=np.uint8)
        if pixels.shape!=(y1-y0,x1-x0,4):
            raise ValueError('Unexpected decoder dimensions')
        canvas[y0-y:y1-y,x0-x:x1-x]=pixels
    return canvas

class RasterReader:
    def __init__(self,path,mpp_x=None,mpp_y=None):
        self.path=Path(path)
        with Image.open(path) as source:
            if source.width*source.height>40_000_000:
                raise ValueError('Raster input exceeds 40 million pixels; use a native WSI reader')
            if source.getexif().get(274,1)!=1:
                raise ValueError('Normalize EXIF orientation before registration; coordinates use stored pixels')
            rgba=source.convert('RGBA')
            background=Image.new('RGBA',rgba.size,'white')
            self.image=Image.alpha_composite(background,rgba)
        self.dimensions=self.image.size
        self.mpp_x,self.mpp_y=calibration(mpp_x,mpp_y)
        self.calibration_origin='user-provided' if self.mpp_x is not None else 'unavailable'
        self.level_downsamples=[1.0]
        self.level_dimensions=[self.dimensions]
        self.levels={0:self.image}
        while max(self.level_dimensions[-1])>512:
            ds=self.level_downsamples[-1]*2
            self.level_downsamples.append(ds)
            self.level_dimensions.append(tuple(max(1,math.ceil(v/ds)) for v in self.dimensions))
    def read_rgba(self,level,x,y,width,height):
        if level not in self.levels:
            self.levels[level]=self.image.resize(self.level_dimensions[level],Image.Resampling.LANCZOS)
        return clipped(self.level_dimensions[level],x,y,width,height,
            lambda a,b,w,h:self.levels[level].crop((a,b,a+w,b+h)))
    def close(self):
        for image in self.levels.values(): image.close()
        self.levels.clear()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()

class NativeReader:
    def __init__(self,path,backend,mpp_x=None,mpp_y=None):
        self.path=Path(path); self.backend=backend
        if backend=='isyntax':
            try:
                from isyntax import ISyntax
            except ImportError as exc:
                raise ValueError('Install a compatible pyisyntax-fastio reader for iSyntax input') from exc
            self.slide=ISyntax.open(self.path,cache_size=256)
            found=(float(self.slide.mpp_x),float(self.slide.mpp_y))
        else:
            try:
                import openslide
            except ImportError as exc:
                raise ValueError('Install the openslide extra and OpenSlide native library') from exc
            self.slide=openslide.OpenSlide(str(self.path))
            px=self.slide.properties.get(openslide.PROPERTY_NAME_MPP_X)
            py=self.slide.properties.get(openslide.PROPERTY_NAME_MPP_Y)
            found=(float(px),float(py)) if px and py else (None,None)
        try:
            override=calibration(mpp_x,mpp_y)
            if found[0] is not None:
                self.mpp_x,self.mpp_y=calibration(*found)
                if override[0] is not None and not all(math.isclose(a,b,rel_tol=1e-6) for a,b in zip(found,override)):
                    raise ValueError('Supplied calibration differs from native metadata')
                self.calibration_origin='source-metadata'
            else:
                self.mpp_x,self.mpp_y=override
                self.calibration_origin='user-provided' if override[0] is not None else 'unavailable'
            self.dimensions=tuple(map(int,self.slide.dimensions))
            self.level_dimensions=[tuple(map(int,v)) for v in self.slide.level_dimensions]
            self.level_downsamples=list(map(float,self.slide.level_downsamples))
        except Exception:
            self.slide.close(); raise
    def read_rgba(self,level,x,y,width,height):
        def decode(a,b,w,h):
            if self.backend=='isyntax':
                return self.slide.read_region(a,b,w,h,level=level)
            ds=self.level_downsamples[level]
            image=self.slide.read_region((round(a*ds),round(b*ds)),level,(w,h)).convert('RGBA')
            return Image.alpha_composite(Image.new('RGBA',image.size,'white'),image)
        return clipped(self.level_dimensions[level],x,y,width,height,decode)
    def close(self): self.slide.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()

def open_reader(source):
    backend=source.get('reader','isyntax')
    if backend=='raster':
        return RasterReader(source['source'],source.get('mpp_x'),source.get('mpp_y'))
    if backend not in ('isyntax','openslide'):
        raise ValueError('Unknown reader')
    return NativeReader(source['source'],backend,source.get('mpp_x'),source.get('mpp_y'))

def choose_stored_level(reader,target_mpp):
    if target_mpp<=0: raise ValueError('Resolution must be positive')
    base=math.sqrt((reader.mpp_x or 1)*(reader.mpp_y or 1))
    return min(range(len(reader.level_downsamples)),
        key=lambda i:abs(math.log(base*reader.level_downsamples[i]/target_mpp)))
