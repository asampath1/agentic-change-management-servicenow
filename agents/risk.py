"""Risk agent: likelihood that a change fails, learned from the change history.

The model is a logistic regression over the signals the other agents report. Every weight starts from the same
neutral prior: +0.5 log-odds for each warning sign the policy names, -0.5 for each mitigation, and "trust the
history" (1.0) for the past failure rates. The 260 closed changes then move the weights (MAP fit, Gaussian prior),
so signals the history supports gain weight and the rest stay near the prior. No evaluation data is used.

Free-text clues are regular expressions written from the wording seen in the HISTORY only.

Two ways to turn the estimate into a band (``RiskAgent(mode=...)``):
* ``point``        - the most likely failure probability
* ``conservative`` - (default) the upper end of an 80% one-sided credible range. When the history says little about a
                     combination of factors, this leans towards escalation (policy: missing a risky change costs
                     more than an extra review). The 80% level was fixed before any evaluation run.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from .history import logit
from .policy import band

CLUES = {
    "irreversible": re.compile(r"one-way|cannot (?:be )?(?:undone|reverted?)|cannot revert|without a full restore|"
                               r"no rollback|not reversible", re.I),
    "first_time": re.compile(r"\bfirst (?:time|use)\b", re.I),
    "vendor": re.compile(r"\bvendor\b", re.I),
    "big_bang": re.compile(r"\ball (?:nodes|instances)\b|at the same time|at once\b", re.I),
    "canary": re.compile(r"staged rollout|\bcanary\b|% of traffic|one node first", re.I),
    "rehearsed": re.compile(r"\brehears", re.I),
}

# feature -> (prior mean, prior sd)
PRIOR = {
    "intercept": (0.0, 1.0),
    "family_logit": (1.0, 0.5), "group_dev": (1.0, 0.5), "ci_dev": (1.0, 0.5),
    "criticality": (0.5, 0.5), "dependents": (0.25, 0.25),
    "emergency": (0.5, 0.5),
    "implementation_weak": (0.5, 0.5), "implementation_missing": (0.5, 0.5),
    "backout_weak": (0.5, 0.5), "backout_missing": (0.5, 0.5),
    "test_weak": (0.5, 0.5), "test_missing": (0.5, 0.5),
    "business_hours": (0.5, 0.5), "outside_window": (0.5, 0.5), "freeze": (0.5, 0.5),
    "conflict_same_ci": (0.5, 0.5), "conflict_related_ci": (0.5, 0.5),
    "open_problem": (0.5, 0.5), "open_incident": (0.5, 0.5), "short_lead": (0.5, 0.5),
    "irreversible": (0.5, 0.5), "first_time": (0.5, 0.5), "vendor": (0.5, 0.5), "big_bang": (0.5, 0.5),
    "canary": (-0.5, 0.5), "rehearsed": (-0.5, 0.5),
}
NAMES = list(PRIOR)
LABELS = {
    "family_logit": "how often this kind of change failed before", "group_dev": "resolver group's track record",
    "ci_dev": "this CI's change track record", "criticality": "service tier", "dependents": "CIs that depend on it",
    "emergency": "emergency change", "business_hours": "planned in business hours",
    "outside_window": "outside the maintenance window", "freeze": "inside a freeze",
    "conflict_same_ci": "overlaps another change on the same CI", "conflict_related_ci": "overlaps a change on a dependent CI",
    "open_problem": "open problem on the CI", "open_incident": "open incident on the CI",
    "short_lead": "raised less than 48 h before start", "irreversible": "text says it cannot easily be undone",
    "first_time": "text says it is done for the first time", "vendor": "text says a vendor does the work",
    "big_bang": "text says everything changes at once", "canary": "text describes a staged rollout",
    "rehearsed": "text says it was rehearsed",
}


def text_of(ch) -> str:
    return " ".join([ch.description, ch.implementation_plan, ch.backout_plan, ch.test_plan])


def features(ch, quality: dict, impact: dict, history: dict, schedule: dict,
             clues: Optional[dict] = None) -> dict[str, float]:
    """``clues``: text factors found by a reader (``agents.text_reader``); default: the history regexes."""
    x = {n: 0.0 for n in NAMES}
    x["intercept"] = 1.0
    base = logit(history["overall_rate"])
    x["family_logit"] = logit(history["family_stats"]["smoothed_rate"]) - base
    x["group_dev"] = logit(history["group_stats"]["smoothed_rate"]) - base
    x["ci_dev"] = logit(history["ci_stats"]["smoothed_rate"]) - base
    x["criticality"] = 3 - impact.get("tier", 3)
    x["dependents"] = min(len(impact.get("dependents", [])), 3)
    x["emergency"] = float(ch.type == "emergency")
    for s, r in quality.items():
        if r["status"] != "ok":
            x[f"{s}_{r['status']}"] = 1.0
    x["business_hours"] = float(schedule.get("window") == "business")
    x["outside_window"] = float(schedule.get("window") == "off_hours")
    x["freeze"] = float(bool(schedule.get("freeze")))
    rels = {c["relation"] for c in schedule.get("conflicts", [])}
    x["conflict_same_ci"] = float("same_ci" in rels)
    x["conflict_related_ci"] = float("related_ci" in rels and "same_ci" not in rels)
    x["open_problem"] = float(bool(history.get("open_problems")))
    x["open_incident"] = float(bool(history.get("open_incidents")))
    x["short_lead"] = float(bool(schedule.get("short_lead")))
    if clues is None:
        text = text_of(ch)
        for name, rx in CLUES.items():
            x[name] = float(bool(rx.search(text)))
    else:
        for name in CLUES:
            x[name] = float(name in clues)
    return x


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the system is small and positive definite)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


class RiskModel:
    def __init__(self):
        self.w = {n: PRIOR[n][0] for n in NAMES}
        self.n_train = 0
        self.n_failed = 0
        self.cov: list[list[float]] | None = None      # posterior covariance of the weights (Laplace approximation)

    def fit(self, rows: list[tuple[dict[str, float], int]], iters: int = 25) -> "RiskModel":
        mu = [PRIOR[n][0] for n in NAMES]
        prec = [1 / PRIOR[n][1] ** 2 for n in NAMES]
        w = mu[:]
        X = [[x[n] for n in NAMES] for x, _ in rows]
        Y = [y for _, y in rows]
        k = len(NAMES)
        def objective(w):
            loss = sum(prec[i] * (w[i] - mu[i]) ** 2 / 2 for i in range(k))
            for xi, yi in zip(X, Y):
                z = sum(wj * xj for wj, xj in zip(w, xi))
                loss += math.log1p(math.exp(-abs(z))) + max(z, 0) - yi * z      # log(1 + e^z) - y z, stable
            return loss

        for _ in range(iters):
            g = [prec[i] * (w[i] - mu[i]) for i in range(k)]
            H = [[prec[i] if i == j else 0.0 for j in range(k)] for i in range(k)]
            for xi, yi in zip(X, Y):
                z = sum(wj * xj for wj, xj in zip(w, xi))
                p = 1 / (1 + math.exp(-z))
                d, s = p - yi, p * (1 - p)
                nz = [j for j in range(k) if xi[j]]
                for a in nz:
                    g[a] += d * xi[a]
                    for b in nz:
                        H[a][b] += s * xi[a] * xi[b]
            step = _solve(H, g)
            f0, t = objective(w), 1.0
            while t > 1e-4:                                   # backtracking: only accept improving steps
                cand = [wi - t * si for wi, si in zip(w, step)]
                if objective(cand) <= f0:
                    break
                t /= 2
            w = cand
            if max(abs(t * s) for s in step) < 1e-6:
                break
        self.w = dict(zip(NAMES, w))
        self.n_train, self.n_failed = len(rows), sum(Y)
        # Laplace approximation: covariance = inverse Hessian of the objective at the optimum
        H = [[prec[i] if i == j else 0.0 for j in range(k)] for i in range(k)]
        for xi in X:
            z = sum(wj * xj for wj, xj in zip(w, xi))
            p = 1 / (1 + math.exp(-z))
            nz = [j for j in range(k) if xi[j]]
            for a in nz:
                for b in nz:
                    H[a][b] += p * (1 - p) * xi[a] * xi[b]
        cols = [_solve(H, [1.0 if r == c else 0.0 for r in range(k)]) for c in range(k)]
        self.cov = [[cols[c][r] for c in range(k)] for r in range(k)]
        return self

    def sd(self, x: dict[str, float]) -> float:
        """Standard deviation of the log-odds for this change."""
        if not self.cov:
            return 0.0
        v = [x.get(n, 0.0) for n in NAMES]
        k = len(v)
        return math.sqrt(max(0.0, sum(v[i] * self.cov[i][j] * v[j] for i in range(k) for j in range(k) if v[i] and v[j])))

    def predict(self, x: dict[str, float]) -> tuple[float, dict[str, float]]:
        contrib = {n: self.w[n] * x[n] for n in NAMES if x.get(n)}
        z = sum(contrib.values())
        return 1 / (1 + math.exp(-z)), contrib


class RiskAgent:
    name = "risk"

    Z80 = 0.8416        # one-sided 80%

    def __init__(self, model: RiskModel, mode: str = "conservative"):
        if mode not in ("point", "conservative"):
            raise ValueError(mode)
        self.model, self.mode = model, mode

    def run(self, ch, quality: dict, impact: dict, history: dict, schedule: dict,
            clues: Optional[dict] = None) -> dict:
        x = features(ch, quality, impact, history, schedule, clues)
        p, contrib = self.model.predict(x)
        z = math.log(p / (1 - p))
        p_hi = 1 / (1 + math.exp(-(z + self.Z80 * self.model.sd(x))))
        basis = p_hi if self.mode == "conservative" else p
        drivers = sorted(((n, c) for n, c in contrib.items() if n != "intercept" and abs(c) >= 0.1),
                         key=lambda t: -abs(t[1]))
        return {"probability": round(p, 3), "probability_upper80": round(p_hi, 3), "mode": self.mode,
                "band": band(basis),
                "requester_risk": {"2": "high", "3": "moderate", "4": "low"}.get(ch.risk or "", "unknown"),
                "drivers": [{"factor": n, "label": LABELS.get(n, n), "log_odds": round(c, 2)} for n, c in drivers],
                "text_clues": [n for n in CLUES if x[n]],
                "text_evidence": {n: q for n, q in (clues or {}).items() if n in CLUES}, "features": x}
