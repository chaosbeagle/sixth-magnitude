"""Read explicit Codex session boundaries; never invent model effort or dollar cost."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid

from .store import atomic_json, read, utc

BOUNDARY_NOTE = "Token deltas begin at the recorded baseline counter timestamp, which may precede binding and include an unflushed pre-bind tail. Wall time begins at binding. Final/unflushed tail is excluded."


def snapshot(path):
    import json
    usage=None
    config={}
    for line in Path(path).open(encoding="utf-8"):
        try:
            item=json.loads(line)
        except json.JSONDecodeError:
            continue  # Last line may still be flushing.
        payload=item.get("payload",{})
        if item.get("type")=="turn_context":
            config={"model":payload.get("model"),"effort":payload.get("effort") or payload.get("reasoning_effort")}
        if item.get("type")=="event_msg" and payload.get("type")=="token_count":
            counters=(payload.get("info") or {}).get("total_token_usage")
            if counters:
                usage={"utc":item["timestamp"],"counters":counters,"configuration":dict(config)}
    return usage,config


def bind(store, path):
    path=Path(path).resolve()
    if not path.is_file() or path.suffix!=".jsonl":
        raise ValueError("An explicit Codex session JSONL is required")
    baseline,config=snapshot(path)
    sid=uuid.uuid4().hex
    record={"segment_id":sid,"source_log":str(path),"start_utc":utc(),"end_utc":None,
        "baseline":baseline,"configuration_at_bind":config,"note":BOUNDARY_NOTE}
    with store.writing():
        for existing in (store.root/"telemetry").glob("*.json"):
            old=read(existing)
            if old["end_utc"] is None:
                raise ValueError("Close the active telemetry segment before binding another")
        atomic_json(store.root/"telemetry"/f"{sid}.json",record,exclusive=True)
    return record


def report(store, close=False):
    rows=[]
    for file in sorted((store.root/"telemetry").glob("*.json")):
        segment=read(file)
        if segment["end_utc"]:
            rows.append(segment["report"])
            continue
        end,config=snapshot(segment["source_log"])
        base=segment["baseline"]
        tokens=None
        if base and end:
            keys=set(base["counters"])|set(end["counters"])
            tokens={k: end["counters"][k]-base["counters"][k]
                    if isinstance(end["counters"].get(k),(int,float)) and isinstance(base["counters"].get(k),(int,float)) else None for k in keys}
            if any(v is not None and v<0 for v in tokens.values()):
                raise ValueError("Session counters decreased; do not join unrelated runs")
            if tokens.get("input_tokens") is not None and tokens.get("cached_input_tokens") is not None:
                tokens["uncached_input_tokens"]=tokens["input_tokens"]-tokens["cached_input_tokens"]
        row={"segment_id":segment["segment_id"],"start_utc":segment["start_utc"],"wall_end_utc":utc(),
             "token_baseline_utc":base["utc"] if base else None,
             "usage_cutoff_utc":end["utc"] if end else None,"tokens":tokens,
             "configuration_at_bind":segment["configuration_at_bind"],"configuration_latest":config,
             "configuration_changed":config!=segment["configuration_at_bind"],"dollar_cost":None,
             "boundary_note":BOUNDARY_NOTE}
        row["wall_seconds"]=(datetime.fromisoformat(row["wall_end_utc"])-datetime.fromisoformat(row["start_utc"])).total_seconds()
        if close:
            segment.update(end_utc=row["wall_end_utc"],report=row)
            with store.writing():
                atomic_json(file,segment)
        rows.append(row)
    return {"segments":rows,"available":any(r["tokens"] is not None for r in rows),"reasoning_is_output_subset":True,
            "note":"No linked session means token usage is unavailable, not zero."}
