"""Offline implementations backed by the synthetic dataset in ``synthetic-data/data/csv``.

* ``LocalITSM``                - reads the CSVs, redacts open incidents, writes updates to an outbox
* ``SyntheticLogAnalytics``    - a stand-in Log Analytics workspace built from alert payloads
* ``MockAutomation``           - records runbook jobs instead of running them

The answer key lives in the same data folder. ``LocalITSM`` reads only the ``csv/`` folder,
and ``guarded_open`` refuses any path that looks like the answer key, so an agent cannot
reach it through this connector even by accident.
"""
from __future__ import annotations

import csv
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models import Change, ConfigItem, Incident, KBArticle, Problem, parse_ts
from .base import AutomationClient, ITSMClient, LogAnalyticsClient, redact_open
from .kb_parse import parse_kb

FORBIDDEN_NAME_PARTS = ("ground_truth",)   # the eval answer key: scoring code only
OPEN_STATES = {"1", "2", "3"}              # New, In Progress, On Hold
RESOLVED_STATES = {"6", "7"}               # Resolved, Closed

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "synthetic-data" / "data"


class AnswerKeyAccessError(PermissionError):
    pass


def guarded_open(path: Path, mode: str = "r"):
    name = Path(path).name.lower()
    if any(p in name for p in FORBIDDEN_NAME_PARTS):
        raise AnswerKeyAccessError(f"agents may not read the evaluation answer key: {path}")
    return open(path, mode, encoding="utf-8", newline="" if "r" in mode else None)


def _strip_ref(v: str) -> Optional[str]:
    """'group:Azure Infra Ops' -> 'Azure Infra Ops'; '' -> None."""
    if not v:
        return None
    return v.split(":", 1)[1] if ":" in v else v


class LocalITSM(ITSMClient):
    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR, outbox: Path | str | None = None):
        self.csv_dir = Path(data_dir) / "csv"
        self.outbox = Path(outbox) if outbox else None
        self._rows: dict[str, list[dict[str, str]]] = {}
        groups = self._read("groups")
        # CSV references use the short group name; ServiceNow shows the full "SKR ..." name.
        self._group_name = {_strip_ref(g["_key"]): g["name"] for g in groups}
        self._ci = {c["name"]: c for c in self._read("cis")}

    # ------------------------------------------------------------------ helpers
    def _read(self, table: str) -> list[dict[str, str]]:
        if table not in self._rows:
            with guarded_open(self.csv_dir / f"{table}.csv") as f:
                self._rows[table] = list(csv.DictReader(f))
        return self._rows[table]

    def _grp(self, ref: str) -> Optional[str]:
        short = _strip_ref(ref)
        return self._group_name.get(short, short) if short else None

    def _incident(self, r: dict[str, str]) -> Incident:
        return Incident(
            key=r["_key"], short_description=r["short_description"], description=r["description"],
            caller=_strip_ref(r["caller_id"]), category=r["category"] or None,
            impact=int(r["impact"] or 3), urgency=int(r["urgency"] or 3),
            ci=_strip_ref(r["cmdb_ci"]), business_service=_strip_ref(r["business_service"]),
            opened_at=r["opened_at"] or None, contact_type=r["contact_type"] or None, state=r["state"],
            assignment_group=self._grp(r["assignment_group"]), work_notes=r["work_notes"],
            close_notes=r["close_notes"], caused_by=r["caused_by"] or None,
            problem_id=r["problem_id"] or None, resolved_at=r["resolved_at"] or None)

    # ------------------------------------------------------------------ ITSMClient
    def open_incidents(self) -> list[Incident]:
        return [redact_open(self._incident(r)) for r in self._read("incidents") if r["state"] in OPEN_STATES]

    def resolved_incidents(self) -> list[Incident]:
        return [self._incident(r) for r in self._read("incidents") if r["state"] in RESOLVED_STATES]

    def kb_articles(self) -> list[KBArticle]:
        return [parse_kb(r["_key"], r["short_description"], r["text"], r.get("meta", ""))
                for r in self._read("kb") if r.get("workflow_state", "published") == "published"]

    def problems(self) -> list[Problem]:
        return [Problem(key=r["_key"], short_description=r["short_description"], ci=_strip_ref(r["cmdb_ci"]),
                        known_error=r["known_error"] == "true", cause_notes=r["cause_notes"],
                        workaround=r["workaround"]) for r in self._read("problems")]

    def changes(self, cis: Iterable[str], start: datetime, end: datetime) -> list[Change]:
        wanted = set(cis)
        out = []
        for r in self._read("changes"):
            ci = _strip_ref(r["cmdb_ci"])
            st = parse_ts(r["start_date"])
            if ci in wanted and st and start <= st <= end:
                out.append(Change(key=r["_key"], short_description=r["short_description"],
                                  description=r["description"], ci=ci, type=r["type"],
                                  assignment_group=self._grp(r["assignment_group"]),
                                  start=r["start_date"], end=r["end_date"], state=r["state"]))
        return out

    def config_item(self, name: str) -> Optional[ConfigItem]:
        c = self._ci.get(name)
        if not c:
            return None
        return ConfigItem(name=name, description=c["short_description"].replace("[SYNTH-SKR] ", ""),
                          support_group=self._grp(c["support_group"]), os=c.get("os", ""),
                          ci_class=c.get("sys_class_name", ""))

    def related_cis(self, name: str) -> list[str]:
        out = []
        for r in self._read("relationships"):
            p, c = r["parent"], r["child"]
            if not (p.startswith("ci:") and c.startswith("ci:")):
                continue
            if _strip_ref(p) == name:
                out.append(_strip_ref(c))
            elif _strip_ref(c) == name:
                out.append(_strip_ref(p))
        return out

    def groups(self) -> list[str]:
        return sorted(self._group_name.values())

    def update_incident(self, inc: Incident, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        rec = {"incident": inc.key, "fields": fields, "work_note": work_note,
               "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "target": "local-outbox"}
        if self.outbox:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return rec


class SyntheticLogAnalytics(LogAnalyticsClient):
    """A pretend Log Analytics workspace.

    Each Azure Monitor alert in the dataset carries the metric/log excerpt that fired it
    ("Resource: ...", "w3wp.exe private bytes 15.2 GB", "Error code: ..."). We load those
    lines into an in-memory ``AlertEvidence`` table keyed by resource, which is roughly what a
    real ``AzureDiagnostics`` / ``Perf`` / ``Event`` query would surface for the same resource.
    The KQL text is recorded but not interpreted.
    """

    def __init__(self, itsm: ITSMClient, now: Optional[datetime] = None):
        self.queries: list[dict[str, Any]] = []
        self._rows: dict[str, list[dict[str, Any]]] = {}
        incidents = itsm.open_incidents() + itsm.resolved_incidents()
        self.now = now or max((parse_ts(i.opened_at) for i in incidents if i.opened_at), default=datetime.now(timezone.utc))
        for inc in incidents:
            if not inc.ci or "Auto-generated by Azure Monitor" not in inc.description:
                continue
            for line in inc.description.splitlines()[1:]:
                line = line.strip()
                if line:
                    self._rows.setdefault(inc.ci, []).append(
                        {"TimeGenerated": inc.opened_at, "Resource": inc.ci, "Message": line})

    def query(self, kql: str, resource: Optional[str], hours: int = 24,
              end: Optional[datetime] = None) -> list[dict[str, Any]]:
        self.queries.append({"kql": kql, "resource": resource, "hours": hours})
        if not resource:
            return []
        end = end or self.now
        since = end - timedelta(hours=hours)
        return [r for r in self._rows.get(resource, [])
                if since <= (parse_ts(r["TimeGenerated"]) or end) <= end + timedelta(minutes=5)]


class MockAutomation(AutomationClient):
    """Records runbook starts. Nothing is executed."""

    def __init__(self):
        self.jobs: list[dict[str, Any]] = []

    def start_runbook(self, runbook: str, parameters: dict[str, Any]) -> dict[str, Any]:
        job = {"job_id": str(uuid.uuid4()), "runbook": runbook, "parameters": parameters,
               "status": "Completed", "simulated": True}
        self.jobs.append(job)
        return job


_KV = re.compile(r"^\s*([A-Za-z][A-Za-z /()-]{1,40}):\s*(.+)$")


def parse_alert_fields(description: str) -> dict[str, str]:
    """Pull 'Key: value' pairs from an Azure Monitor alert body."""
    out = {}
    for line in description.splitlines():
        m = _KV.match(line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out
