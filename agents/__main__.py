"""Assess open change requests and review implemented ones.

  python -m agents --out runs/latest            # offline: synthetic dataset, work notes to a local outbox
  python -m agents --task verify                # only the post-implementation review
  python -m agents --source servicenow --out runs/pdi            # live PDI, dry-run (nothing written)
  python -m agents --source servicenow --write --out runs/pdi    # live PDI, posts work notes + AI fields
  python -m agents --out runs/latest -v         # also print each assessment
  python -m agents --only chg:0301,chg:0302     # just these changes
  python -m agents --reasoner claude --out runs/claude   # Claude reads the change text (M5)
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
    ap.add_argument("--source", choices=["local", "servicenow"], default="local")
    ap.add_argument("--write", action="store_true",
                    help="servicenow only: really post work notes and AI fields (default: dry-run)")
    ap.add_argument("--risk-mode", choices=["point", "conservative"], default="conservative",
                    help="band from the 80%% upper bound (default) or the most likely probability")
    ap.add_argument("--reasoner", choices=["heuristic", "claude"], default="heuristic",
                    help="who reads the free text of each change: history regexes (default) or Claude (needs "
                         "ANTHROPIC_API_KEY; results cached in runs/claude-cache.json)")
    ap.add_argument("--claude-model", help="default claude-opus-5-5")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    outbox = out / "work_notes.jsonl"
    outbox.unlink(missing_ok=True)
    t0 = time.time()
    if a.source == "servicenow":
        from .connectors.servicenow import ServiceNowITSM
        itsm = ServiceNowITSM(write=a.write)
    else:
        itsm = LocalITSM(outbox=outbox)
    reader = None
    if a.reasoner == "claude":
        from .text_reader import ClaudeReader
        reader = ClaudeReader(model=a.claude_model)
    orch = ChangeOrchestrator(itsm, risk_mode=a.risk_mode, reader=reader)
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
    if a.source == "servicenow":
        with open(outbox, "w", encoding="utf-8") as f:
            for w in itsm.writes:
                f.write(json.dumps(w) + "\n")
        failed = [w for w in itsm.writes if a.write and not w["sent"]]
        if failed:
            print(f"! {len(failed)} write(s) failed, e.g. {failed[0].get('status')} {failed[0].get('error', '')[:150]}")
    m = orch.model
    (out / "model.json").write_text(json.dumps({"trained_on": m.n_train, "failed": m.n_failed,
                                                "weights": {n: round(m.w[n], 3) for n in NAMES}}, indent=1))
    run = {"source": a.source, "written": bool(a.write and a.source == "servicenow"), "reasoner": a.reasoner, "risk_mode": a.risk_mode, "changes": len(results), "seconds": round(time.time() - t0, 1),
           "bands": dict(Counter(r.risk["band"] for r in results)),
           "recommendations": dict(Counter(r.recommendation for r in results)),
           "reviewed": len(reviews), "suggested_close_codes": dict(Counter(v["suggested_close_code"] for _, v in reviews)),
           "verification_calibration": orch.verification.calibration}
    if reader:
        run["claude"] = reader.report()
        (out / "claude_rejected_quotes.json").write_text(json.dumps(reader.rejected, indent=1))
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
    if reader:
        c = run["claude"]
        print(f"Claude ({c['model']}): read {c['read_changes']} changes in {c['calls']} calls ({c['cached_changes']} from "
              f"cache), {c['input_tokens']} in / {c['output_tokens']} out tokens, about ${c['estimated_usd']}; "
              f"{c['rejected_quotes']} quote(s) rejected, {c['fallback_batches']} batch(es) fell back to regexes.")
    print(f"Wrote {out}/ predictions, assessments, verifications, work_notes.jsonl, model.json, run.json")


if __name__ == "__main__":
    main()
