"""The interactive Codex path must execute the chosen algorithm and bind edits to its panel."""
from pathlib import Path

import numpy as np
import pytest
pytest.importorskip("pathology_tools")
from pathology_tools.native_workbench import LiveWorkbench
from pathology_tools.packets import dump, prepare, read


@pytest.mark.parametrize("size,stages", [
    (1, ["select","review","select","review"]),
    (4, ["select","review"]),
])
def test_native_roundtrip(tmp_path, size, stages):
    weights=tmp_path/"weights.json"
    dump(weights, {"classes":[1,2,3,4],"mean":[0]*10,"scale":[1]*10,
                   "intercept":[1,0,0,0],"coef":[[0]*10 for _ in range(4)]})
    rgb=np.full((35,49,3),230,dtype=np.uint8)
    rgb[7:21,9:25]=[106,66,127]
    paths=[prepare(rgb,(0,0),k,tmp_path/k,weights) for k in ("F01","F02")]
    output=tmp_path/"decisions"
    bench=LiveWorkbench(paths,output,{"batch":size},weights)
    first=bench.handle("workbench_open",{},"open")
    assert len(first)==1+min(2,size)
    with pytest.raises(ValueError,match="current ticket"):
        bench.handle("workbench_submit",{"ticket":"stale","decisions":[]},"invalid")
    observed=[]
    while not bench.done:
        observed.append(bench.stage)
        rows=[{"field":k,"choice":("split_touching" if k=="F02" else "baseline") if bench.stage=="select" else "B",
               "reason":"Synthetic geometry check.","edits":[]} for k in bench.ids]
        bench.handle("workbench_submit",{"ticket":bench.ticket,"decisions":rows},"test-call")
    assert observed==stages
    if size==4:
        assert list(read(output/"step-02-review"/"request.json")["fields"])==["F02","F01"]
    final=read(output/"decisions.json")
    assert final["F02"]["retained"]=="split_touching"
    for path in output.glob("*select/sealed.json"):
        assert all(v["matched_frozen_arrays"] for v in read(path)["live_algorithm"].values())
