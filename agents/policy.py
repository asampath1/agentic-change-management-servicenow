"""The SKR Change Management Policy (KB article "SKR Change Management Policy"), as values the agents can use.

Everything here is published to requesters; none of it comes from the evaluation answer key.
"""
from __future__ import annotations

import re

BANDS = (("low", 0.10), ("moderate", 0.18), ("high", 1.01))   # likelihood of failure -> risk band
BUSINESS_HOURS_IST = (9, 21)        # Monday to Friday, peak store and online trading
MIN_LEAD_HOURS = 48                 # normal changes are raised at least 48 h before the planned start
REVIEW_WINDOW_HOURS = 24            # incidents within 24 h after a change are reviewed against it
PLAN_SECTIONS = ("implementation", "backout", "test")

RECOMMENDATIONS = ("approve", "approve_with_conditions", "cab", "send_back")


def band(p: float) -> str:
    return next(name for name, hi in BANDS if p < hi)


def recommend(risk_band: str, quality: dict[str, str], freeze: bool, change_type: str) -> str:
    """quality: section -> ok | weak | missing."""
    if any(quality.get(s) == "missing" for s in PLAN_SECTIONS):
        return "send_back"                          # every normal/emergency change needs all three plans
    if freeze and change_type != "emergency":
        return "send_back"                          # reschedule outside the freeze (only the ECAB can override)
    return {"high": "cab", "moderate": "approve_with_conditions", "low": "approve"}[risk_band]


PLACEHOLDER = re.compile(r"^\s*(?:n/?a|tbd|tba|none|nil|-+|not applicable\b.*)?\s*\.?\s*$", re.I)
