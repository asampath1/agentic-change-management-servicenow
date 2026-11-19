"""Change-correlation agent: recent changes on the incident's CI and its direct dependencies."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .connectors.base import ITSMClient
from .models import Incident, parse_ts
from .retrieval import tokenize

# Words in an incident that make a given kind of change a plausible cause.
_CHANGE_HINTS = {
    "patch": {"patch", "patching", "update", "updates", "heartbeat", "boot", "reboot", "unreachable", "rdp", "kb"},
    "nsg": {"nsg", "port", "connect", "connection", "connectivity", "timeout", "refused", "deny", "blocked", "firewall"},
    "secret": {"secret", "403", "forbidden", "unauthorized", "keyvault", "vault", "identity", "rotation", "aadsts"},
    "release": {"release", "deploy", "deployment", "5xx", "500", "502", "503", "crash", "crashloopbackoff", "regression"},
    "aksupgrade": {"pod", "pods", "node", "crashloopbackoff", "aks"},
    "sqlmaint": {"sql", "dtu", "blocking", "index", "query"},
}


def _kind(change_text: str) -> str:
    t = change_text.lower()
    if "patch" in t:
        return "patch"
    if "nsg" in t or "network security" in t or "firewall" in t:
        return "nsg"
    if "secret" in t or "rotat" in t or "key vault" in t:
        return "secret"
    if "aks" in t and "upgrade" in t:
        return "aksupgrade"
    if "index" in t or "statistics" in t:
        return "sqlmaint"
    return "release"


class ChangeCorrelationAgent:
    name = "change"

    def __init__(self, itsm: ITSMClient, lookback_hours: int = 72, threshold: float = 0.5):
        self.itsm, self.lookback, self.threshold = itsm, lookback_hours, threshold

    def run(self, inc: Incident) -> list[dict[str, Any]]:
        opened = parse_ts(inc.opened_at)
        if not inc.ci or not opened:
            return []
        neighbours = self.itsm.related_cis(inc.ci)
        changes = self.itsm.changes([inc.ci, *neighbours], opened - timedelta(hours=self.lookback),
                                    opened + timedelta(minutes=30))
        words = set(tokenize(inc.text))
        out = []
        for c in changes:
            start = parse_ts(c.start)
            hours_before = max(0.0, (opened - start).total_seconds() / 3600) if start else self.lookback
            kind = _kind(f"{c.short_description} {c.description}")
            hint = len(words & _CHANGE_HINTS.get(kind, set()))
            score = (1.0 if c.ci == inc.ci else 0.6) * max(0.1, 1 - hours_before / self.lookback)
            score *= 1.0 + 0.25 * min(hint, 2)
            out.append({"key": c.key, "number": c.number, "short_description": c.short_description, "ci": c.ci,
                        "kind": kind, "start": c.start, "state": c.state, "hours_before_incident": round(hours_before, 1),
                        "same_ci": c.ci == inc.ci, "keyword_hits": hint, "score": round(score, 3),
                        "likely_cause": score >= self.threshold})
        return sorted(out, key=lambda x: -x["score"])
