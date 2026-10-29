"""Typed region measurements. Observation-presence labels are never area masks.

Rasterisation uses physical pixel centres; smaller pattern territories override
coarser territories, equal-size conflicting territories remain unresolved.
"""
from __future__ import annotations

import math
import numpy as np
from scipy import ndimage

VERSION = '1.0.0'
GRID_UM = 64.0
EDGE_UM = 500.0
PATTERNS = ['lepidic', 'acinar', 'papillary', 'micropapillary', 'solid', 'complex_glandular']
CODES = {'L': 'lepidic', 'A': 'acinar', 'P': 'papillary', 'M': 'micropapillary',
         'S': 'solid', 'C': 'complex_glandular', '?': 'indeterminate',
         'T': 'tumour_envelope', 'X': 'excluded_region', 'B': 'unassessable_boundary'}
COLOURS = {'L': '#51bd91', 'A': '#64a9e9', 'P': '#c694e8', 'M': '#ffc75b',
           'S': '#ff7474', 'C': '#f195cf', '?': '#aaaeb8', 'T': '#12dfce',
           'X': '#566276', 'B': '#f7f7f7'}
FIELDS = ['high_grade_area_pct', 'largest_high_grade_component_pct',
          'edge_enrichment_pp', 'pattern_mixing_pct']


def validate_measurement(value, geometry):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'version', 'code', 'section_complete', 'boundary_assessable', 'applicability'}:
        raise ValueError('Measurement requires version, code, section_complete, boundary_assessable, applicability')
    if value['version'] != VERSION or value['code'] not in CODES:
        raise ValueError('Unknown measurement version/code')
    if any(type(value[k]) is not bool for k in ['section_complete', 'boundary_assessable']):
        raise ValueError('Measurement coverage flags must be boolean')
    if value['applicability'] not in ['not_assessed', 'nonmucinous_luad', 'not_applicable']:
        raise ValueError('Unknown morphology applicability')
    if geometry['role'] == 'sampling_window':
        raise ValueError('A sampling window is not a measurement territory')
    if value['code'] != 'T' and (value['section_complete'] or value['boundary_assessable'] or value['applicability'] != 'not_assessed'):
        raise ValueError('Only tumour envelopes declare coverage, applicability and boundary assessability')
    return dict(value)


def reviewed(annotation):
    return (annotation['visual']['state'] == 'matched'
            and annotation['visual'].get('content_hash') == annotation['content_hash']
            and annotation['review']['state'] == 'physician_reviewed'
            and annotation['review'].get('content_hash') == annotation['content_hash'])


def _inside(geometry, xs, ys):
    x, y = xs[None, :], ys[:, None]
    if geometry['type'] == 'rectangle':
        x0, y0, x1, y1 = geometry['bbox_level0']
        return (x >= x0) & (x < x1) & (y >= y0) & (y < y1)
    result = np.zeros((len(ys), len(xs)), dtype=bool)
    points = geometry['points']
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        if ay != by:
            result ^= ((ay > y) != (by > y)) & (x < (bx-ax)*(y-ay)/(by-ay)+ax)
    return result


def measure(state, source, purpose='annotation', grid_um=GRID_UM, edge_um=EDGE_UM, raster=False):
    if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
               for v in [grid_um, edge_um]):
        raise ValueError('Positive finite physical measurement scales required')
    annotations = [a for a in state['annotations'].values() if not a['deleted'] and a.get('measurement')]
    for a in annotations:
        validate_measurement(a['measurement'], a['geometry'])
    envelopes = [a for a in annotations if a['measurement']['code'] == 'T']
    result = dict(case_id=state['case_id'], case_revision=state['revision'], source_key=source['source_key'],
                  metric_version=VERSION, grid_um=grid_um, edge_um=edge_um, connectivity=4,
                  rasterisation='physical_cell_centres', overlap_rule='smaller_territory_overrides_equal_size_conflicts_unknown',
                  purpose=purpose, annotation_count=len(annotations), approved_count=sum(reviewed(a) for a in annotations),
                  status='no_tumour_envelope', section_complete=False, coverage_pct=None,
                  research_eligible=False, reviewed_content_hashes=[a['content_hash'] for a in annotations if reviewed(a)],
                  contributing_content_hashes=[a['content_hash'] for a in annotations], next_unresolved_bbox_level0=None)
    result.update({k: None for k in FIELDS})
    result['metric_reasons'] = {k: 'no_tumour_envelope' for k in FIELDS}
    if not envelopes:
        return (result, None) if raster else result
    mx, my = source['mpp_x'], source['mpp_y']
    boxes = np.array([a['geometry']['bbox_level0'] for a in envelopes])
    start = np.floor(boxes[:, :2].min(axis=0)*[mx, my]/grid_um).astype(int)
    end = np.ceil(boxes[:, 2:].max(axis=0)*[mx, my]/grid_um).astype(int)
    nx, ny = (end-start).tolist()
    if nx * ny > 4_000_000:
        raise ValueError('Measurement grid exceeds four million cells; refine the envelope or explicitly choose a coarser protocol')
    xs = (np.arange(start[0], end[0])+.5)*grid_um/mx
    ys = (np.arange(start[1], end[1])+.5)*grid_um/my
    domain = np.zeros((ny, nx), dtype=bool)
    excluded = domain.copy()
    bad_boundary = domain.copy()
    labels = np.full((ny, nx), -1, dtype=np.int8)
    priority = np.full((ny, nx), np.inf)
    conflict = domain.copy()
    for a in annotations:
        g = a['geometry']; x0, y0, x1, y1 = g['bbox_level0']
        ix0, ix1 = np.searchsorted(xs, [x0, x1]); iy0, iy1 = np.searchsorted(ys, [y0, y1])
        if ix0 == ix1 or iy0 == iy1:
            continue
        window = np.s_[iy0:iy1, ix0:ix1]
        inside = _inside(g, xs[ix0:ix1], ys[iy0:iy1])
        code = a['measurement']['code']
        if code in ['T', 'X', 'B']:
            target = {'T': domain, 'X': excluded, 'B': bad_boundary}[code]
            target[window] |= inside
            continue
        # Physical bounding-box area is fixed by the submitted territory, never by outcome.
        size = (x1-x0)*mx*(y1-y0)*my
        p, lab, collisions = priority[window], labels[window], conflict[window]
        label = PATTERNS.index(CODES[code]) if code != '?' else -1
        smaller = inside & (size < p - 1e-6)
        same = inside & np.isclose(size, p, rtol=0, atol=1e-6)
        collisions[same & (lab != label)] = True
        lab[smaller] = label; p[smaller] = size; collisions[smaller] = False
    labels[conflict] = -1
    assessable = domain & ~excluded
    known = assessable & (labels >= 0)
    n, nk = int(assessable.sum()), int(known.sum())
    full = bool(envelopes) and all(a['measurement']['section_complete'] for a in envelopes)
    applicability = {a['measurement']['applicability'] for a in envelopes}
    applicable = applicability == {'nonmucinous_luad'}
    reason = ('not_applicable' if applicability == {'not_applicable'} else 'applicability_unresolved') if not applicable else (
        'no_assessable_tumour' if not n else 'incomplete_pattern_coverage' if nk != n else 'partial_section' if not full else 'ok')
    result.update(status=reason, section_complete=full, applicability=sorted(applicability),
                  envelope_area_mm2=float(domain.sum()*grid_um**2/1e6),
                  assessable_area_mm2=n*grid_um**2/1e6, labelled_area_mm2=nk*grid_um**2/1e6,
                  excluded_area_mm2=float((domain & excluded).sum()*grid_um**2/1e6),
                  unresolved_area_mm2=(n-nk)*grid_um**2/1e6,
                  conflict_area_mm2=float((conflict & assessable).sum()*grid_um**2/1e6),
                  coverage_pct=100*nk/n if n else None,
                  research_eligible=bool(reason == 'ok' and purpose == 'annotation' and all(reviewed(a) and a.get('origin') != 'engineering' for a in annotations)))
    if n != nk:
        iy, ix = np.argwhere(assessable & ~known)[0]
        halfx, halfy = min(1024/mx, source['width']/2), min(1024/my, source['height']/2)
        cx, cy = np.clip(xs[ix], halfx, source['width']-halfx), np.clip(ys[iy], halfy, source['height']-halfy)
        result['next_unresolved_bbox_level0'] = [float(cx-halfx), float(cy-halfy), float(cx+halfx), float(cy+halfy)]
    high = known & np.isin(labels, [3, 4, 5])
    nh = int(high.sum())
    result['observed_high_grade_area_pct'] = 100*nh/nk if nk else None
    result['high_grade_area_mm2'] = nh*grid_um**2/1e6
    result['pattern_area_pct_observed'] = {p: 100*int((known & (labels == i)).sum())/nk if nk else None
                                           for i, p in enumerate(PATTERNS)}
    result['metric_reasons'] = {k: reason for k in FIELDS}
    if reason == 'ok':
        result['high_grade_area_pct'] = 100*nh/n
        if nh:
            cc, _ = ndimage.label(high, structure=ndimage.generate_binary_structure(2, 1))
            largest = int(np.bincount(cc.ravel())[1:].max())
            result['largest_high_grade_component_pct'] = 100*largest/nh
            result['largest_high_grade_component_mm2'] = largest*grid_um**2/1e6
        else:
            result['metric_reasons']['largest_high_grade_component_pct'] = 'no_high_grade_denominator'
        boundary_ok = all(a['measurement']['boundary_assessable'] for a in envelopes) and not (bad_boundary & domain).any()
        # Exclusion holes are not new tumour edges. Pad to make image-border distances explicit.
        distance = ndimage.distance_transform_edt(np.pad(ndimage.binary_fill_holes(domain), 1))[1:-1, 1:-1]*grid_um
        edge, centre = assessable & (distance <= edge_um), assessable & (distance > edge_um)
        if boundary_ok and edge.any() and centre.any():
            ep, cp = 100*int((edge & high).sum())/int(edge.sum()), 100*int((centre & high).sum())/int(centre.sum())
            result.update(edge_high_grade_pct=ep, centre_high_grade_pct=cp, edge_enrichment_pp=ep-cp)
        else:
            result['metric_reasons']['edge_enrichment_pp'] = 'boundary_unassessable' if not boundary_ok else 'no_edge_or_centre'
        edges = different = 0
        for sl1, sl2 in [(np.s_[:, :-1], np.s_[:, 1:]), (np.s_[:-1, :], np.s_[1:, :])]:
            valid = known[sl1] & known[sl2]
            edges += int(valid.sum()); different += int((valid & (labels[sl1] != labels[sl2])).sum())
        if edges:
            counts = np.bincount(labels[known], minlength=6).astype(float)
            expected = 100*(1-float(np.sum(counts*(counts-1)))/(n*(n-1)))
            result.update(pattern_mixing_pct=100*different/edges, mixing_random_composition_pct=expected,
                          mixing_excess_pp=100*different/edges-expected, adjacency_pairs=edges,
                          mixing_null='exact_expectation_of_uniform_fixed_composition_permutation')
        else:
            result['metric_reasons']['pattern_mixing_pct'] = 'no_adjacent_assessable_cells'
    grid = dict(labels=labels, domain=domain, excluded=excluded, known=known, conflict=conflict,
                bbox_level0=[start[0]*grid_um/mx, start[1]*grid_um/my, end[0]*grid_um/mx, end[1]*grid_um/my])
    return (result, grid) if raster else result
