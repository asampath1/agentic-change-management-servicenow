"""Assess open change requests.

  python -m agents --out runs/latest            # offline: synthetic dataset, work notes to a local outbox
  python -m agents --out runs/latest -v         # also print each assessment
  python -m agents --only chg:0301,chg:0302     # just these changes
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from .connectors.local import LocalITSM
from .orchestrator import ChangeOrchestrator, prediction, to_dict
from .risk import NAMES


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs/latest", help="output folder")
    ap.add_argument("--only", help="comma-separated change keys")
    ap.add_argument("--risk-mode", choices=["point", "conservative"], default="conservative",
                    help="band from the 80%% upper bound (default) or the most likely probability")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    outbox = out / "work_notes.jsonl"
    outbox.unlink(missing_ok=True)
    t0 = time.time()
    itsm = LocalITSM(outbox=outbox)
    orch = ChangeOrchestrator(itsm, risk_mode=a.risk_mode)
    results = orch.run(write=True, keys=set(a.only.split(",")) if a.only else None)

    (out / "predictions.json").write_text(json.dumps([prediction(r) for r in results], indent=1))
    (out / "assessments.json").write_text(json.dumps([to_dict(r) for r in results], indent=1))
    m = orch.model
    (out / "model.json").write_text(json.dumps({"trained_on": m.n_train, "failed": m.n_failed,
                                                "weights": {n: round(m.w[n], 3) for n in NAMES}}, indent=1))
    run = {"source": "local", "reasoner": "heuristic", "risk_mode": a.risk_mode, "changes": len(results), "seconds": round(time.time() - t0, 1),
           "bands": dict(Counter(r.risk["band"] for r in results)),
           "recommendations": dict(Counter(r.recommendation for r in results))}
    (out / "run.json").write_text(json.dumps(run, indent=1))

    if a.verbose:
        for r in results:
            print(f"\n=== {r.change_key}  {r.short_description}\n{orch.work_note(r)}")
    print(f"Assessed {len(results)} changes in {run['seconds']} s (model trained on {m.n_train} past changes, "
          f"{m.n_failed} failed). Bands {run['bands']}; recommendations {run['recommendations']}.")
    print(f"Wrote {out}/predictions.json, assessments.json, work_notes.jsonl, model.json")


if __name__ == "__main__":
    main()
