"""Two independent Codex roles for route selection and nuclear image editing."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

import numpy as np

from .codex_session import Rpc
from .composition import bounds
from .free_edit import picture
from .free_session import SETTINGS, prompt
from .packets import dump, sha
from .rounds import object_schema
from .seal_review import SealReview, WORKFLOW
from .source import pixel_hash


ROUTE_SCHEMA = object_schema({"decisions": {"type": "array", "items": object_schema({
    "field": {"type": "string"},
    "route": {"type": "string", "enum": ["tool-guided", "model-guided", "combined"]},
    "candidate": {"type": "string"},
    "roi": {"type": "array", "items": {"type": "integer"}},
    "reason": {"type": "string"},
})}})
REVIEW_PROMPT = """Independently decide how to segment each supplied H&E field. You are the route reviewer.
Examine the native source and named candidate contours yourself. For each field name the best available
tool candidate for comparison. Choose tool-guided if a candidate is a sound start, model-guided
if drawing or writing an image algorithm from the original is preferable, or combined if particular
regions need direct repair on a tool mask. Give a concrete visual reason comparing the alternatives,
including suspected misses, over-segmentation or boundary errors when visible. No need to force edits.
Optionally identify a focus ROI in field coordinates [x,y,width,height], otherwise use [].
Use only the supplied pictures. Return exactly one decision per requested field. You have no editing role."""
HANDOFF = """An independent visual reviewer selected a starting route for each field below. The workbench
has already initialized each committed mask accordingly. Inspect the original against the starting
contours, then choose your own next action. You may keep a good candidate, switch routes or combine
them locally. If you depart from the suggested route, give your visual reason in the edit or seal.
Use relative paths inside your own work directory for all intermediate images, arrays and code.
Before sealing, inspect raw H&E, reviewer start and proposed final at the same coordinates.
The full view already provides the comparison code. Call compare if you need an updated comparison
or want to add a close-up with roi. Look at newly added or lost objects, touching
profiles, cut edges and staining debris. Use the clean source to check that a darker fragment
belongs to a distinct nucleus. A change in count is a reason to look, not a target.
Choose geometry "start" or "final". Use comparison from that view and assign default/groups on the
IDs of the chosen geometry at seal. You may keep the starting outlines and revise only the classes.
For uncertain types, zoom out for tissue context and back in for nuclear shape, lobulation and
chromatin. Record ambiguous IDs and the competing interpretation in the seal reason when relevant."""
REVIEW_TOOL = dict(type="function", name="review_fields", deferLoading=False,
                   description="Inspect the field, propose or review an edit, assign types and seal. Use [] to open.",
                   inputSchema=object_schema({"decisions_json": {"type": "string"}}))


def validate_routes(decisions, fields):
    if not isinstance(decisions, list):
        raise ValueError("Expected a list of field decisions")
    choices = {}
    for row in decisions:
        key = row["field"]
        if key not in fields or key in choices:
            raise ValueError(f"Unexpected or repeated field {key}")
        field = fields[key]
        if row["route"] not in ("tool-guided", "model-guided", "combined") or row["candidate"] not in field.bank:
            raise ValueError(f"Unknown route or unavailable candidate for {key}")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError(f"Missing visual reason for {key}")
        if not isinstance(row["roi"], list):
            raise ValueError(f"Invalid ROI for {key}")
        bounds(row["roi"], field.rgb.shape[:2])
        choices[key] = row
    if choices.keys() != fields.keys():
        raise ValueError("Missing one or more field decisions")
    return choices


def seed_operator(fields, routes):
    for key, field in fields.items():
        choice = routes[key]
        if choice["route"] == "model-guided":
            field.current = np.zeros(field.rgb.shape[:2], dtype=np.int32)
        else:
            field.current = field.bank[choice["candidate"]].astype(np.int32, copy=True)
        field.sync()
        choice["initial_mask_sha256"] = pixel_hash(field.current)


def operator_prompt(individual, classes, python, routes):
    guide = prompt("hybrid", individual, classes, python)
    guide = guide.replace("Baseline is initially current.", "Read the per-field handoff for the initial mask.")
    guide = guide.replace("or seal commits and finishes after inspection.",
                          "or compare the visible proposal with the starting contours before sealing.")
    guide = guide.replace("in a paired pathology benchmark", "in a nuclear segmentation and typing workspace")
    handoff = {key: {name: row[name] for name in ("route", "candidate", "roi", "reason")}
               for key, row in routes.items()}
    return guide + "\n" + HANDOFF + "\n" + json.dumps(handoff, ensure_ascii=False)


def review_input(fields, workspace):
    parts = [{"type": "text", "text": REVIEW_PROMPT}]
    evidence = {}
    for key, field in fields.items():
        source = workspace / (key + ".png")
        gallery = picture(field.rgb, list(field.bank.items()))
        width, height = field.rgb.shape[1], field.rgb.shape[0]
        parts.extend([{"type": "text", "text": f"{key} ({width} x {height}), raw image then candidate gallery: {', '.join(field.bank)}"},
                      {"type": "localImage", "path": str(source)},
                      {"type": "image", "url": gallery["imageUrl"]}])
        evidence[key] = {"source_sha256": sha(source), "candidates": {name: pixel_hash(mask) for name, mask in field.bank.items()}}
    return parts, evidence


class Agent:
    def __init__(self, rpc, role, workspace, settings, receipt, log):
        self.rpc, self.role, self.info, self.log = rpc, role, receipt, log
        self.workspace = workspace
        self.model, self.effort, self.tier = settings
        reviewer = role == "reviewer"
        config = rpc.call("thread/start", dict(
            ephemeral=True, allowProviderModelFallback=False, model=self.model, serviceTier=self.tier,
            config={"model_reasoning_effort": self.effort}, cwd=str(workspace),
            sandbox="read-only" if reviewer else "workspace-write", approvalPolicy="never",
            dynamicTools=[] if reviewer else [REVIEW_TOOL]))
        self.id = receipt["thread_id"] = config["thread"]["id"]
        receipt["accepted"] = {k: config.get(k) for k in ("model", "reasoningEffort", "serviceTier", "modelProvider")}
        if (config.get("model"), config.get("reasoningEffort"), config.get("serviceTier")) != settings:
            raise RuntimeError(f"Unexpected settings for {role}: {receipt['accepted']}")
        receipt["turns"] = []

    def turn(self, parts, *, bench=None):
        params = dict(threadId=self.id, model=self.model, effort=self.effort, serviceTier=self.tier, input=parts)
        if bench is None:
            params["sandboxPolicy"] = {"type": "readOnly", "networkAccess": False}
            params["outputSchema"] = ROUTE_SCHEMA
        else:
            params["sandboxPolicy"] = {"type": "workspaceWrite", "writableRoots": [str(self.workspace)],
                                       "networkAccess": False, "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}
        start = monotonic()
        tid = self.rpc.call("turn/start", params)["turn"]["id"]
        record = {"id": tid, "input_images": sum(p["type"] in ("localImage", "image") for p in parts)}
        self.info["turns"].append(record)
        final = ""
        while True:
            msg = self.rpc.pending.pop(0) if self.rpc.pending else self.rpc.recv()
            method, p = msg.get("method", ""), msg.get("params", {})
            if "id" in msg and method:
                if (bench is None or method != "item/tool/call" or p.get("threadId") != self.id
                        or p.get("turnId") != tid or p.get("tool") != "review_fields"):
                    raise RuntimeError(f"Unexpected tool request for {self.role}: {method}")
                try:
                    actions = json.loads(p["arguments"]["decisions_json"])
                    if not isinstance(actions, list) or any(not isinstance(a, dict) for a in actions):
                        raise ValueError("Use an array of field actions")
                    reply = bench.handle(actions)
                except (KeyError, ValueError, TypeError) as exc:
                    reply = [{"type": "inputText", "text": str(exc)}]
                self.rpc.send({"id": msg["id"], "result": {"success": True, "contentItems": reply}})
                self.log(self.role, dict(call=p["callId"], arguments=p["arguments"],
                                         text=[v["text"] for v in reply if v["type"] == "inputText"],
                                         images=sum(v["type"] == "inputImage" for v in reply)))
            elif p.get("threadId") != self.id:
                continue
            elif "rerout" in method.lower():
                raise RuntimeError(f"{self.role} was rerouted")
            elif method == "thread/tokenUsage/updated":
                self.info["usage"] = p["tokenUsage"]
            elif method == "item/completed":
                item = p["item"]
                kind = item["type"]
                self.log(self.role, {k: v for k, v in item.items()
                                     if k in ("type", "id", "command", "cwd", "tool", "path", "text", "changes", "status", "exitCode")})
                if bench is None and kind not in ("userMessage", "agentMessage", "reasoning", "plan", "contextCompaction"):
                    raise RuntimeError("Reviewer used unexpected capability: " + kind)
                if kind == "agentMessage":
                    final = item.get("text", "")
            elif method == "turn/completed" and p["turn"]["id"] == tid:
                turn = p["turn"]
                record.update(seconds=monotonic()-start, status=turn["status"], server_ms=turn.get("durationMs"))
                if turn["status"] != "completed":
                    raise RuntimeError(f"{self.role} turn failed: {turn.get('error')}")
                return final


def plan_fields(reviewer, bench, workspace, out):
    keys = list(bench.fields)
    routes, evidence = {}, {}
    for start in range(0, len(keys), 4):
        batch = {k: bench.fields[k] for k in keys[start:start+4]}
        parts, hashes = review_input(batch, workspace)
        evidence.update(hashes)
        for attempt in range(2):
            answer = reviewer.turn(parts)
            try:
                choices = validate_routes(json.loads(answer)["decisions"], batch)
            except (KeyError, TypeError, ValueError) as exc:
                if attempt:
                    raise ValueError(f"Reviewer produced an invalid plan: {exc}") from exc
                parts = [{"type": "text", "text": f"Correct the preceding decisions for {list(batch)}. {exc}"}]
                continue
            routes.update(choices)
            break
        dump(out / "route-review.json", {"reviewer_thread_id": reviewer.id, "evidence": evidence, "fields": routes})
    seed_operator(bench.fields, routes)
    dump(out / "route-review.json", {"reviewer_thread_id": reviewer.id, "evidence": evidence, "fields": routes})
    return routes


def usage_summary(roles):
    totals = [role["usage"]["total"] for role in roles.values() if role.get("usage", {}).get("total")]
    missing = [name for name, role in roles.items() if not role.get("usage", {}).get("total")]
    combined = {key: sum(t.get(key, 0) for t in totals) for key in set().union(*totals)}
    return {"total": combined if totals else None, "missing_roles": missing}


def run(host, individual, args):
    out = args.output.resolve()
    out.mkdir(exist_ok=False, parents=True)
    start = monotonic()
    settings = SETTINGS[host]
    roles = {"reviewer": {}, "operator": {}}
    receipt = dict(host=host, mode="hybrid", workflow=WORKFLOW, status="started",
                   model=settings[0], effort=settings[1], tier=settings[2],
                   time_utc=datetime.now(timezone.utc).isoformat(), roles=roles)
    dump(out / "receipt.json", receipt)
    bench = None
    with ExitStack() as stack:
        try:
            operator_dir = Path(stack.enter_context(TemporaryDirectory(prefix="nuclear-operator-"))).resolve()
            reviewer_dir = Path(stack.enter_context(TemporaryDirectory(prefix="nuclear-reviewer-"))).resolve()
            journal = stack.enter_context((out / "events.jsonl").open("w"))
            def log(role, event):
                journal.write(json.dumps({"role": role, **event}) + "\n")
                journal.flush()
            bench = SealReview(args.fields, args.detector, operator_dir, out, args.classes, individual)
            for field in bench.fields:
                shutil.copyfile(operator_dir / (field + ".png"), reviewer_dir / (field + ".png"))
            receipt["sources"] = {f: sha(reviewer_dir / (f + ".png")) for f in bench.fields}
            err = stack.enter_context((out / "stderr.log").open("w"))
            proc = subprocess.Popen(["codex", "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=err, text=True, bufsize=1)
            def close():
                proc.stdin.close()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stack.callback(close)
            rpc = Rpc(proc)
            rpc.call("initialize", {"clientInfo": {"name": "nuclear_route_review", "version": "1"},
                                    "capabilities": {"experimentalApi": True}})
            rpc.send({"method": "initialized"})
            reviewer = Agent(rpc, "reviewer", reviewer_dir, settings, roles["reviewer"], log)
            routes = plan_fields(reviewer, bench, reviewer_dir, out)
            for field in bench.fields.values():
                field.capture_start()
            operator = Agent(rpc, "operator", operator_dir, settings, roles["operator"], log)
            if reviewer.id == operator.id:
                raise RuntimeError("Reviewer and operator must have independent contexts")
            receipt.update(thread_id=operator.id, accepted=roles["operator"]["accepted"])
            instruction = operator_prompt(individual, args.classes, args.python, routes)
            receipt["prompt"] = instruction
            dump(out / "receipt.json", receipt)
            for attempt in range(2):
                operator.turn([{"type": "text", "text": instruction}], bench=bench)
                if not bench.active():
                    break
                instruction = "Complete and seal the remaining fields: " + ", ".join(bench.active())
            if bench.active():
                raise RuntimeError("Operator left unsealed fields")
            receipt["status"] = "completed"
        except BaseException as exc:
            receipt.update(status="failed", error=str(exc))
            raise
        finally:
            receipt["usage"] = usage_summary(roles)
            receipt["seconds"] = monotonic() - start
            if bench is not None:
                dump(out / "actions.json", {k: f.events for k, f in bench.fields.items()})
                receipt["outputs"] = {p.name: sha(p) for p in out.glob("*.npz")}
                authored = out / "authored"
                for f in operator_dir.iterdir():
                    if f.is_file() and not f.is_symlink() and f.suffix in (".py", ".sh"):
                        authored.mkdir(exist_ok=True)
                        shutil.copyfile(f, authored / f.name)
            dump(out / "receipt.json", receipt)
