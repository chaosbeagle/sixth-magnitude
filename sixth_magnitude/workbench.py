"""Source-bound work packets and compact, atomic territory submissions."""
from __future__ import annotations

import hashlib
import math
import re

from . import schema
from .imaging import crop, RENDER_VERSION
from .morphometry import CODES, PATTERNS, VERSION, measure
from .store import atomic_json, digest, identifier, read, utc, validate_geometry

PACKET_VERSION = '1.0.0'


class Workbench:
    def __init__(self, store, images):
        self.store, self.images = store, images

    def packet(self, case, bbox=None):
        source = self.store.source(case)
        reader = self.images.reader(case)
        bbox = bbox or [0, 0, source['width'], source['height']]
        if (not isinstance(bbox, list) or len(bbox) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox)):
            raise ValueError('Invalid work packet bounds')
        x0, y0, x1, y1 = bbox
        if not 0 <= x0 < x1 <= source['width'] or not 0 <= y0 < y1 <= source['height']:
            raise ValueError('Work packet outside source')
        key = digest(dict(version=PACKET_VERSION, source_key=source['source_key'], bbox=bbox))[:32]
        directory = self.store.root / 'work_packets' / key
        if (directory / 'metadata.json').exists():
            return self.load_packet(key, case)
        directory.mkdir(parents=True, exist_ok=True)
        pixels, valid, metadata = crop(reader, bbox, long_edge=1024)
        pixels.save(directory / 'image.png'); valid.save(directory / 'valid.png')
        cells = {}
        for row in range(4):
            for col in range(4):
                cells[f'{chr(65+row)}{col+1}'] = [x0+(x1-x0)*col/4, y0+(y1-y0)*row/4,
                                                  x0+(x1-x0)*(col+1)/4, y0+(y1-y0)*(row+1)/4]
        packet = dict(packet_id=key, case_id=case, source_key=source['source_key'], version=PACKET_VERSION,
                      bbox_level0=bbox, cells=cells, view=metadata, created_utc=utc(),
                      files={n: hashlib.sha256((directory/n).read_bytes()).hexdigest() for n in ['image.png', 'valid.png']})
        atomic_json(directory / 'metadata.json', packet, exclusive=True)
        return packet

    def load_packet(self, key, case=None):
        directory = self.store.root / 'work_packets' / identifier(key)
        p = read(directory / 'metadata.json')
        if p['packet_id'] != key or (case and p['case_id'] != case) or p['source_key'] != self.store.source(p['case_id'])['source_key']:
            raise ValueError('Packet source/case mismatch')
        expected_key=digest(dict(version=PACKET_VERSION, source_key=p['source_key'], bbox=p['bbox_level0']))[:32]
        x0,y0,x1,y1=p['bbox_level0']; w,h=p['view']['output_dimensions']
        expected_cells={f'{chr(65+r)}{c+1}':[x0+(x1-x0)*c/4,y0+(y1-y0)*r/4,x0+(x1-x0)*(c+1)/4,y0+(y1-y0)*(r+1)/4] for r in range(4) for c in range(4)}
        if key != expected_key or p['cells'] != expected_cells or p['view']['image_to_level0_affine'] != [(x1-x0)/w,0,x0,0,(y1-y0)/h,y0]:
            raise ValueError('Packet geometry checksum mismatch')
        for name, sha in p['files'].items():
            if name not in ('image.png', 'valid.png') or hashlib.sha256((directory/name).read_bytes()).hexdigest() != sha:
                raise ValueError('Work packet image checksum mismatch')
        return p

    def evidence(self, case, packet, geometry):
        source = self.store.source(case)
        binding = dict(source_key=source['source_key'], geometry=geometry, packet_id=packet['packet_id'])
        eid = digest(binding)[:32]
        metadata = dict(evidence_id=eid, **binding, kind='work_packet_region', case_id=case,
                        render_version=PACKET_VERSION, packet_image_sha256=packet['files']['image.png'],
                        view=packet['view'], files={}, context_label_support='not_assessed',
                        native_detail_evidence_id=digest(dict(source_key=source['source_key'], geometry=geometry, render=RENDER_VERSION))[:32])
        path = self.store.root / 'evidence' / eid / 'metadata.json'
        if path.exists():
            if read(path) != metadata:
                raise ValueError('Region evidence checksum mismatch')
        else:
            atomic_json(path, metadata, exclusive=True)
        return metadata

    def submit(self, case, request, actor):
        packet = self.load_packet(request['packet_id'], case)
        source = self.store.source(case)
        regions = []
        # One small string labels visible, homogeneous cells. Omitted cells stay unassessed.
        compact = request.get('labels', '').strip()
        if compact:
            for group in compact.split(';'):
                match = re.fullmatch(r'\s*([LAPMSC?TXB])\s*:\s*([A-D][1-4](?:\s*,\s*[A-D][1-4])*)\s*', group)
                if not match:
                    raise ValueError('Use A:A1,A2; S:B1; ?:C3 (codes before colon; cell IDs after)')
                code, cells = match.groups()
                for cell in re.split(r'\s*,\s*', cells):
                    regions.append((code, packet['cells'][cell], None))
        polygon = request.get('polygon')
        if polygon is not None:
            if not isinstance(polygon, list) or not 3 <= len(polygon) <= 2000:
                raise ValueError('Polygon needs 3–2000 image vertices')
            width, height = packet['view']['output_dimensions']
            a, _, c, _, e, f = packet['view']['image_to_level0_affine']
            points = []
            for p in polygon:
                if (not isinstance(p, list) or len(p) != 2 or
                    any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in p)
                    or not 0 <= p[0] <= width or not 0 <= p[1] <= height):
                    raise ValueError('Polygon vertex outside the inspected image')
                points.append([a*p[0]+c, e*p[1]+f])
            regions.append((request['code'], [min(p[0] for p in points), min(p[1] for p in points),
                                             max(p[0] for p in points), max(p[1] for p in points)], points))
        items = []
        for code, bbox, points in regions:
            if code not in CODES:
                raise ValueError('Unknown territory code')
            physical = max((bbox[2]-bbox[0])*source['mpp_x'], (bbox[3]-bbox[1])*source['mpp_y'])
            geometry = validate_geometry(dict(type='polygon' if points else 'rectangle', role='quality_region' if code in 'XB' else 'tissue_region',
                                              bbox_level0=bbox, points=points, context_side_um=max(512, physical*1.25)), source)
            m = dict(version=VERSION, code=code, section_complete=False, boundary_assessable=False, applicability='not_assessed')
            if code == 'T':
                m.update({k: request.get(k, m[k]) for k in ('section_complete', 'boundary_assessable', 'applicability')})
            observation = schema.defaults()
            observation['label'] = CODES[code]
            if CODES[code] in PATTERNS:
                observation['focus'].update(pattern_assessment_state='assessed', patterns_present=[CODES[code]])
            observation['focus']['note'] = 'Territory assignment from a visual work packet; context not separately assessed.'
            # Revisiting the same territory replaces its code; a tumour envelope is a separate layer.
            category = code if code in 'TXB' else 'pattern'
            uid = category + '_' + digest(dict(source_key=source['source_key'], geometry=geometry))[:20]
            items.append((dict(annotation_id=uid, geometry=geometry, observation=observation, measurement=m), self.evidence(case, packet, geometry)))
        if request.get('replace_annotation_id'):
            if len(items) != 1 or not request.get('expected_content_hash'):
                raise ValueError('A correction requires one region and its saved content hash')
            items[0][0].update(annotation_id=request['replace_annotation_id'], expected_content_hash=request['expected_content_hash'])
        return self.store.batch_save(case, request, actor, items)

    def brief(self, case):
        state = self.store.state(case)
        metrics = measure(state, self.store.source(case), self.store.manifest['purpose'])
        # Full provenance remains in exports; a worker needs only its next decision.
        for key in ('reviewed_content_hashes', 'contributing_content_hashes'):
            metrics.pop(key, None)
        active = [a for a in state['annotations'].values() if not a['deleted']]
        return dict(case_id=case, revision=state['revision'], status=state['status'], regions=len(active),
                    pending_visual=sum(a['visual']['state'] != 'matched' for a in active),
                    measurement=metrics)
