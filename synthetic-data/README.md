# Synthetic change-management dataset

The fictional retailer *ShopKart Retails* (SKR) has the same people, groups, CMDB, services, KB and problems as in the
[v1.0 incident project](https://github.com/asampath1/agentic-aiops-servicenow-azure). On top of that, the dataset has
six months of change requests with outcomes, freeze and maintenance schedules, and two evaluation sets. Every record
carries the tag `SYNTH-SKR-CHG`.

```bash
python3 generate_data.py                                # writes data/ (deterministic: seed 7, now = 2026-11-20 06:00 UTC)
python3 generate_data.py --now "2026-12-01 06:00:00"    # re-anchor all dates, e.g. just before loading a PDI
```

## What is in it

| Table (CSV) | Rows | Notes |
|---|---|---|
| changes | 336 | 260 closed (history), 16 scheduled (the future calendar), 40 to assess, 20 to verify |
| incidents | 297 | background incidents, incidents caused by failed changes, and recent incidents near the verify changes |
| schedules / schedule_spans | 6 / 14 | freeze periods (sale events; month-end close for Payments, Orders, Data) and maintenance windows |
| kb | 21 | 20 troubleshooting articles plus the **SKR Change Management Policy** (windows, freezes, approval rules) |
| users, groups, cis, services, relationships, problems | | as in v1.0; CMDB dependencies give each change its blast radius |

## How outcomes are decided (hidden from the agents)

`change_model.py` gives each change a true failure probability. It is a logistic model of factors the agents can
observe:
- service tier and blast radius,
- change kind,
- the resolver group's track record,
- plan quality,
- the timing (business hours, outside the maintenance window, freeze),
- conflicts, open problems or incidents on the CI, and short lead time.

It also includes free-text clues and some noise. For history changes, the outcome is drawn from that probability, in time order, so clashes with other
changes and incidents already open on the CI count in the history as well.
Failed changes raise the incidents that follow them, and about 85% of those incidents are linked back to the change.

Risk bands come from the true probability: low < 0.10, moderate < 0.18, high ≥ 0.18. The expected recommendation
follows the published policy:
- send back if a plan is missing, or if the change falls in a freeze and is not an emergency;
- otherwise CAB for high, approve with conditions for moderate, and approve for low.

**Free-text clues** (irreversible, first time, vendor-run, big-bang, staged rollout, rehearsed) are written in two
phrasings. Phrasing A appears in the history and phrasing B only in the evaluation set. The 6 "messy" evaluation
changes have clean structured fields: their risk band changes only because of what the text says.

## Evaluation sets (`data/ground_truth.json`, scoring code only)

- **assess** (40 changes in state Assess):
  - risk band (13 low, 15 moderate, 12 high), the factors behind it, and the expected recommendation
    (11 approve, 10 with conditions, 10 CAB, 9 send back);
  - plan quality per section, conflicts with other changes, freeze overlaps and window class.
- **verify** (20 changes in state Review):
  - the outcome: 12 successful, 5 successful with issues, 3 unsuccessful;
  - the incidents each change caused;
  - 5 distractor incidents: raised before the change, on another CI, more than 24 h later, or with an unrelated
    symptom.

Nothing that holds an answer reaches the CSVs or the load plan (see `tests/test_answer_key_isolation.py`).

## Loading into ServiceNow

`load_to_snow.py` is carried over from v1.0 unchanged. The new tables (`cmn_schedule`, `cmn_schedule_span`) and the
change fields still need checking on the PDI. That is milestone M4.
