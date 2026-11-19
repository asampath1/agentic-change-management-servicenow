"""Score change assessments against the answer key (synthetic-data/data/ground_truth.json).

  python -m evaluation.score runs/latest/predictions.json
  python -m evaluation.score runs/latest/predictions.json --out evaluation/results/heuristic-baseline

This is the ONLY code that reads the answer key. Agents never import this module.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

DEFAULT_TRUTH = Path(__file__).resolve().parents[1] / "synthetic-data" / "data" / "ground_truth.json"
BANDS = ["low", "moderate", "high"]
RECS = ["approve", "approve_with_conditions", "cab", "send_back"]
SECTIONS = ["implementation", "backout", "test"]


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": _r(p), "recall": _r(r), "f1": _r(f), "tp": tp, "fp": fp, "fn": fn}


def _r(x):
    return round(x, 3) if isinstance(x, float) else x


def macro_f1(pairs: list[tuple[str, str]], labels: list[str]) -> float:
    fs = []
    for lab in labels:
        tp = sum(1 for t, p in pairs if t == lab and p == lab)
        fp = sum(1 for t, p in pairs if t != lab and p == lab)
        fn = sum(1 for t, p in pairs if t == lab and p != lab)
        fs.append(_prf(tp, fp, fn)["f1"] or 0.0)
    return round(sum(fs) / len(fs), 3)


def confusion(pairs: list[tuple[str, str]], labels: list[str]) -> dict:
    c = Counter(pairs)
    return {t: {p: c[(t, p)] for p in labels} for t in labels}


def score_assess(preds: list[dict], truth: list[dict]) -> dict:
    by_key = {p["key"]: p for p in preds}
    rows = [(t, by_key.get(t["key"])) for t in truth]
    missing = [t["key"] for t, p in rows if p is None]
    rows = [(t, p) for t, p in rows if p is not None]
    n = len(rows)
    bands = [(t["risk_band"], p["risk_band"]) for t, p in rows]
    recs = [(t["recommendation"], p["recommendation"]) for t, p in rows]

    # The safety metric: a truly high-risk change waved through (plain approval, no conditions, no CAB).
    missed_high = [t["key"] for t, p in rows if t["risk_band"] == "high" and p["recommendation"] == "approve"]
    high_as_low = [t["key"] for t, p in rows if t["risk_band"] == "high" and p["risk_band"] == "low"]
    over_escalated = [t["key"] for t, p in rows if t["risk_band"] == "low" and p["recommendation"] == "cab"]

    q_pairs = [(t["quality"][s], p["quality"][s]) for t, p in rows for s in SECTIONS]
    flag = lambda v: v != "ok"
    tp = sum(1 for t, p in q_pairs if flag(t) and flag(p))
    fp = sum(1 for t, p in q_pairs if not flag(t) and flag(p))
    fn = sum(1 for t, p in q_pairs if flag(t) and not flag(p))
    miss_tp = sum(1 for t, p in q_pairs if t == "missing" and p == "missing")
    miss_fp = sum(1 for t, p in q_pairs if t != "missing" and p == "missing")
    miss_fn = sum(1 for t, p in q_pairs if t == "missing" and p != "missing")

    tc = {(t["key"], c["key"]) for t, _ in rows for c in t["conflicts"]}
    pc = {(t["key"], c["key"]) for t, p in rows for c in p["conflicts"]}
    tf = {t["key"] for t, _ in rows if t["freeze"]}
    pf = {t["key"] for t, p in rows if p["freeze"]}

    messy = [(t, p) for t, p in rows if t.get("messy")]
    return {
        "n": n, "missing_predictions": missing,
        "risk_band": {"accuracy": _r(sum(a == b for a, b in bands) / n), "macro_f1": macro_f1(bands, BANDS),
                      "confusion (truth -> predicted)": confusion(bands, BANDS)},
        "safety": {"missed_high_risk": len(missed_high), "missed_high_risk_changes": missed_high,
                   "high_rated_low": len(high_as_low), "high_rated_low_changes": high_as_low,
                   "true_high": sum(1 for t, _ in rows if t["risk_band"] == "high"),
                   "over_escalated_low_to_cab": len(over_escalated)},
        "recommendation": {"accuracy": _r(sum(a == b for a, b in recs) / n), "macro_f1": macro_f1(recs, RECS),
                           "confusion (truth -> predicted)": confusion(recs, RECS)},
        "quality": {"section_accuracy": _r(sum(a == b for a, b in q_pairs) / len(q_pairs)),
                    "not_ok_detection": _prf(tp, fp, fn), "missing_detection": _prf(miss_tp, miss_fp, miss_fn)},
        "conflicts": _prf(len(tc & pc), len(pc - tc), len(tc - pc)),
        "freeze": _prf(len(tf & pf), len(pf - tf), len(tf - pf)),
        "window_accuracy": _r(sum(t["window"] == p["window"] for t, p in rows) / n),
        "messy_subset": {"n": len(messy),
                         "risk_band_correct": sum(t["risk_band"] == p["risk_band"] for t, p in messy),
                         "missed_high_risk": sum(1 for t, p in messy
                                                 if t["risk_band"] == "high" and p["recommendation"] == "approve")},
    }


def report(s: dict) -> str:
    a = s["assess"]
    rb, sf, rc, q = a["risk_band"], a["safety"], a["recommendation"], a["quality"]
    L = ["# Change assessment scores", "",
         f"Assessed {a['n']} changes" + (f"; missing predictions: {a['missing_predictions']}" if a["missing_predictions"] else "") + ".", "",
         "| Metric | Result |", "|---|---|",
         f"| **Missed high risk** (truly high, recommended plain approval) | **{sf['missed_high_risk']} of {sf['true_high']}** |",
         f"| High-risk changes rated low | {sf['high_rated_low']} of {sf['true_high']} |",
         f"| Low-risk changes sent to CAB (over-escalation) | {sf['over_escalated_low_to_cab']} |",
         f"| Risk band accuracy / macro F1 | {rb['accuracy']:.0%} / {rb['macro_f1']:.2f} |",
         f"| Recommendation accuracy / macro F1 | {rc['accuracy']:.0%} / {rc['macro_f1']:.2f} |",
         f"| Plan quality: section accuracy | {q['section_accuracy']:.0%} |",
         f"| Plan quality: missing plans found (P / R) | {q['missing_detection']['precision']} / {q['missing_detection']['recall']} |",
         f"| Plan quality: weak or missing found (P / R) | {q['not_ok_detection']['precision']} / {q['not_ok_detection']['recall']} |",
         f"| Conflicts (P / R) | {a['conflicts']['precision']} / {a['conflicts']['recall']} |",
         f"| Freeze overlaps (P / R) | {a['freeze']['precision']} / {a['freeze']['recall']} |",
         f"| Window class accuracy | {a['window_accuracy']:.0%} |",
         f"| Messy subset (risk only in free text): band correct | {a['messy_subset']['risk_band_correct']} of {a['messy_subset']['n']} |",
         "", "Risk band confusion (rows = truth, columns = predicted):", "",
         "| truth \\ predicted | " + " | ".join(BANDS) + " |", "|---|" + "---|" * len(BANDS)]
    for t, row in rb["confusion (truth -> predicted)"].items():
        L.append(f"| {t} | " + " | ".join(str(row[p]) for p in BANDS) + " |")
    L += ["", "Recommendation confusion:", "", "| truth \\ predicted | " + " | ".join(RECS) + " |", "|---|" + "---|" * len(RECS)]
    for t, row in rc["confusion (truth -> predicted)"].items():
        L.append(f"| {t} | " + " | ".join(str(row[p]) for p in RECS) + " |")
    if sf["missed_high_risk_changes"]:
        L += ["", "Missed high-risk changes: " + ", ".join(sf["missed_high_risk_changes"])]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("predictions")
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--out", help="folder for scores.json + report.md (default: next to the predictions)")
    a = ap.parse_args()
    preds = json.loads(Path(a.predictions).read_text())
    truth = json.loads(Path(a.truth).read_text())
    scores = {"assess": score_assess(preds, truth["assess"])}
    out = Path(a.out) if a.out else Path(a.predictions).parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "scores.json").write_text(json.dumps(scores, indent=1))
    text = report(scores)
    (out / "report.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
