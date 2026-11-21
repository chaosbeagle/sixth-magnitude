"""Astra-based toolbox access in original workspace coordinates."""
from pathlib import Path
from .imaging import normalize_source
from .readers import open_reader
from .store import Store

POLICY = {"framework":"Sixth Magnitude v2","model":"gpt-6.2-astra","tier":"priority", "workflow":"individual-rescue-v2",
          "effort":"low","batch":1,
          "instruction":"Inspect this individual field carefully. Compare centre and edges, consider "
                        "tissue context, and state the specific location and morphology supporting your choice."}

def schedule(bench):
    from pathology_tools.evidence import unique_alternatives
    from pathology_tools.nuclei import LOCAL_TOOL
    for key in bench.fields:
        yield "select", [key]
        yield "review", [key]
        if bench.reviews[key]["choice"] == "neither":
            if bench.protocol >= 3:
                bench.alternatives[key] = unique_alternatives(bench.fields[key], ["baseline", bench.proposals[key]])
                if bench.alternatives[key]:
                    yield "alternatives", [key]
                if key in bench.reserve:
                    yield "rescue", [key]
            elif LOCAL_TOOL in bench.summaries[key]["tools"] and bench.proposals[key] != LOCAL_TOOL:
                yield "rescue", [key]
        if bench.reviews[key]["edits"]:
            yield "confirm", [key]
        else:
            bench.finish(key)


def command(args):
    from pathology_tools.packets import prepare
    if args.operation=="inspect":
        if getattr(args, "codex_session", None):
            if args.replay_only:
                raise ValueError("A live Codex session requires a fresh output")
            from pathology_tools.codex_session import run
            return run(args.fields, args.output, POLICY, args.codex_session, schedule=schedule)
        if len(set(f.name for f in args.fields)) != len(args.fields):
            raise ValueError("Each field needs a unique directory name")
        from pathology_tools.rounds import start_inspection, invoke, save_review, inspection_lock
        from pathology_tools.inspection import recorded_policy, replay_legacy_native, steps
        from pathology_tools.packets import dump
        with inspection_lock(args.output):
            record = recorded_policy(args.output, POLICY)
            if not record or "protocol" in record:
                return steps(args.fields, args.output, POLICY, args.replay_only, schedule=schedule)
            legacy_policy = record["policy"]
            if (Path(args.output)/"transport.json").exists():
                if not args.replay_only:
                    raise ValueError("Use --replay-only for a sealed native run or choose a fresh output")
                return replay_legacy_native(args.fields, args.output, legacy_policy)
            fields, output = start_inspection(args.fields, args.output, legacy_policy)
            final = {}
            for key in fields:
                folder = output / key
                first = invoke(folder/"select", [key], fields, "select", {}, legacy_policy, args.replay_only)
                proposal = {key:first[0]["choice"]}
                review = invoke(folder/"review", [key], fields, "review", proposal, legacy_policy, args.replay_only)
                final.update(save_review(fields, output, proposal, review))
            dump(output/"decisions.json", final)
            return final
    source = normalize_source(Store(args.run).source(args.asset_id))
    x,y,w,h = args.roi
    if min(x,y)<0 or min(w,h)<1 or max(w,h)>1024 or x+w>source["width"] or y+h>source["height"]:
        raise ValueError("ROI must be inside the source and at most 1024 pixels per side")
    with open_reader(source) as reader:
        rgb = reader.read_rgba(0,x,y,w,h)[:,:,:3]
    mpp = [source["mpp_x"], source["mpp_y"]] if source.get("mpp_x") and source.get("mpp_y") else None
    return str(prepare(rgb,(x,y),args.asset_id,args.field,args.classifier,source["source_key"],
                       reference=getattr(args,"reference_image",None),
                       native=source.get("reader") in ("openslide","isyntax"), mpp=mpp,
                       legacy=getattr(args,"legacy_packet",False)))

def arguments(sub):
    p = sub.add_parser("toolbox",help="Prepare a bounded H&E field or inspect prepared fields with Astra")
    ops = p.add_subparsers(dest="operation",required=True)
    make = ops.add_parser("prepare")
    make.add_argument("--run",required=True)
    make.add_argument("--asset-id",required=True)
    make.add_argument("--roi",nargs=4,type=int,metavar=("X","Y","W","H"),required=True)
    make.add_argument("--classifier",type=Path)
    make.add_argument("--reference-image",type=Path,help="Aligned native slide or canonical RGB raster in source level-zero coordinates")
    make.add_argument("--legacy-packet",action="store_true",help="Reconstruct the five frozen pilot candidates")
    make.add_argument("--field",type=Path,required=True)
    see = ops.add_parser("inspect")
    see.add_argument("--fields",type=Path,nargs="+",required=True)
    see.add_argument("--output",type=Path,required=True)
    see.add_argument("--codex-session", type=Path, help="Operate the workbench in a single live Codex session using this configuration")

    see.add_argument("--replay-only",action="store_true",help="Require sealed decisions and use no model connection")
