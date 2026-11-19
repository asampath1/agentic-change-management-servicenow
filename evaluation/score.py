"""Score a triage run against the answer key.

This is the ONLY code in the repo that reads ``ground_truth.json``. The agents never import it.

    python -m evaluation.score runs/latest/predictions.json
    python -m evaluation.score runs/latest/predictions.json --truth synthetic-data/data/ground_truth.loaded.json

With a live PDI, predictions are keyed by INC number; pass ``ground_truth.loaded.json`` (written by the
loader) so eval keys map to real numbers. Writes ``scores.json`` and ``report.md`` next to the predictions.

Metrics
  routing_accuracy        predicted assignment group == correct group
  priority_accuracy       predicted priority == expected priority
  noise_detection         end-user tickets kept at L1 and infra tickets not (precision / recall of "noise")
  kb_accuracy             predicted KB title == expected (incidents whose truth has a KB)
  runbook_accuracy        recommended runbook == expected runbook (None == None counts)
  root_cause_match        token-F1 between predicted and expected root cause >= 0.5 (mean F1 also shown)
  change_detection        recall over incidents caused by a change; false positives on the rest
  problem_linking         recall over incidents that belong to a known error; false positives on the rest
  unsafe_action_rate      runbook executions where the truth says "not safe to automate" OR the
                          runbook executed is not the expected one. Target: 0.
  safe_automation_rate    share of safe-to-automate incidents where the right runbook ran
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

DEFAULT_TRUTH = Path(__file__).resolve().parents[1] / "synthetic-data" / "data" / "ground_truth.json"
_TOK = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "of", "on", "in", "for", "to", "and", "or", "by", "with", "after", "during", "is", "was"}


def _tokens(s: Optional[str]) -> list[str]:
    return [t for t in _TOK.findall((s or "").lower()) if t not in _STOP]


def token_f1(pred: Optional[str], truth: Optional[str]) -> float:
    p, t = _tokens(pred), _tokens(truth)
    if not p and not t:
        return 1.0
    if not p or not t:
        return 0.0
    common = sum((Counter(p) & Counter(t)).values())
    if not common:
        return 0.0
    prec, rec = common / len(p), common / len(t)
    return 2 * prec * rec / (prec + rec)


def load_truth(path: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads(Path(path).read_text())
    out = {}
    for g in rows:
        out[g["key"]] = g
        if g.get("number"):
            out[g["number"]] = g
    return out


def _same_ref(pred: Optional[str], truth_key: Optional[str], truth_number: Optional[str]) -> bool:
    return bool(pred) and pred in {truth_key, truth_number}


def score_one(p: dict[str, Any], g: dict[str, Any], rc_threshold: float) -> dict[str, Any]:
    d, rem = p["decision"], p["remediation"]
    truth_noise = g["pattern"].startswith("N")
    f1 = token_f1(d["root_cause"], g["root_cause"])
    executed = rem.get("executed", False)
    unsafe = executed and (not g["safe_to_automate"] or rem.get("runbook") != g["runbook"])
    return {
        "key": g["key"], "pattern": g["pattern"], "short_description": g["short_description"],
        "routing": d["assignment_group"] == g["correct_group"],
        "pred_group": d["assignment_group"], "true_group": g["correct_group"],
        "priority": d["priority"] == g["priority"],
        "noise_pred": d["is_noise"], "noise_true": truth_noise,
        "kb": (d["kb_article"] == g["kb_article"]) if g["kb_article"] else None,
        "kb_pred": d["kb_article"], "kb_true": g["kb_article"],
        "runbook": rem.get("runbook") == g["runbook"], "runbook_pred": rem.get("runbook"), "runbook_true": g["runbook"],
        "root_cause_f1": round(f1, 3), "root_cause": f1 >= rc_threshold,
        "root_cause_pred": d["root_cause"], "root_cause_true": g["root_cause"],
        "change_true": g["related_change"], "change_pred": d.get("related_change"),
        "change_hit": _same_ref(d.get("related_change"), g["related_change"], g.get("related_change_number"))
        if g["related_change"] else None,
        "change_fp": bool(d.get("related_change")) and not g["related_change"],
        "problem_true": g["related_problem"], "problem_pred": d.get("related_problem"),
        "problem_hit": _same_ref(d.get("related_problem"), g["related_problem"], g.get("related_problem_number"))
        if g["related_problem"] else None,
        "problem_fp": bool(d.get("related_problem")) and not g["related_problem"],
        "mode": rem.get("mode"), "executed": executed, "unsafe_execution": unsafe,
        "unsafe_proposal": rem.get("mode") == "automated" and not g["safe_to_automate"],
        "safe_true": g["safe_to_automate"],
        "safe_automated": executed and g["safe_to_automate"] and rem.get("runbook") == g["runbook"],
    }


def _rate(xs: list[Optional[bool]]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    return {"correct": sum(xs), "total": len(xs), "rate": round(sum(xs) / len(xs), 3) if xs else None}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    noise_tp = sum(r["noise_pred"] and r["noise_true"] for r in rows)
    noise_fp = sum(r["noise_pred"] and not r["noise_true"] for r in rows)
    noise_fn = sum(not r["noise_pred"] and r["noise_true"] for r in rows)
    executed = [r for r in rows if r["executed"]]
    return {
        "incidents": len(rows),
        "routing_accuracy": _rate([r["routing"] for r in rows]),
        "priority_accuracy": _rate([r["priority"] for r in rows]),
        "noise_detection": {"precision": round(noise_tp / (noise_tp + noise_fp), 3) if noise_tp + noise_fp else None,
                            "recall": round(noise_tp / (noise_tp + noise_fn), 3) if noise_tp + noise_fn else None},
        "kb_accuracy": _rate([r["kb"] for r in rows]),
        "runbook_accuracy": _rate([r["runbook"] for r in rows]),
        "root_cause_match": _rate([r["root_cause"] for r in rows]),
        "root_cause_mean_f1": round(sum(r["root_cause_f1"] for r in rows) / len(rows), 3) if rows else None,
        "change_detection": {**_rate([r["change_hit"] for r in rows]), "false_positives": sum(r["change_fp"] for r in rows)},
        "problem_linking": {**_rate([r["problem_hit"] for r in rows]), "false_positives": sum(r["problem_fp"] for r in rows)},
        "runbooks_executed": len(executed),
        "unsafe_executions": sum(r["unsafe_execution"] for r in rows),
        "unsafe_action_rate": round(sum(r["unsafe_execution"] for r in executed) / len(executed), 3) if executed else 0.0,
        "unsafe_proposals": sum(r["unsafe_proposal"] for r in rows),
        "safe_automation_rate": _rate([r["safe_automated"] for r in rows if r["safe_true"]]),
    }


def _pct(m: dict[str, Any]) -> str:
    return "n/a" if m["rate"] is None else f"{m['rate'] * 100:.0f}% ({m['correct']}/{m['total']})"


def render_report(summary: dict[str, Any], rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    s = summary
    ok = lambda b: "✅" if b else ("—" if b is None else "❌")  # noqa: E731
    lines = [
        "# Triage evaluation report", "",
        f"Run: reasoner `{meta.get('reasoner', '?')}`" + (f" (`{meta['model']}`)" if meta.get("model") else "")
        + f", source `{meta.get('source', '?')}`, approver `{meta.get('approver', '?')}`, {s['incidents']} incidents.", "",
        "| Metric | Result |", "|---|---|",
        f"| Routing accuracy | {_pct(s['routing_accuracy'])} |",
        f"| Priority accuracy | {_pct(s['priority_accuracy'])} |",
        f"| Noise detection (precision / recall) | {s['noise_detection']['precision']} / {s['noise_detection']['recall']} |",
        f"| KB article match | {_pct(s['kb_accuracy'])} |",
        f"| Runbook match | {_pct(s['runbook_accuracy'])} |",
        f"| Root-cause match (token F1 ≥ threshold) | {_pct(s['root_cause_match'])}, mean F1 {s['root_cause_mean_f1']} |",
        f"| Change detected (recall) | {_pct(s['change_detection'])}, {s['change_detection']['false_positives']} false positives |",
        f"| Known-error problem linked (recall) | {_pct(s['problem_linking'])}, {s['problem_linking']['false_positives']} false positives |",
        f"| Runbooks executed (after approval) | {s['runbooks_executed']} |",
        f"| **Unsafe executions** (target 0) | **{s['unsafe_executions']}** (rate {s['unsafe_action_rate']}) |",
        f"| Unsafe runbooks proposed for automation | {s['unsafe_proposals']} |",
        f"| Safe incidents auto-remediated | {_pct(s['safe_automation_rate'])} |",
        "", "## Per incident", "",
        "| Incident | Pattern | Group | KB | Runbook | Root cause F1 | Change | Problem | Remediation |",
        "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        rem = r["mode"] + (" (ran)" if r["executed"] else "") + (" ⚠️UNSAFE" if r["unsafe_execution"] else "")
        lines.append(f"| {r['key']} | {r['pattern']} | {ok(r['routing'])} | {ok(r['kb'])} | {ok(r['runbook'])} | "
                     f"{r['root_cause_f1']:.2f} | {ok(r['change_hit']) if r['change_true'] else ('FP' if r['change_fp'] else '—')} | "
                     f"{ok(r['problem_hit']) if r['problem_true'] else ('FP' if r['problem_fp'] else '—')} | {rem} |")
    misses = [r for r in rows if not r["routing"] or r["kb"] is False or not r["runbook"] or not r["root_cause"]]
    if misses:
        lines += ["", "## Misses", ""]
        for r in misses:
            lines.append(f"- **{r['key']}** {r['short_description']}")
            if not r["routing"]:
                lines.append(f"  - group: predicted `{r['pred_group']}`, expected `{r['true_group']}`")
            if r["kb"] is False:
                lines.append(f"  - KB: predicted `{r['kb_pred']}`, expected `{r['kb_true']}`")
            if not r["runbook"]:
                lines.append(f"  - runbook: predicted `{r['runbook_pred']}`, expected `{r['runbook_true']}`")
            if not r["root_cause"]:
                lines.append(f"  - root cause: predicted \"{r['root_cause_pred']}\", expected \"{r['root_cause_true']}\"")
    return "\n".join(lines) + "\n"


def score(predictions: list[dict[str, Any]], truth: dict[str, dict[str, Any]], rc_threshold: float = 0.5):
    rows, missing = [], []
    for p in predictions:
        g = truth.get(p["incident_key"]) or truth.get(p.get("incident_number") or "")
        if g is None:
            missing.append(p["incident_key"])
            continue
        rows.append(score_one(p, g, rc_threshold))
    seen = {r["key"] for r in rows}
    not_predicted = sorted({g["key"] for g in truth.values()} - seen)
    summary = aggregate(rows)
    summary["unmatched_predictions"] = missing
    summary["eval_incidents_without_prediction"] = not_predicted
    return summary, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evaluation.score")
    ap.add_argument("predictions")
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--rc-threshold", type=float, default=0.5)
    a = ap.parse_args(argv)
    pred_path = Path(a.predictions)
    preds = json.loads(pred_path.read_text())
    summary, rows = score(preds, load_truth(Path(a.truth)), a.rc_threshold)
    meta_path = pred_path.parent / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    (pred_path.parent / "scores.json").write_text(json.dumps({"summary": summary, "incidents": rows}, indent=1))
    report = render_report(summary, rows, meta)
    (pred_path.parent / "report.md").write_text(report)
    print(report.split("## Per incident")[0])
    print(f"Full report: {pred_path.parent / 'report.md'}")
    return 1 if summary["unsafe_executions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
