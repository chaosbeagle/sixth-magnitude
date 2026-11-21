"""Run a real Codex turn with a live workbench exposed through native tool calls."""
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import tempfile
from threading import Thread
import time

from .native_workbench import LiveWorkbench, tool_definitions, utc
from .packets import dump, read
from .rounds import inspection_lock

PROMPT = ("Operate the live pathology workbench. First call workbench_open, examine the pictures, "
          "then use workbench_submit for selections and segmentation reviews, or workbench_confirm for close-up verdicts. Each submission applies your choices and "
          "displays the next pictures. Continue until the workbench returns COMPLETE. Only use these "
          "workbench tools. The legend is cyan epithelial, yellow lymphocyte, green macrophage, orange "
          "neutrophil, white untyped. Judge the original H&E against the contours. "
          "Use only visible evidence and leave unsupported type changes alone.")


class Rpc:
    def __init__(self, proc):
        self.proc = proc
        self.messages = Queue()
        self.seq = 0
        self.pending = []
        Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        try:
            for line in self.proc.stdout:
                self.messages.put(json.loads(line))
        finally:
            self.messages.put(None)

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg, separators=(",",":"))+"\n")
        self.proc.stdin.flush()

    def recv(self, timeout=240):
        try:
            msg = self.messages.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError("Codex stopped reporting events") from exc
        if msg is None:
            raise RuntimeError("Codex app-server exited")
        return msg

    def call(self, method, params):
        self.seq += 1
        self.send({"id":self.seq, "method":method, "params":params})
        while True:
            msg = self.recv()
            if msg.get("id")==self.seq and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]
            self.pending.append(msg)


def events(rpc, bench, turn_id, telemetry, journal):
    allowed_items = {"userMessage","agentMessage","reasoning","dynamicToolCall","plan",
                     "enteredReviewMode","exitedReviewMode","contextCompaction"}
    clock = time.monotonic()
    last_delivery = None
    while True:
        if time.monotonic()-clock > 1200:
            raise TimeoutError("Workbench turn exceeded 20 minutes")
        msg = rpc.pending.pop(0) if rpc.pending else rpc.recv()
        method, p = msg.get("method"), msg.get("params",{})
        if method=="item/tool/call" and "id" in msg:
            if p.get("turnId")!=turn_id or p.get("threadId")!=telemetry["engine_thread_id"]:
                raise ValueError("Tool call belongs to a different Codex turn")
            name = p["tool"]
            if name not in ("workbench_open","workbench_submit","workbench_confirm"):
                raise ValueError("Unexpected tool: "+name)
            received = time.monotonic()
            rec = {"tool":name,"call_id":p["callId"],"ticket":p["arguments"].get("ticket"),
                   "received_at":utc(),"elapsed_since_previous_display":received-last_delivery if last_delivery else None}
            try:
                parts = bench.handle(name,p["arguments"],p["callId"])
                ok = True
            except (KeyError,TypeError,ValueError) as exc:
                bench.errors += 1
                rec["error"] = str(exc)
                if bench.errors > 3 or "differs from the frozen" in str(exc):
                    raise
                parts = [{"type":"inputText","text":str(exc)+". Correct your submission for the same ticket."}]
                ok = False
            rpc.send({"id":msg["id"],"result":{"contentItems":parts,"success":ok}})
            last_delivery = time.monotonic()
            rec.update(success=ok,images_delivered=sum(x["type"]=="inputImage" for x in parts),
                       local_action_seconds=last_delivery-received)
            telemetry["workbench_calls"].append(rec)
            journal.write(json.dumps(rec)+"\n")
            journal.flush()
        elif "id" in msg and method:
            raise RuntimeError("Unexpected server request: "+method)
        elif p.get("threadId") != telemetry["engine_thread_id"]:
            continue
        elif method=="thread/tokenUsage/updated" and p.get("turnId")==turn_id:
            telemetry["token_usage"]=p["tokenUsage"]
        elif method and ("verification" in method.lower() or "rerout" in method.lower()):
            telemetry["model_events"].append({"method":method,"params":p})
            if "rerout" in method.lower():
                raise ValueError("Codex reported a model reroute")
        elif method=="item/completed" and p.get("turnId")==turn_id:
            item = p["item"]
            if item["type"] not in allowed_items:
                raise ValueError("Unexpected non-workbench item: "+item["type"])
            if item["type"]=="agentMessage":
                telemetry["last_message"]=item.get("text","")
        elif method=="turn/completed" and p["turn"]["id"]==turn_id:
            t=p["turn"]
            telemetry["server_turn"]={k:t.get(k) for k in ("id","status","durationMs","startedAt","completedAt","error")}
            if t["status"]!="completed" or not bench.done:
                raise RuntimeError(f"Turn ended before completion: {t.get('error') or t['status']}")
            return


def run(paths, output, base_policy, session_file, *, schedule=None, bench_factory=LiveWorkbench,
        definitions=tool_definitions, prompt=PROMPT):
    cfg=read(session_file)
    if cfg["framework"] != base_policy["framework"]:
        raise ValueError("Session configuration belongs to a different framework")
    policy = {**base_policy,"model":cfg["model"],"tier":cfg["service_tier"],"effort":cfg["reasoning_effort"]}
    output=Path(output).resolve()
    if len({p.name for p in paths})!=len(paths):
        raise ValueError("Each field needs a unique folder name")
    classifier = (Path(session_file).resolve().parent/cfg["classifier"]).resolve() if cfg.get("classifier") else None
    with inspection_lock(output):
        if (output/"transport.json").exists() or (output/"inspection.json").exists():
            raise ValueError("This native run already has evidence. Choose a fresh output for a new attempt.")
        started=time.monotonic()
        telemetry={"outer_thread_id":cfg["project_thread_id"],"started_at":utc(),"request":{
            "model":policy["model"],"reasoningEffort":policy["effort"],"serviceTier":policy["tier"]},
            "workbench_calls":[],"model_events":[],"token_usage":None,"state":"starting"}
        dump(output/"transport.json",telemetry)
        proc=None
        try:
            bench=bench_factory(paths,output,policy,classifier,schedule=schedule)
            with tempfile.TemporaryDirectory(prefix="codex-pathology-") as blank, (output/"server.stderr").open("w") as err, (output/"operations.jsonl").open("x") as journal:
                proc=subprocess.Popen(["codex","app-server","--listen","stdio://"],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
                rpc=Rpc(proc)
                rpc.call("initialize",{"clientInfo":{"name":"pathology_workbench","version":"0.1.0"},
                         "capabilities":{"experimentalApi":True}})
                rpc.send({"method":"initialized"})
                config=rpc.call("thread/start",{"ephemeral":True,"allowProviderModelFallback":False,
                    "model":policy["model"],"serviceTier":policy["tier"],"config":{"model_reasoning_effort":policy["effort"]},
                    "cwd":blank,"sandbox":"read-only","approvalPolicy":"never","dynamicTools":definitions()})
                telemetry["engine_thread_id"]=config["thread"]["id"]
                telemetry["accepted"]={k:config.get(k) for k in ("model","reasoningEffort","serviceTier","modelProvider")}
                if any(config.get(k)!=v for k,v in telemetry["request"].items()):
                    raise ValueError("Codex did not accept the requested model, effort and service tier")
                instructions=policy["instruction"]+" "+prompt
                telemetry.update(prompt=instructions,state="running",verified_at=utc())
                dump(output/"transport.json",telemetry)
                before=time.monotonic()
                reply=rpc.call("turn/start",{"threadId":telemetry["engine_thread_id"],
                    "model":policy["model"],"effort":policy["effort"],"serviceTier":policy["tier"],
                    "input":[{"type":"text","text":instructions}]})
                turn_id=reply["turn"]["id"]
                telemetry["turn_id"]=turn_id
                events(rpc,bench,turn_id,telemetry,journal)
                telemetry.update(turn_seconds=time.monotonic()-before,completed_rounds=bench.completed,
                                 invalid_submissions=bench.errors,state="completed")
        except BaseException as exc:
            telemetry.update(state="failed",error=str(exc))
            raise
        finally:
            if proc is not None:
                proc.stdin.close()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=6)
            telemetry.update(ended_at=utc(),elapsed_seconds=time.monotonic()-started)
            dump(output/"transport.json",telemetry)
    return {"output":str(output),"state":telemetry["state"],"fields":len(paths),"accepted":telemetry["accepted"],
            "seconds":telemetry["elapsed_seconds"],"rounds":telemetry["completed_rounds"]}
