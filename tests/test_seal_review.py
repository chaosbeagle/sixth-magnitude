import json
import re
from unittest.mock import Mock

import numpy as np
import pytest

from pathology_tools import reviewed_hybrid
from pathology_tools.packets import prepare
from pathology_tools.seal_review import SealCanvas, WORKFLOW
from sixth_magnitude.hybrid_entry import main as astra
from sols_hammer.hybrid_entry import main as sol


def comparison(parts):
    return re.search(r"comparison ([0-9a-f]{16})", parts[0]["text"])[1]


def test_comparison_tracks_pending_edits_and_selected_ids(tmp_path):
    work, out = tmp_path/"work", tmp_path/"out"
    work.mkdir()
    out.mkdir()
    path = tmp_path/"F"
    prepare(np.full((40,40,3), 224, np.uint8), [0,0], "F", path, native=True)
    canvas = SealCanvas(path, "hybrid", None, work, out, ["a","b"])
    canvas.current[4:10,4:10] = 12
    canvas.capture_start()
    code = comparison(canvas.apply({"action":"compare"}))
    edited = np.zeros_like(canvas.start)
    edited[10:20,10:20] = 21
    np.save(work/"edit.npy", edited)
    canvas.apply({"action":"pixels","file":"edit.npy"})
    seal = dict(action="seal", geometry="start", comparison=code, groups=[{"ids":[21],"class":2}])
    with pytest.raises(ValueError, match="latest"):
        canvas.apply(seal)
    parts = canvas.apply({"action":"compare","roi":[2,2,20,20]})
    assert len(parts)==3 and all(p["type"]=="inputImage" for p in parts[1:])
    seal["comparison"] = comparison(parts)
    with pytest.raises(ValueError, match="ID"):
        canvas.apply(seal)
    assert np.array_equal(canvas.pending,edited) and not (out/"F.npz").exists()
    canvas.apply({**seal, "groups":[{"ids":[12],"class":2}]})
    with np.load(out/"F.npz") as result:
        assert np.array_equal(result["labels"],canvas.start)
        assert result["ids"].tolist()==[12] and result["types"].tolist()==[2]


@pytest.mark.parametrize(("entry","individual"), [(astra,True),(sol,False)])
def test_public_hybrid_seals_and_replays_without_inference(tmp_path,monkeypatch,entry,individual):
    import sys
    from pathlib import Path

    path, out = tmp_path/"F", tmp_path/"out"
    prepare(np.full((32,32,3), 224, np.uint8), [0,0], "F", path, native=True)

    class ScriptedRole:
        def __init__(self,rpc,role,workspace,settings,receipt,log):
            self.id=role
            receipt.update(thread_id=role, turns=[{"status":"completed"}], accepted={},
                           usage={"total":{"totalTokens":0}})

        def turn(self,parts,*,bench=None):
            if bench is None:
                return json.dumps({"decisions":[dict(field="F",route="model-guided",candidate="baseline",
                                                     reason="Synthetic blank fixture",roi=[])]})
            assert bench.individual==individual
            parts=bench.handle([])
            if not individual:
                parts=bench.handle([dict(field="F",action="paint",strokes=[dict(id=5,points=[[2,2],[6,2],[5,6]])])])
            # Either the initial view or a full proposal preview suffices.
            bench.handle([dict(field="F",action="seal",geometry="start" if individual else "final",
                               comparison=comparison(parts),default=1)])

    monkeypatch.setattr(reviewed_hybrid.subprocess,"Popen",Mock())
    monkeypatch.setattr(reviewed_hybrid,"Rpc",Mock())
    monkeypatch.setattr(reviewed_hybrid,"Agent",ScriptedRole)
    monkeypatch.setattr(sys,"argv",["host","workbench","--mode","hybrid","--fields",str(path),"--output",str(out)])
    entry()
    assert json.loads((out/"receipt.json").read_text())["workflow"]==WORKFLOW
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    from verify_route_review import verify
    assert verify(out,tmp_path,None)["F"]["nuclei"]==(0 if individual else 1)
