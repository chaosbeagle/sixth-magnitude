"""Coordinate-preserving edits to native-resolution nuclear instance maps."""
import numpy as np
from scipy import ndimage as ndi
from skimage.filters import sobel
from skimage.segmentation import watershed, random_walker
from .nuclei import optical_density


def bounds(roi, shape):
    h, w = shape
    if not roi:
        return (0, 0, w, h)
    if len(roi) != 4 or any(type(v) is not int for v in roi):
        raise ValueError("ROI needs four integer field-pixel coordinates [x,y,width,height]")
    x, y, width, height = roi
    if min(x, y) < 0 or min(width, height) < 1 or x+width > w or y+height > h:
        raise ValueError("ROI outside field")
    return x, y, width, height


def point_indices(points, shape):
    for xy in points:
        if len(xy) != 2 or any(type(v) is not int for v in xy):
            raise ValueError("Points must be distinct integer [x,y] field pixels")
        x, y = xy
        bounds([x, y, 1, 1], shape)
    if len(set(map(tuple, points))) != len(points):
        raise ValueError("Duplicate seed")
    return tuple(np.array(points, dtype=int).reshape(-1, 2).T[::-1])


def selected(labels, ids):
    if len(set(ids)) != len(ids) or any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError("Use distinct positive object IDs")
    absent = set(ids) - set(np.unique(labels))
    if absent:
        raise ValueError(f"Absent IDs {sorted(absent)}")
    return np.isin(labels, ids)


def compose(current, donor, parents, children, roi):
    """Insert whole donor instances. Conflicts require an explicit change of scope."""
    if current.shape != donor.shape or not (parents or children):
        raise ValueError("Supply aligned maps and at least one object")
    remove, add = selected(current, parents), selected(donor, children)
    x, y, w, h = bounds(roi, current.shape)
    scope = remove | add
    if int(scope.sum()) != int(scope[y:y+h, x:x+w].sum()):
        raise ValueError("Complete selected objects cross the ROI; expand the rectangle")
    conflicts = np.unique(current[add & ~remove])
    if np.any(conflicts > 0):
        raise ValueError(f"Overlap with retained IDs {conflicts[conflicts > 0].tolist()}; explicitly include them or cancel")
    out = current.astype(np.int32, copy=True)
    out[remove] = 0
    for i, child in enumerate(children, int(current.max())+1):
        out[donor == child] = i
    return out, scope


def centers(labels, ids):
    """An interior peak per donor instance, including concave masks."""
    selected(labels, ids)
    result = []
    for i in ids:
        mask = labels == i
        distance = ndi.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
        y, x = np.unravel_index(distance.argmax(), mask.shape)
        result.append([int(x), int(y)])
    return result


def seeded(rgb, current, parent, points, roi):
    region = selected(current, [parent])
    ij = point_indices(points, current.shape)
    if len(points) < 2 or not region[ij].all():
        raise ValueError("Use at least two distinct seed points inside the selected parent")
    markers = np.zeros_like(current)
    markers[ij] = np.arange(1, len(points)+1)
    gradient = sobel(ndi.gaussian_filter(optical_density(rgb), 1))
    donor = watershed(gradient, markers, mask=region)
    if not np.array_equal(donor > 0, region):
        raise ValueError("Include a seed for every disconnected part of this parent")
    return compose(current, donor, [parent], list(range(1, len(points)+1)), roi)


def walker(rgb, current, parents, foreground, background, roi):
    if not roi or not foreground or not background:
        raise ValueError("Random walker needs a bounded ROI and both foreground/background points")
    x, y, w, h = bounds(roi, current.shape)
    ij = point_indices(foreground + background, current.shape)
    yy, xx = ij[0]-y, ij[1]-x
    if min(yy) < 0 or min(xx) < 0 or max(yy) >= h or max(xx) >= w:
        raise ValueError("All seeds must lie inside the ROI")
    markers = np.zeros((h, w), dtype=int)
    markers[yy, xx] = [1]*len(foreground) + [2]*len(background)
    local = random_walker(optical_density(rgb[y:y+h, x:x+w]), markers, mode="cg_j")
    foreground_mask = local == 1
    if ((y and foreground_mask[0].any()) or (y+h < current.shape[0] and foreground_mask[-1].any()) or
            (x and foreground_mask[:, 0].any()) or (x+w < current.shape[1] and foreground_mask[:, -1].any())):
        raise ValueError("New foreground reaches the ROI edge; expand ROI or add background seeds")
    donor = np.zeros_like(current)
    donor[y:y+h, x:x+w], count = ndi.label(foreground_mask)
    return compose(current, donor, parents, list(range(1, count+1)), roi)


def delta(before, after, scope):
    if before.shape != after.shape or np.any(before[~scope] != after[~scope]):
        raise ValueError("Edit changed IDs outside the authorized scope")
    both = (before > 0) & (after > 0)
    pairs = np.unique(np.stack((before[both], after[both]), axis=1), axis=0)
    old, new = [np.unique(pairs[:, i], return_counts=True) for i in (0, 1)]
    return dict(added=len(set(np.unique(after)) - {0} - set(new[0])),
                removed=len(set(np.unique(before)) - {0} - set(old[0])),
                split=int((old[1] > 1).sum()), merged=int((new[1] > 1).sum()),
                gained_pixels=int(((after > 0) & (before == 0)).sum()),
                lost_pixels=int(((before > 0) & (after == 0)).sum()),
                outside_unchanged=int((~scope).sum()))
