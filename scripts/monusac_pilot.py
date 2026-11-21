"""Prepare or score the registered, patient-disjoint MoNuSAC pilot (see docs/sol-hammer-plan.zh-TW.md)."""
import argparse
import csv
from io import BytesIO
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from pathology_tools.nuclei import TOOLS, NAMES, measurements
from pathology_tools.packets import dump, read, sha
from pathology_tools.source import align_rgb
from sixth_magnitude.cli import initialize
from sixth_magnitude.toolbox import command as sesta
import subprocess
import sys

SOURCE = "097b60ee25ed0c0dd891399dc7cf7fae8748285f"
HASHES = {"train":"c0d7c0c99600e960a38ed92e67cfa93a80eafb68186d8b958d90a8452dc9cbda",
          "test":"196566e3bf4ddc06d880e244864c27f1b60b3db91a421b0daa0465ff81bf382f"}
def image(field):
    return Image.open(BytesIO(field["bytes"]))

def rows(file, columns):
    for batch in pq.ParquetFile(file).iter_batches(batch_size=1,columns=columns):
        yield batch.to_pylist()[0]

def train(file, output, reference_manifest=None):
    references = {}
    provenance = []
    if reference_manifest:
        records = read(reference_manifest)
        references = {record["row"]: record for record in records}
        if len(references) != len(records):
            raise ValueError("Duplicate training reference row")
    xs, ys = [], []
    for idx, row in enumerate(rows(file, ["image","inst_map","type_map","patient"])):
        rgb, labels, types = [np.asarray(image(row[k])) for k in ("image","inst_map","type_map")]
        if reference_manifest:
            ref = references.pop(idx, None)
            if ref is None or ref["patient"] != row["patient"]:
                raise ValueError(f"Missing or misassigned training reference for row {idx}")
            path = reference_manifest.parent/ref["path"]
            rgb, color = align_rgb(rgb, ref.get("origin_xy", [0, 0]), path)
            provenance.append({"row":idx, "status":color["status"], "pixels":color["working_rgb_sha256"]})
        ids, features = measurements(rgb,labels)
        uniq, first = np.unique(labels,return_index=True)
        cls = np.zeros(int(labels.max())+1,dtype=int)
        cls[uniq] = types.ravel()[first]
        keep = np.isin(cls[ids], [1,2,3,4])
        xs.append(features[keep]); ys.append(cls[ids][keep])
    if references:
        raise ValueError("Training reference manifest contains rows outside this split")
    features, classes = np.concatenate(xs), np.concatenate(ys)
    scale = StandardScaler().fit(features)
    lr = LogisticRegression(C=1.0,max_iter=500,class_weight="balanced",random_state=1)
    lr.fit(scale.transform(features),classes)
    extra = {"color_basis":"reference_rgb", "training_reference_manifest_sha256":sha(reference_manifest),
             "verified_training_fields":provenance} if reference_manifest else {}
    dump(output, {**extra, "classes":lr.classes_.tolist(), "coef":lr.coef_.tolist(),
        "intercept":lr.intercept_.tolist(),"mean":scale.mean_.tolist(),"scale":scale.scale_.tolist(),
        "training_counts":np.bincount(classes,minlength=5).tolist(),"source_sha256":sha(file)})

def prepare(data, root):
    data, root = data.resolve(), root.resolve()
    for split, expected in HASHES.items():
        if sha(data/f"{split}.parquet") != expected:
            raise ValueError(f"Unexpected {split} source hash")
    train_p, test_p = [set(pq.read_table(data/f"{s}.parquet",columns=["patient"]).column(0).to_pylist())
                       for s in ("train","test")]
    overlap = sorted(train_p & test_p)
    root.mkdir(exist_ok=True,parents=True)
    if (root/"panel.json").exists():
        raise ValueError("Panel already exists; reuse it or choose a new directory")
    weights = root/"classifier.json"
    if not weights.exists():
        train(data/"train.parquet", weights)
    elif read(weights).get("source_sha256") != HASHES["train"]:
        raise ValueError("Classifier mismatch")
    eligible = {}
    for idx,r in enumerate(rows(data/"test.parquet",["patient","image","tissue"])):
        im = image(r["image"])
        if min(im.size)>=192 and r["patient"] not in train_p and r["patient"] not in eligible:
            eligible[r["patient"]] = (idx,im.convert("RGB"),r["tissue"])
    records, assets = [], []
    for i,patient in enumerate(sorted(eligible)[:8], 1):
        idx, im, tissue = eligible[patient]
        key = f"F{i:02}"; w,h = [min(320,v) for v in im.size]
        x,y = (im.width-w)//2,(im.height-h)//2
        folder = root/"images"; folder.mkdir(exist_ok=True)
        path = folder/(key+".png")
        if not path.exists():
            im.save(path)
        crop = im.crop((x,y,x+w,y+h))
        crop.save(folder/(key+"-field.png"))
        records.append({"field":key,"patient":patient,"row":idx,"tissue":tissue,
                        "roi_xywh":[x,y,w,h],"input_sha256":sha(path)})
        assets.append({"asset_id":key,"reader":"raster","path":path})
    if len(records)!=8:
        raise ValueError("Need eight eligible patients")
    workspace = root/"workbench"
    if not (workspace/"run_manifest.json").exists():
        initialize(workspace, assets, purpose="engineering")
    for r in records:
        key = r["field"]
        field = root/"sesta-fields"/key
        if not field.exists():
            sesta(argparse.Namespace(operation="prepare",run=workspace,asset_id=key,roi=r["roi_xywh"],
                                    classifier=weights,field=field,legacy_packet=True))
        sol = root/"sol-fields"/key
        if not sol.exists():
            subprocess.run([sys.executable,"-m","sols_hammer.cli","prepare","--image",
                str(root/"images"/(key+"-field.png")),"--field",str(sol),"--classifier",str(weights),"--legacy-packet"],check=True)
        if read(field/"field.json")["files"] != read(sol/"field.json")["files"]:
            raise ValueError("Frameworks received different source pixels or candidate masks")
    dump(root/"panel.json",{"dataset_revision":SOURCE,"data_sha256":HASHES,"sha256_classifier":sha(weights),
              "train_patients":len(train_p),"test_patients":len(test_p),
              "excluded_overlapping_test_patients":overlap,"records":records})

def score_masks(truth, truth_class, labels, predicted_class):
    valid = truth_class != 5
    g, p = truth[valid].astype(np.int64), labels[valid].astype(np.int64)
    gs, ps = np.bincount(g), np.bincount(p)
    ng, npred = np.count_nonzero(gs[1:]), np.count_nonzero(ps[1:])
    both = (g>0) & (p>0)
    pairs, intersections = np.unique(g[both]*len(ps) + p[both],return_counts=True)
    gi,pi = np.divmod(pairs,len(ps))
    ious = intersections / (gs[gi] + ps[pi] - intersections)
    hit = ious>0.5
    gi,pi = gi[hit],pi[hit]
    if len(set(gi))!=len(gi) or len(set(pi))!=len(pi):
        raise ValueError("Nonunique strict IoU match")
    keys, first = np.unique(g,return_index=True)
    class_by_id = np.zeros(len(gs),dtype=int)
    class_by_id[keys] = truth_class[valid][first]
    n = len(gi)
    correct = int(np.sum(class_by_id[gi]==predicted_class[pi]))
    return {"truth":int(ng),"predicted":int(npred),"tp":n,"fn":int(ng)-n,"fp":int(npred)-n,
            "correct_type":correct,"wrong_type":n-correct,
            "iou_sum":float(ious[hit].sum()),"matched_mean_iou":float(ious[hit].mean()) if n else None,
            "f1":2*n/int(ng+npred) if ng+npred else None,
            "true_classes":np.bincount(class_by_id[keys[keys>0]],minlength=5)[1:5].tolist()}

def score(data,root):
    manifest = read(root/"panel.json")
    if sha(data/"test.parquet")!=manifest["data_sha256"]["test"]:
        raise ValueError("Test source hash mismatch")
    decisions = {host:read(root/host/"decisions.json") for host in ["astra","sol"]}
    selected = {r["row"]:r for r in manifest["records"]}
    results = []
    for idx,row in enumerate(rows(data/"test.parquet",["inst_map","type_map"])):
        if idx not in selected: continue
        r=selected[idx]; k=r["field"]; x,y,w,h=r["roi_xywh"]
        gold, classes = [np.asarray(image(row[name]))[y:y+h,x:x+w] for name in ("inst_map","type_map")]
        field=root/"sesta-fields"/k
        for tool in read(field/"field.json")["tools"]:
            with np.load(field/(tool+".npz")) as pred:
                results.append({"field":k,"condition":tool,**score_masks(gold,classes,pred["labels"],pred["types"])})
        for host,d in decisions.items():
            if sha(root/host/(k+".npz"))!=d[k]["sha256"]: raise ValueError("Unsealed prediction")
            for phase,tool in [("proposal",d[k]["proposed"]),("review",d[k]["retained"])]:
                with np.load(field/(tool+".npz")) as pred:
                    results.append({"field":k,"condition":host+"_"+phase,**score_masks(gold,classes,pred["labels"],pred["types"])})
            with np.load(root/host/(k+".npz")) as pred:
                results.append({"field":k,"condition":host+"_final",**score_masks(gold,classes,pred["labels"],pred["types"])})
    results.sort(key=lambda r:(r["field"],r["condition"]))
    dump(root/"scores.json", results)
    with (root/"paired.csv").open("w",newline="") as f:
        out=csv.DictWriter(f,fieldnames=list(results[0])); out.writeheader();out.writerows(results)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("op",choices=["prepare","score","train-verified"])
    p.add_argument("--data",type=Path,default=Path("data/monusac"))
    p.add_argument("--run",type=Path,default=Path("runs/monusac-pilot"))
    p.add_argument("--reference-manifest",type=Path)
    p.add_argument("--classifier-out",type=Path)
    a=p.parse_args()
    if a.op=="train-verified":
        if not a.reference_manifest or not a.classifier_out or a.classifier_out.exists():
            p.error("Supply a training --reference-manifest and an unused --classifier-out")
        if sha(a.data/"train.parquet") != HASHES["train"]:
            p.error("Training source hash differs from the registered split")
        a.classifier_out.parent.mkdir(parents=True,exist_ok=True)
        train(a.data/"train.parquet",a.classifier_out,a.reference_manifest)
    else:
        (prepare if a.op=="prepare" else score)(a.data,a.run)
