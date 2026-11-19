# agentic-change-management-servicenow

Multi-agent AI that prepares ServiceNow change requests for the change advisory board (CAB) and verifies changes
after they are made. It scores risk with evidence, finds conflicts and freeze-window clashes, drafts a CAB summary,
and checks afterwards whether the change caused incidents. A person makes every approval decision.

> **Disclaimer:** *ShopKart Retails (SKR)* is a fictional company. All people, systems and tickets are synthetic.
> No client or employer data is used.

> **Status: in progress.** M1 (dataset) and M2 (assessment agents) done; verification agent next.
> Plan: [docs/design.md](docs/design.md).

## Quick start (offline, no credentials)

```bash
python -m agents --out runs/latest                         # assess the 40 open change requests
python -m evaluation.score runs/latest/predictions.json    # score against the hidden answer key
pip install pytest && python -m pytest -q
```

Docs: [dataset](synthetic-data/README.md) · [agents](agents/README.md) · [evaluation and baseline results](evaluation/README.md)

Follows on from [agentic-aiops-servicenow-azure](https://github.com/asampath1/agentic-aiops-servicenow-azure)
(v1.0, incident triage and auto-remediation).
