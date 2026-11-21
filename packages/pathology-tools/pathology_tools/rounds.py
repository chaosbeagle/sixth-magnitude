"""Two frozen visual decisions. Hosts provide the policy, inputs and batching."""
import json
import hashlib
import os
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import time
import numpy as np
from .nuclei import NAMES, TOOLS
from .packets import dump, read, sha, sheet

LEGEND = ("Nucleus contours and IDs: cyan epithelial, yellow lymphocyte, green macrophage, "
          "orange neutrophil, white untyped. H&E is the raw image. Each attachment is a different field. "
          "Only use the attached images. No shell, file, network, or other tool calls. "
          "Output exactly one decision per field, with the field's ID and a brief specific visual reason.")
SELECT = ("Inspect the H&E and A overlay. Choose one action per field: baseline (keep); "
          "recover_faint (lower stain threshold); suppress_debris (raise threshold, reject small particles); "
          "split_touching (closer watershed seeds); reduce_fragments (wider seed spacing). "
          "Optimize one nucleus per visible boundary. Use baseline when evidence is inconclusive.")
REVIEW = ("Compare raw H&E, A baseline and B proposed. Choose A or B as the better segmentation; "
          "when tied choose A. IDs refer only to the panel chosen. Optionally correct up to three "
          "clearly mistyped nuclei by integer ID and cell type. Leave all other classifications alone.")

def object_schema(fields):
    return {"type":"object", "properties": fields, "required": list(fields), "additionalProperties":False}

def schema(stage):
    edit = object_schema({"id":{"type":"integer"}, "cell_type":{"type":"string","enum":list(NAMES[1:])}})
    fields = {"field":{"type":"string"}, "choice":{"type":"string",
              "enum":list(TOOLS) if stage=="select" else ["A","B"]}, "reason":{"type":"string"}}
    if stage=="review":
        fields["edits"] = {"type":"array", "items":edit}
    return object_schema({"decisions":{"type":"array","items":object_schema(fields)}})

def validate(rows, ids, stage, fields, proposals):
    if len(rows) != len(ids) or {r["field"] for r in rows} != set(ids):
        raise ValueError("Missing, duplicate or unexpected field ID")
    for r in rows:
        if not r["reason"].strip():
            raise ValueError("Empty visual reason")
        if stage=="select" and r["choice"] not in TOOLS:
            raise ValueError("Unknown tool")
        if stage=="review":
            if r["choice"] not in ("A","B"):
                raise ValueError("Unknown review panel")
            tool = "baseline" if r["choice"]=="A" else proposals[r["field"]]
            with np.load(fields[r["field"]] / f"{tool}.npz") as f:
                found = set(np.unique(f["labels"])) - {0}
            edits = r["edits"]
            if len(edits)>3 or len({e["id"] for e in edits})!=len(edits):
                raise ValueError("Too many or duplicate class edits")
            if any(e["id"] not in found or e["cell_type"] not in NAMES[1:] for e in edits):
                raise ValueError("Edit refers to missing ID or class")

@contextmanager
def inspection_lock(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output/".inspection.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell()==0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def invoke(root, ids, fields, stage, proposals, policy, replay_only=False):
    root.mkdir(parents=True, exist_ok=True)
    prompt = " ".join([policy["instruction"], LEGEND, SELECT if stage=="select" else REVIEW,
                       "Field IDs in attachment order: " + ", ".join(ids)])
    images = []
    receipt = root / "request.json"
    for key in ids:
        image = root / (key + ".png")
        rendered = sheet(fields[key], key, proposals[key] if stage=="review" else None)
        if not receipt.exists():
            rendered.save(image)
        else:
            buf = BytesIO()
            rendered.save(buf, format="PNG")
            if sha(image)!=hashlib.sha256(buf.getvalue()).hexdigest():
                raise ValueError("Recorded attachment differs from current field or proposal")
        images.append(image)
    request = {"policy":policy, "stage":stage, "prompt":prompt, "schema":schema(stage),
               "images":{p.name:sha(p) for p in images},
               "fields":{k:sha(fields[k]/"field.json") for k in ids}}
    if receipt.exists() and read(receipt)!=request:
        raise ValueError("Resume inputs differ from recorded request")
    if not receipt.exists():
        dump(receipt, request)
    accepted = root / "sealed.json"
    if accepted.exists():
        saved = read(accepted)
        if saved["request_sha256"] != sha(receipt):
            raise ValueError("Sealed response is bound to a different request")
        raw_answer = root / saved["attempt"] / "answer.json"
        if saved["answer_sha256"] != sha(raw_answer) or read(raw_answer)["decisions"] != saved["rows"]:
            raise ValueError("Sealed response differs from original model answer")
        validate(saved["rows"], ids, stage, fields, proposals)
        return saved["rows"]
    if replay_only:
        raise ValueError(f"No sealed decision for offline replay at {root}")
    attempt, answer = execute_model(root, prompt, images, schema(stage), policy)
    rows = read(answer)["decisions"]
    validate(rows, ids, stage, fields, proposals)
    dump(accepted,{"request_sha256":sha(receipt),"answer_sha256":sha(answer),
                   "attempt":attempt.name,"rows":rows})
    return rows

def execute_model(root, prompt, images, response_schema, policy):
    attempt = root / f"attempt-{1 + len(list(root.glob('attempt-*'))):02}"
    attempt.mkdir()
    dump(attempt/"schema.json", response_schema)
    answer = attempt/"answer.json"
    with tempfile.TemporaryDirectory(prefix="blind-vision-") as blank:
        cmd = ["codex","exec","--ignore-user-config","--ephemeral","--skip-git-repo-check",
               "-s","read-only","-C",blank,"-m",policy["model"],
               "-c",'model_reasoning_effort="'+policy["effort"]+'"',
               "-c",'service_tier="'+policy["tier"]+'"', "--json",
               "--output-schema",str(attempt/"schema.json"),"-o",str(answer)]
        for image in images:
            cmd.extend(["-i",str(image)])
        cmd.extend(["--",prompt])
        started = time.monotonic()
        with (attempt/"events.jsonl").open("w") as out, (attempt/"stderr.txt").open("w") as err:
            result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=240)
        elapsed = time.monotonic()-started
    events = [json.loads(s) for s in (attempt/"events.jsonl").read_text().splitlines() if s.strip()]
    forbidden = [e for e in events if e.get("item",{}).get("type") not in
                 (None,"agent_message","reasoning","plan") and e.get("type","").startswith("item.")]
    usage = [e.get("usage") for e in events if e.get("type")=="turn.completed"]
    meta = {"elapsed_seconds":elapsed,"returncode":result.returncode,"usage":usage,
            "tool_events":len(forbidden),"requested_model":policy["model"],"requested_tier":policy["tier"],
            "response_model":None,"response_tier":None}
    dump(attempt/"receipt.json",meta)
    if result.returncode or forbidden or len(usage)!=1 or not answer.exists():
        raise ValueError(f"Visual call failed or invoked tools; inspect {attempt}")
    return attempt, answer

def start_inspection(fields, output, policy, protocol=None):
    fields = {Path(f).name:Path(f).resolve() for f in fields} if not isinstance(fields,dict) else fields
    if not fields:
        raise ValueError("At least one field is required")
    for key, f in fields.items():
        info = read(f/"field.json")
        if not key.isascii() or not key.replace("-","").replace("_","").isalnum():
            raise ValueError("Invalid field ID")
        required = {"source.png", *(name+".npz" for name in info["tools"])}
        if not required.issubset(info["files"]) or any(Path(name).name != name for name in info["files"]):
            raise ValueError("Field manifest omits a candidate or contains an invalid file name")
        if any(sha(f/name) != h for name,h in info["files"].items()):
            raise ValueError("Candidate packet changed on disk")
    output = Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    manifest = {"policy":policy, "field_hashes":{k:sha(v/"field.json") for k,v in fields.items()}}
    if protocol is not None:
        manifest.update(protocol=protocol, field_order=list(fields))
    manifest_path = output/"inspection.json"
    if manifest_path.exists() and read(manifest_path)!=manifest:
        raise ValueError("Inspection inputs or policy changed; choose a new output")
    if not manifest_path.exists():
        dump(manifest_path,manifest)
    return fields, output

def save_review(fields, output, proposals, decisions, *, verify_existing=False):
    final = {}
    for r in decisions:
        k = r["field"]
        tool = proposals[k] if r["choice"]=="B" else "baseline"
        with np.load(fields[k]/f"{tool}.npz") as f:
            labels, types = f["labels"], f["types"].copy()
        for edit in r["edits"]:
            types[edit["id"]] = NAMES.index(edit["cell_type"])
        if verify_existing and (output/(k+".npz")).exists():
            with np.load(output/(k+".npz")) as existing:
                if not (np.array_equal(existing["labels"], labels) and np.array_equal(existing["types"], types)):
                    raise ValueError("Saved prediction differs from sealed decisions")
        else:
            with tempfile.NamedTemporaryFile(dir=output, delete=False, suffix=".npz") as f:
                temp = Path(f.name)
            try:
                np.savez_compressed(temp,labels=labels,types=types)
                os.replace(temp, output/(k+".npz"))
            finally:
                temp.unlink(missing_ok=True)
        final[k] = {"proposed":proposals[k], "retained":tool, "edits":r["edits"],
                    "sha256":sha(output/(k+".npz"))}
    return final
