"""Deterministic exports from saved versions; no inferred visual/human approval."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
from PIL import Image

from .store import atomic_json, read, utc
from .morphometry import measure, FIELDS


def flat(value, prefix=""):
    rows = {}
    for key,item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item,dict):
            rows.update(flat(item,name))
        else:
            rows[name]=item
    return rows


def table(directory, name, rows, columns):
    columns = list(dict.fromkeys(columns+[k for row in rows for k in row]))
    with (directory/f"{name}.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    if rows:
        arrow=pa.Table.from_pylist([{k:r.get(k) for k in columns} for r in rows])
    else:
        arrow=pa.table({k:pa.array([],type=pa.string()) for k in columns})
    pq.write_table(arrow,directory/f"{name}.parquet")


def export_run(store):
    if store.profile == "generic":
        from .generic_export import export_generic
        return export_generic(store)
    states={case:store.state(case) for case in store.cases}
    signature=hashlib.sha256(json.dumps({c:s["revision"] for c,s in states.items()},sort_keys=True).encode()).hexdigest()[:12]
    directory=store.root/"exports"/(utc().replace(":","").replace(".","-")+"_"+signature)
    directory.mkdir(parents=True,exist_ok=False)
    annotations,observations,research,coverage,events,sessions,evidence_rows=[],[],[],[],[],[],[]
    features=[]
    measurements=[]
    for case,state in states.items():
        source=store.source(case)
        measurement, grid=measure(state, source, store.manifest['purpose'], raster=True)
        if grid is not None:
            # Portable measured map, not a segmentation generated from the image.
            pixels=np.zeros(grid['domain'].shape,dtype=np.uint8)
            pixels[grid['domain']]=1
            pixels[grid['domain'] & grid['excluded']]=2
            pixels[grid['known']]=grid['labels'][grid['known']]+3
            pixels[grid['domain'] & grid['conflict']]=9
            Image.fromarray(pixels).save(directory/f'{case}.morphometry.png')
            measurement['raster']={'file':f'{case}.morphometry.png','bbox_level0':grid['bbox_level0'],
                'codes':{'0':'outside','1':'unresolved','2':'excluded','3':'lepidic','4':'acinar','5':'papillary','6':'micropapillary','7':'solid','8':'complex_glandular','9':'conflict'}}
        measurements.append(measurement)
        for file in store.revisions(case):
            row=read(file)
            events.append({"case_id":case,"case_revision":row["revision"],**flat(row["last_event"])})
        for sid,session in state["review_sessions"].items():
            sessions.append({"case_id":case,"session_id":sid,**flat(session)})
        active=[a for a in state["annotations"].values() if not a["deleted"]]
        coverage.append({"case_id":case,"slide_token":source["slide_token"],"source_key":source["source_key"],
            "status":state["status"],"case_revision":state["revision"],"annotation_count":len(active),
            "task_mode":store.manifest["task_mode"],"whole_slide_exhaustive":False,"status_note":state["status_note"]})
        xml=ET.Element("ASAP_Annotations")
        nodes=ET.SubElement(xml,"Annotations")
        groups=ET.SubElement(xml,"AnnotationGroups")
        for annotation in active:
            obs=annotation["observation"]
            geom=annotation["geometry"]
            x0,y0,x1,y1=geom["bbox_level0"]
            points=geom["points"] or [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
            common={"run_id":store.manifest["run_id"],"case_id":case,"slide_token":source["slide_token"],
                "annotation_id":annotation["id"],"annotation_version":annotation["version"],"case_revision":state["revision"],
                "source_key":source["source_key"],"geometry_revision":annotation["geometry_revision"],
                "observation_revision":annotation["observation_revision"],"evidence_id":annotation["evidence_id"],
                "visual_state":annotation["visual"]["state"],"physician_review_state":annotation["review"]["state"],
                "content_hash":annotation["content_hash"],"label_origin":annotation["origin"],"label":obs["label"]}
            annotations.append({**common,**flat({"geometry":geom}),"width_um":(x1-x0)*source["mpp_x"],
                "height_um":(y1-y0)*source["mpp_y"],"schema_version":obs["schema_version"],**flat({'measurement':annotation.get('measurement',{})})})
            evidence=read(store.root/"evidence"/annotation["evidence_id"]/"metadata.json")
            evidence_rows.append({**common,"metadata":evidence})
            for scope in ("focus","context"):
                observation={**common,"scope":scope,"support": "focus_geometry" if scope=="focus" else "context_window_minus_focus_geometry",
                    **flat(obs[scope]),**flat({k:obs[k] for k in ("relationship","quality","special")})}
                observations.append(observation)
                if (store.manifest["purpose"]=="annotation" and annotation['origin']!='engineering' and annotation["review"]["state"]=="physician_reviewed"
                    and annotation["review"].get("content_hash")==annotation["content_hash"]
                    and annotation["visual"]["state"]=="matched" and annotation["visual"].get("content_hash")==annotation["content_hash"]):
                    research.append({**observation,"reviewer_id":annotation["review"]["actor"]["id"],"review_utc":annotation["review"]["utc"]})
            features.append({"type":"Feature","id":annotation["id"],"geometry":{"type":"Polygon","coordinates":[points+[points[0]]] },
                "properties":{**common,"coordinate_frame":"reader_level0_px","geometry_role":geom["role"],"classification":{"name":obs["label"]},"observation":obs,"measurement":annotation.get('measurement')}})
            node=ET.SubElement(nodes,"Annotation",{"Name":annotation["id"],"Type":"Polygon","PartOfGroup":obs["label"],"Color":"#24DDCD"})
            coords=ET.SubElement(node,"Coordinates")
            for i,(x,y) in enumerate(points):
                ET.SubElement(coords,"Coordinate",{"Order":str(i),"X":format(x,".12g"),"Y":format(y,".12g")})
        for label in sorted({a["observation"]["label"] for a in active}):
            ET.SubElement(ET.SubElement(groups,"Group",{"Name":label,"PartOfGroup":"None","Color":"#24DDCD"}),"Attributes")
        ET.indent(xml)
        (directory/f"{case}.xml").write_bytes(ET.tostring(xml,encoding="utf-8",xml_declaration=True))
        offset=(3<<len(source["downsamples"]))-3
        xml.insert(0,ET.Comment(f"Slidescape 0.49 padded frame, offset {offset} px; canonical JSON remains reader_level0"))
        for coordinate in xml.findall("./Annotations/Annotation/Coordinates/Coordinate"):
            for axis in ("X","Y"):
                coordinate.set(axis,format(float(coordinate.get(axis))+offset,".12g"))
        (directory/f"{case}.slidescape049.xml").write_bytes(ET.tostring(xml,encoding="utf-8",xml_declaration=True))
    table(directory,"regions",annotations,["run_id","case_id","annotation_id","geometry_revision"])
    table(directory,"observations",observations,["run_id","case_id","annotation_id","scope","tumour_evidence","patterns_present"])
    table(directory,"research_labels",research,["run_id","case_id","annotation_id","scope","reviewer_id","review_utc"])
    table(directory,"coverage",coverage,["case_id","status"])
    table(directory,"review_sessions",sessions,["case_id","session_id","start_utc","end_utc","seconds"])
    metric_rows=[flat(m) for m in measurements]
    research_metrics=[flat(m) for m in measurements if m['research_eligible']]
    table(directory,'morphometry_candidates',metric_rows,['case_id','case_revision','status','coverage_pct',*FIELDS])
    table(directory,'morphometry_research',research_metrics,['case_id','case_revision','status','coverage_pct',*FIELDS])
    atomic_json(directory/'morphometry.json',measurements)
    # Event JSON preserves variable result payloads without forcing incompatible table types.
    atomic_json(directory/"events.json",events)
    atomic_json(directory/"evidence_manifest.json",evidence_rows)
    atomic_json(directory/"annotations.geojson",{"type":"FeatureCollection","features":features})
    atomic_json(directory/"canonical_snapshot.json",{"run":store.manifest,"cases":states})
    summary={"run_id":store.manifest["run_id"],"created_utc":utc(),"case_revisions":{c:s["revision"] for c,s in states.items()},
        "regions":len(annotations),"observations":len(observations),"research_labels":len(research),"research_morphometry_cases":len(research_metrics),
        "physician_review_seconds_closed_intervals":sum(s["seconds"] or 0 for s in sessions),
        "open_review_intervals":sum(s["end_utc"] is None for s in sessions),"purpose":store.manifest["purpose"],
        "coordinate_frame":"reader_level0_px","model_requested":store.manifest["model_requested"],
        "effort_requested":store.manifest["effort_requested"],"slidescape_gui_import_tested_this_export":False}
    summary["files"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}
    atomic_json(directory/"export_manifest.json",summary)
    return {"directory":str(directory),**{k:summary[k] for k in ("regions","observations","research_labels","research_morphometry_cases")}}
