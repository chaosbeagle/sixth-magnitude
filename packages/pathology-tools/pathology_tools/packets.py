"""Reopenable, immutable field packets used by either host."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage.segmentation import find_boundaries
from .nuclei import classification, segment, segmentation_qc, TOOLS, LOCAL_TOOL, COLORS
from .source import align_rgb

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, value):
    path = Path(path)
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as f:
        name = f.name
        try:
            f.write(data)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def read(path):
    return json.loads(Path(path).read_text())

def prepare(rgb, origin, asset, output, classifier=None, source_key=None, *,
            reference=None, native=False, mpp=None, legacy=False):
    output = Path(output)
    if output.exists():
        raise ValueError(f"Choose an unused output directory: {output}")
    if (rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1
            or max(rgb.shape[:2]) > 1024 or rgb.dtype != np.uint8):
        raise ValueError("Expected an 8-bit RGB field, each side at most 1024 native pixels")
    if len(origin) != 2 or any(not isinstance(v, (int, np.integer)) or v < 0 for v in origin):
        raise ValueError("Expected a non-negative level-zero origin")
    origin = [int(v) for v in origin]
    if mpp is not None and (len(mpp) != 2 or not all(np.isfinite(v) and v > 0 for v in mpp)):
        raise ValueError("Expected two positive micrometres-per-pixel values")
    if mpp is not None:
        mpp = [float(v) for v in mpp]
    if legacy and reference is not None:
        raise ValueError("A frozen legacy packet cannot apply reference color corrections")
    rgb, color = align_rgb(rgb, origin, reference, native)
    weights = read(classifier) if classifier else None
    basis = weights.get("color_basis", "unreported") if weights else "untyped"
    if basis == "reference_rgb" and color["status"] == "unverified":
        raise ValueError("This classifier requires native or reference-verified RGB")
    tools = TOOLS if legacy else {**TOOLS, LOCAL_TOOL: {"signal": "local optical density", "watershed": "image gradient"}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        root = Path(tmp) / "field"
        root.mkdir()
        Image.fromarray(rgb).save(root / "source.png")
        for tool in tools:
            labels = segment(rgb, tool)
            extra = {}
            if weights:
                types, logits = classification(rgb, labels, weights)
                if not legacy:
                    extra = {"logits": logits, "classes": weights["classes"]}
            else:
                types = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
            np.savez_compressed(root / f"{tool}.npz", labels=labels, types=types, **extra)
            if not legacy and tool == "baseline":
                qc = segmentation_qc(rgb, labels)
        files = {p.name: sha(p) for p in sorted(root.iterdir())}
        manifest = {"asset": asset, "origin_xy": list(origin),
             "shape_yx": list(rgb.shape[:2]), "coordinate_frame": "source_pixels",
             "source_key": source_key, "tools": tools, "files": files}
        if not legacy:
            manifest = {"packet_version": 2, **manifest, "color": color, "baseline_qc": qc,
                        "mpp_xy": mpp, "classifier_color_basis": basis}
        manifest["classifier_sha256"] = sha(classifier) if classifier else None
        dump(root / "field.json", manifest)
        os.rename(root, output)
    return output

def panel(field, title, tool=None):
    with Image.open(field / "source.png") as im:
        rgb = np.asarray(im.convert("RGB")).copy()
    centers = []
    if tool is not None:
        with np.load(field / f"{tool}.npz") as pred:
            labels, types = pred["labels"], pred["types"]
        edge = find_boundaries(labels, mode="inner")
        palette = np.array([ImageColor.getrgb(c) for c in COLORS], dtype=np.uint8)
        rgb[edge] = palette[types[labels[edge]]]
        ids = np.unique(labels[labels > 0])
        if len(ids):
            centers = zip(ids, ndi.center_of_mass(labels > 0, labels, ids))
    base = Image.fromarray(rgb).resize((rgb.shape[1]*2, rgb.shape[0]*2), Image.Resampling.NEAREST)
    view = Image.new("RGB", (base.width, base.height + 26), "#20242a")
    view.paste(base, (0, 26))
    draw = ImageDraw.Draw(view)
    draw.text((5, 5), title, fill="white")
    font = ImageFont.load_default(size=11)
    for idx, (y, x) in centers:
        draw.text((round(2*x), 26+round(2*y)), str(idx), font=font, anchor="mm",
                  fill="white", stroke_width=1, stroke_fill="black")
    return view

def sheet(field, asset, proposal=None):
    field = Path(field)
    parts = [panel(field, asset + " | H&E"), panel(field, "A | baseline", "baseline")]
    if proposal is not None:
        parts.append(panel(field, "B | " + proposal, proposal))
    out = Image.new("RGB", (sum(p.width for p in parts), parts[0].height))
    left = 0
    for p in parts:
        out.paste(p, (left, 0))
        left += p.width
    return out
