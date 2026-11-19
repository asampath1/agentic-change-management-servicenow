"""Unit tests for the change agents (no answer key involved)."""
from datetime import datetime

import pytest

from agents.connectors.local import LocalITSM
from agents.models import Change, ScheduleSpan
from agents.orchestrator import ChangeOrchestrator
from agents.policy import band, recommend
from agents.quality import QualityAgent
from agents.risk import CLUES, RiskModel
from agents.schedule import ScheduleAgent, covers


@pytest.fixture(scope="module")
def orch():
    return ChangeOrchestrator(LocalITSM())


def test_policy_recommendation():
    ok = {"implementation": "ok", "backout": "ok", "test": "ok"}
    assert recommend("low", ok, False, "normal") == "approve"
    assert recommend("moderate", ok, False, "normal") == "approve_with_conditions"
    assert recommend("high", ok, False, "normal") == "cab"
    assert recommend("low", dict(ok, backout="missing"), False, "normal") == "send_back"
    assert recommend("low", ok, True, "normal") == "send_back"
    assert recommend("high", ok, True, "emergency") == "cab"           # ECAB, not a send-back
    assert recommend("low", dict(ok, test="weak"), False, "normal") == "approve"
    assert [band(p) for p in (0.05, 0.10, 0.17, 0.18, 0.9)] == ["low", "moderate", "moderate", "high", "high"]


@pytest.mark.parametrize("text,status", [
    ("", "missing"), ("N/A", "missing"), ("TBD", "missing"), ("Not applicable - low risk change.", "missing"),
    ("Revert.", "weak"), ("Rollback if required.", "weak"), ("Owner to verify.", "weak"),
    ("Swap back to the previous slot or redeploy the last release from the pipeline (under 10 min).", "ok"),
    ("Re-apply the exported policy.", "ok"),
])
def test_quality_backout_rules(text, status):
    assert QualityAgent([]).section("backout", text)["status"] == status


def test_quality_implementation_needs_steps():
    q = QualityAgent([])
    assert q.section("implementation", "Deploy the new build to the production slot and check it.")["status"] == "weak"
    assert q.section("implementation", "1. Export config.\n2. Apply the setting.\n3. Confirm it is live.")["status"] == "ok"


def test_maintenance_window_spans():
    t1 = ScheduleSpan("T1", "maintenance", "tier 1", datetime(2026, 5, 18, 19, 30), datetime(2026, 5, 18, 23, 30),
                      "weekly", "136")              # IST Tue/Thu/Sun 01:00-05:00
    std = ScheduleSpan("Std", "maintenance", "tier 2;tier 3", datetime(2026, 5, 18, 16, 30),
                       datetime(2026, 5, 19, 0, 30), "daily")
    assert covers(t1, datetime(2026, 11, 23, 19, 30), datetime(2026, 11, 23, 22, 30))       # Monday UTC = Tue IST
    assert not covers(t1, datetime(2026, 11, 24, 19, 30), datetime(2026, 11, 24, 20, 30))   # Tuesday UTC
    assert not covers(t1, datetime(2026, 11, 23, 22, 30), datetime(2026, 11, 24, 0, 0))     # runs past 05:00 IST
    assert covers(std, datetime(2026, 11, 24, 23, 0), datetime(2026, 11, 25, 0, 30))        # after midnight UTC
    assert t1.applies("SKR Order Processing", 1) and not t1.applies("SKR HR Portal", 3)


def test_conflicts_same_and_dependent_ci(orch):
    itsm = orch.itsm
    sched = next(c for c in itsm.changes() if c.state == "-2")
    probe = Change(key="probe", short_description="probe", ci=sched.ci, start=sched.start, end=sched.end, state="-4")
    rels = {c["key"]: c["relation"] for c in orch.schedule.conflicts(probe, itsm.changes_in_state("-2"))}
    assert rels.get(sched.key) == "same_ci"
    dep = itsm.dependents(sched.ci) or itsm.dependencies(sched.ci)
    if dep:
        probe.ci = dep[0]
        rels = {c["key"]: c["relation"] for c in orch.schedule.conflicts(probe, itsm.changes_in_state("-2"))}
        assert rels.get(sched.key) == "related_ci"


def test_risk_model_learns_from_history(orch):
    m = orch.model
    assert m.n_train == 260 and m.n_failed > 10
    assert m.w["intercept"] < -1.5                      # most changes succeed
    for flag in ("backout_missing", "freeze", "irreversible", "conflict_same_ci"):
        assert m.w[flag] > 0, flag
    for mitigation in ("canary", "rehearsed"):
        assert m.w[mitigation] < 0, mitigation


def test_conservative_mode_never_lowers_the_band(orch):
    point = ChangeOrchestrator(orch.itsm, model=orch.model, risk_mode="point")
    cons = ChangeOrchestrator(orch.itsm, model=orch.model, risk_mode="conservative")
    order = {"low": 0, "moderate": 1, "high": 2}
    for ch in orch.itsm.changes_in_state("-4"):
        a, b = point.assess(ch), cons.assess(ch)
        assert a.risk["probability"] == b.risk["probability"]
        assert order[b.risk["band"]] >= order[a.risk["band"]]


def test_text_clues_come_from_history_wording():
    assert CLUES["irreversible"].search("Note: this migration is one-way; rolling back means restoring from backup.")
    assert CLUES["canary"].search("Staged rollout: one node first, then the rest after health checks.")
    assert not CLUES["vendor"].search("Our own engineer runs the pipeline.")


def test_work_note_says_recommendation_only(orch):
    a = orch.assess(orch.itsm.changes_in_state("-4")[0])
    note = orch.work_note(a)
    assert note.startswith("[AI change assessment - recommendation only")
    assert "Recommendation:" in note
