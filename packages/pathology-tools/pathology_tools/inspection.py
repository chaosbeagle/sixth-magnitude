"""Run one-shot Codex and replay through the same decision machine as the native session."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from .native_workbench import LiveWorkbench, decision_schema
from .packets import read, sha, sheet
from .rounds import execute_model, save_review, start_inspection, validate


def recorded_policy(output, policy):
    path = Path(output)/"inspection.json"
    if not path.exists():
        return None
    info = read(path)
    if info["policy"]["framework"] != policy["framework"]:
        raise ValueError("Saved run belongs to a different framework")
    return info


def steps(paths, output, policy, replay_only=False, *, schedule=None):
    info = recorded_policy(output, policy)
    if (Path(output)/"transport.json").exists() and not replay_only:
        raise ValueError("Use --replay-only for a saved native run or choose a fresh output")
    if info and replay_only:
        policy = info["policy"]
    bench = LiveWorkbench(paths, output, policy, execute_candidates=False, schedule=schedule)
    bench.handle("workbench_open", {}, "open")
    while not bench.done:
        folder = bench.current
        sealed = (folder/"sealed.json").exists()
        attempt = None
        if sealed:
            rows = bench.sealed()["rows"]
        elif replay_only:
            raise ValueError(f"No sealed decision for offline replay at {folder}")
        else:
            prompt = (policy["instruction"] + " Use only the supplied evidence. Return the requested JSON, "
                      "with no tool calls. " + bench.prompt)
            images = [folder/(k+".png") for k in bench.ids]
            attempt, answer = execute_model(folder, prompt, images, decision_schema(bench.stage), policy)
            rows = read(answer)["decisions"]
        operation = "workbench_confirm" if bench.stage == "confirm" else "workbench_submit"
        bench.handle(operation, {"ticket": bench.ticket, "decisions": rows}, bench.ticket,
                     replay=sealed, attempt=attempt)
    return bench.decisions


def replay_legacy_native(paths, output, policy):
    fields, output = start_inspection(paths, output, policy)
    proposals, final = {}, {}
    for folder in sorted(output.glob("step-*")):
        req, seal = [read(folder/name) for name in ("request.json", "sealed.json")]
        rows = seal["rows"]
        if (sha(folder/"request.json") != seal["request_sha256"] or sha(folder/"answer.json") != seal["answer_sha256"]
                or rows != read(folder/"answer.json")["decisions"]):
            raise ValueError("Legacy decision differs from sealed request or answer")
        stage, ids = req["stage"], list(req["fields"])
        for key in ids:
            buf = BytesIO()
            sheet(fields[key], key, proposals[key] if stage == "review" else None).save(buf, format="PNG")
            name = key+".png"
            if (sha(fields[key]/"field.json") != req["fields"][key]
                    or sha256(buf.getvalue()).hexdigest() != sha(folder/name) or sha(folder/name) != req["images"][name]):
                raise ValueError("Legacy field or attachment differs from the sealed request")
        validate(rows, ids, stage, fields, proposals)
        if stage == "select":
            proposals.update({r["field"]: r["choice"] for r in rows})
        else:
            final.update(save_review(fields, output, proposals, rows, verify_existing=True))
    if set(final) != set(fields) or final != read(output/"decisions.json"):
        raise ValueError("Incomplete legacy predictions or inconsistent outcomes")
    return final
