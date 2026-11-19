"""Offline ITSM backed by the synthetic dataset in ``synthetic-data/data/csv``.

The answer key lives in the same data folder. ``LocalITSM`` reads only the ``csv/`` folder, and ``guarded_open``
refuses any path that looks like the answer key, so an agent cannot reach it through this connector even by accident.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any, Optional

from ..models import Change, ConfigItem, Incident, KBArticle, Problem, ScheduleSpan, parse_ts
from .base import ITSMClient, redact_open
from .kb_parse import parse_kb

FORBIDDEN_NAME_PARTS = ("ground_truth",)   # the eval answer key: scoring code only
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "synthetic-data" / "data"
TIER = {"1": 1, "2": 2, "3": 3}


class AnswerKeyAccessError(PermissionError):
    pass


def guarded_open(path: Path, mode: str = "r"):
    name = Path(path).name.lower()
    if any(p in name for p in FORBIDDEN_NAME_PARTS):
        raise AnswerKeyAccessError(f"agents may not read the evaluation answer key: {path}")
    return open(path, mode, encoding="utf-8", newline="" if "r" in mode else None)


def _ref(v: str) -> Optional[str]:
    """'group:Azure Infra Ops' -> 'Azure Infra Ops'; '' -> None."""
    if not v:
        return None
    return v.split(":", 1)[1] if ":" in v else v


class LocalITSM(ITSMClient):
    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR, outbox: Path | str | None = None):
        self.csv_dir = Path(data_dir) / "csv"
        self.outbox = Path(outbox) if outbox else None
        self._rows: dict[str, list[dict[str, str]]] = {}

    def _read(self, table: str) -> list[dict[str, str]]:
        if table not in self._rows:
            with guarded_open(self.csv_dir / f"{table}.csv") as f:
                self._rows[table] = list(csv.DictReader(f))
        return self._rows[table]

    @cached_property
    def _group_name(self) -> dict[str, str]:
        # CSV references use the short group name; ServiceNow shows the full "SKR ..." name.
        return {_ref(g["_key"]): g["name"] for g in self._read("groups")}

    def _grp(self, ref: str) -> Optional[str]:
        short = _ref(ref)
        return self._group_name.get(short, short) if short else None

    @cached_property
    def _services(self) -> dict[str, tuple[str, int]]:
        """CI name -> (service name, tier)."""
        svc = {_ref(s["_key"]): (s["name"], TIER.get(s["busines_criticality"][:1], 3)) for s in self._read("services")}
        out = {}
        for r in self._read("relationships"):
            if r["parent"].startswith("svc:") and r["child"].startswith("ci:"):
                out[_ref(r["child"])] = svc[_ref(r["parent"])]
        return out

    @cached_property
    def _deps(self) -> list[tuple[str, str]]:
        """(a, b): CI a depends on CI b."""
        return [(_ref(r["parent"]), _ref(r["child"])) for r in self._read("relationships")
                if r["parent"].startswith("ci:") and r["child"].startswith("ci:")]

    # ------------------------------------------------------------------ ITSMClient
    @cached_property
    def _changes(self) -> list[Change]:
        out = []
        for r in self._read("changes"):
            out.append(redact_open(Change(
                key=r["_key"], short_description=r["short_description"], description=r["description"],
                justification=r["justification"], implementation_plan=r["implementation_plan"],
                backout_plan=r["backout_plan"], test_plan=r["test_plan"], type=r["type"], category=r["category"] or None,
                ci=_ref(r["cmdb_ci"]), business_service=_ref(r["business_service"]),
                assignment_group=self._grp(r["assignment_group"]), requested_by=_ref(r["requested_by"]),
                start=r["start_date"] or None, end=r["end_date"] or None, work_start=r["work_start"] or None,
                work_end=r["work_end"] or None, risk=r["risk"] or None, impact=r["impact"] or None, state=r["state"],
                opened_at=r["opened_at"] or None, work_notes=r["work_notes"], close_code=r["close_code"] or None,
                close_notes=r["close_notes"])))
        return out

    def changes(self) -> list[Change]:
        return list(self._changes)

    @cached_property
    def _incidents(self) -> list[Incident]:
        return [Incident(key=r["_key"], short_description=r["short_description"], description=r["description"],
                         ci=_ref(r["cmdb_ci"]), business_service=_ref(r["business_service"]),
                         category=r["category"] or None, impact=int(r["impact"] or 3), urgency=int(r["urgency"] or 3),
                         state=r["state"], opened_at=r["opened_at"] or None, resolved_at=r["resolved_at"] or None,
                         assignment_group=self._grp(r["assignment_group"]), caused_by=r["caused_by"] or None,
                         work_notes=r["work_notes"], close_notes=r["close_notes"])
                for r in self._read("incidents")]

    def incidents(self) -> list[Incident]:
        return list(self._incidents)

    def problems(self) -> list[Problem]:
        return [Problem(key=r["_key"], short_description=r["short_description"], ci=_ref(r["cmdb_ci"]),
                        known_error=r["known_error"] == "true", cause_notes=r["cause_notes"],
                        workaround=r["workaround"], state=r["state"], opened_at=r["opened_at"] or None)
                for r in self._read("problems")]

    def kb_articles(self) -> list[KBArticle]:
        return [parse_kb(r["_key"], r["short_description"], r["text"], r.get("meta", ""))
                for r in self._read("kb") if r.get("workflow_state", "published") == "published"]

    def schedules(self) -> list[ScheduleSpan]:
        sched = {r["_key"]: r for r in self._read("schedules")}
        out = []
        for r in self._read("schedule_spans"):
            s = sched[r["schedule"]]
            out.append(ScheduleSpan(schedule=s["name"], kind=s["type"], applies_to=s["u_applies_to"],
                                    start=parse_ts(r["start_date_time"]), end=parse_ts(r["end_date_time"]),
                                    repeat=r["repeat_type"], days_of_week=r["days_of_week"],
                                    description=s["description"]))
        return out

    def config_item(self, name: str) -> Optional[ConfigItem]:
        c = next((r for r in self._read("cis") if r["name"] == name), None)
        if not c:
            return None
        svc, tier = self._services.get(name, (None, 3))
        return ConfigItem(name=name, description=c["short_description"].split("] ", 1)[-1],
                          support_group=self._grp(c["support_group"]), ci_class=c.get("sys_class_name", ""),
                          service=svc, tier=tier)

    def dependents(self, name: str) -> list[str]:
        return sorted(a for a, b in self._deps if b == name)

    def dependencies(self, name: str) -> list[str]:
        return sorted(b for a, b in self._deps if a == name)

    def update_change(self, ch: Change, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        rec = {"change": ch.key, "fields": fields, "work_note": work_note,
               "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "target": "local-outbox"}
        if self.outbox:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return rec
