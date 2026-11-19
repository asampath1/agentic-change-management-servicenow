#!/usr/bin/env python3
"""
Generate a synthetic Azure Managed Services ITSM dataset for a ServiceNow PDI.

Fictional company: ShopKart Retails (all names, emails and IPs are fake).

Output (in ./data):
  load_plan.json        ordered records for load_to_snow.py (references as "@key")
  csv/*.csv             the same data as CSV per table, for reading / Excel
  ground_truth.json     answer key for the OPEN incidents (agent evaluation set)
  summary.txt           counts

Usage:
  python3 generate_data.py                 # defaults: 180 days, ~320 incidents
  python3 generate_data.py --days 90 --incidents 150 --eval 20 --seed 7
"""
import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import patterns as P

COMPANY = "ShopKart Retails"
DOMAIN = "shopkart-demo.example"
TAG = "SYNTH-SKR"            # stamped on incidents/changes/problems (correlation_id) for easy filtering
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

CHANGE_TEMPLATES = {
    "patch": ("Monthly Windows patching - {ci}", "Apply monthly cumulative updates via Azure Update Manager to {ci}.", "standard"),
    "nsg": ("NSG rule update for {ci} subnet", "Add inbound deny rules per security review SR-{n} for subnet hosting {ci}.", "normal"),
    "secret": ("Rotate secrets in Key Vault used by {ci}", "Quarterly rotation of secrets and access policy clean-up for {ci}.", "normal"),
    "release": ("Deploy release to {ci}", "Deploy application release via pipeline to {ci} (blue/green slot).", "normal"),
    "sqlmaint": ("Index maintenance on {ci}", "Weekly index rebuild / statistics update on {ci}.", "standard"),
    "aksupgrade": ("AKS node pool upgrade on {ci}", "Upgrade node image on {ci}.", "normal"),
}


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


class Gen:
    def __init__(self, days, n_inc, n_eval, seed):
        random.seed(seed)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start = self.now - timedelta(days=days)
        self.n_inc, self.n_eval = n_inc, n_eval
        self.recs = defaultdict(list)          # table-group -> list of records
        self.group_members = defaultdict(list)
        self.business_users = []
        self.ci = {}                           # ci name -> dict
        self.kb_key = {}                       # pattern id -> kb key
        self.chg_n = 0
        self.inc_n = 0
        self.truth = []

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

    # ---------------------------------------------------------------- changes
    def change(self, ctype, ci, when, caused_issue=False, closed=True):
        self.chg_n += 1
        title, desc, chtype = CHANGE_TEMPLATES[ctype]
        key = f"chg:{self.chg_n:04d}"
        start = when
        end = when + timedelta(hours=random.choice([1, 2, 3]))
        grp = self.ci[ci]["group"] if ctype not in ("patch",) else "Azure Infra Ops"
        if ctype == "nsg":
            grp = "Network & Security Ops"
        if ctype == "secret":
            grp = "Identity & Access Mgmt"
        rec = {"_table": "change_request", "_key": key,
               "short_description": title.format(ci=ci),
               "description": desc.format(ci=ci, n=random.randint(100, 999)),
               "type": chtype, "cmdb_ci": f"@ci:{ci}", "assignment_group": f"@group:{grp}",
               "assigned_to": "@" + random.choice(self.group_members[grp]),
               "start_date": ts(start), "end_date": ts(end),
               "work_start": ts(start), "work_end": ts(end),
               "risk": "3" if chtype == "normal" else "4", "impact": "2",
               "correlation_id": TAG,
               "_retry_drop": ["state", "close_code", "close_notes", "work_start", "work_end"]}
        if closed:
            rec["state"] = "3"  # Closed
            rec["close_code"] = "successful_issues" if caused_issue else "successful"
            rec["close_notes"] = ("Implemented. Post-implementation issue reported - see related incident."
                                  if caused_issue else "Implemented successfully, verified by owner.")
        else:
            rec["state"] = "-1"  # Implement
        self.recs["changes"].append(rec)
        return key

    def background_changes(self):
        months = max(1, (self.now - self.start).days // 30)
        win = [n for n, c in self.ci.items() if c["kind"] in ("winvm", "iisvm", "aadconnect")]
        for m in range(months):
            base = self.start + timedelta(days=30 * m + 12)
            for ci in random.sample(win, k=min(4, len(win))):
                self.change("patch", ci, base.replace(hour=18, minute=30) + timedelta(hours=random.randint(0, 6)))
        others = [("release", "appsvc"), ("release", "func"), ("sqlmaint", "sql"), ("aksupgrade", "aks"), ("secret", "kv")]
        for _ in range(18):
            ctype, kind = random.choice(others)
            cands = [n for n, c in self.ci.items() if c["kind"] == kind]
            self.change(ctype, random.choice(cands), business_time(self.start, self.now - timedelta(days=3)))

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

    # ---------------------------------------------------------------- incidents
    def pick_ci(self, pat):
        cands = [n for n, c in self.ci.items() if c["kind"] in pat["kinds"]]
        weights = []
        prb = pat.get("problem")
        hot = PROBLEMS[prb][1] if prb else None
        for n in cands:
            weights.append(5 if n == hot else 1)
        return random.choices(cands, weights=weights)[0]

    def render(self, s, v):
        try:
            return s.format(**v)
        except (KeyError, IndexError):
            return s

    def incident(self, pat, opened, eval_mode=False):
        self.inc_n += 1
        key = f"inc:{self.inc_n:04d}"
        ci = self.pick_ci(pat)
        meta = self.ci[ci]
        v = pat["vars"]()
        v.update({"ci": ci, "svc": meta["svc"]})
        is_alert = random.random() < pat["alert_ratio"]
        impact = random.randint(*pat["impact"])
        urgency = random.randint(*pat["urgency"])
        caller = "user:synth.svc.azuremonitor" if is_alert else random.choice(self.business_users)
        short = self.render(pat["alert"] if is_alert else pat["user"], v)
        desc = self.render(pat["desc"] if is_alert else pat["user_desc"], v)
        if is_alert:
            desc = "Auto-generated by Azure Monitor action group.\n" + desc

        chg_key = None
        if pat.get("change_caused"):
            chg_key = self.change(pat["change_caused"], ci, opened - timedelta(hours=random.uniform(0.5, 6)),
                                  caused_issue=True, closed=not eval_mode)

        rec = {"_table": "incident", "_key": key, "short_description": short[:160], "description": desc,
               "caller_id": "@" + caller, "category": pat["category"], "impact": str(impact), "urgency": str(urgency),
               "cmdb_ci": f"@ci:{ci}", "business_service": f"@svc:{meta['svc']}", "correlation_id": TAG,
               "contact_type": "" if is_alert else random.choice(["phone", "email", "self-service"])}
        if not rec["contact_type"]:
            rec.pop("contact_type")

        cause = self.render(random.choice(pat["causes"]), v)
        prb = pat.get("problem")
        link_problem = prb and (PROBLEMS[prb][1] in (None, ci))

        if eval_mode:
            rec["state"] = "1"
            rec["_post_patch"] = {"opened_at": ts(opened)}
            self.truth.append({"key": key, "short_description": rec["short_description"], "ci": ci,
                               "pattern": pat["id"], "correct_group": f"SKR {pat['group']}",
                               "root_cause": cause, "expected_resolution": self.render(pat["resolution"], v),
                               "runbook": pat["runbook"], "safe_to_automate": pat["automatable"],
                               "kb_article": pat["kb"]["title"],
                               "related_change": chg_key, "related_problem": f"prb:{prb}" if link_problem else None,
                               "priority": priority_of(impact, urgency)})
        else:
            grp = pat["group"]
            pri = priority_of(impact, urgency)
            lo, hi = RESOLVE_HOURS[pri]
            resolved = opened + timedelta(hours=random.uniform(lo, hi))
            if resolved > self.now - timedelta(hours=1):
                resolved = self.now - timedelta(hours=1)
            closed = resolved + timedelta(days=3)
            notes, t = [], opened
            for line in pat["notes"]:
                t += timedelta(minutes=random.randint(5, 50))
                notes.append(f"[{(t + timedelta(hours=5, minutes=30)).strftime('%d-%b %H:%M')} IST] {self.render(line, v)}")
            notes.append(f"Root cause: {cause}")
            if pat.get("runbook"):
                notes.append(f"Runbook used: {pat['runbook']}" if pat["automatable"] else f"Manual action per {pat['runbook']} (approval obtained)")
            if chg_key:
                notes.append(f"Caused by recent change on {ci} (see Caused by Change field).")
            rec.update({"assignment_group": f"@group:{grp}",
                        "assigned_to": "@" + random.choice(self.group_members[grp]),
                        "work_notes": "\n".join(notes),
                        "close_code": {"workaround": "Workaround provided", "change": "Resolved by change"}.get(
                            pat.get("close_code"), "Solution provided"),
                        "close_notes": self.render(pat["resolution"], v) + f" Root cause: {cause}.",
                        "state": "7" if closed < self.now else "6"})
            post = {"opened_at": ts(opened), "resolved_at": ts(resolved)}
            if closed < self.now:
                post["closed_at"] = ts(closed)
            rec["_post_patch"] = post
        # Eval incidents are "just opened": the links are what the agents must find, so they live
        # only in ground_truth.json. Writing them here would hand the answer to anything reading the PDI.
        if chg_key and not eval_mode:
            rec["caused_by"] = "@" + chg_key
        if link_problem and not eval_mode:
            rec["problem_id"] = f"@prb:{prb}"
        self.recs["incidents"].append(rec)

    def noise_incident(self, n, opened, eval_mode=False):
        self.inc_n += 1
        key = f"inc:{self.inc_n:04d}"
        caller = random.choice(self.business_users)
        rec = {"_table": "incident", "_key": key, "short_description": n["short"], "description": n["desc"],
               "caller_id": "@" + caller, "category": n["category"], "impact": "3", "urgency": "3",
               "contact_type": random.choice(["phone", "email", "self-service"]), "correlation_id": TAG}
        if eval_mode:
            rec["state"] = "1"
            rec["_post_patch"] = {"opened_at": ts(opened)}
            self.truth.append({"key": key, "short_description": n["short"], "ci": None, "pattern": n["id"],
                               "correct_group": "SKR Service Desk L1", "root_cause": "End-user issue",
                               "expected_resolution": n["resolution"], "runbook": None, "safe_to_automate": False,
                               "kb_article": n["kb"]["title"] if n["kb"] else None,
                               "related_change": None, "related_problem": None, "priority": 5})
        else:
            resolved = min(opened + timedelta(hours=random.uniform(0.5, 8)), self.now - timedelta(hours=1))
            closed = resolved + timedelta(days=3)
            rec.update({"assignment_group": "@group:Service Desk L1",
                        "assigned_to": "@" + random.choice(self.group_members["Service Desk L1"]),
                        "work_notes": f"Contacted caller. {n['resolution']}",
                        "close_code": "Solution provided", "close_notes": n["resolution"],
                        "state": "7" if closed < self.now else "6"})
            post = {"opened_at": ts(opened), "resolved_at": ts(resolved)}
            if closed < self.now:
                post["closed_at"] = ts(closed)
            rec["_post_patch"] = post
        self.recs["incidents"].append(rec)

    def incidents(self):
        weights = [p["weight"] for p in P.PATTERNS]
        hist = self.n_inc - self.n_eval
        n_noise = int(hist * 0.12)
        times = sorted(business_time(self.start, self.now - timedelta(days=2)) for _ in range(hist))
        noise_idx = set(random.sample(range(hist), n_noise))
        for i, t in enumerate(times):
            if i in noise_idx:
                self.noise_incident(random.choice(P.NOISE), t)
            else:
                self.incident(random.choices(P.PATTERNS, weights=weights)[0], t)
        # evaluation set: open, unassigned, last 36 hours; cover every pattern at least once where possible
        pats = list(P.PATTERNS)
        random.shuffle(pats)
        n_eval_noise = max(2, self.n_eval // 6)
        for i in range(self.n_eval - n_eval_noise):
            pat = pats[i % len(pats)]
            self.incident(pat, self.now - timedelta(hours=random.uniform(1, 36)), eval_mode=True)
        for i in range(n_eval_noise):
            self.noise_incident(P.NOISE[i % len(P.NOISE)], self.now - timedelta(hours=random.uniform(1, 36)), eval_mode=True)

    # ---------------------------------------------------------------- output
    ORDER = ["users", "groups", "group_members", "services", "cis", "relationships", "kb",
             "changes", "problems", "incidents"]

    def write(self, out):
        out.mkdir(parents=True, exist_ok=True)
        (out / "csv").mkdir(exist_ok=True)
        # changes must exist before incidents reference them; they are created during incidents(), fine since
        # the ORDER puts all changes before incidents.
        plan = {"generated_at": ts(self.now), "tag": TAG, "company": COMPANY,
                "stages": [{"stage": s, "records": self.recs[s]} for s in self.ORDER]}
        (out / "load_plan.json").write_text(json.dumps(plan, indent=1))
        (out / "ground_truth.json").write_text(json.dumps(self.truth, indent=1))
        for s in self.ORDER:
            rows = self.recs[s]
            if s == "cis":   # the CI class is the record's table; agents use it to check runbook targets
                rows = [dict(r, sys_class_name=r["_table"]) for r in rows]
            cols = []
            for r in rows:
                for k in r:
                    if not k.startswith("_") or k == "_key":
                        if k not in cols:
                            cols.append(k)
                for k in r.get("_post_patch", {}):
                    if k not in cols:
                        cols.append(k)
            with open(out / "csv" / f"{s}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    row = {k: (v.replace("@lookup:", "").lstrip("@") if isinstance(v, str) else v)
                           for k, v in r.items() if k in cols}
                    row.update(r.get("_post_patch", {}))
                    w.writerow(row)
        c = Counter()
        for inc in self.recs["incidents"]:
            c["open (eval set)" if inc.get("state") == "1" else "resolved/closed"] += 1
        lines = [f"{COMPANY} synthetic ITSM dataset  (tag {TAG})", f"Window: {ts(self.start)} -> {ts(self.now)} UTC", ""]
        lines += [f"{s:15s} {len(self.recs[s]):5d}" for s in self.ORDER]
        lines += ["", "Incidents: " + ", ".join(f"{k}={v}" for k, v in c.items()),
                  f"Change-caused incidents (history only; eval links are in ground_truth.json): {sum(1 for i in self.recs['incidents'] if 'caused_by' in i)}",
                  f"Incidents linked to problems (history only): {sum(1 for i in self.recs['incidents'] if 'problem_id' in i)}"]
        (out / "summary.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=180, help="history window in days")
    ap.add_argument("--incidents", type=int, default=320, help="total incidents incl. eval set")
    ap.add_argument("--eval", type=int, default=24, help="open incidents kept as an evaluation set")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    g = Gen(a.days, a.incidents, a.eval, a.seed)
    g.people(); g.cmdb(); g.kb(); g.problems(); g.background_changes(); g.incidents()
    g.write(Path(a.out))


if __name__ == "__main__":
    main()
