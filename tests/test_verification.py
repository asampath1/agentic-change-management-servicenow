"""Verification agent (post-implementation review); no answer key involved."""
from datetime import timedelta

import pytest

from agents.connectors.local import LocalITSM
from agents.models import Change, Incident
from agents.orchestrator import ChangeOrchestrator
from agents.verification import BACKOUT


@pytest.fixture(scope="module")
def orch():
    return ChangeOrchestrator(LocalITSM())


def test_link_model_is_fitted_on_history(orch):
    cal = orch.verification.calibration
    assert cal["history_candidates"] > 30 and cal["history_linked"] > 10
    w = cal["weights"]
    assert w["symptom_same_kind"] > 0 and w["same_ci"] > 0
    assert w["hours_after_end_per_24h"] < 0          # the later an incident, the less likely the change caused it


@pytest.mark.parametrize("note,backout", [
    ("[19-Nov 01:31 IST] Validation failed, errors on app-x. Backout executed. Change to be re-planned.", True),
    ("Rolled back after smoke test failure.", True),
    ("Completed, smoke tests passed.", False),
    ("Completed. Some errors seen afterwards; monitoring.", False),
])
def test_backout_detection(note, backout):
    assert bool(BACKOUT.search(note)) == backout


def _probe(orch, opened_offset_h, ci_same=True):
    """A synthetic Review change on a real CI, plus an incident opened relative to its end."""
    v = orch.verification
    base = next(c for c in orch.history.closed if c.ci and c.work_end)
    ch = Change(key="probe", short_description=base.short_description, ci=base.ci, state="0",
                work_start=base.work_start, work_end=base.work_end, start=base.start, end=base.end)
    end = ch.end_dt
    far = next(n for n in ("vm-jump-01", "rsv-backup-prod", "vpngw-hub-prod")
               if n != ch.ci and n not in orch.itsm.related(ch.ci))
    inc = Incident(key="inc:probe", short_description="probe", ci=ch.ci if ci_same else far,
                   opened_at=(end + timedelta(hours=opened_offset_h)).strftime("%Y-%m-%d %H:%M:%S"))
    v.incidents.append(inc)
    v.by_key[inc.key] = inc
    try:
        near, out = v.candidates(ch)
    finally:
        v.incidents.remove(inc)
        del v.by_key[inc.key]
    return {r["key"]: r for r in near}, {r["key"]: r["reason"] for r in out}


def test_time_and_ci_rules(orch):
    near, out = _probe(orch, 2)
    assert "inc:probe" in near
    near, out = _probe(orch, -6)                      # works last 1-3 h, so this is before the start
    assert "before the change" in out["inc:probe"]
    near, out = _probe(orch, 30)
    assert "more than 24 h" in out["inc:probe"]
    near, out = _probe(orch, 2, ci_same=False)
    assert "not related" in out["inc:probe"]


def test_review_changes_get_a_suggestion_only(orch):
    reviews = orch.verify(write=False)
    assert len(reviews) == 20
    for ch, v in reviews:
        assert v["suggested_close_code"] in ("successful", "successful_issues", "unsuccessful")
        note = orch.verification.work_note(ch, v)
        assert note.startswith("[AI post-implementation review - suggestion only")
