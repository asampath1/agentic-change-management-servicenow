# Change assessment scores

Assessed 40 changes.

| Metric | Result |
|---|---|
| **Missed high risk** (truly high, recommended plain approval) | **1 of 12** |
| High-risk changes rated low | 1 of 12 |
| Low-risk changes sent to CAB (over-escalation) | 1 |
| Risk band accuracy / macro F1 | 50% / 0.49 |
| Recommendation accuracy / macro F1 | 65% / 0.66 |
| Plan quality: section accuracy | 100% |
| Plan quality: missing plans found (P / R) | 1.0 / 1.0 |
| Plan quality: weak or missing found (P / R) | 1.0 / 1.0 |
| Conflicts (P / R) | 1.0 / 1.0 |
| Freeze overlaps (P / R) | 1.0 / 1.0 |
| Window class accuracy | 100% |
| Messy subset (risk only in free text): band correct | 1 of 6 |

Risk band confusion (rows = truth, columns = predicted):

| truth \ predicted | low | moderate | high |
|---|---|---|---|
| low | 4 | 8 | 1 |
| moderate | 1 | 8 | 6 |
| high | 1 | 3 | 8 |

Recommendation confusion:

| truth \ predicted | approve | approve_with_conditions | cab | send_back |
|---|---|---|---|---|
| approve | 4 | 6 | 1 | 0 |
| approve_with_conditions | 1 | 6 | 3 | 0 |
| cab | 1 | 2 | 7 | 0 |
| send_back | 0 | 0 | 0 | 9 |

Missed high-risk changes: chg:0307
