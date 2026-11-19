"""Assess open change requests and review implemented ones.

  python -m agents --out runs/latest            # offline: synthetic dataset, work notes to a local outbox
  python -m agents --task verify                # only the post-implementation review
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
from .orchestrator import ChangeOrchestrator, prediction, to_dict, verify_prediction
from .risk import NAMES


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs/latest", help="output folder")
    ap.add_argument("--only", help="comma-separated change keys")
    ap.add_argument("--task", choices=["assess", "verify", "all"], default="all")
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
    keys = set(a.only.split(",")) if a.only else None
    results = orch.run(write=True, keys=keys) if a.task in ("assess", "all") else []
    reviews = orch.verify(write=True, keys=keys) if a.task in ("verify", "all") else []

    if results:
        (out / "predictions.json").write_text(json.dumps([prediction(r) for r in results], indent=1))
        (out / "assessments.json").write_text(json.dumps([to_dict(r) for r in results], indent=1))
    if reviews:
        (out / "verify_predictions.json").write_text(json.dumps([verify_prediction(c, v) for c, v in reviews], indent=1))
        (out / "verifications.json").write_text(json.dumps([dict(key=c.key, short_description=c.short_description, **v)
                                                            for c, v in reviews], indent=1))
    m = orch.model
    (out / "model.json").write_text(json.dumps({"trained_on": m.n_train, "failed": m.n_failed,
                                                "weights": {n: round(m.w[n], 3) for n in NAMES}}, indent=1))
    run = {"source": "local", "reasoner": "heuristic", "risk_mode": a.risk_mode, "changes": len(results), "seconds": round(time.time() - t0, 1),
           "bands": dict(Counter(r.risk["band"] for r in results)),
           "recommendations": dict(Counter(r.recommendation for r in results)),
           "reviewed": len(reviews), "suggested_close_codes": dict(Counter(v["suggested_close_code"] for _, v in reviews)),
           "verification_calibration": orch.verification.calibration}
    (out / "run.json").write_text(json.dumps(run, indent=1))

    if a.verbose:
        for r in results:
            print(f"\n=== {r.change_key}  {r.short_description}\n{orch.work_note(r)}")
    print(f"Assessed {len(results)} changes in {run['seconds']} s (model trained on {m.n_train} past changes, "
          f"{m.n_failed} failed). Bands {run['bands']}; recommendations {run['recommendations']}.")
    if reviews:
        cal = orch.verification.calibration
        print(f"Reviewed {len(reviews)} implemented changes: {run['suggested_close_codes']} (link threshold "
              f"{cal['threshold']}, F1 {cal['f1_on_history']} on {cal['history_candidates']} history candidates).")
    print(f"Wrote {out}/ predictions, assessments, verifications, work_notes.jsonl, model.json, run.json")


if __name__ == "__main__":
    main()
