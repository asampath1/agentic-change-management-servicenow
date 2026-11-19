"""Properties of the generated change dataset (synthetic-data/generate_data.py)."""
import csv
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SD = ROOT / "synthetic-data"
DATA = SD / "data"
FMT = "%Y-%m-%d %H:%M:%S"


def _model():
    spec = importlib.util.spec_from_file_location("change_model", SD / "change_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(name):
    with open(DATA / "csv" / f"{name}.csv", newline="") as f:
        return {r["_key"]: r for r in csv.DictReader(f)}


def _truth():
    return json.loads((DATA / "ground_truth.json").read_text())


def _t(s):
    return datetime.strptime(s, FMT)


def test_generator_is_deterministic(tmp_path):
    for out in ("a", "b"):
        subprocess.run([sys.executable, "generate_data.py", "--out", str(tmp_path / out)], cwd=SD, check=True,
                       capture_output=True)
    for f in ("ground_truth.json", "load_plan.json", "csv/changes.csv", "csv/incidents.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f
    assert (tmp_path / "a" / "ground_truth.json").read_bytes() == (DATA / "ground_truth.json").read_bytes(), \
        "committed data is stale: rerun synthetic-data/generate_data.py"


def test_eval_set_shape():
    t = _truth()
    assert len(t["assess"]) == 40 and len(t["verify"]) == 20
    bands = {a["risk_band"] for a in t["assess"]}
    assert bands == {"low", "moderate", "high"}
    assert {a["recommendation"] for a in t["assess"]} == {"approve", "approve_with_conditions", "cab", "send_back"}
    assert sum(a["messy"] for a in t["assess"]) == 6
    assert {v["outcome"] for v in t["verify"]} == {"successful", "successful_issues", "unsuccessful"}


def test_recommendation_follows_published_policy():
    M = _model()
    for a in _truth()["assess"]:
        assert a["recommendation"] == M.recommendation(a["risk_band"], a["quality"], bool(a["freeze"]), a["type"])
        assert a["risk_band"] == M.band(a["failure_probability"])


def test_eval_phrasing_never_appears_in_history():
    """Free-text risk clues use phrasing A in history and phrasing B in the eval set only."""
    M = _model()
    phrases_b = []
    for name, (_w, _k, a, b) in M.HIDDEN.items():
        phrases_b += [p for v in (b.values() if isinstance(b, dict) else [b]) for p in v]
    changes, t = _rows("changes"), _truth()
    eval_keys = {a["key"] for a in t["assess"]}
    for k, r in changes.items():
        text = " ".join([r["description"], r["implementation_plan"], r["backout_plan"]])
        if k not in eval_keys:
            assert not any(p in text for p in phrases_b), k
    for a in t["assess"]:
        r = changes[a["key"]]
        text = " ".join([r["description"], r["implementation_plan"]])
        assert a["messy"] == bool(a["hidden_factors"])
        assert sum(p in text for p in phrases_b) >= len(a["hidden_factors"]), a["key"]


def test_assess_changes_are_planned_in_the_future():
    t, changes = _truth(), _rows("changes")
    now = _t(t["generated_at"])
    for a in t["assess"]:
        r = changes[a["key"]]
        assert r["state"] == "-4"
        assert _t(r["opened_at"]) < now < _t(r["start_date"]) < _t(r["end_date"])
        if a["freeze"]:
            assert a["slot"] == "freeze" or a["type"] == "emergency"


def test_verify_incidents_fit_the_review_window():
    t, changes, incs = _truth(), _rows("changes"), _rows("incidents")
    for v in t["verify"]:
        r = changes[v["key"]]
        assert r["state"] == "0" and r["work_start"] and r["work_end"]
        ws, we = _t(r["work_start"]), _t(r["work_end"])
        assert bool(v["caused_incidents"]) == (v["outcome"] != "successful")
        for k in v["caused_incidents"]:
            assert ws <= _t(incs[k]["opened_at"]) <= we + timedelta(hours=24), k
        for d in v["distractor_incidents"]:
            opened = _t(incs[d["key"]]["opened_at"])
            if d["why"] == "before":
                assert opened < ws
            if d["why"] == "late":
                assert opened > we + timedelta(hours=24)
