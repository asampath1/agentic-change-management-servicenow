"""Change orchestrator: runs the specialist agents on each change, recommends, and writes back a work note.

The AI never approves or moves a change: it writes its assessment (work note + its own risk field) and a person
decides.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from .connectors.base import ITSMClient
from .history import HistoryAgent
from .impact import ImpactAgent
from .models import Assessment, Change
from .policy import recommend
from .quality import QualityAgent
from .risk import RiskAgent, RiskModel, features
from .schedule import ScheduleAgent

ASSESS_STATE = "-4"
WINDOW_TEXT = {"tier1": "Tue, Thu or Sun 01:00-05:00 IST", "other": "daily 22:00-06:00 IST"}
REC_TEXT = {"approve": "Approve (change manager)", "approve_with_conditions": "Approve with conditions (change manager)",
            "cab": "Refer to CAB", "send_back": "Send back to the requester"}


class ChangeOrchestrator:
    def __init__(self, itsm: ITSMClient, model: Optional[RiskModel] = None, risk_mode: str = "conservative"):
        self.itsm = itsm
        self.history = HistoryAgent(itsm)
        self.quality = QualityAgent(self.history.closed)
        self.impact = ImpactAgent(itsm)
        self.schedule = ScheduleAgent(itsm)
        self.model = model or self.train()
        self.risk = RiskAgent(self.model, mode=risk_mode)

    # ------------------------------------------------------------------ learning from history
    def training_rows(self) -> list[tuple[dict, int]]:
        rows = []
        closed = self.history.closed
        for c in closed:
            q, imp = self.quality.run(c), self.impact.run(c)
            hist = self.history.run(c, at=c.start_dt, exclude=c.key)      # leave-one-out rates
            sch = self.schedule.run(c, pool=closed)
            rows.append((features(c, q, imp, hist, sch), int(self.history.failed(c))))
        return rows

    def train(self) -> RiskModel:
        return RiskModel().fit(self.training_rows())

    # ------------------------------------------------------------------ assessment
    def assess(self, ch: Change) -> Assessment:
        q, imp = self.quality.run(ch), self.impact.run(ch)
        hist, sch = self.history.run(ch), self.schedule.run(ch)
        risk = self.risk.run(ch, q, imp, hist, sch)
        statuses = {s: r["status"] for s, r in q.items()}
        rec = recommend(risk["band"], statuses, bool(sch["freeze"]), ch.type)
        a = Assessment(change_key=ch.key, change_number=ch.number, short_description=ch.short_description,
                       quality=q, impact=imp, history=hist, schedule=sch, risk=risk, recommendation=rec)
        a.conditions = self.conditions(ch, a)
        a.summary = self.summary(ch, a)
        return a

    def conditions(self, ch: Change, a: Assessment) -> list[str]:
        out = []
        for s, r in a.quality.items():
            if r["status"] == "missing":
                out.append(f"Add a {s} plan ({r['reason']}).")
            elif r["status"] == "weak":
                out.append(f"Make the {s} plan specific ({r['reason']}).")
        sch, hist = a.schedule, a.history
        if sch["freeze"]:
            out.append(("ECAB approval needed: " if ch.type == "emergency" else "Reschedule outside the freeze: ")
                       + ", ".join(sch["freeze"]) + ".")
        if sch["window"] in ("business", "off_hours"):
            win = WINDOW_TEXT["tier1" if a.impact.get("tier") == 1 else "other"]
            out.append(f"Move into the maintenance window ({win})"
                       + (", out of business hours." if sch["window"] == "business" else "."))
        for c in sch["conflicts"]:
            where = "the same CI" if c["relation"] == "same_ci" else f"dependent CI {c['ci']}"
            out.append(f"Re-sequence with {c['key']} on {where} ({c['short_description']}, {c['start']} UTC).")
        for i in hist["open_incidents"]:
            out.append(f"Hold until {i['key']} on {ch.ci} is resolved ({i['short_description'][:70]}).")
        for p in hist["open_problems"]:
            out.append(f"Open problem {p['key']} on {ch.ci}: confirm this change does not make it worse.")
        if sch.get("short_lead"):
            out.append(f"Raised {sch['lead_time_hours']} h before start (policy: at least 48 h).")
        if "irreversible" in a.risk["text_clues"]:
            out.append("Backout relies on a restore: confirm restore time and data loss are acceptable.")
        if a.recommendation == "cab" or a.risk["band"] == "high":
            out.append("Resolver group and service owner on standby for the whole window.")
        return out

    def summary(self, ch: Change, a: Assessment) -> str:
        r, imp, h, sch = a.risk, a.impact, a.history, a.schedule
        fam = h["family_stats"]
        rng = f" (up to {r['probability_upper80']:.0%} allowing for uncertainty)" if r["mode"] == "conservative" else ""
        lines = [f"{r['band'].upper()} risk: estimated {r['probability']:.0%} chance of failure or an incident{rng} "
                 f"(requester rated it {r['requester_risk']})."]
        deps = imp.get("dependents", [])
        lines.append(f"{imp.get('ci')} is in {imp.get('service') or 'no known service'} (tier {imp.get('tier')}); "
                     + (f"{len(deps)} CI(s) depend on it: {', '.join(deps)}." if deps else "nothing else depends on it."))
        if fam["n"]:
            lines.append(f"Similar past changes ('{h['family']}'): {fam['failed']} of {fam['n']} failed or caused incidents"
                         + (f", e.g. {', '.join(fam['failed_examples'])}." if fam["failed_examples"] else "."))
        win = {"maint": f"inside {sch.get('maintenance_window')}", "business": "in business hours",
               "off_hours": "outside the maintenance window"}.get(sch["window"], sch["window"])
        lines.append(f"Planned {sch.get('start')} to {sch.get('end')} UTC, {win}.")
        if r["drivers"]:
            lines.append("Main risk drivers: " + "; ".join(f"{d['label']} ({d['log_odds']:+.2f})" for d in r["drivers"][:5]) + ".")
        lines.append(f"Recommendation: {REC_TEXT[a.recommendation]}.")
        return "\n".join(lines)

    def work_note(self, a: Assessment) -> str:
        parts = ["[AI change assessment - recommendation only; the change manager / CAB decides]", a.summary]
        if a.conditions:
            parts.append("Conditions / actions:\n" + "\n".join(f"- {c}" for c in a.conditions))
        return "\n\n".join(parts)

    def run(self, write: bool = True, keys: Optional[set[str]] = None) -> list[Assessment]:
        out = []
        for ch in self.itsm.changes_in_state(ASSESS_STATE):
            if keys and ch.key not in keys:
                continue
            a = self.assess(ch)
            if write:
                self.itsm.update_change(ch, {"u_ai_risk": a.risk["band"], "u_ai_recommendation": a.recommendation},
                                        self.work_note(a))
            out.append(a)
        return out


def prediction(a: Assessment) -> dict:
    """The fields the scorer compares with the answer key."""
    return {"key": a.change_key, "number": a.change_number, "risk_band": a.risk["band"],
            "failure_probability": a.risk["probability"], "recommendation": a.recommendation,
            "quality": {s: r["status"] for s, r in a.quality.items()},
            "conflicts": [{"key": c["key"], "relation": c["relation"]} for c in a.schedule["conflicts"]],
            "freeze": a.schedule["freeze"], "window": a.schedule["window"]}


def to_dict(a: Assessment) -> dict:
    d = asdict(a)
    d["risk"] = {k: v for k, v in d["risk"].items() if k != "features"}
    return d
