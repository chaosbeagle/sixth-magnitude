"""Evidence-bound field selection, review, and conditional object confirmation."""
import base64
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .evidence import alternative_sheet, edit_evidence, field_summary
from .nuclei import LOCAL_TOOL, NAMES, TOOLS, classify, segment
from .packets import dump, read, sha, sheet
from .rounds import object_schema, save_review, start_inspection, validate

PROTOCOL = 3
FEATURES = ["internal_structure", "relative_size", "tissue_context", "surface_appearance", "insufficient"]
LEGEND = ("Contours: cyan epithelial, yellow lymphocyte, green macrophage, orange neutrophil, white untyped. "
          "QC flags and classifier predictions are fallible. A negative flag does not certify segmentation.")
SELECT = ("Choose baseline to accept geometry and typing, inspect_baseline for a typing question, "
          "recover_faint for a weaker stain threshold, suppress_debris for a stronger one, split_touching or "
          "reduce_fragments for changed seed spacing. Where listed, local_gradient uses stain-independent local "
          "optical density and gradient watershed. Choose only tools available for that field. Submit no edits.")
REVIEW = ("Compare raw, A baseline and B candidate. Choose A, B, or neither when both are inadequate. "
          "Choose A on a tie. You may nominate up to three type changes on the retained panel by ID and type. "
          "They are proposals until a separate close-up review. Submit no edits with neither.")
CONFIRM = ("Decide each pending ID after viewing its clean source, baseline and retained boundaries, and a "
           "same-scale peer from the proposed class when available. Peer classes are predictions. Areas are native "
           "pixels. The score difference is uncalibrated. Check whether the boundary cuts through the object. "
           "For each ID give apply or keep, boundary_clear, the decisive feature, and a specific observation. "
           "Roundness or darkness alone is surface_appearance. Use keep when evidence is insufficient.")


def utc():
    return datetime.now(timezone.utc).isoformat()


def decision_schema(stage=None):
    array = lambda item: {"type": "array", "items": item}
    string = {"type": "string"}
    if stage == "confirm":
        verdict = object_schema({"id": {"type": "integer"}, "decision": {"type": "string", "enum": ["apply", "keep"]},
                                 "boundary_clear": {"type": "boolean"}, "feature": {"type": "string", "enum": FEATURES},
                                 "reason": string})
        decision = object_schema({"field": string, "verdicts": array(verdict)})
    else:
        select = [*TOOLS, LOCAL_TOOL, "inspect_baseline"]
        review = ["A", "B", "neither"]
        if stage == "alternatives":
            options = [*TOOLS, LOCAL_TOOL, "neither"]
        else:
            options = select if stage == "select" else review if stage in ("review", "rescue") else select + review
        edit = object_schema({"id": {"type": "integer"}, "cell_type": {"type": "string", "enum": list(NAMES[1:])}})
        decision = object_schema({"field": string, "choice": {"type": "string", "enum": options},
                                  "reason": string, "edits": array(edit)})
    return object_schema({"decisions": array(decision)})


def tool_definitions():
    tools = [{"type": "function", "name": "workbench_open", "deferLoading": False,
              "description": "View the current workbench ticket and its attached images.", "inputSchema": object_schema({})}]
    for name, stage, description in (
            ("workbench_submit", None, "Submit field selections or nominate class edits after segmentation review."),
            ("workbench_confirm", "confirm", "Adjudicate proposed edits after looking at the individual object crops.")):
        properties = {"ticket": {"type": "string"}, **decision_schema(stage)["properties"]}
        tools.append({"type": "function", "name": name, "deferLoading": False, "description": description,
                      "inputSchema": object_schema(properties)})
    return tools


def batches(keys, size):
    keys = list(keys)
    for i in range(0, len(keys), size):
        yield keys[i:i+size]


class LiveWorkbench:
    def __init__(self, paths, output, policy, classifier=None, *, execute_candidates=True, schedule=None):
        manifest = Path(output)/"inspection.json"
        self.protocol = read(manifest).get("protocol") if manifest.exists() else PROTOCOL
        if self.protocol not in (2, 3):
            raise ValueError("Unsupported evidence protocol")
        self.fields, self.output = start_inspection(paths, output, policy, protocol=self.protocol)
        self.policy = policy
        self.execute_candidates = execute_candidates
        self.weights = read(classifier) if classifier else None
        if execute_candidates:
            weight_hash = sha(classifier) if classifier else None
            for field in self.fields.values():
                if read(field/"field.json")["classifier_sha256"] != weight_hash:
                    raise ValueError("Classifier differs from the one used to freeze the input packet")
        self.summaries = {k: field_summary(field) for k, field in self.fields.items()}
        self.selections, self.proposals, self.reviews, self.decisions = {}, {}, {}, {}
        self.alternatives, self.reserve = {}, {}
        self.opened = self.done = False
        self.current = None
        self.errors = self.completed = 0
        self.rounds = schedule(self) if schedule else self.schedule()

    def schedule(self):
        size = self.policy["batch"]
        # Direct library callers retain a two-pass schedule. The two hosts inject their own workflow.
        groups = [[k] for k in self.fields] if size == 1 else [list(self.fields)]
        for group in groups:
            for keys in batches(group, size):
                yield "select", keys
            order = sorted(group, key=lambda k: self.proposals[k] == "baseline")
            for keys in batches(order, size):
                yield "review", keys
            for k in order:
                if not self.reviews[k]["edits"]:
                    self.finish(k)
            for keys in batches([k for k in order if self.reviews[k]["edits"]], size):
                yield "confirm", keys

    def candidate(self, key):
        return self.reserve.get(key, LOCAL_TOOL) if self.stage == "rescue" else self.proposals[key]

    def retained(self, key):
        row = self.reviews[key]
        return row["candidate"] if row["choice"] == "B" else "baseline"

    def finish(self, key, verdicts=(), triaged=False):
        row = self.selections[key] if triaged else self.reviews[key]
        tool = "baseline" if triaged else self.retained(key)
        applied, adjudicated = [], []
        for v in verdicts:
            fact = self.facts[key][str(v["id"])]
            if v["decision"] == "keep":
                outcome = "kept_by_reviewer"
            elif not v["boundary_clear"]:
                outcome = "held_uncertain_boundary"
            elif v["feature"] in ("surface_appearance", "insufficient"):
                outcome = "held_insufficient_evidence"
            elif v["feature"] == "relative_size" and fact["peer_id"] is None:
                outcome = "held_no_size_comparator"
            else:
                outcome = "applied"
                applied.append({"id": v["id"], "cell_type": fact["to"]})
            adjudicated.append({**v, "outcome": outcome})
        saved = save_review(self.fields, self.output, {key: tool},
                            [{"field": key, "choice": "B", "edits": applied}], verify_existing=True)[key]
        self.decisions[key] = {**saved, "proposed": self.proposals[key], "selected": self.selections[key]["choice"],
                               "status": "triaged" if triaged else "unresolved" if row["choice"] == "neither" else "reviewed",
                               "review_candidate": None if triaged else row["candidate"], "reason": row["reason"],
                               "nominations": row["edits"], "adjudications": adjudicated}
        if self.protocol >= 3:
            self.decisions[key]["additional_candidates_shown"] = self.alternatives.get(key, [])

    def display(self):
        if self.current is None:
            try:
                self.stage, self.ids = next(self.rounds)
            except StopIteration:
                if set(self.decisions) != set(self.fields):
                    raise ValueError("Missing final predictions")
                final = self.output/"decisions.json"
                if final.exists() and read(final) != self.decisions:
                    raise ValueError("Recorded outcomes differ from the decisions reconstructed from the seals")
                if not final.exists():
                    dump(final, self.decisions)
                self.done = True
                return [{"type": "inputText", "text": f"COMPLETE. All {len(self.fields)} fields are sealed."}]
            self.ticket = f"step-{self.completed+1:02}-{self.stage}"
            self.current = self.output/self.ticket
            self.current.mkdir(exist_ok=True)
            self.prepare_display()
        parts = [{"type": "inputText", "text": self.prompt}]
        for key in self.ids:
            png = (self.current/(key+".png")).read_bytes()
            parts.append({"type": "inputImage", "imageUrl": "data:image/png;base64,"+base64.b64encode(png).decode("ascii")})
        return parts

    def prepare_display(self):
        stage = self.stage
        self.facts, images = {}, {}
        existing = self.current/"request.json"
        for k in self.ids:
            if stage == "confirm":
                frame, facts = edit_evidence(self.fields[k], k, self.retained(k), self.reviews[k]["edits"],
                                             paired=self.protocol >= 3)
            elif stage == "alternatives":
                frame, facts = alternative_sheet(self.fields[k], self.alternatives[k])
            else:
                tool = None if stage == "select" else self.candidate(k)
                frame = sheet(self.fields[k], k, tool)
                facts = self.summaries[k]
                if tool and self.protocol >= 3:
                    with np.load(self.fields[k]/(tool+".npz")) as z:
                        labels = z["labels"]
                    facts = {**facts, "B": {"tool": tool, "objects": int(labels.max()),
                                             "foreground_fraction": round(float((labels > 0).mean()), 3)}}
            self.facts[k] = facts
            png = self.current/(k+".png")
            buf = BytesIO()
            frame.save(buf, format="PNG")
            digest = sha256(buf.getvalue()).hexdigest()
            if existing.exists():
                if not png.exists() or sha(png) != digest:
                    raise ValueError("Recorded attachment differs from current field or proposal")
            else:
                png.write_bytes(buf.getvalue())
            images[png.name] = digest
        action = CONFIRM if stage == "confirm" else SELECT if stage == "select" else REVIEW
        if stage == "confirm" and self.protocol >= 3:
            action += (" Compare both predicted classes at the same scale. A missing peer is unknown, and failure "
                       "to see a hallmark does not establish the competing class.")
        if stage == "alternatives":
            action = ("A and B were judged inadequate. Compare the remaining distinct geometries in the contact sheet. "
                      "Name one of the displayed tools for full-size review or choose neither. Submit no edits.")
        operation = "workbench_confirm" if stage == "confirm" else "workbench_submit"
        self.prompt = (f"Ticket {self.ticket}. Fields in order: {', '.join(self.ids)}. {LEGEND} {action} "
                       f"In the native session use {operation}. Submit exactly one row for each field. "
                       f"Measurements by field: {json.dumps(self.facts, separators=(',', ':'))}")
        request = {"protocol": self.protocol, "policy": self.policy, "stage": stage, "ticket": self.ticket,
                   "fields": {k: sha(self.fields[k]/"field.json") for k in self.ids}, "images": images,
                   "prompt": self.prompt, "evidence": self.facts}
        if existing.exists():
            recorded = read(existing)
            recorded.pop("prepared_at", None)
            if recorded != request:
                raise ValueError("Recorded request differs from reconstructed evidence")
        else:
            dump(existing, {**request, "prepared_at": utc()})

    def rerun(self, key, tool):
        field = self.fields[key]
        if not self.execute_candidates:
            return {"tool": tool, "mode": "frozen", "reference_npz_sha256": sha(field/(tool+".npz"))}
        with Image.open(field/"source.png") as img:
            rgb = np.array(img.convert("RGB"))
        started = time.monotonic()
        labels = segment(rgb, tool)
        types = classify(rgb, labels, self.weights) if self.weights else np.zeros(int(labels.max())+1, np.uint8)
        with np.load(field/(tool+".npz")) as original:
            if not (np.array_equal(original["labels"], labels) and np.array_equal(original["types"], types)):
                raise ValueError("Live algorithm result differs from the frozen candidate")
        return {"tool": tool, "runtime_seconds": time.monotonic()-started,
                "reference_npz_sha256": sha(field/(tool+".npz")), "matched_frozen_arrays": True}

    def check(self, rows):
        if len(rows) != len(self.ids) or {r["field"] for r in rows} != set(self.ids):
            raise ValueError("Missing, duplicate or unexpected field ID")
        for r in rows:
            k = r["field"]
            if self.stage == "confirm":
                verdicts = r["verdicts"]
                if sorted(v["id"] for v in verdicts) != sorted(int(i) for i in self.facts[k]):
                    raise ValueError("Confirm exactly the nominated object IDs on this ticket")
                for v in verdicts:
                    if (type(v["id"]) is not int or v["decision"] not in ("apply", "keep") or v["feature"] not in FEATURES
                            or type(v["boundary_clear"]) is not bool or not v["reason"].strip()):
                        raise ValueError("Incomplete confirmation evidence")
                continue
            choice, edits = r["choice"], r["edits"]
            if not r["reason"].strip():
                raise ValueError("A concrete visual reason is required")
            if self.stage == "select":
                if choice not in [*self.summaries[k]["tools"], "inspect_baseline"] or edits:
                    raise ValueError("Select a tool available in this packet and leave edits empty")
            elif self.stage == "alternatives":
                if choice not in [*self.alternatives[k], "neither"] or edits:
                    raise ValueError("Choose a displayed alternative or neither, with no type edits")
            elif choice == "neither":
                if edits:
                    raise ValueError("An unresolved segmentation cannot support type edits")
            else:
                validate([r], [k], "review", self.fields, {k: self.candidate(k)})
                tool = self.candidate(k) if choice == "B" else "baseline"
                with np.load(self.fields[k]/(tool+".npz")) as z:
                    types = z["types"]
                if any(type(e["id"]) is not int or types[e["id"]] == NAMES.index(e["cell_type"]) for e in edits):
                    raise ValueError("Nominate an existing integer ID with a different proposed type")

    def sealed(self):
        seal = read(self.current/"sealed.json")
        answer = self.current/"answer.json"
        if (sha(self.current/"request.json") != seal["request_sha256"] or sha(answer) != seal["answer_sha256"]
                or read(answer)["decisions"] != seal["rows"]):
            raise ValueError("Sealed decision differs from its request or original answer")
        if seal.get("attempt"):
            original = self.current/seal["attempt"]/"answer.json"
            if sha(original) != seal["attempt_sha256"] or read(original)["decisions"] != seal["rows"]:
                raise ValueError("Sealed decision differs from its original attempt")
        return seal

    def handle(self, name, args, call_id, *, attempt=None, replay=False):
        if self.done:
            raise ValueError("The workbench is complete")
        if name == "workbench_open":
            self.opened = True
            return self.display()
        if not self.opened or args.get("ticket") != self.ticket:
            raise ValueError("Open the workbench or use the current ticket")
        expected = "workbench_confirm" if self.stage == "confirm" else "workbench_submit"
        if name != expected:
            raise ValueError(f"Use {expected} for the current ticket")
        rows = args["decisions"]
        self.check(rows)
        executed = {}
        if replay:
            if self.sealed()["rows"] != rows:
                raise ValueError("Replay differs from the sealed decision")
        elif (self.current/"sealed.json").exists():
            raise ValueError("This ticket already has a sealed decision")
        if self.stage == "select":
            for row in rows:
                key, choice = row["field"], row["choice"]
                candidate = "baseline" if choice == "inspect_baseline" else choice
                executed[key] = self.rerun(key, candidate)
                self.proposals[key], self.selections[key] = candidate, row
        elif self.stage == "alternatives":
            for row in rows:
                k = row["field"]
                if row["choice"] == "neither":
                    self.reviews[k]["reason"] = row["reason"]
                else:
                    self.reserve[k] = row["choice"]
        elif self.stage in ("review", "rescue"):
            for row in rows:
                k = row["field"]
                if self.stage == "rescue":
                    executed[k] = self.rerun(k, self.candidate(k))
                self.reviews[k] = {**row, "candidate": self.candidate(k)}
        if not replay:
            dump(self.current/"answer.json", {"decisions": rows})
            seal = {"rows": rows, "call_id": call_id, "closed_at": utc(),
                    "request_sha256": sha(self.current/"request.json"), "answer_sha256": sha(self.current/"answer.json"),
                    "live_algorithm": executed}
            if attempt:
                seal.update(attempt=attempt.name, attempt_sha256=sha(attempt/"answer.json"))
            dump(self.current/"sealed.json", seal)
        # Publish object overrides only after the entire ticket is validated and durably sealed.
        if self.stage == "confirm":
            for row in rows:
                self.finish(row["field"], row["verdicts"])
        self.completed += 1
        self.current = None
        return self.display()
