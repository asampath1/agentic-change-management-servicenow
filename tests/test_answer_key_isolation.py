"""The agents must never read the evaluation answer key, and must never see outcomes on open changes."""
import builtins
import io
from pathlib import Path

import pytest

from agents.connectors.base import REDACTED_OPEN_FIELDS
from agents.connectors.local import AnswerKeyAccessError, DEFAULT_DATA_DIR, LocalITSM, guarded_open

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def test_guarded_open_refuses_answer_key():
    with pytest.raises(AnswerKeyAccessError):
        guarded_open(DEFAULT_DATA_DIR / "ground_truth.json")
    with pytest.raises(AnswerKeyAccessError):
        guarded_open(DEFAULT_DATA_DIR / "ground_truth.loaded.json")


def test_full_run_never_opens_answer_key(monkeypatch, tmp_path):
    opened = []
    real_open, real_io_open = builtins.open, io.open

    def spy(file, *a, **kw):
        opened.append(str(file))
        if "ground_truth" in str(file):
            raise AssertionError(f"agent code opened the answer key: {file}")
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    from agents.orchestrator import ChangeOrchestrator
    itsm = LocalITSM(outbox=tmp_path / "out.jsonl")
    results = ChangeOrchestrator(itsm).run()
    assert len(results) == 40
    assert any("changes.csv" in p for p in opened)            # the spy really was in place
    assert not any("ground_truth" in p for p in opened)


def test_no_agent_module_references_answer_key():
    offenders = []
    for py in AGENTS_DIR.rglob("*.py"):
        text = py.read_text()
        if "ground_truth" in text and py.name != "local.py":   # local.py only names it to block it
            offenders.append(str(py))
        if "change_model" in text:                              # the hidden outcome model
            offenders.append(str(py))
    assert not offenders, offenders
    local = (AGENTS_DIR / "connectors" / "local.py").read_text()
    assert 'FORBIDDEN_NAME_PARTS = ("ground_truth",)' in local
    assert local.count("ground_truth") == 1


def test_open_changes_are_redacted():
    for ch in LocalITSM().changes():
        if ch.state != "3":
            for f in REDACTED_OPEN_FIELDS:
                assert not getattr(ch, f), f"{ch.key}.{f} visible on an open change"


def _csv(name):
    import csv
    with open(DEFAULT_DATA_DIR / "csv" / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def test_dataset_has_no_answers_on_eval_changes():
    """What agents (and the PDI) can read must not carry outcomes for the changes being evaluated."""
    import json
    truth = json.loads((DEFAULT_DATA_DIR / "ground_truth.json").read_text())   # test code may read it
    eval_keys = {t["key"] for t in truth["assess"]} | {t["key"] for t in truth["verify"]}
    changes = {r["_key"]: r for r in _csv("changes")}
    for k in eval_keys:
        assert not changes[k]["close_code"] and not changes[k]["close_notes"], k
    caused = {i for t in truth["verify"] for i in t["caused_incidents"]}
    for r in _csv("incidents"):
        if r["_key"] in caused:
            assert not r["caused_by"] and not r["work_notes"], r["_key"]
    header = " ".join(open(DEFAULT_DATA_DIR / "csv" / "changes.csv").readline().split(","))
    for leak in ("failure_probability", "risk_band", "hidden", "outcome", "recommendation"):
        assert leak not in header
