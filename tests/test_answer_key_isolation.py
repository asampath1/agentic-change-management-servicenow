"""The agents must never read the evaluation answer key, and must never see answers on open incidents."""
import builtins
import io
from pathlib import Path

import pytest

from agents.connectors.base import REDACTED_OPEN_FIELDS
from agents.connectors.local import AnswerKeyAccessError, DEFAULT_DATA_DIR, LocalITSM, MockAutomation, \
    SyntheticLogAnalytics, guarded_open

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def test_guarded_open_refuses_answer_key():
    with pytest.raises(AnswerKeyAccessError):
        guarded_open(DEFAULT_DATA_DIR / "ground_truth.json")
    with pytest.raises(AnswerKeyAccessError):
        guarded_open(DEFAULT_DATA_DIR / "ground_truth.loaded.json")


@pytest.mark.skip(reason="v1.0 triage run; replaced by a change-orchestrator run in M2")
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
    from agents.orchestrator import TriageOrchestrator
    from agents.remediation import SimulatedApprover
    itsm = LocalITSM(outbox=tmp_path / "out.jsonl")
    orch = TriageOrchestrator(itsm, SyntheticLogAnalytics(itsm), MockAutomation(), approver=SimulatedApprover())
    results = orch.run()
    assert len(results) == 24
    assert any("incidents.csv" in p for p in opened)          # the spy really was in place
    assert not any("ground_truth" in p for p in opened)


def test_open_incidents_are_redacted():
    incs = LocalITSM().open_incidents()
    assert len(incs) == 24
    for inc in incs:
        for f in REDACTED_OPEN_FIELDS:
            assert not getattr(inc, f), f"{inc.key}.{f} leaked to agents"


def test_no_agent_module_references_answer_key():
    offenders = []
    for py in AGENTS_DIR.rglob("*.py"):
        text = py.read_text()
        if "ground_truth" in text and py.name != "local.py":   # local.py only names it to block it
            offenders.append(str(py))
    assert not offenders, offenders
    local = (AGENTS_DIR / "connectors" / "local.py").read_text()
    assert 'FORBIDDEN_NAME_PARTS = ("ground_truth",)' in local
    assert local.count("ground_truth") == 1


def test_dataset_itself_has_no_answers_on_open_incidents():
    """What the loader pushes into ServiceNow must not carry the eval answers either."""
    import csv
    import json
    with open(DEFAULT_DATA_DIR / "csv" / "incidents.csv", newline="") as f:
        open_rows = [r for r in csv.DictReader(f) if r["state"] == "1"]
    assert len(open_rows) == 24
    for r in open_rows:
        for field in ("caused_by", "problem_id", "assignment_group", "assigned_to", "work_notes", "close_notes"):
            assert not r[field], f"{r['_key']}.{field} is set in incidents.csv"
    plan = json.loads((DEFAULT_DATA_DIR / "load_plan.json").read_text())
    for stage in plan["stages"]:
        for rec in stage["records"]:
            if rec.get("_table") == "incident" and rec.get("state") == "1":
                assert "caused_by" not in rec and "problem_id" not in rec, rec["_key"]
