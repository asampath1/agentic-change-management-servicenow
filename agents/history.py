"""History agent: how did similar past changes go, and is the CI already in trouble?"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Callable, Optional

from .connectors.base import ITSMClient
from .models import Change, parse_ts
from .retrieval import BM25

FAILED_CODES = {"unsuccessful", "successful_issues"}


def family(ch: Change) -> str:
    """'Deploy release 4.12.3 to app-ecom-api-prod' -> 'deploy release'. Groups changes of the same kind."""
    s = ch.short_description.lower().split(":")[0].split(" - ")[0]
    if ch.ci:
        s = s.replace(ch.ci.lower(), " ")
    for sep in (" on ", " to ", " for ", " in "):
        s = s.split(sep)[0]
    words = [w for w in re.findall(r"[a-z][a-z0-9-]*", s) if not any(ch_.isdigit() for ch_ in w)]
    return " ".join(words[:4]) or "other"


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


class HistoryAgent:
    name = "history"

    def __init__(self, itsm: ITSMClient):
        self.itsm = itsm
        self.closed = [c for c in itsm.changes() if c.state == "3"]
        self.incidents = itsm.incidents()
        self.problems = itsm.problems()
        self.caused: dict[str, list[str]] = defaultdict(list)
        for inc in self.incidents:
            if inc.caused_by:
                self.caused[inc.caused_by].append(inc.key)
        self.family = {c.key: family(c) for c in self.closed}
        self.overall = sum(self.failed(c) for c in self.closed) / max(1, len(self.closed))
        self.index = BM25(self.closed, [f"{c.short_description} {c.description}" for c in self.closed])

    def failed(self, c: Change) -> bool:
        return (c.close_code in FAILED_CODES) or bool(self.caused.get(c.key))

    def stats(self, pred: Callable[[Change], bool], exclude: Optional[str] = None, strength: float = 10) -> dict:
        rows = [c for c in self.closed if c.key != exclude and pred(c)]
        bad = [c for c in rows if self.failed(c)]
        smoothed = (len(bad) + strength * self.overall) / (len(rows) + strength)
        return {"n": len(rows), "failed": len(bad), "rate": round(len(bad) / len(rows), 3) if rows else None,
                "smoothed_rate": round(smoothed, 3), "failed_examples": [c.key for c in bad[-3:]]}

    def open_incidents(self, ci: str, at: Optional[datetime] = None) -> list[dict]:
        out = []
        for i in self.incidents:
            if i.ci != ci:
                continue
            if at is None:
                is_open = i.is_open
            else:
                o, r = parse_ts(i.opened_at), parse_ts(i.resolved_at)
                is_open = bool(o and o < at and (r is None or r > at))
            if is_open:
                out.append({"key": i.key, "short_description": i.short_description, "opened_at": i.opened_at})
        return out

    def open_problems(self, ci: str, at: Optional[datetime] = None) -> list[dict]:
        return [{"key": p.key, "short_description": p.short_description, "known_error": p.known_error}
                for p in self.problems
                if p.ci == ci and p.is_open and (at is None or (parse_ts(p.opened_at) or at) <= at)]

    def run(self, ch: Change, at: Optional[datetime] = None, exclude: Optional[str] = None) -> dict:
        fam = family(ch)
        similar = []
        for c, score in self.index.search(f"{ch.short_description} {ch.description}", k=6):
            if c.key in (ch.key, exclude) or len(similar) >= 5:
                continue
            similar.append({"key": c.key, "short_description": c.short_description, "ci": c.ci,
                            "outcome": c.close_code, "caused_incidents": self.caused.get(c.key, []),
                            "score": round(score, 2)})
        return {"family": fam, "overall_rate": round(self.overall, 3),
                "family_stats": self.stats(lambda c: self.family[c.key] == fam, exclude),
                "ci_stats": self.stats(lambda c: c.ci == ch.ci, exclude, strength=20),
                "group_stats": self.stats(lambda c: c.assignment_group == ch.assignment_group, exclude, strength=20),
                "similar": similar,
                "open_incidents": self.open_incidents(ch.ci, at) if ch.ci else [],
                "open_problems": self.open_problems(ch.ci, at) if ch.ci else []}
