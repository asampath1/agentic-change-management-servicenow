"""Turn the specialist agents' evidence into a triage decision.

Two interchangeable reasoners:

* ``HeuristicReasoner`` - deterministic, offline, no API key. The baseline.
* ``ClaudeReasoner``    - asks Claude (default ``claude-opus-5-5``) to weigh the same evidence,
  constrained by a JSON schema whose enums only allow known groups / KB titles / candidate
  change and problem ids. Falls back to the heuristic decision on any API failure.

Neither reasoner decides whether a runbook is safe. That comes from the KB article only,
in ``RemediationAgent``.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Optional

from .models import Evidence, Incident, TriageDecision, priority_from
from .retrieval import overlap

L1 = "SKR Service Desk L1"
END_USER = "End-user issue"


_SPECIFIC = {
    "version": re.compile(r"\b\d+\.\d+\.\d+\b"),
    "update": re.compile(r"\bKB\d{6,}\b"),
    "error_code": re.compile(r"\b(?:AADSTS\d+|UserError\w+|ExtensionSnapshot\w+|[A-Z][a-z]+(?:[A-Z][a-z]+)+(?:Error|Failed\w*))\b"),
    "process": re.compile(r"\b[\w-]+\.exe\b|\b(?:sqlservr|java|python3)\b"),
}


def adapt_cause(cause: str, signals: dict[str, list[str]]) -> tuple[str, bool]:
    """Re-state a past incident's root cause with this incident's specifics.

    "Faulty release 4.9.7 (regression)" + signal version 4.8.4 -> "Faulty release 4.8.4 (regression)".
    Returns (cause, True) when at least one specific was filled from this incident's evidence.
    """
    filled = False
    for kind, rx in _SPECIFIC.items():
        if rx.search(cause) and signals.get(kind):
            cause = rx.sub(signals[kind][0], cause, count=1)
            filled = True
    return cause, filled


class HeuristicReasoner:
    name = "heuristic"

    def __init__(self, groups: list[str], change_threshold: float = 0.5, evidence_bonus: float = 0.5):
        self.groups, self.change_threshold = groups, change_threshold
        self.evidence_bonus = evidence_bonus

    def decide(self, inc: Incident, ev: Evidence) -> TriageDecision:
        sims, kbs = ev.similar_incidents, ev.kb_candidates
        top_sim = sims[0]["score"] if sims else 1.0

        # --- routing: similar-incident vote + top KB's resolver group + CI support group
        votes: dict[str, float] = defaultdict(float)
        for s in sims:
            if s["assignment_group"]:
                votes[s["assignment_group"]] += s["score"] / top_sim
        top_kb = kbs[0] if kbs else None
        if top_kb and top_kb["resolver_group"]:
            votes[top_kb["resolver_group"]] += 1.5
        if ev.ci and ev.ci.get("support_group"):
            votes[ev.ci["support_group"]] += 0.5
        if not inc.ci:
            votes[L1] += 1.0
        group = max(votes, key=votes.get) if votes else L1
        total = sum(votes.values()) or 1.0
        is_noise = group == L1

        # --- change correlation (before KB: a likely change re-weights the KB choice)
        change = next((c for c in ev.change_candidates if c["score"] >= self.change_threshold), None)
        if change and kbs:
            # Past incidents that were caused by a change are the best guide to what this one is.
            boost: dict[str, float] = defaultdict(float)
            for s in sims:
                if s["change_caused"] and s["kb_article"]:
                    boost[s["kb_article"]] += 0.5 * s["score"] / top_sim
            kbs = sorted(({**k, "score": k["score"] + boost.get(k["title"], 0.0)} for k in kbs),
                         key=lambda k: -k["score"])
            top_kb = kbs[0]
            if top_kb["resolver_group"]:
                votes[top_kb["resolver_group"]] += 1.0
                group = max(votes, key=votes.get)
                is_noise = group == L1

        # --- KB: noise tickets only get a KB that names the issue, infra tickets take the top match
        kb_title: Optional[str] = None
        if top_kb and (not is_noise or (top_kb["resolver_group"] in (None, L1) and top_kb["score"] >= 1.0)):
            kb_title = top_kb["title"]
        if is_noise and top_kb and top_kb["resolver_group"] not in (None, L1):
            kb_title = None
        kb = next((k for k in kbs if k["title"] == kb_title), None)
        if kb and kb["resolver_group"] and not is_noise:
            group = kb["resolver_group"]   # the KB names the team that owns this failure mode

        # --- root cause: causes recorded on similar incidents fixed with the same runbook,
        #     re-ranked by overlap with this incident's text and diagnostic signals.
        if is_noise:
            root = END_USER
        else:
            signals = ev.diagnostics.get("signals", {})
            ctx = inc.text + " " + " ".join(" ".join(v) for v in signals.values())
            # Vote over cause *templates*: specifics (versions, error codes, processes, KB numbers)
            # from past incidents are swapped for this incident's own signals where it has them.
            cands: dict[str, float] = defaultdict(float)
            confirmed: set[str] = set()
            for s in sims:
                if not s["root_cause"] or (kb and kb["runbook"] and s["runbook"] != kb["runbook"]):
                    continue
                w = s["score"] / top_sim
                if change and s["change_caused"]:
                    w *= 1.5
                cause, filled = adapt_cause(s["root_cause"], signals)
                cands[cause] += w * (1.5 if filled else 1.0)
                if filled:
                    confirmed.add(cause)
            total_w = sum(cands.values()) or 1.0
            for c in list(cands):
                # A cause whose specifics (process, error code, version, update) this incident's own evidence
                # confirms beats a cause that is merely common in the history of this CI.
                cands[c] = (cands[c] / total_w + 0.5 * overlap(c, ctx)
                            + (self.evidence_bonus if c in confirmed else 0.0))
            if cands:
                root = max(cands, key=cands.get)
            else:
                root = kb["cause"] if kb else "Undetermined - needs engineer review"

        # --- known-error problem
        problem = None
        for p in ev.problem_candidates:
            same_rb = not kb or not p["runbooks"] or kb["runbook"] in p["runbooks"]
            if same_rb and (p["same_ci_votes"] >= 0.8 or (p["votes"] >= 1.5 and p["ci"] == inc.ci)):
                problem = p["key"]
                break

        conf = votes.get(group, 0) / total
        if kb and len(kbs) > 1:
            conf *= min(1.0, kb["score"] / max(kbs[1]["score"], 1e-6) / 1.5)
        return TriageDecision(
            assignment_group=group, priority=priority_from(inc.impact, inc.urgency), is_noise=is_noise,
            kb_article=kb_title, root_cause=root, related_change=change["key"] if change else None,
            related_problem=problem, confidence=round(min(1.0, conf), 3),
            rationale=(f"group vote {votes.get(group, 0):.2f}/{total:.2f}; top KB '{kb_title}'; "
                       f"{len(sims)} similar incidents; change={change['key'] if change else 'none'}"),
            reasoner=self.name)


# ------------------------------------------------------------------------------------ Claude

SYSTEM = """You are the triage lead for an Azure managed-services team that works incidents in ServiceNow.
For one new, unassigned incident you get evidence gathered by specialist agents: KB article matches,
similar resolved incidents (with their resolver group and recorded root cause), recent changes on the CI
and its dependencies, Log Analytics findings, and known-error problem records.

Decide:
- assignment_group: the resolver group that fixed this kind of incident before. End-user issues with no
  Azure resource (passwords, Outlook, access requests, laptops) go to SKR Service Desk L1 and are noise.
- kb_article: the KB article that documents this failure, or NONE if no listed article fits.
- root_cause: one sentence, in the style of the root causes on similar incidents, specific to this
  incident's evidence (process names, error codes, change). For end-user issues write "End-user issue".
- related_change: a change id from the candidates only if the timing and the symptoms make it the
  likely cause; otherwise NONE.
- related_problem: a known-error problem id only if this incident is another occurrence of it; otherwise NONE.
- confidence: 0..1.
Use only the evidence given. Do not invent ids."""


class ClaudeReasoner:
    name = "claude"

    def __init__(self, groups: list[str], kb_titles: list[str], model: str | None = None,
                 effort: str = "medium", fallback: HeuristicReasoner | None = None):
        import anthropic  # lazy: offline runs don't need the SDK

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("AIOPS_CLAUDE_MODEL", "claude-opus-5-5")
        self.effort = effort
        self.groups, self.kb_titles = groups, kb_titles
        self.fallback = fallback or HeuristicReasoner(groups)
        self.usage: list[dict[str, Any]] = []

    def _schema(self, ev: Evidence) -> dict[str, Any]:
        changes = [c["key"] for c in ev.change_candidates] + ["NONE"]
        problems = [p["key"] for p in ev.problem_candidates] + ["NONE"]
        return {"type": "object", "additionalProperties": False,
                "required": ["assignment_group", "is_noise", "kb_article", "root_cause", "related_change",
                             "related_problem", "confidence", "rationale"],
                "properties": {
                    "assignment_group": {"type": "string", "enum": self.groups},
                    "is_noise": {"type": "boolean"},
                    "kb_article": {"type": "string", "enum": self.kb_titles + ["NONE"]},
                    "root_cause": {"type": "string"},
                    "related_change": {"type": "string", "enum": changes},
                    "related_problem": {"type": "string", "enum": problems},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"}}}

    @staticmethod
    def _evidence_payload(inc: Incident, ev: Evidence) -> dict[str, Any]:
        return {
            "incident": {"id": inc.key, "short_description": inc.short_description, "description": inc.description,
                         "ci": inc.ci, "ci_details": ev.ci, "business_service": inc.business_service,
                         "category": inc.category, "opened_at": inc.opened_at, "caller": inc.caller,
                         "contact_type": inc.contact_type},
            "kb_candidates": [{k: v for k, v in c.items() if k != "key"} for c in ev.kb_candidates],
            "similar_resolved_incidents": [{k: s[k] for k in ("short_description", "ci", "assignment_group",
                                                              "root_cause", "runbook", "kb_article", "change_caused",
                                                              "problem", "score")} for s in ev.similar_incidents],
            "change_candidates": ev.change_candidates,
            "diagnostics": {k: ev.diagnostics.get(k) for k in ("ran", "queries", "findings", "signals", "reason")},
            "problem_candidates": ev.problem_candidates,
        }

    def decide(self, inc: Incident, ev: Evidence) -> TriageDecision:
        import anthropic

        prompt = ("Triage this incident from the evidence below.\n\n"
                  + json.dumps(self._evidence_payload(inc, ev), indent=1, default=list))
        try:
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": self._schema(ev)}},
                # Server-side refusal fallback (routes a declined request to another model).
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            if resp.stop_reason != "end_turn":
                raise RuntimeError(f"stop_reason={resp.stop_reason}")
            text = next(b.text for b in resp.content if b.type == "text")
            d = json.loads(text)
            self.usage.append({"incident": inc.key, "input_tokens": resp.usage.input_tokens,
                               "output_tokens": resp.usage.output_tokens})
        except (anthropic.APIError, RuntimeError, StopIteration, json.JSONDecodeError) as e:
            dec = self.fallback.decide(inc, ev)
            dec.rationale = f"[Claude unavailable: {e}] " + dec.rationale
            return dec

        none = lambda v: None if v in (None, "NONE") else v  # noqa: E731
        return TriageDecision(
            assignment_group=d["assignment_group"], priority=priority_from(inc.impact, inc.urgency),
            is_noise=bool(d["is_noise"]), kb_article=none(d["kb_article"]), root_cause=d["root_cause"],
            related_change=none(d["related_change"]), related_problem=none(d["related_problem"]),
            confidence=max(0.0, min(1.0, float(d["confidence"]))), rationale=d["rationale"], reasoner=self.model)
