"""Immutable JSON case revisions with atomic commit, deduplication and review binding."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
import uuid

from .profiles import load as load_profile
from .morphometry import validate_measurement, CODES, PATTERNS


def utc():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def annotation_content(annotation):
    content = {k: annotation[k] for k in ('geometry', 'observation', 'evidence_id')}
    if annotation.get('measurement') is not None:
        content['measurement'] = annotation['measurement']
    return content


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encoded(value)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive and path.exists():
            raise FileExistsError(path)
        os.replace(temp, path)
        if path.read_bytes() != data:
            raise OSError("JSON readback mismatch")
    finally:
        temp.unlink(missing_ok=True)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise ValueError("Invalid identifier")
    return value


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read(self.root / "run_manifest.json")
        self.profile = self.manifest.get("profile", "lung-pathology")
        self.schema = load_profile(self.profile)
        self.review_role = "physician" if self.profile == "lung-pathology" else "reviewer"
        self.accepted_state = "physician_reviewed" if self.profile == "lung-pathology" else "reviewer_accepted"
        snapshot = self.root / "observation.schema.json"
        if snapshot.exists() and digest(read(snapshot)) != digest(self.schema.SCHEMA):
            raise ValueError("Run schema differs from installed schema; use an explicit migration")
        self.cases = {c["case_id"]: c for c in self.manifest["cases"]}
        if len(self.cases) != len(self.manifest["cases"]):
            raise ValueError("Duplicate case IDs")
        self._lock = RLock()
        self._operation_cache = {}

    @contextmanager
    def writing(self):
        # The OS releases this lock after a process exits; no stale lock files.
        with self._lock, (self.root / ".write.lock").open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def source(self, case):
        if case not in self.cases:
            raise ValueError("Unknown case")
        return self.cases[case]["source"]

    def revisions(self, case):
        self.source(case)
        return sorted((self.root / "cases" / case / "revisions").glob("r[0-9]*.json"))

    def state(self, case):
        files = self.revisions(case)
        if files:
            state = read(files[-1])
            if state["case_id"] != case or state["source_key"] != self.source(case)["source_key"]:
                raise ValueError("Saved case/source identity mismatch")
            for annotation in state["annotations"].values():
                if digest(annotation_content(annotation)) != annotation["content_hash"]:
                    raise ValueError("Saved annotation content checksum mismatch")
            return state
        return {"case_id": case, "revision": 0, "annotations": {},
            "status": "not_started", "status_note": None, "review_sessions": {}, "last_event": None}

    def queue(self):
        rows = []
        for case, item in self.cases.items():
            state = self.state(case)
            active = [a for a in state["annotations"].values() if not a["deleted"]]
            rows.append({"case_id": case, "slide_token": item["source"]["slide_token"],
                "status": state["status"], "count": len(active),
                "pending_visual": sum(a["visual"]["state"] != "matched" for a in active),
                "physician_reviewed": sum(a["review"]["state"] == self.accepted_state for a in active),
                "planned_rois": len(item.get("planned_rois", [])), "revision": state["revision"]})
        return rows

    def mutate(self, case, request, actor, effect):
        operation = identifier(request.get("operation_id"))
        if (not isinstance(actor, dict) or actor.get("role") not in ("ai", self.review_role, "engineering")
                or not isinstance(actor.get("id"), str) or not actor["id"].strip()):
            raise ValueError("Actor role and identity required")
        if request.get("source_key") != self.source(case)["source_key"]:
            raise Conflict("Source identity mismatch; reopen the case")
        request_hash = digest({"request": request, "actor": actor})
        with self.writing():
            # A retry returns its original committed response even after restart.
            cache = self._operation_cache.setdefault(case, {'seen': set(), 'events': {}})
            for file in self.revisions(case):
                if file.name in cache['seen']:
                    continue
                saved = read(file)
                event = saved["last_event"]
                cache['events'][event['operation_id']] = (saved['revision'], event)
                cache['seen'].add(file.name)
            if operation in cache['events']:
                revision, event = cache['events'][operation]
                if event['request_hash'] != request_hash:
                    raise Conflict('Operation ID reused with different content')
                return {'revision':revision, 'result':event['result'], 'replayed':True,
                        'current_revision':self.state(case)['revision'], 'readback':'PASS'}
            old = self.state(case)
            if request.get("base_revision") != old["revision"]:
                raise Conflict("Revision conflict; reload the saved state")
            state = deepcopy(old)
            result = effect(state)
            state.update(revision=old["revision"] + 1, schema_version=self.schema.VERSION,
                         source_key=self.source(case)["source_key"], previous_sha256=digest(old))
            event = {"event_id": str(uuid.uuid4()), "utc": utc(), "operation_id": operation,
                     "request_hash": request_hash, "action": request["action"], "actor": actor,
                     "result": result}
            state["last_event"] = event
            target = self.root / "cases" / case / "revisions" / f'r{state["revision"]:06d}.json'
            atomic_json(target, state, exclusive=True)
            cache['events'][operation] = (state['revision'], event)
            cache['seen'].add(target.name)
            return {"revision": state["revision"], "result": result, "replayed": False, "readback": "PASS"}

    def save(self, case, request, actor, evidence):
        return self.mutate(case, request, actor, self._save_effect(case, request, actor, evidence))

    def _save_effect(self, case, request, actor, evidence):
        observation = self.schema.validate(request["observation"])
        geometry = validate_geometry(request["geometry"], self.source(case))
        if evidence["source_key"] != self.source(case)["source_key"] or evidence["geometry"] != geometry:
            raise Conflict("Evidence does not match the final geometry/source")
        uid = identifier(request["annotation_id"])
        plan_id = request.get("planned_roi_id")
        if plan_id is not None and plan_id not in {p["id"] for p in self.cases[case].get("planned_rois", [])}:
            raise ValueError("Unknown planned ROI")

        def effect(state):
            previous = state["annotations"].get(uid)
            if previous and previous["deleted"]:
                raise ValueError("Deleted annotation IDs cannot be reused")
            if request.get('expected_content_hash') and (not previous or previous['content_hash'] != request['expected_content_hash']):
                raise Conflict('The territory being edited has changed')
            if self.profile != "lung-pathology" and request.get("measurement") is not None:
                raise ValueError("Morphometry belongs to the lung-pathology profile")
            measurement = validate_measurement(request.get('measurement', previous.get('measurement') if previous else None), geometry)
            if measurement and CODES[measurement['code']] in PATTERNS:
                if observation['focus']['patterns_present'] != [CODES[measurement['code']]]:
                    raise ValueError('Pattern observation and measurement territory disagree; edit the typed region in the agent workbench')
            content = dict(geometry=geometry, observation=observation, evidence_id=evidence['evidence_id'])
            if measurement is not None:
                content['measurement'] = measurement
            content_hash = digest(content)
            unchanged = previous and previous["content_hash"] == content_hash
            version = previous["version"] + int(not unchanged) if previous else 1
            annotation = {"id": uid, "version": version, "geometry": geometry,
                "geometry_revision": (previous["geometry_revision"] + int(previous["geometry"] != geometry)) if previous else 1,
                "observation_revision": (previous["observation_revision"] + int(previous["observation"] != observation)) if previous else 1,
                "observation": observation, "evidence_id": evidence["evidence_id"], "content_hash": content_hash,
                "origin": previous["origin"] if previous else ("human_observation" if actor["role"] == self.review_role else "ai_candidate" if actor["role"] == "ai" else "engineering"),
                "actor": actor, "created_utc": previous["created_utc"] if previous else utc(), "updated_utc": utc(),
                "visual": previous["visual"] if unchanged else {"state": "pending"},
                "review": previous["review"] if unchanged else {"state": "not_reviewed"},
                "deleted": False, "planned_roi_id": plan_id or (previous.get("planned_roi_id") if previous else None),
                "legacy_ref": request.get("legacy_ref") or (previous.get("legacy_ref") if previous else None)}
            if measurement is not None:
                annotation['measurement'] = measurement
                annotation['measurement_revision'] = (previous.get('measurement_revision', 0) + int(previous.get('measurement') != measurement)) if previous else 1
            state["annotations"][uid] = annotation
            state["status"] = "in_progress"
            return {"annotation_id": uid, "version": version, "content_hash": content_hash}
        return effect

    def batch_save(self, case, request, actor, items):
        if not 1 <= len(items) <= 64:
            raise ValueError('A batch must contain 1–64 regions')
        ids = [r['annotation_id'] for r, e in items]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate region in batch')
        effects = [self._save_effect(case, r, actor, e) for r, e in items]
        return self.mutate(case, request, actor, lambda state: {'regions': [fn(state) for fn in effects]})

    def action(self, case, request, actor):
        if request['action'] in ('batch_visual', 'batch_review', 'batch_delete'):
            items = request['items']
            if not 1 <= len(items) <= 64 or len({i['annotation_id'] for i in items}) != len(items):
                raise ValueError('Batch requires 1–64 distinct regions')
            effects = [self._action_effect(case, {**i, 'action': request['action'][6:]}, actor) for i in items]
            return self.mutate(case, request, actor, lambda state: {'regions': [fn(state) for fn in effects]})
        return self.mutate(case, request, actor, self._action_effect(case, request, actor))

    def _action_effect(self, case, request, actor):
        def effect(state):
            action = request["action"]
            if action == "case_status":
                status = request["status"]
                if status not in ("in_progress", "completed_planned_sampling", "deferred", "unreadable"):
                    raise ValueError("Unknown case status")
                if status == "completed_planned_sampling":
                    pending = [a for a in state["annotations"].values() if not a["deleted"] and a["visual"]["state"] != "matched"]
                    if pending:
                        raise ValueError("仍有已保存但未完成視覺確認的標註")
                    expected = {p["id"] for p in self.cases[case].get("planned_rois", [])}
                    completed = {a.get("planned_roi_id") for a in state["annotations"].values() if not a["deleted"]}
                    if self.manifest["task_mode"] == "fixed_roi" and expected - completed:
                        raise ValueError(f"仍有 {len(expected - completed)} 個預定位置未處理；可暫緩病例後繼續其他病例")
                state.update(status=status, status_note=request.get("note"))
                return {"status": status}
            if action in ("review_start", "review_stop"):
                if actor["role"] != self.review_role:
                    raise ValueError("Human reviewer role required for review timer")
                sid = identifier(request["review_session_id"])
                sessions = state["review_sessions"]
                if action == "review_start":
                    if sid in sessions:
                        raise ValueError("Review session already exists")
                    if any(s['actor'] == actor and s['end_utc'] is None for s in sessions.values()):
                        raise ValueError("Close the reviewer's existing interval before starting another")
                    sessions[sid] = {"actor": actor, "start_utc": utc(), "end_utc": None, "seconds": None}
                else:
                    session = sessions[sid]
                    if session["actor"] != actor or session["end_utc"] is not None:
                        raise Conflict("Review timer identity/state mismatch")
                    session["end_utc"] = utc()
                    session["seconds"] = (datetime.fromisoformat(session["end_utc"]) - datetime.fromisoformat(session["start_utc"])).total_seconds()
                return {"review_session_id": sid}
            uid = request["annotation_id"]
            annotation = state["annotations"][uid]
            if annotation["deleted"] or request.get("content_hash") != annotation["content_hash"]:
                raise Conflict("Annotation changed; inspect its current version")
            if action == "visual":
                if request.get("evidence_id") != annotation["evidence_id"] or request.get("state") not in ("matched", "mismatch", "unavailable"):
                    raise ValueError("Visual event requires the exact evidence and valid state")
                annotation["visual"] = {"state": request["state"], "actor": actor, "utc": utc(),
                    "content_hash": annotation["content_hash"], "note": request.get("note"), "view_packet_id": request.get('view_packet_id')}
                if request["state"] != "matched":
                    annotation["review"] = {"state": "not_reviewed"}
            elif action == "review":
                if actor["role"] != self.review_role:
                    raise ValueError(f"Only the {self.review_role} role can record acceptance decisions")
                decision = request["decision"]
                if decision not in ("accept", "reject", "defer"):
                    raise ValueError("Unknown review decision")
                if decision == "accept" and annotation["visual"]["state"] != "matched":
                    raise ValueError("請先查看並確認目前疊圖")
                annotation["review"] = {"state": {"accept": self.accepted_state, "reject": "rejected", "defer": "deferred"}[decision],
                    "action": decision, "actor": actor, "utc": utc(), "content_hash": annotation["content_hash"],
                    "geometry_revision": annotation["geometry_revision"], "observation_revision": annotation["observation_revision"],
                    "note": request.get("note")}
            elif action == "delete":
                annotation["deleted"] = True
                annotation["deleted_utc"] = utc()
                annotation["deletion_actor"] = actor
                state["status"] = "in_progress"
            else:
                raise ValueError("Unknown action")
            return {"annotation_id": uid, "version": annotation["version"], "content_hash": annotation["content_hash"]}
        return effect


def validate_geometry(value, source):
    context_key = "context_side_px" if source.get("coordinate_unit") == "pixel" else "context_side_um"
    if not isinstance(value, dict) or set(value) != {"type", "role", "bbox_level0", context_key, "points"}:
        raise ValueError("Geometry requires type, role, bbox_level0, context_side_px or context_side_um, points")
    if value["type"] not in ("rectangle", "polygon") or value["role"] not in ("sampling_window", "tissue_region", "quality_region", "inspection_region"):
        raise ValueError("Unsupported geometry type or role")
    bbox = value["bbox_level0"]
    if not isinstance(bbox, list) or len(bbox) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
        raise ValueError("Invalid finite bounding box")
    x0, y0, x1, y1 = bbox
    if not 0 <= x0 < x1 <= source["width"] or not 0 <= y0 < y1 <= source["height"]:
        raise ValueError("Focus geometry outside slide bounds")
    points = value["points"]
    if value["type"] == "rectangle":
        if points is not None:
            raise ValueError("Rectangle points must be null")
    else:
        if value["role"] == "sampling_window":
            raise ValueError("Use a rectangle for a sampling window")
        if not isinstance(points, list) or not 3 <= len(points) <= 2000:
            raise ValueError("Polygon needs 3–2000 vertices")
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                raise ValueError("Invalid polygon vertex")
        if bbox != [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]:
            raise ValueError("Polygon bounds mismatch")
        area = abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(points, points[1:] + points[:1]))) / 2
        if area < 1:
            raise ValueError("Degenerate polygon")
        # Reject self-crossing outlines; the exported object must have a well-defined interior.
        segments = list(zip(points, points[1:] + points[:1]))
        def orient(a, b, c):
            return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        for i, (a,b) in enumerate(segments):
            if a == b:
                raise ValueError("Duplicate consecutive polygon vertices")
            for j in range(i+2,len(segments)):
                if i == 0 and j == len(segments)-1:
                    continue
                c,d = segments[j]
                if (max(a[0],b[0]) < min(c[0],d[0]) or max(c[0],d[0]) < min(a[0],b[0])
                    or max(a[1],b[1]) < min(c[1],d[1]) or max(c[1],d[1]) < min(a[1],b[1])):
                    continue
                if orient(a,b,c)*orient(a,b,d) <= 0 and orient(c,d,a)*orient(c,d,b) <= 0:
                    raise ValueError("Self-intersecting polygon")
    side = value[context_key]
    if isinstance(side, bool) or not isinstance(side, (int, float)) or not math.isfinite(side) or not 0 < side <= 100000:
        raise ValueError("Invalid context size")
    if side <= max((x1 - x0) * (source["mpp_x"] or 1), (y1 - y0) * (source["mpp_y"] or 1)):
        raise ValueError("Context must extend beyond the focus; increase its size")
    return deepcopy(value)
