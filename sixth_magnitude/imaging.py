"""Source-bound native pyramid tiles and reconstructible multiscale evidence."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
import hashlib
from io import BytesIO
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .readers import open_reader, choose_stored_level
from .store import atomic_json, digest, read, utc, validate_geometry

RENDER_VERSION = "sixth-magnitude-extent-3"


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    with path.open("rb") as stream:
        header = hashlib.sha256(stream.read(65536)).hexdigest()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "header_64k_sha256": header}


def source_key(source):
    fields = ["source_asset_id", "size_bytes", "mtime_ns", "header_64k_sha256", "width", "height", "mpp_x", "mpp_y"]
    fields += [k for k in ("reader", "coordinate_unit", "calibration_origin") if k in source]
    return digest({k: source[k] for k in fields})[:24]


def normalize_source(source):
    source = dict(source)
    path = Path(source["source"]).resolve()
    found = fingerprint(path)
    for key, value in found.items():
        if key in source and source[key] != value:
            raise ValueError(f"Source fingerprint mismatch: {key}")
    source.update(found, source=str(path), source_full_file_hash_performed=False)
    required = ("width", "height", "mpp_x", "mpp_y", "level_dimensions", "downsamples")
    if any(key not in source for key in required):
        with open_reader(source) as reader:
            source.update(width=reader.dimensions[0], height=reader.dimensions[1], mpp_x=reader.mpp_x,
                mpp_y=reader.mpp_y, level_dimensions=reader.level_dimensions, downsamples=reader.level_downsamples,
                calibration_origin=reader.calibration_origin)
    source.setdefault("source_asset_id", path.stem)
    source.setdefault("coordinate_unit", "micrometre" if source["mpp_x"] is not None else "pixel")
    source["source_key"] = source_key(source)
    return source


def crop(reader, bbox, long_edge=1024):
    x0, y0, x1, y1 = bbox
    w_um, h_um = (x1-x0)*(reader.mpp_x or 1), (y1-y0)*(reader.mpp_y or 1)
    size = (max(1, round(long_edge*w_um/max(w_um,h_um))), max(1, round(long_edge*h_um/max(w_um,h_um))))
    level = choose_stored_level(reader, min(w_um/size[0], h_um/size[1]))
    ds = reader.level_downsamples[level]
    extent = [v/ds for v in bbox]
    ix0, iy0, ix1, iy1 = math.floor(extent[0])-2, math.floor(extent[1])-2, math.ceil(extent[2])+2, math.ceil(extent[3])+2
    if (ix1-ix0)*(iy1-iy0) > 25_000_000:
        raise ValueError("Requested decode too large; use a smaller field")
    pixels = reader.read_rgba(level, ix0, iy0, ix1-ix0, iy1-iy0)
    image = Image.fromarray(pixels[..., :3]).transform(size, Image.Transform.EXTENT,
        (extent[0]-ix0,extent[1]-iy0,extent[2]-ix0,extent[3]-iy0), Image.Resampling.BICUBIC, fillcolor="white")
    width, height = reader.dimensions
    # Pixel centers define exact validity, including any out-of-slide context.
    xs = x0+(np.arange(size[0])+.5)*(x1-x0)/size[0]
    ys = y0+(np.arange(size[1])+.5)*(y1-y0)/size[1]
    mask = ((ys[:,None]>=0)&(ys[:,None]<height)&(xs[None,:]>=0)&(xs[None,:]<width)).astype(np.uint8)*255
    meta = {"desired_level0_bbox": bbox, "decoded_level0_bbox": [ix0*ds,iy0*ds,ix1*ds,iy1*ds],
        "source_level": level, "source_downsample": ds, "output_dimensions": list(size),
        "output_mpp_x": w_um/size[0] if reader.mpp_x else None, "output_mpp_y": h_um/size[1] if reader.mpp_y else None,
        "coordinate_unit": "micrometre" if reader.mpp_x else "pixel",
        "output_source_px_x": (x1-x0)/size[0], "output_source_px_y": (y1-y0)/size[1],
        "image_to_level0_affine": [(x1-x0)/size[0],0,x0,0,(y1-y0)/size[1],y0],
        "resampling": "Pillow.BICUBIC_EXTENT", "boundary_padded": bool((mask==0).any()),
        "valid_fraction": float((mask>0).mean())}
    return image, Image.fromarray(mask), meta


def geometry_mask(geometry, bbox, size):
    """Evaluate membership at output pixel centers, in reader coordinates."""
    xs=bbox[0]+(np.arange(size[0])+.5)*(bbox[2]-bbox[0])/size[0]
    ys=bbox[1]+(np.arange(size[1])+.5)*(bbox[3]-bbox[1])/size[1]
    x,y=xs[None,:],ys[:,None]
    if geometry["type"]=="rectangle":
        x0,y0,x1,y1=geometry["bbox_level0"]
        return (x>=x0)&(x<x1)&(y>=y0)&(y<y1)
    inside=np.zeros((size[1],size[0]),dtype=bool)
    points=geometry["points"]
    for (ax,ay),(bx,by) in zip(points,points[1:]+points[:1]):
        if ay!=by:
            inside^=((ay>y)!=(by>y))&(x<(bx-ax)*(y-ay)/(by-ay)+ax)
    return inside


class Images:
    def __init__(self, store):
        self.store = store
        self.readers = OrderedDict()

    def reader(self, case):
        source = self.store.source(case)
        stat = Path(source["source"]).stat()
        if stat.st_size != source["size_bytes"] or stat.st_mtime_ns != source["mtime_ns"]:
            raise ValueError("Source file changed; re-register it before viewing")
        key = source["source_key"]
        if key not in self.readers:
            if fingerprint(source["source"]) != {k: source[k] for k in ("size_bytes", "mtime_ns", "header_64k_sha256")}:
                raise ValueError("Source fingerprint changed")
            reader = open_reader(source)
            valid = (list(reader.dimensions)==[source["width"],source["height"]]
                     and [list(x) for x in reader.level_dimensions]==source["level_dimensions"]
                     and reader.level_downsamples==source["downsamples"]
                     and reader.mpp_x==source["mpp_x"] and reader.mpp_y==source["mpp_y"])
            if not valid:
                reader.close()
                raise ValueError("Native reader metadata does not match source manifest")
            self.readers[key] = reader
            if len(self.readers)>2:
                _, old = self.readers.popitem(last=False)
                old.close()
        self.readers.move_to_end(key)
        return self.readers[key]

    def close(self):
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()

    def tile(self, case, key, level, x, y):
        self.reader(case)
        if key != self.store.source(case)["source_key"]:
            raise ValueError("Tile source key mismatch")
        return self._tile(case, key, level, x, y)

    @lru_cache(maxsize=192)
    def _tile(self, case, key, level, x, y):
        reader = self.reader(case)
        if not 0 <= level < len(reader.level_dimensions) or x<0 or y<0:
            raise ValueError("Invalid pyramid tile")
        w,h = reader.level_dimensions[level]
        if x*512>=w or y*512>=h:
            raise ValueError("Tile outside source")
        image = Image.fromarray(reader.read_rgba(level,x*512,y*512,min(512,w-x*512),min(512,h-y*512))).convert("RGB")
        stream = BytesIO()
        image.save(stream, "PNG")
        return stream.getvalue()

    def prepare(self, case, geometry):
        source = self.store.source(case)
        geometry = validate_geometry(geometry, source)
        reader = self.reader(case)
        eid = digest({"source_key": source["source_key"], "geometry": geometry, "render": RENDER_VERSION})[:32]
        directory = self.store.root / "evidence" / eid
        path = directory / "metadata.json"
        if path.exists():
            data = read(path)
            if data["source_key"] != source["source_key"] or data["geometry"] != geometry:
                raise ValueError("Evidence identity mismatch")
            if all((directory/v["file"]).is_file() and hashlib.sha256((directory/v["file"]).read_bytes()).hexdigest()==v["sha256"]
                   for v in data["files"].values()):
                return data
            raise ValueError("Evidence file damaged; preserve and regenerate in a new run")
        directory.mkdir(parents=True,exist_ok=True)
        bbox = geometry["bbox_level0"]
        cx,cy = (bbox[0]+bbox[2])/2,(bbox[1]+bbox[3])/2
        side = geometry.get("context_side_px", geometry.get("context_side_um"))
        ctx = [cx-side/(source["mpp_x"] or 1)/2,cy-side/(source["mpp_y"] or 1)/2,cx+side/(source["mpp_x"] or 1)/2,cy+side/(source["mpp_y"] or 1)/2]
        data = {"evidence_id": eid, "source_key": source["source_key"], "source_asset_id": source["source_asset_id"],
            "geometry": geometry, "render_version": RENDER_VERSION, "created_utc": utc(), "views": {}, "files": {},
            "context_label_support": "context_window_minus_focus_geometry", "visual_state": "pending"}
        for kind, bounds in (("focus",bbox),("context",ctx)):
            image,mask,meta = crop(reader,bounds)
            data["views"][kind] = meta
            for name,pixels in ((kind,image),(kind+"_valid_mask",mask)):
                file = directory / (name+".png")
                pixels.save(file)
                data["files"][name] = {"file": file.name,"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
            # Overlay derives from this exact geometry; label text stays in the UI.
            overlay=image.copy()
            draw=ImageDraw.Draw(overlay)
            a,_,c,_,e,f=meta["image_to_level0_affine"]
            points=geometry["points"] or [[bbox[0],bbox[1]],[bbox[2],bbox[1]],[bbox[2],bbox[3]],[bbox[0],bbox[3]]]
            mapped=[((x-c)/a,(y-f)/e) for x,y in points]
            draw.line(mapped+[mapped[0]], fill="#24ddcd", width=3)
            file=directory/(kind+"_overlay.png")
            overlay.save(file)
            data["files"][kind+"_overlay"]={"file":file.name,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}
            # Explicit support masks exclude background padding and the focus from context.
            inside=geometry_mask(geometry,bounds,image.size)
            support=Image.fromarray(((np.asarray(mask)>0)&(~inside if kind=="context" else inside)).astype(np.uint8)*255)
            file=directory/(kind+"_support_mask.png")
            support.save(file)
            data["files"][kind+"_support_mask"]={"file":file.name,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}
        atomic_json(path,data,exclusive=True)
        return data
