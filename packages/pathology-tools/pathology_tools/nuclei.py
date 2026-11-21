"""Deterministic H&E candidate masks and object measurements. Sizes are in pixels."""
import numpy as np
from scipy import ndimage as ndi
from skimage.color import rgb2hed
from skimage.feature import peak_local_max
from skimage.filters import sobel, threshold_otsu
from skimage.measure import regionprops
from skimage.segmentation import relabel_sequential, watershed

# Threshold multiplier, spacing between seeds, minimum object area at native scale.
TOOLS = {"baseline": (1.0, 8, 24), "recover_faint": (0.76, 8, 24),
         "suppress_debris": (1.24, 8, 42), "split_touching": (1.0, 5, 24),
         "reduce_fragments": (1.0, 13, 24)}
NAMES = ("background", "epithelial", "lymphocyte", "macrophage", "neutrophil")
COLORS = ("white", "#00d6ef", "#ffe036", "#65fc75", "#ff774d")

# Keep the five original presets available for replay of version-one packets.
LOCAL_TOOL = "local_gradient"


def optical_density(rgb):
    return -np.log((rgb.astype(np.float64) + 1) / 256).mean(axis=2)


def segmentation_qc(rgb, labels):
    """Observable quality signals. Thresholds route a second look and never declare a mask correct."""
    h = ndi.gaussian_filter(rgb2hed(rgb)[:, :, 0], 1.0)
    strong = h > threshold_otsu(h) if np.ptp(h) > 1e-8 else np.zeros(h.shape, bool)
    filled = ndi.binary_fill_holes(strong)
    cc, _ = ndi.label(filled)
    largest = np.bincount(cc.ravel())[1:].max(initial=0) / labels.size
    added = (filled & ~strong).mean()
    od = ndi.gaussian_filter(optical_density(rgb), 1)
    dark = od > threshold_otsu(od) if np.ptp(od) > 1e-8 else np.zeros(h.shape, bool)
    missed = np.count_nonzero(dark & (labels == 0))
    fraction = missed / max(1, int(dark.sum()))
    dark_cc, _ = ndi.label(dark)
    areas = np.bincount(dark_cc.ravel())
    covered = np.bincount(dark_cc[labels > 0], minlength=len(areas))
    discrete = (areas >= TOOLS["baseline"][2]) & (areas <= 0.02*labels.size)
    discrete[0] = False
    uncovered_objects = int(np.count_nonzero(discrete & (covered < 0.25*areas)))
    flags = []
    if largest >= 0.35:
        flags.append("large_connected_foreground")
    if added >= 0.08:
        flags.append("extensive_hole_filling")
    if fraction >= 0.35 and (uncovered_objects >= 3 or (not labels.any() and uncovered_objects > 0)):
        flags.append("uncovered_dark_structures")
    return {"objects": int(labels.max()), "foreground_fraction": float((labels > 0).mean()),
            "largest_connected_fraction": float(largest), "filled_hole_fraction": float(added),
            "uncovered_dark_fraction": float(fraction), "uncovered_dark_objects": uncovered_objects, "flags": flags}


def segment(rgb, tool):
    if tool == LOCAL_TOOL:
        # Dark local contrast uses all three channels symmetrically. Place watershed ridges
        # on the image gradient, with no global stain threshold or unconditional hole filling.
        h = ndi.gaussian_filter(optical_density(rgb), 1)
        residual = h - ndi.gaussian_filter(h, 8)
        if np.ptp(residual) < 1e-8:
            return np.zeros(h.shape, dtype=np.int32)
        mask = residual > max(0.0, threshold_otsu(residual))
        elevation = sobel(h)
        spacing, area = 8, 24
    else:
        factor, spacing, area = TOOLS[tool]
        h = ndi.gaussian_filter(rgb2hed(rgb)[:, :, 0], 1.0)
        if np.ptp(h) < 1e-8:
            return np.zeros(h.shape, dtype=np.int32)
        mask = ndi.binary_fill_holes(h > threshold_otsu(h) * factor)
        elevation = None
    cc, count = ndi.label(mask)
    keep = np.bincount(cc.ravel(), minlength=count + 1) >= area
    keep[0] = False
    mask = keep[cc]
    distance = ndi.distance_transform_edt(mask)
    peaks = peak_local_max(distance, min_distance=spacing, labels=cc * mask,
                           exclude_border=False)
    seeds = np.zeros(mask.shape, dtype=np.int32)
    if len(peaks):
        seeds[tuple(peaks.T)] = np.arange(1, len(peaks) + 1)
    # Guarantee even a small component at the border has a seed.
    missing = np.setdiff1d(np.flatnonzero(keep), cc[seeds > 0])
    if len(missing):
        for i, loc in enumerate(ndi.maximum_position(distance, cc, missing), len(peaks) + 1):
            seeds[loc] = i
    labels = watershed(-distance if elevation is None else elevation, seeds, mask=mask)
    labels[np.bincount(labels.ravel())[labels] < area] = 0
    return np.asarray(relabel_sequential(labels)[0], dtype=np.int32)

def measurements(rgb, labels):
    stain = rgb2hed(rgb)
    ids, features = [], []
    for r in regionprops(labels):
        pixels = stain[r.slice][r.image]
        ids.append(r.label)
        features.append([np.log1p(r.area), r.eccentricity, r.extent, r.solidity,
                         np.log1p(r.axis_major_length), np.log1p(r.axis_minor_length),
                         *pixels[:, :2].mean(axis=0), *pixels[:, :2].std(axis=0)])
    return np.asarray(ids, dtype=int), np.asarray(features, dtype=float).reshape(-1, 10)

def classification(rgb, labels, weights):
    ids, x = measurements(rgb, labels)
    types = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    classes = np.asarray(weights["classes"], dtype=int)
    scores = np.zeros((len(types), len(classes)), dtype=np.float64)
    if len(x):
        z = (x - weights["mean"]) / weights["scale"]
        logits = z @ np.asarray(weights["coef"]).T + weights["intercept"]
        types[ids] = classes[np.argmax(logits, axis=1)]
        scores[ids] = logits
    return types, scores


def classify(rgb, labels, weights):
    return classification(rgb, labels, weights)[0]
