"""Verification agent: after a change is implemented, did it cause incidents? Suggest the close code.

Steps for a change in state Review:
1. Candidates: incidents opened between work start and 24 h after work end (policy review window) on the changed CI
   or a CI directly related to it. Earlier, later or elsewhere is ruled out, with the reason recorded.
2. Link check: a small logistic model over four signals - similarity (TF-IDF cosine) to incidents that changes of
   the same kind caused in the HISTORY, similarity to any change-caused incident (a fallback for kinds with few
   examples), same CI or a related one, and how soon after the work it was opened. It is fitted on the history's
   candidates (linked vs not linked, leave-one-out) and links when the probability passes the threshold that best
   separates them in the history. History labels are noisy: some caused incidents were never linked.
3. Close code: "unsuccessful" if the implementer's notes say a backout was executed; "successful_issues" if incidents
   are linked; otherwise "successful".

Everything is a suggestion in a work note: the agent never links incidents or closes the change itself.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from .connectors.base import ITSMClient
from .history import HistoryAgent, family
from .models import Change, Incident, parse_ts
from .policy import REVIEW_WINDOW_HOURS
from .retrieval import tokenize

REVIEW_STATE = "0"
BACKOUT = re.compile(r"\bback ?out (?:was )?(?:executed|performed|done)|\brolled back\b|\breverted\b|\bbackout executed\b",
                     re.I)


class VerificationAgent:
    name = "verification"

    def __init__(self, itsm: ITSMClient, history: HistoryAgent):
        self.itsm, self.history = itsm, history
        self.incidents = itsm.incidents()
        self.by_key = {i.key: i for i in self.incidents}
        ci_words = {t for c in {i.ci for i in self.incidents if i.ci} | {c.ci for c in history.closed if c.ci}
                    for t in tokenize(c.replace("-", " "))}
        self._skip = ci_words
        docs = [self._tokens(i) for i in self.incidents]
        df = Counter(t for d in docs for t in set(d))
        self.idf = {t: math.log((1 + len(docs)) / (1 + n)) + 1 for t, n in df.items()}
        # symptom profile per change family: tokens of incidents that its past changes caused
        self.profile: dict[str, Counter] = defaultdict(Counter)
        self.all_caused: Counter = Counter()
        self.caused_by_change: dict[str, list[str]] = defaultdict(list)
        fam = {c.key: family(c) for c in history.closed}
        for i in self.incidents:
            if i.caused_by in fam:
                toks = self._tokens(i)
                self.profile[fam[i.caused_by]].update(toks)
                self.all_caused.update(toks)
                self.caused_by_change[i.caused_by].append(i.key)
        self.weights = [0.0] * len(self.SIGNALS)
        self.threshold, self.calibration = self._calibrate()

    # ------------------------------------------------------------------ similarity
    def _tokens(self, inc: Incident) -> list[str]:
        return [t for t in tokenize(f"{inc.short_description} {inc.description}") if t not in self._skip]

    def _vec(self, counts: Counter) -> dict[str, float]:
        v = {t: n * self.idf.get(t, 1.0) for t, n in counts.items() if n > 0}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def _cos(self, inc: Incident, prof: Optional[Counter], leave_out: Optional[Counter]) -> float:
        if not prof:
            return 0.0
        if leave_out:
            prof = prof - leave_out
        a, b = self._vec(Counter(self._tokens(inc))), self._vec(prof)
        return sum(x * b.get(t, 0.0) for t, x in a.items())

    def symptom_score(self, inc: Incident, fam: str, leave_out: Optional[Counter] = None) -> float:
        return round(self._cos(inc, self.profile.get(fam), leave_out), 3)

    SIGNALS = ("intercept", "symptom_same_kind", "symptom_any_change", "same_ci", "hours_after_end_per_24h")

    def signals(self, inc: Incident, fam: str, row: dict, leave_out: Optional[Counter] = None) -> list[float]:
        return [1.0, self._cos(inc, self.profile.get(fam), leave_out), self._cos(inc, self.all_caused, leave_out),
                float(row["same_ci"]), max(0.0, row["hours_after_end"]) / 24]

    def link_probability(self, x: list[float]) -> float:
        z = sum(w * v for w, v in zip(self.weights, x))
        return 1 / (1 + math.exp(-z))

    # ------------------------------------------------------------------ candidates
    def candidates(self, ch: Change) -> tuple[list[dict], list[dict]]:
        """(in-window incidents on the CI or a related CI, ruled-out incidents with the reason)."""
        ws, we = parse_ts(ch.work_start or ch.start), parse_ts(ch.work_end or ch.end)
        if not (ws and we and ch.ci):
            return [], []
        related = self.itsm.related(ch.ci)
        near, ruled_out = [], []
        horizon = we + timedelta(hours=REVIEW_WINDOW_HOURS)
        for i in self.incidents:
            o = parse_ts(i.opened_at)
            if not o or o < ws - timedelta(days=1) or o > horizon + timedelta(days=2):
                continue                                         # nowhere near the change
            if i.caused_by and i.caused_by != ch.key:
                continue                                         # already attributed to another change
            on_ci = i.ci == ch.ci or i.ci in related
            row = {"key": i.key, "ci": i.ci, "opened_at": i.opened_at, "short_description": i.short_description,
                   "hours_after_end": round((o - we).total_seconds() / 3600, 1), "same_ci": i.ci == ch.ci}
            if o < ws:
                if on_ci:
                    ruled_out.append(dict(row, reason="opened before the change started"))
            elif o > horizon:
                if on_ci:
                    ruled_out.append(dict(row, reason=f"opened more than {REVIEW_WINDOW_HOURS} h after the change"))
            elif not on_ci:
                ruled_out.append(dict(row, reason="different CI, not related in the CMDB"))
            else:
                near.append(row)
        return near, ruled_out

    def _calibrate(self) -> tuple[float, dict]:
        """Fit the link model on history candidates, then pick the probability threshold with the best F1 there."""
        X, Y = [], []
        for c in self.history.closed:
            fam = family(c)
            near, _ = self.candidates(c)
            own = Counter()
            for k in self.caused_by_change.get(c.key, []):
                own.update(self._tokens(self.by_key[k]))                # leave this change's own incidents out
            for row in near:
                X.append(self.signals(self.by_key[row["key"]], fam, row, own))
                Y.append(int(self.by_key[row["key"]].caused_by == c.key))
        w = [0.0] * len(self.SIGNALS)
        for _ in range(3000):                                           # plain gradient descent, small L2
            grad = [0.01 * wi for wi in w]
            grad[0] = 0.0
            for x, y in zip(X, Y):
                p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))
                for j, v in enumerate(x):
                    grad[j] += (p - y) * v / len(X)
            w = [wi - 0.5 * g for wi, g in zip(w, grad)]
        self.weights = w
        scored = [(self.link_probability(x), bool(y)) for x, y in zip(X, Y)]
        best = (0.0, 0.5)
        for thr in sorted({s for s, _ in scored}):
            tp = sum(1 for s, y in scored if s >= thr and y)
            fp = sum(1 for s, y in scored if s >= thr and not y)
            fn = sum(1 for s, y in scored if s < thr and y)
            f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
            if f1 > best[0]:
                best = (f1, thr)
        return best[1], {"history_candidates": len(scored), "history_linked": sum(y for _, y in scored),
                         "f1_on_history": round(best[0], 3), "threshold": round(best[1], 3),
                         "weights": {n: round(v, 3) for n, v in zip(self.SIGNALS, w)}}

    # ------------------------------------------------------------------ verdict
    def run(self, ch: Change) -> dict:
        fam = family(ch)
        near, ruled_out = self.candidates(ch)
        linked = []
        for row in near:
            inc = self.by_key[row["key"]]
            x = self.signals(inc, fam, row)
            p = self.link_probability(x)
            row = dict(row, link_probability=round(p, 3), symptom_same_kind=round(x[1], 2), symptom_any_change=round(x[2], 2))
            where = "same CI" if row["same_ci"] else "related CI " + row["ci"]
            if p >= self.threshold:
                linked.append(dict(row, reason=f"{where}, {row['hours_after_end']:+.1f} h from the end of the work, "
                                                 f"symptom like past change failures ({x[1]:.2f} same kind, {x[2]:.2f} any); "
                                                 f"link likelihood {p:.0%}"))
            else:
                ruled_out.append(dict(row, reason=f"{where} but symptom unlike past change failures "
                                                  f"(link likelihood {p:.0%} < {self.threshold:.0%})"))
        backout = bool(BACKOUT.search(ch.work_notes or ""))
        code = "unsuccessful" if backout else ("successful_issues" if linked else "successful")
        return {"family": fam, "backout_in_notes": backout, "suggested_close_code": code,
                "linked_incidents": linked, "ruled_out": ruled_out, "threshold": self.threshold,
                "window": {"from": ch.work_start, "to_end_plus_hours": REVIEW_WINDOW_HOURS}}

    def work_note(self, ch: Change, v: dict) -> str:
        label = {"successful": "Successful", "successful_issues": "Successful with issues",
                 "unsuccessful": "Unsuccessful"}[v["suggested_close_code"]]
        parts = ["[AI post-implementation review - suggestion only; the change owner closes the change]",
                 f"Suggested close code: {label}."]
        if v["backout_in_notes"]:
            parts.append("The implementation notes say a backout was executed.")
        if v["linked_incidents"]:
            parts.append("Incidents that look caused by this change (suggest setting Caused by Change):\n"
                         + "\n".join(f"- {i['key']}: {i['short_description'][:80]} ({i['reason']})"
                                     for i in v["linked_incidents"]))
        else:
            parts.append(f"No incident on {ch.ci} or related CIs matches this change within "
                         f"{REVIEW_WINDOW_HOURS} h of the work.")
        nearby = [i for i in v["ruled_out"] if "not related" not in i["reason"]]
        elsewhere = len(v["ruled_out"]) - len(nearby)
        if nearby:
            parts.append("Checked on this CI or related CIs and not linked:\n" + "\n".join(
                f"- {i['key']} on {i['ci']}: {i['reason']}" for i in nearby[:8]))
        if elsewhere:
            parts.append(f"{elsewhere} other incident(s) in the window were on unrelated CIs.")
        return "\n\n".join(parts)
