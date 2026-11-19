# Evaluation

`score.py` compares an assessment run with the answer key (`synthetic-data/data/ground_truth.json`). It is the only
code that reads the answer key.

```bash
python -m agents --out runs/latest           # assess the 40 open changes, review the 20 implemented ones (offline)
python -m evaluation.score runs/latest       # writes scores.json + report.md into the run folder
```

## Metrics

| Metric | What it checks |
|---|---|
| **Missed high risk** | Truly high-risk changes that got a plain "approve" (no conditions, no CAB). The safety metric; the target is 0 |
| High rated low | Truly high-risk changes rated low |
| Over-escalation | Low-risk changes sent to the CAB (wasted CAB time) |
| Risk band accuracy, macro F1 | Low / moderate / high against the band of the true failure probability |
| Recommendation accuracy, macro F1 | Approve / approve with conditions / CAB / send back |
| Plan quality | Per section (implementation, backout, test): ok / weak / missing |
| Conflicts, freeze | Overlapping changes on the same or a dependent CI, and freeze overlaps (precision / recall) |
| Window class | Maintenance window / business hours / other |
| Messy subset | The 6 changes whose risk shows only in free text worded unlike anything in the history |

## Heuristic baseline (no LLM)

The Risk agent learns its weights from the 260 closed changes. Two modes turn the estimate into a risk band:
- **point**: the most likely probability.
- **conservative** (the default): the upper end of an 80% credible range, which leans towards escalation when the
  history is thin. It is the default because waving a risky change through costs far more than one extra review.

The 80% level was fixed before any evaluation run, and neither mode was tuned on the evaluation set.

| | point | conservative |
|---|---|---|
| **Missed high risk** | **3 of 12** | **1 of 12** |
| High rated low | 4 of 12 | 1 of 12 |
| Low sent to CAB | 0 | 1 |
| Risk band accuracy | 62% | 50% |
| Recommendation accuracy | 75% | 65% |
| Plan quality, conflicts, freeze, window | 100% | 100% |
| Messy subset: band correct / missed high | 2 of 6 / 3 | 1 of 6 / 1 |

What this shows:
- **Deterministic checks are solved.** Plan quality, conflicts, freezes and maintenance windows are all 100%.
- **All missed high-risk changes are in the messy subset.** The built-in rules recognise only free-text clues worded
  like the history ("one-way", "first time", "vendor"). The held-out wording, for example "the legacy columns are
  dropped and their data is not copied anywhere", gets past them. Reading that text is the job of the Claude
  reasoner (milestone M5).
- **Conservative mode trades accuracy for safety.** It catches 2 more high-risk changes but sends more low and
  moderate changes up a level.
- **Estimates lean low.** The history under-records failures: some failed changes were closed as "successful" and
  their incidents never linked. A model trained on it inherits that optimism, which is a realistic problem.

## Post-implementation verification

The 20 changes in state Review. The answer key holds each one's real outcome and the incidents it caused, plus 5
distractor incidents: raised before the change, more than 24 h later, on an unrelated CI, or with an unrelated symptom.

| Metric | Result |
|---|---|
| **Missed failures** (failed or caused incidents, suggested Successful) | **0 of 8** |
| False alarms (went fine, flagged Successful with issues) | 2 of 12 |
| Close code accuracy | 90% (18 of 20) |
| Caused-incident links (precision / recall) | 0.78 / 0.70 |
| Distractor incidents wrongly linked | 1 of 5 |

How it decides:
- **Time and CMDB rules** rule out most distractors. Only incidents between the start of the work and 24 h after it,
  on the changed CI or a directly related one, are considered.
- **A link model learned from the history** decides the rest. It is a logistic regression over four signals:
  - symptom similarity to past failures of the same kind of change;
  - similarity to any change-caused incident;
  - same CI;
  - hours after the change.
- **The close code** is "Unsuccessful" when the implementation notes say a backout was executed.

**Disclosure:** the first version used a single similarity threshold. It scored 2 missed failures and links of
0.44 / 0.40, and reached only 0.68 F1 on the history it was calibrated on. I diagnosed the problem on history data
only: with 1-3 past failures per kind of change, leaving a change's own incidents out left nothing to compare with.
I replaced the threshold with the link model, which reached 0.80 F1 on the history, and stopped there. The test set
was scored once before and once after this change.

Full results: [`results/heuristic-point/`](results/heuristic-point/report.md) and
[`results/heuristic-conservative/`](results/heuristic-conservative/report.md). Each folder has the predictions, the
work notes the agents would post, and the learned weights (`model.json`; the link model is in `run.json`). The two
modes differ only in assessment; verification is the same.
