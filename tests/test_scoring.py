from evaluation.score import macro_f1, score_assess, score_verify

TRUTH = [
    {"key": "a", "risk_band": "high", "recommendation": "cab", "conflicts": [{"key": "x"}], "freeze": [], "window": "maint",
     "quality": {"implementation": "ok", "backout": "missing", "test": "ok"}, "messy": True},
    {"key": "b", "risk_band": "low", "recommendation": "approve", "conflicts": [], "freeze": ["F"], "window": "business",
     "quality": {"implementation": "ok", "backout": "ok", "test": "weak"}, "messy": False},
]


def test_missed_high_risk_counts_plain_approvals_only():
    preds = [dict(TRUTH[0], risk_band="low", recommendation="approve"), dict(TRUTH[1])]
    s = score_assess(preds, TRUTH)
    assert s["safety"]["missed_high_risk"] == 1 and s["safety"]["high_rated_low"] == 1
    assert s["messy_subset"]["missed_high_risk"] == 1
    preds[0]["recommendation"] = "send_back"
    assert score_assess(preds, TRUTH)["safety"]["missed_high_risk"] == 0


def test_perfect_predictions():
    s = score_assess([dict(t) for t in TRUTH], TRUTH)
    assert s["risk_band"]["accuracy"] == 1.0 and s["recommendation"]["accuracy"] == 1.0
    assert s["conflicts"]["recall"] == 1.0 and s["freeze"]["precision"] == 1.0
    assert s["quality"]["section_accuracy"] == 1.0


def test_macro_f1():
    assert macro_f1([("a", "a"), ("b", "b")], ["a", "b"]) == 1.0
    assert macro_f1([("a", "b"), ("b", "a")], ["a", "b"]) == 0.0


def test_verify_scoring():
    truth = [{"key": "a", "outcome": "successful_issues", "caused_incidents": ["i1"], "distractor_incidents": [{"key": "d1"}]},
             {"key": "b", "outcome": "successful", "caused_incidents": [], "distractor_incidents": []}]
    preds = [{"key": "a", "outcome": "successful", "caused_incidents": ["d1"]},
             {"key": "b", "outcome": "successful", "caused_incidents": []}]
    s = score_verify(preds, truth)
    assert s["missed_failures"] == 1 and s["false_alarms"] == 0 and s["distractors_linked"] == 1
    assert s["incident_links"]["recall"] == 0.0
