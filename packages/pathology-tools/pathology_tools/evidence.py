"""Model-visible, label-free evidence for field triage and an individual proposed edit."""
from hashlib import sha256
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage.segmentation import find_boundaries

from .nuclei import NAMES, segmentation_qc
from .packets import read


def field_summary(field):
    info = read(field/"field.json")
    if "baseline_qc" in info:
        qc = info["baseline_qc"]
    else:
        with Image.open(field/"source.png") as im, np.load(field/"baseline.npz") as base:
            qc = segmentation_qc(np.asarray(im.convert("RGB")), base["labels"])
    return {"color": info.get("color", {}).get("status", "unverified"),
            "classifier_color_basis": info.get("classifier_color_basis", "unreported"),
            "tools": list(info["tools"]), **qc}


def _center_and_extent(mask):
    yy, xx = np.where(mask)
    return (round(float(xx.mean())), round(float(yy.mean()))), max(np.ptp(xx), np.ptp(yy)) + 1


def _crop(image, xy, side):
    left, top = xy[0] - side//2, xy[1] - side//2
    canvas = Image.new("RGB", (side, side), "white")
    box = (max(0, left), max(0, top), min(image.width, left+side), min(image.height, top+side))
    canvas.paste(image.crop(box), (box[0]-left, box[1]-top))
    return canvas.resize((144, 144), Image.Resampling.NEAREST)


def _outline(rgb, labels):
    result = np.array(rgb)
    result[find_boundaries(labels, mode="inner")] = [0, 226, 238]
    return Image.fromarray(result)


def unique_alternatives(field, tried):
    """Deduplicate partitions by pixel membership, including label maps with permuted IDs."""
    seen, remaining = set(), []
    tools = list(read(field/"field.json")["tools"])
    for name in dict.fromkeys([*tried, *tools]):
        with np.load(field/(name+".npz")) as f:
            labels = f["labels"]
        ids, first = np.unique(labels, return_index=True)
        ordered = ids[np.argsort(first)]
        nonzero = ordered[ordered != 0]
        lut = np.zeros(int(labels.max())+1, dtype=np.int32)
        lut[nonzero] = np.arange(1, len(nonzero)+1)
        key = sha256(lut[labels].tobytes()).digest()
        if key not in seen and name not in tried:
            remaining.append(name)
        seen.add(key)
    return remaining


def alternative_sheet(field, names):
    with Image.open(field/"source.png") as im:
        raw = im.convert("RGB")
    width = min(raw.width, 360)
    height = max(1, round(raw.height*width/raw.width))
    if height > 360:
        width, height = max(1, round(width*360/height)), 360
    cell_w = max(190, width)
    canvas = Image.new("RGB", (3*cell_w, ((len(names)+3)//3)*(height+28)), "#18242d")
    pen = ImageDraw.Draw(canvas)
    facts = {}
    for i, name in enumerate([None, *names]):
        if name is None:
            tile, caption = raw, "Clean source"
        else:
            with np.load(field/(name+".npz")) as f:
                labels = f["labels"]
            tile = _outline(raw, labels)
            facts[name] = {"objects": int(labels.max()), "foreground_fraction": round(float((labels>0).mean()), 3)}
            caption = f"{name} | {labels.max()} objects"
        x, y = i % 3 * cell_w, (i//3)*(height+28)
        pen.text((x+4,y+4), caption, font=ImageFont.load_default(size=13))
        canvas.paste(tile.resize((width,height),Image.Resampling.LANCZOS), (x,y+26))
    return canvas, facts


def predicted_peer(types, area, kind, excluded):
    peers = np.flatnonzero((types == kind) & (area > 0)) if kind else np.array([], dtype=int)
    peers = peers[~np.isin(peers, [0, *excluded])]
    if not peers.size:
        return None
    return int(peers[np.argmin(abs(area[peers] - np.median(area[peers])))])


def edit_evidence(field, key, tool, edits, *, paired=False):
    """Crop identical pixel scales, compare object ownership in A/B, and identify predicted peers."""
    info = read(field/"field.json")
    with Image.open(field/"source.png") as im:
        raw = im.convert("RGB")
    with np.load(field/"baseline.npz") as base:
        baseline = base["labels"]
    with np.load(field/(tool+".npz")) as selected:
        labels, types = selected["labels"], selected["types"]
        logits = selected["logits"] if "logits" in selected else None
        classes = selected["classes"].tolist() if logits is not None else []
    area = np.bincount(labels.ravel(), minlength=len(types))
    base_area = np.bincount(baseline.ravel())
    baseline_view, chosen_view = _outline(raw, baseline), _outline(raw, labels)
    canvas = Image.new("RGB", (768 if paired else 616, 30+200*len(edits)), "#18242d")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=13)
    title = ("Clean source / A / retained boundary / predicted original-class peer / proposed-class peer"
             if paired else "Clean source / baseline A / retained boundary / same-scale predicted peer")
    draw.text((8, 6), key+" | "+title, font=font)
    facts = {}
    for i, edit in enumerate(edits):
        pid, to = edit["id"], NAMES.index(edit["cell_type"])
        mask = labels == pid
        center, extent = _center_and_extent(mask)
        voters = np.bincount(baseline[mask], minlength=len(base_area))
        voters[0] = 0
        parent = int(voters.argmax()) if voters.any() else None
        excluded = [e["id"] for e in edits] if paired else [pid]
        peer = predicted_peer(types, area, to, excluded)
        peer_xy, peer_extent = _center_and_extent(labels == peer) if peer is not None else (None, 0)
        old = int(types[pid])
        old_peer = predicted_peer(types, area, old, excluded) if paired else None
        old_xy, old_extent = _center_and_extent(labels == old_peer) if old_peer is not None else (None, 0)
        side = int(max(48, extent+16, peer_extent+16, old_extent+16))
        margin = None
        if logits is not None and old in classes and to in classes:
            margin = round(float(logits[pid, classes.index(old)] - logits[pid, classes.index(to)]), 3)
        siblings = {}
        if parent is not None:
            ids, counts = np.unique(labels[baseline == parent], return_counts=True)
            siblings = {str(int(j)): int(n) for j, n in zip(ids, counts) if j > 0 and j != pid}
        mpp = info.get("mpp_xy")
        facts[str(pid)] = {
            "from": NAMES[old] if old else "untyped", "to": edit["cell_type"],
            "source_xy": [int(a+b) for a, b in zip(info["origin_xy"], center)],
            "area_px": int(area[pid]), "area_um2": round(float(area[pid]*mpp[0]*mpp[1]), 2) if mpp else None,
            "baseline_id": parent,
            "baseline_iou": round(float(voters[parent]/(base_area[parent]+area[pid]-voters[parent])), 3) if parent else None,
            "other_retained_ids_in_baseline_px": siblings,
            "peer_id": peer, "peer_area_px": int(area[peer]) if peer is not None else None,
            "from_minus_to_logit": margin, "crop_width_px": side}
        if paired:
            facts[str(pid)].update(original_peer_id=old_peer,
                                   original_peer_area_px=int(area[old_peer]) if old_peer is not None else None)
        y = 32+i*200
        draw.text((8, y), f"#{pid}   {facts[str(pid)]['from']} to {edit['cell_type']}   |   {area[pid]} px   |   window {side} px", font=font)
        views = [(raw, center), (baseline_view, center), (chosen_view, center)]
        if paired:
            views.append((raw, old_xy))
        views.append((raw, peer_xy))
        for col, (view, xy) in enumerate(views):
            if xy is not None:
                canvas.paste(_crop(view, xy, side), (8+152*col, y+23))
        peer_text = f"peer #{peer}   {area[peer]} px" if peer is not None else "no predicted peer"
        captions = ["raw", "A baseline", "retained"]
        if paired:
            captions.append(f"from #{old_peer}  {area[old_peer]} px" if old_peer is not None else "no original peer")
        captions.append(peer_text)
        for col, name in enumerate(captions):
            draw.text((8+152*col, y+169), name, font=font)
    return canvas, facts
