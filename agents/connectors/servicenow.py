"""ServiceNow Table API connector (live PDI) for the change-management dataset.

Uses the same env vars as ``synthetic-data/load_to_snow.py``: ``SN_INSTANCE`` (e.g. dev123456), ``SN_USER``,
``SN_PASSWORD``. Records are keyed by their instance numbers (CHG..., INC..., PRB...).

Writes are dry-run unless ``write=True``: in dry-run the PATCH body is returned but not sent. A write only adds a
work note and sets the AI fields (u_ai_risk, u_ai_recommendation, u_ai_close_code); it never changes the state,
approvals or links of a change.

Not exercised in CI (no PDI there); the offline ``LocalITSM`` implements the same interface.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any, Optional

from ..models import Change, ConfigItem, Incident, KBArticle, Problem, ScheduleSpan
from .base import ITSMClient, redact_open
from .kb_parse import parse_kb

TAG = "SYNTH-SKR-CHG"
IST = timedelta(hours=5, minutes=30)
TIER = {"1": 1, "2": 2, "3": 3}


def _dv(v: Any) -> Optional[str]:
    """With sysparm_display_value=all every field is {'display_value', 'value'}."""
    if isinstance(v, dict):
        return v.get("display_value") or None
    return v or None


def _val(v: Any) -> Optional[str]:
    if isinstance(v, dict):
        return v.get("value") or None
    return v or None


def _span_utc(value: str) -> datetime:
    """cmn_schedule_span times are written in IST as 'YYYYMMDDTHHMMSS' by the loader."""
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S") - IST


class ServiceNowITSM(ITSMClient):
    def __init__(self, instance: str | None = None, user: str | None = None, password: str | None = None,
                 write: bool = False, timeout: int = 60, tag: str = TAG):
        import requests  # imported lazily so offline runs don't need it

        self.instance = instance or os.environ["SN_INSTANCE"]
        self.base = self.instance if self.instance.startswith("http") else f"https://{self.instance}.service-now.com"
        self.s = requests.Session()
        self.s.auth = (user or os.environ["SN_USER"], password or os.environ["SN_PASSWORD"])
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.write, self.timeout, self.tag = write, timeout, tag
        self.writes: list[dict[str, Any]] = []

    def _get(self, table: str, query: str, fields: str, limit: int = 500) -> list[dict[str, Any]]:
        out, offset = [], 0
        while True:
            r = self.s.get(f"{self.base}/api/now/table/{table}", timeout=self.timeout, params={
                "sysparm_query": query, "sysparm_fields": fields, "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true", "sysparm_limit": limit, "sysparm_offset": offset})
            r.raise_for_status()
            batch = r.json().get("result", [])
            out.extend(batch)
            if len(batch) < limit:
                return out
            offset += limit

    # ------------------------------------------------------------------ CMDB
    @cached_property
    def _services(self) -> dict[str, int]:
        return {_dv(r["name"]): TIER.get((_val(r["busines_criticality"]) or "3")[:1], 3)
                for r in self._get("cmdb_ci_service", "nameSTARTSWITHSKR ", "name,busines_criticality")}

    @cached_property
    def _cis(self) -> dict[str, dict]:
        rows = self._get("cmdb_ci", "short_descriptionLIKESYNTH-SKR^sys_class_name!=cmdb_ci_service",
                         "name,short_description,support_group,sys_class_name")
        return {_dv(r["name"]): r for r in rows}

    @cached_property
    def _rels(self) -> list[tuple[str, str]]:
        rows = self._get("cmdb_rel_ci", "parent.short_descriptionLIKESYNTH-SKR^ORchild.short_descriptionLIKESYNTH-SKR",
                         "parent,child")
        return [(_dv(r["parent"]), _dv(r["child"])) for r in rows]

    @cached_property
    def _ci_service(self) -> dict[str, str]:
        return {c: p for p, c in self._rels if p in self._services}

    def config_item(self, name: str) -> Optional[ConfigItem]:
        r = self._cis.get(name)
        if not r:
            return None
        svc = self._ci_service.get(name)
        return ConfigItem(name=name, description=re.sub(r"^\[[^\]]*\]\s*", "", _dv(r["short_description"]) or ""),
                          support_group=_dv(r["support_group"]), ci_class=_val(r["sys_class_name"]) or "",
                          service=svc, tier=self._services.get(svc, 3))

    def dependents(self, name: str) -> list[str]:
        return sorted(a for a, b in self._rels if b == name and a in self._cis)

    def dependencies(self, name: str) -> list[str]:
        return sorted(b for a, b in self._rels if a == name and b in self._cis)

    # ------------------------------------------------------------------ records
    _CHG_FIELDS = ("sys_id,number,short_description,description,justification,implementation_plan,backout_plan,"
                   "test_plan,type,category,cmdb_ci,business_service,assignment_group,requested_by,start_date,end_date,"
                   "work_start,work_end,risk,impact,state,opened_at,work_notes,close_code,close_notes")

    @cached_property
    def _changes(self) -> list[Change]:
        out = []
        for r in self._get("change_request", f"correlation_id={self.tag}^ORDERBYnumber", self._CHG_FIELDS):
            out.append(redact_open(Change(
                key=_dv(r["number"]), number=_dv(r["number"]), sys_id=_val(r["sys_id"]),
                short_description=_dv(r["short_description"]) or "", description=_val(r["description"]) or "",
                justification=_val(r["justification"]) or "", implementation_plan=_val(r["implementation_plan"]) or "",
                backout_plan=_val(r["backout_plan"]) or "", test_plan=_val(r["test_plan"]) or "",
                type=_val(r["type"]) or "normal", category=_val(r["category"]), ci=_dv(r["cmdb_ci"]),
                business_service=_dv(r["business_service"]), assignment_group=_dv(r["assignment_group"]),
                requested_by=_dv(r["requested_by"]), start=_val(r["start_date"]), end=_val(r["end_date"]),
                work_start=_val(r["work_start"]), work_end=_val(r["work_end"]), risk=_val(r["risk"]),
                impact=_val(r["impact"]), state=_val(r["state"]), opened_at=_val(r["opened_at"]),
                work_notes=_dv(r["work_notes"]) or "", close_code=_val(r["close_code"]),
                close_notes=_val(r["close_notes"]) or "")))
        return out

    def changes(self) -> list[Change]:
        return list(self._changes)

    @cached_property
    def _incidents(self) -> list[Incident]:
        rows = self._get("incident", f"correlation_id={self.tag}",
                         "number,short_description,description,cmdb_ci,business_service,category,impact,urgency,state,"
                         "opened_at,resolved_at,assignment_group,u_caused_by,work_notes,close_notes")
        return [Incident(key=_dv(r["number"]), number=_dv(r["number"]), short_description=_dv(r["short_description"]) or "",
                         description=_val(r["description"]) or "", ci=_dv(r["cmdb_ci"]),
                         business_service=_dv(r["business_service"]), category=_val(r["category"]),
                         impact=int(_val(r["impact"]) or 3), urgency=int(_val(r["urgency"]) or 3), state=_val(r["state"]),
                         opened_at=_val(r["opened_at"]), resolved_at=_val(r["resolved_at"]),
                         assignment_group=_dv(r["assignment_group"]), caused_by=_dv(r.get("u_caused_by")),
                         work_notes=_dv(r["work_notes"]) or "", close_notes=_val(r["close_notes"]) or "")
                for r in rows]

    def incidents(self) -> list[Incident]:
        return list(self._incidents)

    def problems(self) -> list[Problem]:
        rows = self._get("problem", "correlation_idSTARTSWITHSYNTH-SKR",
                         "number,short_description,cmdb_ci,known_error,cause_notes,workaround,state,opened_at")
        return [Problem(key=_dv(r["number"]), number=_dv(r["number"]), short_description=_dv(r["short_description"]) or "",
                        ci=_dv(r["cmdb_ci"]), known_error=_val(r["known_error"]) == "true",
                        cause_notes=_val(r["cause_notes"]) or "", workaround=_val(r["workaround"]) or "",
                        state=_val(r["state"]), opened_at=_val(r["opened_at"])) for r in rows]

    def kb_articles(self) -> list[KBArticle]:
        # KB articles may stay in Draft after insert on a PDI; they are read regardless of workflow state.
        rows = self._get("kb_knowledge", "metaLIKESYNTH-SKR", "number,short_description,text,meta")
        return [parse_kb(_dv(r["number"]), _dv(r["short_description"]) or "", _val(r["text"]) or "", _val(r["meta"]) or "")
                for r in rows]

    def schedules(self) -> list[ScheduleSpan]:
        scheds = {_val(r["sys_id"]): r for r in
                  self._get("cmn_schedule", "nameSTARTSWITHSKR ", "sys_id,name,type,description")}
        if not scheds:
            return []
        out = []
        for r in self._get("cmn_schedule_span", "scheduleIN" + ",".join(scheds),
                           "schedule,start_date_time,end_date_time,repeat_type,days_of_week"):
            s = scheds.get(_val(r["schedule"]))
            if not s:
                continue
            start_ist = _val(r["start_date_time"])
            start, end = _span_utc(start_ist), _span_utc(_val(r["end_date_time"]))
            shift = (start.date() - datetime.strptime(start_ist[:8], "%Y%m%d").date()).days   # IST -> UTC weekday
            dow = "".join(sorted(str((int(d) - 1 + shift) % 7 + 1) for d in (_val(r["days_of_week"]) or "") if d.isdigit()))
            desc = _val(s["description"]) or ""
            m = re.search(r"Applies to:\s*([^.\n]+)", desc)
            out.append(ScheduleSpan(schedule=_dv(s["name"]), kind=_val(s["type"]) or "", applies_to=m.group(1).strip() if m else "all",
                                    start=start, end=end, repeat=_val(r["repeat_type"]) or "", days_of_week=dow,
                                    description=desc))
        return out

    # ------------------------------------------------------------------ write-back
    def update_change(self, ch: Change, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        body = dict(fields, work_notes=work_note)
        rec = {"change": ch.number, "sys_id": ch.sys_id, "fields": fields, "work_note": work_note,
               "target": self.base, "sent": False}
        if self.write:
            r = self.s.patch(f"{self.base}/api/now/table/change_request/{ch.sys_id}", json=body, timeout=self.timeout)
            rec["sent"], rec["status"] = r.status_code < 300, r.status_code
            if r.status_code >= 300:
                rec["error"] = r.text[:300]
        self.writes.append(rec)
        return rec
