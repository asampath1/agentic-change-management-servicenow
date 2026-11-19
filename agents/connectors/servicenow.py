"""ServiceNow Table API connector (live PDI).

Uses the same env vars as ``synthetic-data/load_to_snow.py``:
``SN_INSTANCE`` (e.g. dev123456), ``SN_USER``, ``SN_PASSWORD``.

Writes are dry-run unless ``write=True``: in dry-run the PATCH body is returned but not sent.
Not exercised in CI (no PDI there); the offline ``LocalITSM`` implements the same interface.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Iterable, Optional

from ..models import TS_FMT, Change, ConfigItem, Incident, KBArticle, Problem
from .base import ITSMClient, redact_open
from .kb_parse import parse_kb

TAG = "SYNTH-SKR"


def _dv(v: Any) -> Optional[str]:
    """Reference fields come back as {'display_value', 'link', 'value'} with display_value=all."""
    if isinstance(v, dict):
        return v.get("display_value") or None
    return v or None


def _val(v: Any) -> Optional[str]:
    if isinstance(v, dict):
        return v.get("value") or None
    return v or None


class ServiceNowITSM(ITSMClient):
    def __init__(self, instance: str | None = None, user: str | None = None, password: str | None = None,
                 write: bool = False, timeout: int = 60, tag: str = TAG, open_tag: str | None = None,
                 mark_in_progress: bool = False):
        """``tag`` selects history, KB and problems; ``open_tag`` (default: ``tag``) selects the incidents to
        triage. Live alerts use ``SYNTH-SKR-LIVE`` so they never mix with the 24 eval incidents.
        ``mark_in_progress`` sets state In Progress (2) on write: the AI has taken the ticket. Off for the eval
        set, whose incidents must stay New (state 1) so later eval runs still find them."""
        import requests  # imported lazily so offline runs don't need it

        self.instance = instance or os.environ["SN_INSTANCE"]
        self.base = self.instance if self.instance.startswith("http") else f"https://{self.instance}.service-now.com"
        self.s = requests.Session()
        self.s.auth = (user or os.environ["SN_USER"], password or os.environ["SN_PASSWORD"])
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.write, self.timeout, self.tag = write, timeout, tag
        self.open_tag = open_tag or tag
        self.mark_in_progress = mark_in_progress
        self._group_ids: dict[str, str] = {}
        self._ci_cache: dict[str, Optional[ConfigItem]] = {}

    # ------------------------------------------------------------------ helpers
    def _get(self, table: str, query: str, fields: str, limit: int = 1000) -> list[dict[str, Any]]:
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

    _INC_FIELDS = ("sys_id,number,short_description,description,caller_id,category,impact,urgency,cmdb_ci,"
                   "business_service,opened_at,contact_type,state,assignment_group,work_notes,close_notes,"
                   "caused_by,problem_id,u_caused_by,u_problem_id,resolved_at")
    # Some PDIs have no incident.problem_id / incident.caused_by; load_to_snow.py --relink then stores the
    # links in custom u_ reference fields. Fields that don't exist are simply absent from the response.

    def _incident(self, r: dict[str, Any]) -> Incident:
        def num(x):
            try:
                return int(_val(x) or 3)
            except ValueError:
                return 3
        return Incident(
            key=_dv(r.get("number")) or _val(r.get("sys_id")), number=_dv(r.get("number")), sys_id=_val(r.get("sys_id")),
            short_description=_dv(r.get("short_description")) or "", description=_dv(r.get("description")) or "",
            caller=_dv(r.get("caller_id")), category=_val(r.get("category")),
            impact=num(r.get("impact")), urgency=num(r.get("urgency")),
            ci=_dv(r.get("cmdb_ci")), business_service=_dv(r.get("business_service")),
            opened_at=_val(r.get("opened_at")), contact_type=_val(r.get("contact_type")), state=_val(r.get("state")),
            assignment_group=_dv(r.get("assignment_group")), work_notes=_dv(r.get("work_notes")) or "",
            close_notes=_dv(r.get("close_notes")) or "",
            caused_by=_dv(r.get("caused_by")) or _dv(r.get("u_caused_by")),
            problem_id=_dv(r.get("problem_id")) or _dv(r.get("u_problem_id")), resolved_at=_val(r.get("resolved_at")))

    # ------------------------------------------------------------------ ITSMClient
    def open_incidents(self) -> list[Incident]:
        # Not filtered on an empty assignment group: a PDI's assignment rules fill it from the CI's
        # support group on insert. Whatever is there is redacted before the agents see it.
        rows = self._get("incident", f"correlation_id={self.open_tag}^state=1", self._INC_FIELDS)
        return [redact_open(self._incident(r)) for r in rows]

    def resolved_incidents(self) -> list[Incident]:
        rows = self._get("incident", f"correlation_id={self.tag}^stateIN6,7", self._INC_FIELDS)
        return [self._incident(r) for r in rows]

    def kb_articles(self) -> list[KBArticle]:
        # Not filtered on workflow_state=published: the KB publish workflow can leave inserted articles in draft.
        rows = self._get("kb_knowledge", f"metaLIKE{self.tag}^workflow_state!=retired",
                         "sys_id,number,short_description,text,meta")
        return [parse_kb(_dv(r["number"]) or _val(r["sys_id"]), _dv(r["short_description"]) or "",
                         _val(r.get("text")) or "", _dv(r.get("meta")) or "") for r in rows]

    def problems(self) -> list[Problem]:
        rows = self._get("problem", f"correlation_id={self.tag}",
                         "sys_id,number,short_description,cmdb_ci,known_error,cause_notes,workaround")
        return [Problem(key=_dv(r["number"]), number=_dv(r["number"]), short_description=_dv(r["short_description"]) or "",
                        ci=_dv(r.get("cmdb_ci")), known_error=str(_val(r.get("known_error"))).lower() == "true",
                        cause_notes=_dv(r.get("cause_notes")) or "", workaround=_dv(r.get("workaround")) or "")
                for r in rows]

    def changes(self, cis: Iterable[str], start: datetime, end: datetime) -> list[Change]:
        names = ",".join(cis)
        if not names:
            return []
        q = (f"cmdb_ci.nameIN{names}^start_date>={start.strftime(TS_FMT)}"
             f"^start_date<={end.strftime(TS_FMT)}")
        rows = self._get("change_request", q, "sys_id,number,short_description,description,cmdb_ci,type,"
                                              "assignment_group,start_date,end_date,state")
        return [Change(key=_dv(r["number"]), number=_dv(r["number"]), short_description=_dv(r["short_description"]) or "",
                       description=_dv(r.get("description")) or "", ci=_dv(r.get("cmdb_ci")), type=_val(r.get("type")),
                       assignment_group=_dv(r.get("assignment_group")), start=_val(r.get("start_date")),
                       end=_val(r.get("end_date")), state=_dv(r.get("state"))) for r in rows]

    def config_item(self, name: str) -> Optional[ConfigItem]:
        if name in self._ci_cache:
            return self._ci_cache[name]
        rows = self._get("cmdb_ci", f"name={name}", "name,short_description,support_group,sys_class_name", limit=1)
        ci = None
        if rows:
            r = rows[0]
            ci = ConfigItem(name=name, description=(_dv(r.get("short_description")) or "").replace("[SYNTH-SKR] ", ""),
                            support_group=_dv(r.get("support_group")), ci_class=_val(r.get("sys_class_name")) or "")
        self._ci_cache[name] = ci
        return ci

    def related_cis(self, name: str) -> list[str]:
        rows = self._get("cmdb_rel_ci", f"parent.name={name}^ORchild.name={name}", "parent,child")
        out = []
        for r in rows:
            p, c = _dv(r.get("parent")), _dv(r.get("child"))
            other = c if p == name else p
            if other and not other.startswith("SKR "):   # skip business services
                out.append(other)
        return out

    def groups(self) -> list[str]:
        rows = self._get("sys_user_group", "nameSTARTSWITHSKR ", "sys_id,name")
        self._group_ids = {_dv(r["name"]): _val(r["sys_id"]) for r in rows}
        return sorted(self._group_ids)

    def update_incident(self, inc: Incident, fields: dict[str, Any], work_note: str) -> dict[str, Any]:
        body: dict[str, Any] = {"work_notes": work_note}
        if fields.get("assignment_group"):
            if not self._group_ids:
                self.groups()
            body["assignment_group"] = self._group_ids.get(fields["assignment_group"], fields["assignment_group"])
        for k in ("impact", "urgency"):
            if k in fields:
                body[k] = str(fields[k])
        if self.mark_in_progress:
            body["state"] = "2"   # In Progress; the Logic App resolves it once the alert clears after a successful runbook
        rec = {"incident": inc.number, "sys_id": inc.sys_id, "body": body, "sent": False, "target": self.base}
        if self.write and inc.sys_id:
            r = self.s.patch(f"{self.base}/api/now/table/incident/{inc.sys_id}", json=body, timeout=self.timeout)
            rec.update(sent=True, status=r.status_code)
        return rec
