"""Reconstruct a completed two-role hybrid run from recorded routes and edits."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from pathology_tools.free_edit import Canvas
from pathology_tools.packets import read, sha
from pathology_tools.seal_review import SealCanvas, WORKFLOW
from pathology_tools.source import pixel_hash


def verify(run, sources, detector):
    receipt = read(run / "receipt.json")
    review = read(run / "route-review.json")
    actions = read(run / "actions.json")
    roles = receipt["roles"]
    assert receipt["status"] == "completed" and receipt["workflow"] in ("independent-route-review-v1", WORKFLOW)
    canvas_class = SealCanvas if receipt["workflow"] == WORKFLOW else Canvas
    assert roles["reviewer"]["thread_id"] == review["reviewer_thread_id"] != roles["operator"]["thread_id"]
    assert set(review["fields"]) == set(receipt["sources"]) == set(actions)
    assert all(t["status"] == "completed" for role in roles.values() for t in role["turns"])
    if not receipt["usage"]["missing_roles"]:
        for key, value in receipt["usage"]["total"].items():
            assert value == sum(r["usage"]["total"].get(key, 0) for r in roles.values())
    fields = {}
    with TemporaryDirectory() as directory:
        work = Path(directory)
        replay = work / "output"
        replay.mkdir()
        for key, choice in review["fields"].items():
            evidence = review["evidence"][key]
            assert sha(sources/key/"source.png") == receipt["sources"][key] == evidence["source_sha256"]
            with np.load(run/(key+".npz")) as saved:
                labels = saved["labels"].copy()
                types = saved["types"].copy()
                ids = saved["ids"].copy()
            # Only the class count is used by Canvas when validating integer type IDs.
            canvas = canvas_class(sources/key, "hybrid", detector, work, replay, range(int(types.max(initial=0))))
            assert evidence["candidates"] == {name: pixel_hash(arr) for name, arr in canvas.bank.items()}
            seed = (np.zeros_like(canvas.current) if choice["route"] == "model-guided"
                    else np.array(canvas.bank[choice["candidate"]], dtype=np.int32))
            assert pixel_hash(seed) == choice["initial_mask_sha256"]
            canvas.current = seed.copy()
            if isinstance(canvas, SealCanvas):
                canvas.capture_start()
            canvas.sync()
            canvas.opened = True
            for action in actions[key]:
                row = action.copy()
                if "archived_array" in row:
                    name = row.pop("archived_array")
                    if Path(name).name != name or Path(row["file"]).name != row["file"]:
                        raise ValueError("Expected a local mask basename")
                    (work/row["file"]).write_bytes((run/name).read_bytes())
                canvas.apply(row)
                canvas.events.append(row)
            assert canvas.finished and receipt["outputs"][key+".npz"] == sha(run/(key+".npz"))
            with np.load(replay/(key+".npz")) as rebuilt:
                assert np.array_equal(rebuilt["labels"], labels)
                assert np.array_equal(rebuilt["ids"], ids) and np.array_equal(rebuilt["types"], types)
            fields[key] = dict(route=choice["route"], candidate=choice["candidate"], nuclei=len(ids),
                               changed_pixels=int(np.count_nonzero(labels != seed)))
    return fields


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--packets", required=True, type=Path)
    parser.add_argument("--detector", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.run, args.packets, args.detector), indent=2))
