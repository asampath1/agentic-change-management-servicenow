# Evaluation

`score.py` compares an assessment run with the answer key (`synthetic-data/data/ground_truth.json`). It is the only
code that reads the answer key.

```bash
python -m agents --out runs/latest                       # assess the 40 open changes (offline)
python -m evaluation.score runs/latest/predictions.json  # writes scores.json + report.md next to it
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

Full results: [`results/heuristic-point/`](results/heuristic-point/report.md) and
[`results/heuristic-conservative/`](results/heuristic-conservative/report.md). Each folder has the predictions, the
work notes the agents would post, and the learned weights (`model.json`).
