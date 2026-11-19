# Project context for Claude

Portfolio project: multi-agent AI for change management (risk, CAB recommendation, post-implementation verification)
for the fictional retailer ShopKart Retails (SKR) on a ServiceNow PDI. All data is synthetic (tag `SYNTH-SKR-CHG`).
Successor to `../agentic-aiops-servicenow-azure` (frozen at v1.0; do not edit it). Design: `docs/design.md`.

## Hard rules
- Agents must never read `ground_truth.json`. Only the evaluation scorer may; a test enforces this.
- Answer-bearing fields on open changes (outcome, CAB decision, caused incidents) are redacted before agents see them.
- The AI never approves or moves a change's state; it only recommends and writes work notes / its own risk field.
- ServiceNow writes are dry-run unless `--write` is passed. Never commit credentials.
- Claude via the Anthropic API defaults to `claude-opus-5-5`.
- Docs are written in the first person singular ("I"): solo project.
- The user commits and pushes; don't commit unless asked.
