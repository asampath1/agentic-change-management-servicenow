# agentic-change-management-servicenow

Multi-agent AI that prepares ServiceNow change requests for the change advisory board (CAB) and verifies changes
after they are made. It scores risk with evidence, finds conflicts and freeze-window clashes, drafts a CAB summary,
and checks afterwards whether the change caused incidents. A person makes every approval decision.

> **Disclaimer:** *ShopKart Retails (SKR)* is a fictional company. All people, systems and tickets are synthetic.
> No client or employer data is used.

> **Status: complete (2026-11-20).** Dataset, assessment and verification agents, live ServiceNow run
> ([write-up](docs/live-pdi.md)) and the Claude comparison are done. Plan: [docs/design.md](docs/design.md).

**New here or not technical?** Start with the plain-language overview: [docs/overview.md](docs/overview.md).

## Quick start (offline, no credentials)

```bash
python -m agents --out runs/latest       # assess 40 open change requests, review 20 implemented ones
python -m evaluation.score runs/latest   # score against the hidden answer key
pip install pytest && python -m pytest -q
```

## Results

40 change requests waiting for a decision and 20 implemented changes, scored against a hidden answer key.

| | Heuristic (no LLM) | Claude reads the text |
|---|---|---|
| **High-risk changes waved through** | 1 of 12 | **0 of 12** |
| Recommendation accuracy | 65% | 78% |
| Risk hidden only in free text: band right | 1 of 6 | 6 of 6 |
| Missing or vague plans, clashes, freezes | 100% | 100% |
| Failed changes caught afterwards | 8 of 8 | 8 of 8 |

Claude does one job: it reads each change's description and plans for warning signs ("cannot be undone", "vendor",
"all at once", ...) and must quote the text as evidence. The learning, policy and checks are the same in both
columns. The Claude run cost $0.83 for 300 changes. Details: [evaluation](evaluation/README.md#claude-text-reader-m5).

The agents also ran live against a ServiceNow developer instance and wrote their assessment onto all 60 changes,
with the same results as offline ([screenshots](docs/live-pdi.md)).

```bash
python -m agents --reasoner claude --out runs/claude      # needs ANTHROPIC_API_KEY (pip install anthropic)
python -m agents --source servicenow --out runs/pdi       # live PDI, dry run (SN_INSTANCE, SN_USER, SN_PASSWORD)
```

Docs: [dataset](synthetic-data/README.md) · [agents](agents/README.md) · [evaluation and baseline results](evaluation/README.md)

Follows on from [agentic-aiops-servicenow-azure](https://github.com/asampath1/agentic-aiops-servicenow-azure)
(v1.0, incident triage and auto-remediation).
