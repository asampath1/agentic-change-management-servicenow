# Agents

```bash
python -m agents --out runs/latest                        # assess changes in Assess, review changes in Review (offline)
python -m agents --task verify --out runs/v               # only the post-implementation review
python -m agents --risk-mode point -v --out runs/p        # band from the most likely value; print each work note
```

| Agent | Module | Looks at | Reports |
|---|---|---|---|
| Quality | `quality.py` | implementation, backout and test plans | ok / weak / missing per section, with the reason |
| Impact | `impact.py` | CMDB: the CI, what depends on it, services and tiers | blast radius, highest tier affected |
| History | `history.py` | closed changes and the incidents linked to them, open incidents and problems | failure rate for this kind of change, CI and group; similar past changes |
| Schedule | `schedule.py` | maintenance windows, freeze (blackout) schedules, every open change | window class, freezes hit, conflicts (same or dependent CI), lead time |
| Risk | `risk.py` | all of the above, plus free-text clues | failure probability, risk band, main drivers |
| Verification | `verification.py` | changes in Review: incidents after the work on the CI or related CIs, implementation notes | suggested close code, incidents that look caused by the change, and why others were ruled out |
| Orchestrator | `orchestrator.py` | the published policy (`policy.py`) | recommendation, conditions, CAB summary, work note |

**How risk is learned.**
- The Risk agent is a logistic regression over the signals above. Each weight starts from the same neutral prior:
  +0.5 log-odds per warning sign, −0.5 per mitigation, and "trust the history" for past failure rates.
- The 260 closed changes then move the weights (MAP fit with a Gaussian prior).
- Text clues are regular expressions written from the wording in the history only.
- The learned weights are saved with each run as `model.json`.

**Guardrails.**
- The agents only recommend. They write a work note and their own fields (`u_ai_risk`, `u_ai_recommendation`), and
  never touch the change's state or approvals.
- Verification only suggests: the close code goes in `u_ai_close_code`, and incident links are proposed in the work
  note, never written to Caused by Change.
- Locally, writes go to `work_notes.jsonl`.
- `connectors/servicenow.py` is still the v1.0 incident connector; it is rewritten for changes in M4.
