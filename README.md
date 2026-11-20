# agentic-change-management-servicenow

Multi-agent AI that prepares ServiceNow change requests for the change advisory board (CAB) and verifies changes
after they are made. It scores risk with evidence, finds conflicts and freeze-window clashes, drafts a CAB summary,
and checks afterwards whether the change caused incidents. A person makes every approval decision.

> **Disclaimer:** *ShopKart Retails (SKR)* is a fictional company. All people, systems and tickets are synthetic.
> No client or employer data is used.

> **Status: in progress.** M1 (dataset), M2 (assessment agents), M3 (verification) and M4 (live ServiceNow run,
> [write-up](docs/live-pdi.md)) done; optional Claude comparison next.
> Plan: [docs/design.md](docs/design.md).

**New here or not technical?** Start with the plain-language overview: [docs/overview.md](docs/overview.md).

## Quick start (offline, no credentials)

```bash
python -m agents --out runs/latest       # assess 40 open change requests, review 20 implemented ones
python -m evaluation.score runs/latest   # score against the hidden answer key
pip install pytest && python -m pytest -q
```

Docs: [dataset](synthetic-data/README.md) · [agents](agents/README.md) · [evaluation and baseline results](evaluation/README.md)

Follows on from [agentic-aiops-servicenow-azure](https://github.com/asampath1/agentic-aiops-servicenow-azure)
(v1.0, incident triage and auto-remediation).
