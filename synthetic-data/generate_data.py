#!/usr/bin/env python3
"""
Generate the synthetic change-management dataset for a ServiceNow PDI.

Fictional company: ShopKart Retails (all names, emails and IPs are fake). Same people, CMDB, KB and problems as the
v1.0 incident project, plus change requests with plans, freeze and maintenance schedules, and change outcomes drawn
from a hidden model (change_model.py). Failed changes create the incidents that follow them.

Output (in ./data):
  load_plan.json        ordered records for load_to_snow.py (references as "@key")
  csv/*.csv             the same data as CSV per table
  ground_truth.json     answer key for the evaluation changes (scoring code only)
  summary.txt           counts

Evaluation sets:
  assess   open changes (state Assess) to risk-score and recommend on
  verify   implemented changes (state Review) to verify: did they cause incidents?

Usage:
  python3 generate_data.py                                  # defaults below
  python3 generate_data.py --now "2026-12-01 06:00:00"      # re-anchor dates (e.g. before loading a PDI)
"""
import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import change_model as M
import patterns as P

UTC = timezone.utc
IST = timedelta(hours=5, minutes=30)
T1_DAYS = {1, 3, 6}                                   # Tue, Thu, Sun (IST weekday numbers, Mon = 0)
DEFAULT_NOW = "2026-11-20 06:00:00"
STATE = {"new": "-5", "assess": "-4", "authorize": "-3", "scheduled": "-2", "implement": "-1", "review": "0",
         "closed": "3", "canceled": "4"}
OPEN_CHANGE_STATES = {"-5", "-4", "-3", "-2", "-1"}
MONTH_END_SERVICES = ("Payments Integration", "Order Processing", "Data & Analytics")

COMPANY = "ShopKart Retails"
DOMAIN = "shopkart-demo.example"
TAG = "SYNTH-SKR-CHG"        # stamped on every record (correlation_id / description) for easy filtering
FMT = "%Y-%m-%d %H:%M:%S"    # ServiceNow internal date-time format (UTC)

# --------------------------------------------------------------------------- people
FIRST = ["Arjun", "Priya", "Rahul", "Ananya", "Vikram", "Sneha", "Karthik", "Divya", "Suresh", "Meera",
         "Rohan", "Lakshmi", "Aditya", "Kavya", "Naveen", "Pooja", "Sanjay", "Deepa", "Harish", "Nisha",
         "James", "Emma", "Liam", "Olivia", "Noah", "Sophia", "Lucas", "Mia", "Ethan", "Chloe",
         "Mohammed", "Fatima", "Wei", "Mei", "Carlos", "Lucia", "Tomasz", "Anna", "Kenji", "Yuki",
         "Ravi", "Shalini", "Ganesh", "Revathi", "Manoj", "Bhavna", "Ajay", "Sowmya", "Imran", "Zara"]
LAST = ["Iyer", "Sharma", "Reddy", "Nair", "Menon", "Gupta", "Pillai", "Rao", "Krishnan", "Joshi",
        "Kumar", "Subramanian", "Patel", "Das", "Bose", "Verma", "Chopra", "Mishra", "Banerjee", "Shetty",
        "Smith", "Brown", "Wilson", "Taylor", "Clarke", "Walker", "Hughes", "Garcia", "Silva", "Kowalski",
        "Khan", "Chen", "Wong", "Tanaka", "Sato", "Muller", "Rossi", "Fernandes", "Dsouza", "Thomas"]

GROUPS = [  # name, size, description
    ("Service Desk L1", 5, "First-line support; password, access, end-user issues; initial triage"),
    ("Azure Infra Ops", 5, "Azure IaaS: VMs, disks, patching, monitoring agents"),
    ("Azure Database Ops", 3, "Azure SQL / SQL MI performance, blocking, backups"),
    ("App Support - eCommerce", 4, "Storefront, ordering and payments applications"),
    ("App Support - Enterprise Apps", 3, "HR portal, order integration functions, Service Bus"),
    ("Identity & Access Mgmt", 3, "Entra ID, Entra Connect, Key Vault access, service principals"),
    ("Network & Security Ops", 3, "NSG, Azure Firewall, VPN, certificates"),
    ("Cloud Platform Engineering", 3, "AKS, landing zone, policy, IaC"),
    ("Backup & DR", 2, "Recovery Services vaults, restore tests, DR"),
    ("Data Platform Ops", 3, "Data Factory, data lake, data warehouse"),
]
DEPTS = ["Store Operations", "Finance", "HR", "Supply Chain", "Marketing", "Customer Care", "Warehouse"]

# --------------------------------------------------------------------------- CMDB
# (service name, criticality, [ (ci name, kind, table, description, support group) ])
SERVICES = [
    ("E-Commerce Storefront", "1 - most critical", [
        ("vm-ecom-web-01", "iisvm", "cmdb_ci_win_server", "Storefront IIS web node 1 (Windows 2019)", "App Support - eCommerce"),
        ("vm-ecom-web-02", "iisvm", "cmdb_ci_win_server", "Storefront IIS web node 2 (Windows 2019)", "App Support - eCommerce"),
        ("app-ecom-api-prod", "appsvc", "cmdb_ci_appl", "Storefront REST API (App Service, .NET 8)", "App Support - eCommerce"),
        ("sql-ecom-catalog-prod", "sql", "cmdb_ci_db_mssql_instance", "Product catalog Azure SQL DB", "Azure Database Ops"),
        ("redis-ecom-cache", "redis", "cmdb_ci_appl", "Azure Cache for Redis - sessions", "App Support - eCommerce"),
        ("kv-ecom-prod", "kv", "cmdb_ci_appl", "Key Vault - storefront secrets & TLS", "Network & Security Ops"),
    ]),
    ("Order Processing", "1 - most critical", [
        ("vm-orders-app-01", "linuxvm", "cmdb_ci_linux_server", "Order management app server (Ubuntu 22.04)", "App Support - Enterprise Apps"),
        ("func-orders-processor", "func", "cmdb_ci_appl", "Azure Function - order confirmation processor", "App Support - Enterprise Apps"),
        ("sql-orders-prod", "sql", "cmdb_ci_db_mssql_instance", "Orders Azure SQL DB (S3)", "Azure Database Ops"),
        ("sb-orders-queue", "sb", "cmdb_ci_appl", "Service Bus namespace - orders-queue", "App Support - Enterprise Apps"),
    ]),
    ("Payments Integration", "1 - most critical", [
        ("app-payments-prod", "appsvc", "cmdb_ci_appl", "Payments gateway adapter (App Service)", "App Support - eCommerce"),
        ("vm-payments-01", "linuxvm", "cmdb_ci_linux_server", "Payments reconciliation batch (RHEL 9)", "App Support - eCommerce"),
        ("kv-payments-prod", "kv", "cmdb_ci_appl", "Key Vault - payment API keys", "Network & Security Ops"),
    ]),
    ("HR Portal", "3 - less critical", [
        ("app-hrportal-prod", "appsvc", "cmdb_ci_appl", "Employee HR self-service portal", "App Support - Enterprise Apps"),
        ("sql-hr-prod", "sql", "cmdb_ci_db_mssql_instance", "HR Azure SQL DB", "Azure Database Ops"),
        ("vm-hr-batch-01", "winvm", "cmdb_ci_win_server", "HR payroll export batch server", "App Support - Enterprise Apps"),
    ]),
    ("Data & Analytics", "2 - somewhat critical", [
        ("vm-etl-01", "winvm", "cmdb_ci_win_server", "Legacy SSIS ETL server", "Data Platform Ops"),
        ("adf-analytics-prod", "adf", "cmdb_ci_appl", "Azure Data Factory - analytics pipelines", "Data Platform Ops"),
        ("st-datalake-prod", "storage", "cmdb_ci_appl", "ADLS Gen2 data lake storage", "Data Platform Ops"),
        ("sql-dwh-prod", "sql", "cmdb_ci_db_mssql_instance", "Data warehouse Azure SQL DB", "Azure Database Ops"),
    ]),
    ("Identity Services", "1 - most critical", [
        ("vm-aadconnect-01", "aadconnect", "cmdb_ci_win_server", "Entra Connect sync server", "Identity & Access Mgmt"),
        ("vm-dc-01", "winvm", "cmdb_ci_win_server", "Domain controller 1 (Azure)", "Identity & Access Mgmt"),
        ("vm-dc-02", "winvm", "cmdb_ci_win_server", "Domain controller 2 (Azure)", "Identity & Access Mgmt"),
    ]),
    ("Shared Platform", "2 - somewhat critical", [
        ("azfw-hub-prod", "firewall", "cmdb_ci_ip_firewall", "Azure Firewall - hub VNet", "Network & Security Ops"),
        ("vpngw-hub-prod", "vpngw", "cmdb_ci_appl", "VPN gateway - site-to-site links", "Network & Security Ops"),
        ("aks-platform-prod", "aks", "cmdb_ci_appl", "AKS cluster - inventory/pricing microservices", "Cloud Platform Engineering"),
        ("rsv-backup-prod", "backup", "cmdb_ci_appl", "Recovery Services vault - VM & SQL backups", "Backup & DR"),
        ("vm-jump-01", "winvm", "cmdb_ci_win_server", "Jump host for operations", "Azure Infra Ops"),
    ]),
]

# Problems: key -> (short description, CI it is tied to (or None = any), cause, fix, state)
PROBLEMS = {
    "PRB_STOREFRONT_LEAK": ("Recurring memory leak in Storefront IIS app pool", None,
                            "Session cache objects never released in Storefront 4.x", "Fix scheduled in release 4.12", 104),
    "PRB_ORDERS_DTU": ("Recurring DTU exhaustion on sql-orders-prod", "sql-orders-prod",
                       "Missing non-clustered index for usp_GetOrderHistory", "Index to be added via change; temporary scale-up", 104),
    "PRB_CERT_RENEWAL": ("Externally issued TLS certificates expiring without renewal", None,
                         "External-CA certs not covered by auto-renew; no 30-day alert", "Add expiry alerting and renewal process", 103),
    "PRB_AADSYNC": ("Entra Connect sync stops after monthly patching", "vm-aadconnect-01",
                    "ADSync service does not start cleanly after reboot", "Set service recovery options; vendor case open", 103),
}

POLICY_HTML = (
    "<h3>Change types</h3><ul>"
    "<li><b>Standard</b>: pre-approved template, low risk, no CAB.</li>"
    "<li><b>Normal</b>: assessed by the change manager; high-risk changes go to the weekly CAB.</li>"
    "<li><b>Emergency</b>: restores service or fixes a critical security issue; approved by the emergency CAB (ECAB).</li></ul>"
    "<h3>Required plans</h3><p>Normal and emergency changes need an implementation plan, a backout plan and a test plan. "
    "A change with any of them empty, \"N/A\" or \"TBD\" is sent back to the requester. Normal changes are raised at "
    "least 48 hours before the planned start.</p>"
    "<h3>Maintenance windows (IST)</h3><ul><li>Tier-1 services (criticality 1): Tue, Thu and Sun 01:00-05:00.</li>"
    "<li>All other services: daily 22:00-06:00.</li></ul>"
    "<p>Business hours (peak store and online trading): Monday to Friday 09:00-21:00 IST. Work in business hours "
    "carries the most customer impact.</p>"
    "<h3>Freeze periods</h3><p>No normal or standard changes to production during a freeze (published as blackout "
    "schedules: sale events for all services; month-end finance close for Payments Integration, Order Processing and "
    "Data &amp; Analytics). Only emergency changes, approved by the ECAB.</p>"
    "<h3>Risk</h3><p>Risk is the likelihood that the change fails or causes an incident: <b>Low</b> below 10%, "
    "<b>Moderate</b> 10% to 18%, <b>High</b> 18% or more. Judge it from the service tier, what depends on the CI, "
    "the plans, the timing, conflicts, open problems and incidents, and how similar past changes went.</p>"
    "<h3>Approval by risk</h3><ul><li>Low: the change manager approves.</li>"
    "<li>Moderate: the change manager approves with conditions (for example: move into the maintenance window, add a "
    "test step, have the resolver group on standby).</li><li>High: CAB review.</li></ul>"
    "<h3>Conflicts</h3><p>Changes that overlap in time on the same CI or a directly dependent CI must be re-sequenced or "
    "approved together.</p>"
    "<h3>Post-implementation review</h3><p>Incidents on the changed CI or its dependents within 24 hours after the change "
    "are reviewed. Close codes: Successful; Successful with issues (incidents, no backout); Unsuccessful (backout executed "
    "or the change failed).</p>")


def ts(dt):
    return dt.strftime(FMT)


def priority_of(impact, urgency):
    s = impact + urgency
    return {2: 1, 3: 2, 4: 3, 5: 4, 6: 5}[s]


RESOLVE_HOURS = {1: (1, 4), 2: (2, 12), 3: (4, 48), 4: (12, 120), 5: (24, 160)}


def business_time(start, end):
    """Random datetime between start and end, weighted to weekdays / daytime IST."""
    while True:
        dt = start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))
        ist_hour = (dt.hour + 5) % 24
        weekday = dt.weekday() < 5
        if random.random() < (0.85 if weekday else 0.35) * (0.9 if 7 <= ist_hour <= 21 else 0.4):
            return dt


def ist_at(d, hour, minute=0):
    """UTC datetime for an IST wall-clock time on IST date d."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(hours=hour, minutes=minute) - IST


def in_maintenance(tier, s, e):
    si, ei = s + IST, e + IST
    if tier == 1:
        return (si.weekday() in T1_DAYS and si >= si.replace(hour=1, minute=0, second=0)
                and ei <= si.replace(hour=5, minute=0, second=0))
    if si.hour >= 22:
        wend = (si + timedelta(days=1)).replace(hour=6, minute=0, second=0)
    elif si.hour < 6:
        wend = si.replace(hour=6, minute=0, second=0)
    else:
        return False
    return ei <= wend


def in_business_hours(s, e):
    t = s
    while t < e:
        ti = t + IST
        if ti.weekday() < 5 and 9 <= ti.hour < 21:
            return True
        t += timedelta(minutes=15)
    return False


def window_class(tier, s, e):
    if in_maintenance(tier, s, e):
        return "maint"
    return "business" if in_business_hours(s, e) else "off_hours"


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


class Gen:
    def __init__(self, days, seed, now):
        random.seed(seed)
        self.now = now
        self.start = self.now - timedelta(days=days)
        self.recs = defaultdict(list)          # table-group -> list of records
        self.group_members = defaultdict(list)
        self.business_users = []
        self.ci = {}                           # ci name -> dict
        self.deps = []                         # (a, b): a depends on b
        self.kb_key = {}
        self.chg_n = 0
        self.inc_n = 0
        self.changes = []
        self.truth = {"assess": [], "verify": []}
        self.svc_crit = {svc: crit for svc, crit, _ in SERVICES}
        self.pattern = {p["id"].split("_")[0]: p for p in P.PATTERNS}
        self.problem_cis = {ci or {"PRB_STOREFRONT_LEAK": "vm-ecom-web-01"}.get(k, "kv-ecom-prod")
                            for k, (_, ci, *_r) in PROBLEMS.items()}

    # ---------------------------------------------------------------- people
    def people(self):
        names = [(f, l) for f in FIRST for l in LAST]
        random.shuffle(names)
        used = 0

        def mk_user(first, last, title, dept):
            uname = f"synth.{first.lower()}.{last.lower()}"
            rec = {"_table": "sys_user", "_key": f"user:{uname}", "user_name": uname,
                   "first_name": first, "last_name": last, "email": f"{first.lower()}.{last.lower()}@{DOMAIN}",
                   "title": title, "company": None, "active": "true",
                   "u_synthetic_department": dept}  # unknown fields are ignored by the Table API
            rec.pop("company")
            self.recs["users"].append(rec)
            return rec["_key"]

        # service account for monitoring
        self.recs["users"].append({"_table": "sys_user", "_key": "user:synth.svc.azuremonitor",
                                   "user_name": "synth.svc.azuremonitor", "first_name": "Azure Monitor",
                                   "last_name": "Integration", "email": f"azuremonitor@{DOMAIN}",
                                   "title": "Service account - event integration", "active": "true"})
        for gname, size, gdesc in GROUPS:
            members = []
            for i in range(size):
                f, l = names[used]; used += 1
                title = ("Team Lead - " if i == 0 else "Engineer - ") + gname
                members.append(mk_user(f, l, title, "IT Managed Services"))
            self.group_members[gname] = members
        for _ in range(40):
            f, l = names[used]; used += 1
            dept = random.choice(DEPTS)
            self.business_users.append(mk_user(f, l, f"{dept} Associate", dept))

        for gname, size, gdesc in GROUPS:
            gkey = f"group:{gname}"
            self.recs["groups"].append({"_table": "sys_user_group", "_key": gkey, "name": f"SKR {gname}",
                                        "description": f"[{TAG}] {gdesc}", "manager": "@" + self.group_members[gname][0],
                                        "email": f"{gname.lower().replace(' ', '-').replace('&', 'and')}@{DOMAIN}",
                                        "active": "true"})
            for m in self.group_members[gname]:
                self.recs["group_members"].append({"_table": "sys_user_grmember", "_key": f"grm:{gname}:{m}",
                                                   "group": "@" + gkey, "user": "@" + m})

    # ---------------------------------------------------------------- CMDB
    def cmdb(self):
        ip = 10
        for svc, crit, cis in SERVICES:
            skey = f"svc:{svc}"
            self.recs["services"].append({"_table": "cmdb_ci_service", "_key": skey, "name": f"SKR {svc}",
                                          "short_description": f"[{TAG}] Business service: {svc}",
                                          "busines_criticality": crit, "operational_status": "1",
                                          "used_for": "Production"})
            for name, kind, table, desc, sgroup in cis:
                ip += 1
                rec = {"_table": table, "_key": f"ci:{name}", "name": name, "short_description": f"[{TAG}] {desc}",
                       "operational_status": "1", "install_status": "1", "used_for": "Production",
                       "support_group": f"@group:{sgroup}", "assignment_group": f"@group:{sgroup}",
                       "location": None}
                rec.pop("location")
                if table in ("cmdb_ci_win_server", "cmdb_ci_linux_server"):
                    rec["ip_address"] = f"10.20.{ip // 250}.{ip % 250 + 4}"
                    rec["os"] = "Windows 2019 Datacenter" if table == "cmdb_ci_win_server" else "Linux Ubuntu"
                    rec["cpu_count"] = random.choice(["4", "8", "16"])
                    rec["ram"] = random.choice(["16384", "32768"])
                self.recs["cis"].append(rec)
                self.ci[name] = {"kind": kind, "svc": svc, "group": sgroup}
                # relationship: service depends on CI
                self.recs["relationships"].append({"_table": "cmdb_rel_ci", "_key": f"rel:{svc}:{name}",
                                                   "parent": "@" + skey, "child": f"@ci:{name}",
                                                   "type": "@lookup:cmdb_rel_type:name=Depends on::Used by"})
        # a few technical dependencies (useful for impact analysis / blast radius)
        deps = [("app-ecom-api-prod", "sql-ecom-catalog-prod"), ("app-ecom-api-prod", "redis-ecom-cache"),
                ("app-ecom-api-prod", "kv-ecom-prod"), ("vm-ecom-web-01", "app-ecom-api-prod"),
                ("vm-ecom-web-02", "app-ecom-api-prod"), ("func-orders-processor", "sb-orders-queue"),
                ("func-orders-processor", "sql-orders-prod"), ("vm-orders-app-01", "sql-orders-prod"),
                ("app-payments-prod", "kv-payments-prod"), ("app-hrportal-prod", "sql-hr-prod"),
                ("adf-analytics-prod", "st-datalake-prod"), ("adf-analytics-prod", "sql-dwh-prod"),
                ("adf-analytics-prod", "sql-orders-prod"), ("aks-platform-prod", "sql-ecom-catalog-prod")]
        self.deps = deps
        for a, b in deps:
            self.recs["relationships"].append({"_table": "cmdb_rel_ci", "_key": f"rel:{a}:{b}",
                                               "parent": f"@ci:{a}", "child": f"@ci:{b}",
                                               "type": "@lookup:cmdb_rel_type:name=Depends on::Used by"})

    # ---------------------------------------------------------------- KB
    def kb(self):
        n = 0
        for p in P.PATTERNS + P.NOISE:
            k = p.get("kb")
            if not k:
                continue
            n += 1
            key = f"kb:{p['id']}"
            html = (f"<h3>Symptoms</h3><p>{k['symptoms']}</p><h3>Cause</h3><p>{k['cause']}</p>"
                    f"<h3>Resolution</h3><ol>{''.join(f'<li>{s}</li>' for s in k['steps'])}</ol>")
            if p.get("runbook"):
                html += (f"<h3>Automation</h3><p>Runbook: <b>{p['runbook']}</b> "
                         f"({'safe to run automatically with approval' if p.get('automatable') else 'manual / approval required - do NOT auto-run'})</p>")
            if p.get("group"):
                html += f"<h3>Escalation</h3><p>Resolver group: SKR {p['group']}</p>"
            self.recs["kb"].append({"_table": "kb_knowledge", "_key": key, "short_description": k["title"],
                                    "text": html, "workflow_state": "published",
                                    "kb_knowledge_base": "@lookup:kb_knowledge_base:title=IT",
                                    "meta": f"{TAG} azure managed services {p['id'].split('_', 1)[1].lower().replace('_', ' ')}",
                                    "valid_to": "2100-01-01"})
            self.kb_key[p["id"]] = key
        self.recs["kb"].append({"_table": "kb_knowledge", "_key": "kb:CHANGE_POLICY",
                                "short_description": "SKR Change Management Policy", "text": POLICY_HTML,
                                "workflow_state": "published", "kb_knowledge_base": "@lookup:kb_knowledge_base:title=IT",
                                "meta": f"{TAG} change management policy cab freeze maintenance window", "valid_to": "2100-01-01"})

    # ---------------------------------------------------------------- problems
    def problems(self):
        for key, (sd, ci, cause, fix, state) in PROBLEMS.items():
            ci_name = ci or ("vm-ecom-web-01" if key == "PRB_STOREFRONT_LEAK" else "kv-ecom-prod")
            grp = self.ci[ci_name]["group"]
            self.recs["problems"].append({"_table": "problem", "_key": f"prb:{key}", "short_description": sd,
                                          "description": f"[{TAG}] {sd}. Raised from recurring incident trend.",
                                          "cmdb_ci": f"@ci:{ci_name}", "assignment_group": f"@group:{grp}",
                                          "assigned_to": "@" + self.group_members[grp][0],
                                          "known_error": "true", "cause_notes": cause, "fix_notes": fix,
                                          "workaround": "See linked KB article.", "state": str(state),
                                          "impact": "2", "urgency": "2", "correlation_id": TAG,
                                          "opened_at": ts(self.start + timedelta(days=random.randint(10, 40))),
                                          "_retry_drop": ["state"]})

    # ---------------------------------------------------------------- helpers
    def tier(self, ci):
        return M.TIER[self.svc_crit[self.ci[ci]["svc"]]]

    def dependents(self, ci):
        """CIs that depend on ci (they break if ci breaks)."""
        return [a for a, b in self.deps if b == ci]

    def related(self, ci):
        return {b for a, b in self.deps if a == ci} | {a for a, b in self.deps if b == ci}

    def cis_of(self, kinds, tiers=None):
        return [n for n, c in self.ci.items() if c["kind"] in kinds and (tiers is None or self.tier(n) in tiers)]

    def freeze_hits(self, ci, s, e):
        svc = self.ci[ci]["svc"]
        return sorted({f["name"] for f in self.freezes
                       if overlaps(s, e, f["start"], f["end"]) and (f["applies"] is None or svc in f["applies"])})

    def slot(self, tier, where, base, lo, hi):
        """(start, end) in UTC, lo..hi days after base, in the requested window class."""
        for _ in range(400):
            d = (base + timedelta(days=random.uniform(lo, hi)) + IST).date()
            dur = timedelta(hours=random.choice([1, 2, 3]))
            if where == "maint" and tier == 1:
                if d.weekday() not in T1_DAYS:
                    continue
                s = ist_at(d, 1, random.choice([0, 15, 30, 45, 60]))
            elif where == "maint":
                s = ist_at(d, 22, random.choice(range(0, 181, 15)))
            elif where == "business":
                if d.weekday() >= 5:
                    continue
                s = ist_at(d, 10, random.choice(range(0, 301, 30)))
            else:   # off_hours: Saturday daytime
                if d.weekday() != 5:
                    continue
                s = ist_at(d, 10, random.choice(range(0, 241, 30)))
            e = s + dur
            if s >= base + timedelta(days=lo) and window_class(tier, s, e) == where:
                return s, e
        return None

    def fail_target(self, kind, ci):
        """(CI where the failure shows up, incident pattern) for a failed change, or None."""
        for where, kinds, pid in M.KINDS[kind]["fails"]:
            if where == "self" and self.ci[ci]["kind"] in kinds:
                return ci, self.pattern[pid]
            if where == "dependent":
                deps = [d for d in self.dependents(ci) if self.ci[d]["kind"] in kinds]
                if deps:
                    return random.choice(deps), self.pattern[pid]
        return None

    # ---------------------------------------------------------------- schedules
    def schedules(self):
        d0 = (self.now + IST).date()
        self.freezes = []

        def add_freeze(name, start, end, applies):
            self.freezes.append({"name": name, "start": start, "end": end, "applies": applies})

        add_freeze("SKR Black Friday Mega Sale freeze", ist_at(d0 + timedelta(days=6), 0), ist_at(d0 + timedelta(days=11), 0), None)
        add_freeze("SKR Diwali Sale freeze", ist_at(d0 - timedelta(days=15), 0), ist_at(d0 - timedelta(days=11), 0), None)
        add_freeze("SKR Independence Day Sale freeze", ist_at(d0 - timedelta(days=99), 0), ist_at(d0 - timedelta(days=96), 0), None)
        m = date((self.start + IST).year, (self.start + IST).month, 1)
        horizon = (self.now + timedelta(days=45) + IST).date()
        while m <= horizon:
            nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            add_freeze("SKR Month-end finance close", ist_at(nxt - timedelta(days=1), 12), ist_at(nxt + timedelta(days=1), 12),
                       set(MONTH_END_SERVICES))
            m = nxt

        scheds = {}
        for f in self.freezes:
            if f["name"] not in scheds:
                key = f"sch:{f['name']}"
                applies = "all" if f["applies"] is None else ";".join(f"SKR {s}" for s in sorted(f["applies"]))
                scheds[f["name"]] = key
                self.recs["schedules"].append({"_table": "cmn_schedule", "_key": key, "name": f["name"], "type": "blackout",
                                               "time_zone": "Asia/Kolkata",
                                               "description": f"[{TAG}] Change freeze. Applies to: {applies}",
                                               "u_applies_to": applies})
            self.recs["schedule_spans"].append({
                "_table": "cmn_schedule_span", "_key": f"span:{f['name']}:{ts(f['start'])}", "schedule": "@" + scheds[f["name"]],
                "name": f["name"], "start_date_time": ts(f["start"]), "end_date_time": ts(f["end"]), "repeat_type": "",
                "days_of_week": ""})
        # maintenance windows, stored in UTC (IST 01:00-05:00 Tue/Thu/Sun = UTC 19:30-23:30 Mon/Wed/Sat)
        first = self.start.date()
        mon = first - timedelta(days=first.weekday())
        for name, applies, s, e, rep, dow in (
                ("SKR Tier-1 maintenance window", "tier 1", datetime(mon.year, mon.month, mon.day, 19, 30, tzinfo=UTC),
                 datetime(mon.year, mon.month, mon.day, 23, 30, tzinfo=UTC), "weekly", "136"),
                ("SKR Standard maintenance window", "tier 2;tier 3", datetime(mon.year, mon.month, mon.day, 16, 30, tzinfo=UTC),
                 datetime(mon.year, mon.month, mon.day, 16, 30, tzinfo=UTC) + timedelta(hours=8), "daily", "")):
            key = f"sch:{name}"
            self.recs["schedules"].append({"_table": "cmn_schedule", "_key": key, "name": name, "type": "maintenance",
                                           "time_zone": "Asia/Kolkata", "u_applies_to": applies,
                                           "description": f"[{TAG}] " + ("Tue, Thu, Sun 01:00-05:00 IST" if rep == "weekly"
                                                                         else "Daily 22:00-06:00 IST")
                                                          + f". Applies to: {applies}"})
            self.recs["schedule_spans"].append({"_table": "cmn_schedule_span", "_key": f"span:{name}", "schedule": "@" + key,
                                                "name": name, "start_date_time": ts(s), "end_date_time": ts(e),
                                                "repeat_type": rep, "days_of_week": dow})

    # ---------------------------------------------------------------- changes
    def make_change(self, kind, ci, s, e, state, opened, ctype=None, quality=None, hidden=(), phrasing="A",
                    work=None, work_notes="", close=None):
        self.chg_n += 1
        key = f"chg:{self.chg_n:04d}"
        K, T = M.KINDS[kind], M.TEXT[kind]
        ctype = ctype or K["type"]
        quality = quality or {"implementation": "ok", "backout": "ok", "test": "ok"}
        consumers = self.dependents(ci)
        v = M.fill_vars(kind, ci, self.ci[ci]["svc"], consumers)
        desc = M.render(T["desc"], v)
        texts = {"implementation": (M.render(T["impl"], v), M.IMPL_WEAK, M.IMPL_MISSING),
                 "backout": (M.render(M.IRREVERSIBLE_BACKOUT if "irreversible" in hidden else T["backout"], v),
                             M.BACKOUT_WEAK, M.BACKOUT_MISSING),
                 "test": (M.render(T["test"], v), M.TEST_WEAK, M.TEST_MISSING)}
        plan = {p: {"ok": good, "weak": random.choice(weak), "missing": random.choice(miss)}[quality[p]]
                for p, (good, weak, miss) in texts.items()}
        for h in hidden:
            phrase = M.hidden_phrase(h, kind, phrasing)
            to_plan = h in ("canary", "rehearsed") or random.random() < 0.5
            if to_plan and quality["implementation"] == "ok":
                plan["implementation"] += "\n" + phrase
            else:
                desc += " " + phrase
        group = self.ci[ci]["group"] if K["group"] == "ci" else K["group"]
        tier = self.tier(ci)
        if ctype == "standard":
            risk = "4"
        elif ctype == "emergency":
            risk = "2" if tier == 1 else "3"
        else:
            risk = "3" if tier == 1 else "4"
        if random.random() < 0.15:
            risk = random.choice(["2", "3", "4"])
        rec = {"_table": "change_request", "_key": key, "short_description": M.render(T["title"], v)[:160],
               "description": desc, "justification": M.render(random.choice(T["just"]), v),
               "implementation_plan": plan["implementation"], "backout_plan": plan["backout"], "test_plan": plan["test"],
               "type": ctype, "category": K["category"], "cmdb_ci": f"@ci:{ci}",
               "business_service": f"@svc:{self.ci[ci]['svc']}", "assignment_group": f"@group:{group}",
               "assigned_to": "@" + random.choice(self.group_members[group]),
               "requested_by": "@" + random.choice(self.group_members[group]),
               "start_date": ts(s), "end_date": ts(e), "risk": risk, "impact": str(tier), "state": state,
               "correlation_id": TAG, "_post_patch": {"opened_at": ts(opened)},
               "_retry_drop": ["state", "close_code", "close_notes", "work_start", "work_end"]}
        if work:
            rec["work_start"], rec["work_end"] = ts(work[0]), ts(work[1])
        if work_notes:
            rec["work_notes"] = work_notes
        if close:
            rec["close_code"], rec["close_notes"] = close
        self.recs["changes"].append(rec)
        ch = {"key": key, "kind": kind, "ci": ci, "start": s, "end": e, "opened": opened, "type": ctype, "state": state,
              "quality": quality, "hidden": list(hidden), "group": group, "tier": tier, "requester_risk": risk, "rec": rec}
        self.changes.append(ch)
        return ch

    def features(self, ch, conflict=None, open_incident=False):
        s, e = ch["start"], ch["end"]
        return {"kind": ch["kind"], "ci": ch["ci"], "tier": ch["tier"], "dependents": len(self.dependents(ch["ci"])),
                "type": ch["type"], "quality": ch["quality"], "window": window_class(ch["tier"], s, e),
                "freeze": bool(self.freeze_hits(ch["ci"], s, e)), "conflict": conflict,
                "open_problem": ch["ci"] in self.problem_cis, "open_incident": open_incident,
                "short_lead": ch["type"] == "normal" and (s - ch["opened"]) < timedelta(hours=48),
                "hidden": ch["hidden"], "group": ch["group"], "noise": ch.setdefault("noise", random.gauss(0, 0.3))}

    def random_quality(self, p_ok=0.75, p_weak=0.20):
        q = {"implementation": "ok", "backout": "ok", "test": "ok"}
        r = random.random()
        if r > p_ok:
            part = random.choices(["implementation", "backout", "test"], [0.2, 0.45, 0.35])[0]
            q[part] = "weak" if r <= p_ok + p_weak else "missing"
        return q

    def history(self, n):
        kinds = list(M.KINDS)
        weights = [M.KINDS[k]["weight"] for k in kinds]
        span = (self.now - self.start).days
        planned = []
        while n > 0:
            kind = random.choices(kinds, weights)[0]
            ci = random.choice(self.cis_of(M.KINDS[kind]["cis"]))
            tier = self.tier(ci)
            emergency = random.random() < 0.05
            where = random.choices(["maint", "off_hours", "business"], [0.80, 0.12, 0.08])[0]
            sl = self.slot(tier, where, self.start, 2, span - 4)
            if not sl:
                continue
            s, e = sl
            if self.freeze_hits(ci, s, e) and random.random() < 0.8:
                continue                                   # most requesters respect freezes
            hidden = [h for h in M.HIDDEN if M.hidden_fits(h, kind) and random.random() < 0.05]
            opened = s - (timedelta(hours=random.uniform(1, 8)) if emergency else timedelta(days=random.uniform(1, 10)))
            q = self.random_quality(0.70, 0.22) if M.KINDS[kind]["type"] != "standard" else self.random_quality(0.9, 0.08)
            planned.append(self.make_change(kind, ci, s, e, STATE["closed"], opened,
                                            ctype="emergency" if emergency else None, quality=q, hidden=hidden,
                                            phrasing="A", work=(s, e)))
            n -= 1
        # Outcomes in time order, with the same factors as the evaluation changes: clashes with other changes and
        # incidents already open on the CI (including ones raised by earlier failed changes) count here too.
        for ch in sorted(planned, key=lambda c: c["start"]):
            s, e, ci, kind = ch["start"], ch["end"], ch["ci"], ch["kind"]
            rels = {("same_ci" if o["ci"] == ci else "related_ci") for o in planned
                    if o is not ch and overlaps(s, e, o["start"], o["end"]) and (o["ci"] == ci or o["ci"] in self.related(ci))}
            conflict = "same_ci" if "same_ci" in rels else ("related_ci" if rels else None)
            p, _ = M.failure_probability(self.features(ch, conflict=conflict, open_incident=self.incident_open_at(ci, s)))
            failed = random.random() < p
            target = self.fail_target(kind, ci) if failed else None
            if failed and target:
                r = random.random()
                code = "unsuccessful" if r < 0.45 else ("successful_issues" if r < 0.85 else "successful")
                tci, pat = target
                linked = random.random() < 0.85
                for _ in range(1 if random.random() < 0.8 else 2):
                    self.incident(pat, e + timedelta(hours=random.uniform(0.2, 8)), ci=tci,
                                  chg_key=ch["key"] if linked else None)
                notes = {"unsuccessful": "Backout executed after errors during validation. To be re-planned.",
                         "successful_issues": "Implemented. Post-implementation issue reported, see related incident.",
                         "successful": "Implemented successfully."}[code]
            else:
                code, notes = "successful", random.choice(["Implemented successfully, verified by owner.",
                                                           "Completed as planned.", "Implemented successfully."])
            ch["rec"]["close_code"], ch["rec"]["close_notes"] = code, notes

    def incident_open_at(self, ci, t):
        for r in self.recs["incidents"]:
            if r.get("cmdb_ci") != f"@ci:{ci}":
                continue
            o = datetime.strptime(r["_post_patch"]["opened_at"], FMT).replace(tzinfo=UTC)
            res = r["_post_patch"].get("resolved_at")
            if o < t and (res is None or datetime.strptime(res, FMT).replace(tzinfo=UTC) > t):
                return True
        return False

    def scheduled(self, n):
        """Authorised future changes (state Scheduled): the calendar the new requests must fit around."""
        kinds = [k for k in M.KINDS if k != "backup"]
        while n > 0:
            kind = random.choice(kinds)
            ci = random.choice(self.cis_of(M.KINDS[kind]["cis"]))
            sl = self.slot(self.tier(ci), "maint", self.now, 1, 20)
            if not sl or self.freeze_hits(ci, *sl):
                continue
            self.make_change(kind, ci, *sl, STATE["scheduled"], self.now - timedelta(days=random.uniform(3, 10)))
            n -= 1

    # ---------------------------------------------------------------- evaluation: assess
    def assess_set(self, n):
        slots = (["clean"] * 8 + ["tier1"] * 6 + ["missing"] * 5 + ["weak"] * 4 + ["freeze"] * 4 + ["conflict"] * 4
                 + ["business"] * 3 + ["messy_up"] * 4 + ["messy_down"] * 2)
        slots = (slots * (n // len(slots) + 1))[:n]
        random.shuffle(slots)
        emergency_at = {slots.index("clean"), slots.index("business"), slots.index("tier1")}
        scheduled = [c for c in self.changes if c["state"] == STATE["scheduled"]]
        made = []
        for i, kind_of_slot in enumerate(slots):
            for _attempt in range(200):
                ch = self._assess_one(kind_of_slot, i in emergency_at, scheduled)
                if ch:
                    ch["slot"] = kind_of_slot
                    made.append(ch)
                    break
            else:
                raise RuntimeError(f"could not place assess slot {kind_of_slot}")
        return made

    def _assess_one(self, slot, emergency, scheduled):
        kinds = [k for k in M.KINDS if M.KINDS[k]["type"] == "normal"]
        opened = self.now - timedelta(hours=random.uniform(2, 72))
        tiers = {"clean": {2, 3}, "tier1": {1}, "business": {1}, "messy_down": {1}}.get(slot)
        hidden, phrasing, quality = [], "B", None
        if slot == "conflict":
            other = random.choice(scheduled)
            ci = other["ci"] if random.random() < 0.5 else random.choice(sorted(self.related(other["ci"])) or [other["ci"]])
            kinds = [k for k in kinds if self.ci[ci]["kind"] in M.KINDS[k]["cis"]]
            if not kinds:
                return None
            kind = random.choice(kinds)
            s = other["start"] + timedelta(minutes=random.choice([-30, 0, 30]))
            e = s + timedelta(hours=random.choice([1, 2]))
        else:
            cands = self.cis_of({k for kd in kinds for k in M.KINDS[kd]["cis"]}, tiers)
            if not cands:
                return None
            ci = random.choice(cands)
            kind = random.choice([k for k in kinds if self.ci[ci]["kind"] in M.KINDS[k]["cis"]])
            tier = self.tier(ci)
            if emergency:
                where, lo, hi = random.choice(["maint", "business", "off_hours"]), 0.2, 1.5
            elif slot == "freeze":
                where, lo, hi = "maint", 6, 11
            elif slot == "business":
                where, lo, hi = "business", 1, 18
            else:
                where, lo, hi = "maint", 1, 18
            sl = self.slot(tier, where, self.now, lo, hi)
            if not sl:
                return None
            s, e = sl
            if slot == "freeze" and not self.freeze_hits(ci, s, e):
                return None
        if slot != "freeze" and not emergency and self.freeze_hits(ci, s, e):
            return None
        if s < opened + timedelta(hours=2):
            return None
        if slot != "conflict" and any(o["state"] in OPEN_CHANGE_STATES and overlaps(s, e, o["start"], o["end"])
                                      and (o["ci"] == ci or o["ci"] in self.related(ci)) for o in self.changes):
            return None
        if slot == "missing":
            quality = {"implementation": "ok", "backout": "ok", "test": "ok"}
            quality[random.choices(["backout", "test", "implementation"], [0.5, 0.35, 0.15])[0]] = "missing"
        elif slot == "weak":
            quality = {"implementation": "ok", "backout": "ok", "test": "ok"}
            for part in random.sample(["implementation", "backout", "test"], random.choice([1, 2])):
                quality[part] = "weak"
        elif slot == "messy_up":
            pool = [h for h in ("irreversible", "first_time", "vendor", "big_bang") if M.hidden_fits(h, kind)]
            hidden = random.sample(pool, min(len(pool), random.choice([1, 2])))
            if not any(M.HIDDEN[h][0] >= 0.7 for h in hidden):
                return None
        elif slot == "messy_down":
            hidden = [h for h in ("canary", "rehearsed") if M.hidden_fits(h, kind)]
            if not hidden:
                return None
        ch = self.make_change(kind, ci, s, e, STATE["assess"], opened, ctype="emergency" if emergency else None,
                              quality=quality, hidden=hidden, phrasing=phrasing)
        if slot.startswith("messy"):
            ch["rec"]["risk"] = "4" if self.tier(ci) > 1 else "3"     # the requester sees nothing unusual
            # the free text must matter: its hidden factors move the change into a different risk band
            f = self.features(ch, open_incident=ci in self.open_incident_cis())
            with_text = M.band(M.failure_probability(f)[0])
            without = M.band(M.failure_probability(dict(f, hidden=[]))[0])
            if with_text == without or (slot == "messy_up" and with_text != "high"):
                self.changes.remove(ch)
                self.recs["changes"].remove(ch["rec"])
                self.chg_n -= 1
                return None
        return ch

    def open_incident_cis(self):
        return {r["cmdb_ci"].lstrip("@").split(":", 1)[1] for r in self.recs["incidents"]
                if r.get("state") in ("1", "2") and r.get("cmdb_ci")}

    def assess_truth(self, made):
        open_inc_cis = self.open_incident_cis()
        live = [c for c in self.changes if c["state"] in OPEN_CHANGE_STATES]
        for ch in made:
            conflicts = []
            for o in live:
                if o is ch or not overlaps(ch["start"], ch["end"], o["start"], o["end"]):
                    continue
                if o["ci"] == ch["ci"]:
                    conflicts.append({"key": o["key"], "relation": "same_ci"})
                elif o["ci"] in self.related(ch["ci"]):
                    conflicts.append({"key": o["key"], "relation": "related_ci"})
            rel = ("same_ci" if any(c["relation"] == "same_ci" for c in conflicts)
                   else "related_ci" if conflicts else None)
            f = self.features(ch, conflict=rel, open_incident=ch["ci"] in open_inc_cis)
            p, contrib = M.failure_probability(f)
            b = M.band(p)
            freeze = self.freeze_hits(ch["ci"], ch["start"], ch["end"])
            self.truth["assess"].append({
                "key": ch["key"], "slot": ch["slot"], "short_description": ch["rec"]["short_description"], "ci": ch["ci"],
                "service": self.ci[ch["ci"]]["svc"], "tier": ch["tier"], "kind": ch["kind"], "type": ch["type"],
                "requester_risk": {"2": "high", "3": "moderate", "4": "low"}[ch["rec"]["risk"]],
                "failure_probability": round(p, 3), "risk_band": b, "risk_factors": contrib,
                "recommendation": M.recommendation(b, ch["quality"], bool(freeze), ch["type"]),
                "quality": ch["quality"], "conflicts": conflicts, "freeze": freeze, "window": f["window"],
                "open_problem": f["open_problem"], "open_incident": f["open_incident"], "short_lead": f["short_lead"],
                "hidden_factors": ch["hidden"], "messy": ch["slot"].startswith("messy")})

    # ---------------------------------------------------------------- evaluation: verify
    def verify_set(self, n, n_unsuccessful=3, n_issues=5):
        outcomes = ["unsuccessful"] * n_unsuccessful + ["successful_issues"] * n_issues
        outcomes += ["successful"] * (n - len(outcomes))
        random.shuffle(outcomes)
        for outcome in outcomes:
            while True:
                kind = random.choice(list(M.KINDS))
                ci = random.choice(self.cis_of(M.KINDS[kind]["cis"]))
                target = self.fail_target(kind, ci)
                where = random.choices(["maint", "business", "off_hours"], [0.8, 0.1, 0.1])[0]
                sl = self.slot(self.tier(ci), where, self.now - timedelta(days=3), 0, 2.6)
                if target and sl and sl[1] < self.now - timedelta(hours=6) and not self.freeze_hits(ci, *sl):
                    break
            s, e = sl
            caused, distractors = [], []
            tci, pat = target
            if outcome == "unsuccessful":
                t = s + timedelta(minutes=random.randint(20, 50))
                note = (f"[{(t + IST).strftime('%d-%b %H:%M')} IST] Validation failed, errors on {tci}. "
                        "Backout executed. Change to be re-planned.")
                caused.append(self.incident(pat, t + timedelta(minutes=random.randint(0, 40)), ci=tci, mode="open"))
            else:
                note = random.choice(["Completed, smoke tests passed.", "Implemented as planned.", ""])
                if outcome == "successful_issues":
                    if random.random() < 0.4:
                        note = "Completed. Some errors seen afterwards; monitoring."
                    for _ in range(1 if random.random() < 0.7 else 2):
                        t = e + timedelta(hours=random.uniform(0.2, 6))
                        caused.append(self.incident(pat, min(t, self.now - timedelta(minutes=30)), ci=tci, mode="open"))
            if random.random() < (0.5 if outcome == "successful" else 0.25):
                d = self.distractor(ci, s, e, pat)
                if d:
                    distractors.append(d)
            ch = self.make_change(kind, ci, s, e, STATE["review"], s - timedelta(days=random.uniform(2, 8)),
                                  quality=self.random_quality(0.85, 0.12), work=(s, e), work_notes=note)
            self.truth["verify"].append({"key": ch["key"], "short_description": ch["rec"]["short_description"], "ci": ci,
                                         "kind": kind, "outcome": outcome, "caused_incidents": caused,
                                         "distractor_incidents": distractors})

    def distractor(self, ci, s, e, fail_pat):
        kind_pats = lambda c: [p for p in P.PATTERNS if self.ci[c]["kind"] in p["kinds"] and not p.get("change_caused")
                               and p["id"] != fail_pat["id"]]
        options = []
        if kind_pats(ci):
            options += [("before", ci, s - timedelta(hours=random.uniform(3, 20))),
                        ("unrelated_symptom", ci, e + timedelta(hours=random.uniform(1, 6)))]
        if e + timedelta(hours=30) < self.now:
            near = [c for c in [ci, *self.related(ci)] if kind_pats(c)]
            if near:
                options.append(("late", random.choice(near), e + timedelta(hours=random.uniform(26, 34))))
        far = [c for c in self.ci if c != ci and c not in self.related(ci) and kind_pats(c)]
        options.append(("other_ci", random.choice(far), e + timedelta(hours=random.uniform(0.5, 3))))
        why, dci, t = random.choice(options)
        t = min(t, self.now - timedelta(minutes=30))
        return {"key": self.incident(random.choice(kind_pats(dci)), t, ci=dci, mode="open"), "why": why}

    # ---------------------------------------------------------------- incidents
    def render(self, s, v):
        return M.render(s, v)

    def incident(self, pat, opened, ci=None, chg_key=None, mode="resolved"):
        """mode 'resolved': history with work notes; 'open': just raised, In Progress, no notes or links."""
        self.inc_n += 1
        key = f"inc:{self.inc_n:04d}"
        ci = ci or random.choice(self.cis_of(pat["kinds"]))
        meta = self.ci[ci]
        v = pat["vars"]()
        v.update({"ci": ci, "svc": meta["svc"]})
        is_alert = random.random() < pat["alert_ratio"]
        impact, urgency = random.randint(*pat["impact"]), random.randint(*pat["urgency"])
        caller = "user:synth.svc.azuremonitor" if is_alert else random.choice(self.business_users)
        short = self.render(pat["alert"] if is_alert else pat["user"], v)
        desc = self.render(pat["desc"] if is_alert else pat["user_desc"], v)
        if is_alert:
            desc = "Auto-generated by Azure Monitor action group.\n" + desc
        grp = pat["group"]
        rec = {"_table": "incident", "_key": key, "short_description": short[:160], "description": desc,
               "caller_id": "@" + caller, "category": pat["category"], "impact": str(impact), "urgency": str(urgency),
               "cmdb_ci": f"@ci:{ci}", "business_service": f"@svc:{meta['svc']}", "correlation_id": TAG,
               "assignment_group": f"@group:{grp}", "assigned_to": "@" + random.choice(self.group_members[grp])}
        if not is_alert:
            rec["contact_type"] = random.choice(["phone", "email", "self-service"])
        if mode == "open":
            rec["state"] = "2"
            rec["_post_patch"] = {"opened_at": ts(opened)}
            resolved = opened + timedelta(hours=random.uniform(1, 10))
            if resolved < self.now - timedelta(hours=1) and random.random() < 0.7:
                rec.update({"state": "6", "close_code": "Solution provided",
                            "close_notes": self.render(pat["resolution"], v)})
                rec["_post_patch"]["resolved_at"] = ts(resolved)
            self.recs["incidents"].append(rec)
            return key
        cause = self.render(random.choice(pat["causes"]), v)
        pri = priority_of(impact, urgency)
        lo, hi = RESOLVE_HOURS[pri]
        resolved = min(opened + timedelta(hours=random.uniform(lo, hi)), self.now - timedelta(hours=1))
        closed = resolved + timedelta(days=3)
        notes, t = [], opened
        for line in pat["notes"]:
            t += timedelta(minutes=random.randint(5, 50))
            notes.append(f"[{(t + IST).strftime('%d-%b %H:%M')} IST] {self.render(line, v)}")
        notes.append(f"Root cause: {cause}")
        if chg_key:
            notes.append(f"Caused by recent change on {ci} (see Caused by Change field).")
            rec["caused_by"] = "@" + chg_key
        prb = pat.get("problem")
        if prb and PROBLEMS[prb][1] in (None, ci):
            rec["problem_id"] = f"@prb:{prb}"
        rec.update({"work_notes": "\n".join(notes),
                    "close_code": {"workaround": "Workaround provided", "change": "Resolved by change"}.get(
                        pat.get("close_code"), "Solution provided"),
                    "close_notes": self.render(pat["resolution"], v) + f" Root cause: {cause}.",
                    "state": "7" if closed < self.now else "6"})
        post = {"opened_at": ts(opened), "resolved_at": ts(resolved)}
        if closed < self.now:
            post["closed_at"] = ts(closed)
        rec["_post_patch"] = post
        self.recs["incidents"].append(rec)
        return key

    def noise_incident(self, n, opened):
        self.inc_n += 1
        resolved = min(opened + timedelta(hours=random.uniform(0.5, 8)), self.now - timedelta(hours=1))
        closed = resolved + timedelta(days=3)
        rec = {"_table": "incident", "_key": f"inc:{self.inc_n:04d}", "short_description": n["short"],
               "description": n["desc"], "caller_id": "@" + random.choice(self.business_users), "category": n["category"],
               "impact": "3", "urgency": "3", "contact_type": random.choice(["phone", "email", "self-service"]),
               "correlation_id": TAG, "assignment_group": "@group:Service Desk L1",
               "assigned_to": "@" + random.choice(self.group_members["Service Desk L1"]),
               "work_notes": f"Contacted caller. {n['resolution']}", "close_code": "Solution provided",
               "close_notes": n["resolution"], "state": "7" if closed < self.now else "6"}
        post = {"opened_at": ts(opened), "resolved_at": ts(resolved)}
        if closed < self.now:
            post["closed_at"] = ts(closed)
        rec["_post_patch"] = post
        self.recs["incidents"].append(rec)

    def background_incidents(self, n):
        """Incidents not caused by changes (change-caused ones come from failed changes)."""
        pats = [p for p in P.PATTERNS if not p.get("change_caused")]
        weights = [p["weight"] for p in pats]
        for _ in range(n):
            t = business_time(self.start, self.now - timedelta(days=2))
            if random.random() < 0.12:
                self.noise_incident(random.choice(P.NOISE), t)
            else:
                self.incident(random.choices(pats, weights)[0], t)

    def active_incidents(self, n):
        """A few incidents still being worked: an open issue on a CI is a reason to hold its changes."""
        cands = [c for c in self.ci if self.tier(c) <= 2]
        for ci in random.sample(cands, n):
            pats = [p for p in P.PATTERNS if self.ci[ci]["kind"] in p["kinds"] and not p.get("change_caused")]
            if pats:
                self.incident(random.choice(pats), self.now - timedelta(hours=random.uniform(2, 40)), ci=ci, mode="open")

    # ---------------------------------------------------------------- output
    ORDER = ["users", "groups", "group_members", "services", "cis", "relationships", "kb", "schedules",
             "schedule_spans", "problems", "changes", "incidents"]

    def write(self, out):
        out.mkdir(parents=True, exist_ok=True)
        (out / "csv").mkdir(exist_ok=True)
        self.recs["incidents"].sort(key=lambda r: r["_post_patch"]["opened_at"])
        plan = {"generated_at": ts(self.now), "tag": TAG, "company": COMPANY,
                "stages": [{"stage": s, "records": self.recs[s]} for s in self.ORDER]}
        (out / "load_plan.json").write_text(json.dumps(plan, indent=1))
        truth = {"generated_at": ts(self.now), "bands": {b: f"p < {hi}" for b, hi in M.BANDS}, **self.truth}
        (out / "ground_truth.json").write_text(json.dumps(truth, indent=1))
        for s in self.ORDER:
            rows = self.recs[s]
            if s == "cis":   # the CI class is the record's table
                rows = [dict(r, sys_class_name=r["_table"]) for r in rows]
            cols = []
            for r in rows:
                for k in list(r) + list(r.get("_post_patch", {})):
                    if (not k.startswith("_") or k == "_key") and k not in cols:
                        cols.append(k)
            with open(out / "csv" / f"{s}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    row = {k: (v.replace("@lookup:", "").lstrip("@") if isinstance(v, str) else v)
                           for k, v in r.items() if k in cols}
                    row.update(r.get("_post_patch", {}))
                    w.writerow(row)
        hist = [c for c in self.changes if c["state"] == STATE["closed"]]
        failed = sum(1 for c in hist if c["rec"]["close_code"] != "successful" or
                     any(i.get("caused_by") == "@" + c["key"] for i in self.recs["incidents"]))
        a, v = self.truth["assess"], self.truth["verify"]
        lines = [f"{COMPANY} synthetic change-management dataset  (tag {TAG})",
                 f"Window: {ts(self.start)} -> {ts(self.now)} UTC", ""]
        lines += [f"{s:15s} {len(self.recs[s]):5d}" for s in self.ORDER]
        lines += ["", f"Change history: {len(hist)} closed, {failed} with a recorded failure or caused incident",
                  f"Scheduled (authorised, future): {sum(1 for c in self.changes if c['state'] == STATE['scheduled'])}",
                  f"Eval assess: {len(a)}  bands {dict(Counter(t['risk_band'] for t in a))}",
                  f"             recommendations {dict(Counter(t['recommendation'] for t in a))}",
                  f"             messy {sum(t['messy'] for t in a)}, with conflicts {sum(bool(t['conflicts']) for t in a)}, "
                  f"in freeze {sum(bool(t['freeze']) for t in a)}",
                  f"Eval verify: {len(v)}  outcomes {dict(Counter(t['outcome'] for t in v))}, "
                  f"distractor incidents {sum(len(t['distractor_incidents']) for t in v)}"]
        (out / "summary.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=180, help="history window in days")
    ap.add_argument("--history", type=int, default=260, help="closed changes in the history")
    ap.add_argument("--incidents", type=int, default=240, help="background incidents (not change-caused)")
    ap.add_argument("--assess", type=int, default=40, help="open changes to assess (eval)")
    ap.add_argument("--verify", type=int, default=20, help="implemented changes to verify (eval)")
    ap.add_argument("--seed", type=int, default=7)   # chosen for a balanced eval mix before any agent existed
    ap.add_argument("--now", default=DEFAULT_NOW, help="anchor time, UTC 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    g = Gen(a.days, a.seed, datetime.strptime(a.now, FMT).replace(tzinfo=UTC))
    g.people(); g.cmdb(); g.kb(); g.problems(); g.schedules()
    g.background_incidents(a.incidents)
    g.history(a.history)
    g.scheduled(16)
    g.active_incidents(4)
    g.verify_set(a.verify)
    made = g.assess_set(a.assess)
    g.assess_truth(made)
    g.write(Path(a.out))


if __name__ == "__main__":
    main()
