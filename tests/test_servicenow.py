"""ServiceNow connector + loader conversions, without a PDI (the Table API is faked)."""
import sys
from pathlib import Path

from agents.connectors.servicenow import ServiceNowITSM
from agents.schedule import covers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic-data"))
from load_to_snow import fix_states_script, reuse_query, to_ist_span  # noqa: E402


def f(v, d=None):
    return {"value": v, "display_value": d if d is not None else v}


ROWS = {
    "cmdb_ci_service": [{"name": f("SKR Order Processing"), "busines_criticality": f("1 - most critical")}],
    "cmdb_ci": [{"name": f("sql-orders-prod"), "short_description": f("[SYNTH-SKR] Orders Azure SQL DB"),
                 "support_group": f("g1", "SKR Azure Database Ops"), "sys_class_name": f("cmdb_ci_db_mssql_instance")},
                {"name": f("func-orders-processor"), "short_description": f("[SYNTH-SKR] Function"),
                 "support_group": f("g2", "SKR App Support - Enterprise Apps"), "sys_class_name": f("cmdb_ci_appl")}],
    "cmdb_rel_ci": [{"parent": f("s1", "SKR Order Processing"), "child": f("c1", "sql-orders-prod")},
                    {"parent": f("c2", "func-orders-processor"), "child": f("c1", "sql-orders-prod")}],
    "cmn_schedule": [{"sys_id": f("sch1"), "name": f("SKR Tier-1 maintenance window"), "type": f("maintenance"),
                      "description": f("[SYNTH-SKR-CHG] Tue, Thu, Sun 01:00-05:00 IST. Applies to: tier 1")}],
    "cmn_schedule_span": [{"schedule": f("sch1"), "start_date_time": f("20260519T010000"),
                           "end_date_time": f("20260519T050000"), "repeat_type": f("weekly"), "days_of_week": f("247")}],
    "incident": [{"number": f("INC0011001"), "short_description": f("Blocked processes"), "description": f(""),
                  "cmdb_ci": f("c1", "sql-orders-prod"), "business_service": f(""), "category": f("database"),
                  "impact": f("2"), "urgency": f("2"), "state": f("2"), "opened_at": f("2026-11-18 21:00:00"),
                  "resolved_at": f(""), "assignment_group": f(""), "u_caused_by": f("x", "CHG0030100"),
                  "work_notes": f(""), "close_notes": f("")}],
}


class FakeSN(ServiceNowITSM):
    def __init__(self):                    # no network, no credentials
        self.tag, self.write, self.writes, self.base = "SYNTH-SKR-CHG", False, [], "https://example"

    def _get(self, table, query, fields, limit=500):
        return ROWS.get(table, [])


def test_cmdb_and_tiers():
    sn = FakeSN()
    ci = sn.config_item("sql-orders-prod")
    assert ci.service == "SKR Order Processing" and ci.tier == 1 and ci.description == "Orders Azure SQL DB"
    assert sn.dependents("sql-orders-prod") == ["func-orders-processor"]
    assert sn.dependencies("func-orders-processor") == ["sql-orders-prod"]


def test_schedule_spans_come_back_in_utc():
    span = FakeSN().schedules()[0]
    assert span.applies_to == "tier 1" and span.days_of_week == "136" and span.repeat == "weekly"
    from datetime import datetime
    assert covers(span, datetime(2026, 11, 23, 19, 30), datetime(2026, 11, 23, 22, 30))   # Tue 01:00 IST


def test_incident_link_uses_change_number():
    assert FakeSN().incidents()[0].caused_by == "CHG0030100"


def test_dry_run_write_sends_nothing():
    from agents.models import Change
    rec = FakeSN().update_change(Change(key="CHG1", short_description="x", number="CHG1", sys_id="s"),
                                 {"u_ai_risk": "high"}, "note")
    assert rec["sent"] is False and rec["fields"] == {"u_ai_risk": "high"}


def test_loader_conversions():
    assert to_ist_span({"start_date_time": "2026-05-18 19:30:00", "end_date_time": "2026-05-18 23:30:00",
                        "days_of_week": "136"})["days_of_week"] == "247"
    assert reuse_query("cmdb_ci_appl", {"name": "kv-ecom-prod"}) == "name=kv-ecom-prod"
    assert reuse_query("change_request", {"short_description": "x"}) is None          # changes are always new
    js = fix_states_script({"chg:1": {"sys_id": "abc"}}, {"chg:1": {"state": "3", "type": "standard"}})
    assert '"abc": ["3", "standard"]' in js and "setWorkflow(false)" in js and "SYNTH-SKR-CHG" in js
