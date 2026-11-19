# Change assessment scores

Assessed 40 changes.

| Metric | Result |
|---|---|
| **Missed high risk** (truly high, recommended plain approval) | **3 of 12** |
| High-risk changes rated low | 4 of 12 |
| Low-risk changes sent to CAB (over-escalation) | 0 |
| Risk band accuracy / macro F1 | 62% / 0.63 |
| Recommendation accuracy / macro F1 | 75% / 0.76 |
| Plan quality: section accuracy | 100% |
| Plan quality: missing plans found (P / R) | 1.0 / 1.0 |
| Plan quality: weak or missing found (P / R) | 1.0 / 1.0 |
| Conflicts (P / R) | 1.0 / 1.0 |
| Freeze overlaps (P / R) | 1.0 / 1.0 |
| Window class accuracy | 100% |
| Messy subset (risk only in free text): band correct | 2 of 6 |

Risk band confusion (rows = truth, columns = predicted):

| truth \ predicted | low | moderate | high |
|---|---|---|---|
| low | 10 | 3 | 0 |
| moderate | 6 | 9 | 0 |
| high | 4 | 2 | 6 |

Recommendation confusion:

| truth \ predicted | approve | approve_with_conditions | cab | send_back |
|---|---|---|---|---|
| approve | 10 | 1 | 0 | 0 |
| approve_with_conditions | 5 | 5 | 0 | 0 |
| cab | 3 | 1 | 6 | 0 |
| send_back | 0 | 0 | 0 | 9 |

Missed high-risk changes: chg:0307, chg:0309, chg:0316
