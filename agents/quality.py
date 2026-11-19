"""Quality agent: are the implementation, backout and test plans present and specific?

Rules (from the policy and from what plan text looks like in the change history):
* missing - empty or a placeholder ("N/A", "TBD", "Not applicable ...")
* weak    - three words or fewer; a short line built on a vague phrase ("as per standard process", "if required",
            "owner to verify"); boilerplate reused verbatim across unrelated categories of change in the history;
            or an implementation plan without at least two steps
"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import Change
from .policy import PLACEHOLDER

VAGUE = re.compile(r"\b(as per|standard process|as discussed|follow the|if (?:required|needed)|to (?:verify|confirm)|"
                   r"will (?:monitor|check)|check it|smoke test\.?$|undo the change)\b", re.I)
STEP = re.compile(r"(?:^|\n)\s*\d+[.)]\s+")
FIELDS = {"implementation": "implementation_plan", "backout": "backout_plan", "test": "test_plan"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class QualityAgent:
    name = "quality"

    def __init__(self, history: list[Change], boilerplate_categories: int = 3):
        # text -> categories of change it was used for; plan text reused across unrelated categories says nothing
        seen: dict[tuple[str, str], set] = defaultdict(set)
        for c in history:
            for section, attr in FIELDS.items():
                t = _norm(getattr(c, attr))
                if t:
                    seen[(section, t)].add(c.category)
        self.boilerplate = {k for k, cats in seen.items() if len(cats) >= boilerplate_categories}

    def section(self, section: str, text: str) -> dict:
        t = (text or "").strip()
        if PLACEHOLDER.match(t):
            return {"status": "missing", "reason": "empty or placeholder" + (f' ("{t}")' if t else "")}
        words = len(t.split())
        if words <= 3:
            return {"status": "weak", "reason": f'only {words} word(s): "{t}"'}
        if words <= 6 and VAGUE.search(t):
            return {"status": "weak", "reason": f'vague: "{t}"'}
        if (section, _norm(t)) in self.boilerplate:
            return {"status": "weak", "reason": f'boilerplate reused across unrelated changes: "{t}"'}
        if section == "implementation" and len(STEP.findall("\n" + t)) < 2:
            return {"status": "weak", "reason": "no step-by-step procedure"}
        return {"status": "ok", "reason": ""}

    def run(self, ch: Change) -> dict:
        return {s: self.section(s, getattr(ch, attr)) for s, attr in FIELDS.items()}
