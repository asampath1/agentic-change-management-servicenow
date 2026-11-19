# Project context for Claude

Portfolio project: multi-agent AI for change management (risk, CAB recommendation, post-implementation verification)
for the fictional retailer ShopKart Retails (SKR) on a ServiceNow PDI. All data is synthetic (tag `SYNTH-SKR-CHG`).
Successor to `../agentic-aiops-servicenow-azure` (frozen at v1.0; do not edit it). Design: `docs/design.md`.

## Layout
- `synthetic-data/` generator (`generate_data.py`), hidden outcome model (`change_model.py`), CSVs, load plan,
  ServiceNow loader. See `synthetic-data/README.md`.
- `agents/`, `evaluation/`: copied from v1.0, being reworked for changes (M2).
- `tests/` pytest suite (`python -m pytest -q`).

## Hard rules
- Agents must never read `ground_truth.json` or import/read `synthetic-data/change_model.py` (the hidden weights).
  Only the evaluation scorer may read the answer key; a test enforces both.
- Heuristic rules may come only from the published policy (KB `SKR Change Management Policy`) and from the change
  history. Never tune them on evaluation-change text: the "messy" eval phrasings are held out on purpose.
- After any generator change, rerun `python3 generate_data.py` (in `synthetic-data/`) and commit the data;
  `tests/test_dataset.py` fails if the committed data is stale.
- Answer-bearing fields on open changes (outcome, CAB decision, caused incidents) are redacted before agents see them.
- The AI never approves or moves a change's state; it only recommends and writes work notes / its own risk field.
- ServiceNow writes are dry-run unless `--write` is passed. Never commit credentials.
- Claude via the Anthropic API defaults to `claude-opus-5-5`.
- Docs are written in the first person singular ("I"): solo project.
- The user commits and pushes; don't commit unless asked.
