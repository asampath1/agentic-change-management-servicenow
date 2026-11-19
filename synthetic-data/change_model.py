"""Change kinds, text templates and the HIDDEN outcome model for the change-management dataset.

AGENTS MUST NOT READ OR IMPORT THIS FILE. The weights below decide how likely each change is to fail. The agents
have to infer risk from what the generator writes into the data (CMDB, change history and its incidents, plans,
schedules, open problems), as a change manager would. tests/test_answer_key_isolation.py enforces this.

Free-text risk clues ("hidden factors") come in two phrasings: set A is used in the change history, set B only in
the evaluation changes. A model that just memorises history keywords will miss set B; one that understands the text
should not.
"""
import math
import random

# ----------------------------------------------------------------------------- change kinds
# cis: CI kinds the change applies to; group: "ci" = the CI's support group; base: base failure log-odds;
# weight: share of history; fails: where the failure shows up -> (self|dependent, CI kinds, incident pattern prefix)
KINDS = {
    "patch": dict(cis=("winvm", "iisvm", "aadconnect"), type="standard", group="Azure Infra Ops", category="System Software",
                  base=-3.9, weight=50, fails=[("self", ("winvm", "iisvm"), "P09"), ("self", ("aadconnect",), "P12")]),
    "release": dict(cis=("appsvc", "func", "linuxvm", "aks"), type="normal", group="ci", category="Applications Software",
                    base=-3.3, weight=60, fails=[("self", ("appsvc",), "P04"), ("self", ("func",), "P15"),
                                                  ("self", ("linuxvm",), "P01"), ("self", ("aks",), "P14")]),
    "config": dict(cis=("appsvc", "func", "adf", "redis", "sb"), type="normal", group="ci", category="Applications Software",
                   base=-3.5, weight=35, fails=[("self", ("appsvc",), "P04"), ("self", ("func", "sb"), "P15"),
                                                 ("self", ("adf",), "P18"), ("dependent", ("appsvc",), "P04")]),
    "nsg": dict(cis=("linuxvm", "winvm", "appsvc", "iisvm"), type="normal", group="Network & Security Ops", category="Network",
                base=-3.4, weight=15, fails=[("self", ("linuxvm", "winvm", "appsvc", "iisvm"), "P10")]),
    "fwrule": dict(cis=("firewall", "vpngw"), type="normal", group="Network & Security Ops", category="Network",
                   base=-3.5, weight=10, fails=[("self", ("firewall", "vpngw"), "P16")]),
    "secret": dict(cis=("kv",), type="normal", group="Identity & Access Mgmt", category="Service",
                   base=-3.3, weight=15, fails=[("dependent", ("appsvc", "func"), "P11")]),
    "cert": dict(cis=("kv", "appsvc"), type="normal", group="Network & Security Ops", category="Service",
                 base=-3.7, weight=10, fails=[("self", ("appsvc",), "P04"), ("dependent", ("appsvc",), "P04")]),
    "sqlmaint": dict(cis=("sql",), type="standard", group="Azure Database Ops", category="Database",
                     base=-3.8, weight=30, fails=[("self", ("sql",), "P06")]),
    "schema": dict(cis=("sql",), type="normal", group="Azure Database Ops", category="Database",
                   base=-3.1, weight=12, fails=[("dependent", ("appsvc",), "P04"), ("dependent", ("func",), "P15"),
                                                 ("self", ("sql",), "P06")]),
    "aksupgrade": dict(cis=("aks",), type="normal", group="Cloud Platform Engineering", category="System Software",
                       base=-3.2, weight=8, fails=[("self", ("aks",), "P14")]),
    "scale": dict(cis=("winvm", "linuxvm", "sql"), type="standard", group="ci", category="Hardware",
                  base=-4.1, weight=10, fails=[("self", ("sql",), "P05"), ("self", ("winvm", "linuxvm"), "P01")]),
    "backup": dict(cis=("backup",), type="standard", group="Backup & DR", category="Service",
                   base=-4.0, weight=5, fails=[("self", ("backup",), "P08")]),
}

# Hidden track record per resolver group and per CI (visible only through history outcomes).
GROUP_SKILL = {"App Support - eCommerce": 0.45, "App Support - Enterprise Apps": 0.15, "Azure Database Ops": 0.2,
               "Identity & Access Mgmt": 0.3, "Network & Security Ops": 0.0, "Azure Infra Ops": -0.2,
               "Cloud Platform Engineering": -0.3, "Data Platform Ops": 0.25, "Backup & DR": -0.2, "Service Desk L1": 0.0}
CI_FRAGILITY = {"vm-aadconnect-01": 0.8, "sql-orders-prod": 0.4, "app-payments-prod": 0.3, "aks-platform-prod": 0.2}

# ----------------------------------------------------------------------------- hidden text factors
# name -> (log-odds effect, kinds it fits (None = any), phrasing A (history), phrasing B (eval only))
HIDDEN = {
    # phrasing can be per kind: {kind: [phrases]}
    "irreversible": (1.3, ("schema", "secret", "release"),
                     {"schema": ["Note: this migration is one-way; rolling back means restoring from backup."],
                      "secret": ["Old secret versions are deleted at cut-over, so we cannot revert."],
                      "release": ["This release migrates stored data; once applied it cannot be undone without a full restore."]},
                     {"schema": ["The legacy columns are dropped in the same script and their data is not copied anywhere.",
                                 "Going back would mean restoring last night's backup and re-entering about four hours of data by hand."],
                      "secret": ["After cut-over the previous key versions are purged straight away."],
                      "release": ["Stored records are converted in place to the new format; the previous version of the "
                                  "service cannot read them afterwards."]}),
    "first_time": (0.7, ("release", "config", "nsg", "fwrule", "secret", "cert", "schema", "aksupgrade", "scale"),
                   ["First time this upgrade is done on production.",
                    "This is the first use of the new pipeline for this service."],
                   ["The team has never run this procedure before; we are following the vendor's guide step by step.",
                    "Nobody on the current team has done this; the engineer who did it last time left in June.",
                    "So far this has only been tried on the dev environment."]),
    "vendor": (0.4, None,
               ["Vendor engineer will perform the steps remotely."],
               ["The Microsoft partner will carry out the work over a screen-share while our engineer observes.",
                "Steps will be executed by the vendor's offshore team overnight."]),
    "big_bang": (0.7, ("release", "patch", "aksupgrade", "nsg", "fwrule", "config"),
                 ["All nodes will be updated at once.",
                  "Change applies to all instances at the same time."],
                 ["To finish inside the window, every node is switched together rather than one by one.",
                  "Both regions are updated in the same run.",
                  "No staged rollout this time; the whole fleet moves in one go."]),
    "canary": (-0.6, ("release", "config", "aksupgrade", "patch"),
               ["Staged rollout: one node first, then the rest after health checks.",
                "10% of traffic first via slot swap, then 100% after 30 minutes."],
               ["We will move a single store's traffic first and watch it for an hour before continuing.",
                "The first node gets the change; the others follow only if its error rate stays flat."]),
    "rehearsed": (-0.4, None,
                  ["Rehearsed in pre-prod last week."],
                  ["Dry run done on Tuesday against a copy of production data; timings recorded.",
                   "The same steps were run in staging twice without issues."]),
}


def hidden_fits(name, kind):
    kinds = HIDDEN[name][1]
    return kinds is None or kind in kinds


def hidden_phrase(name, kind, phrasing):
    options = HIDDEN[name][2 if phrasing == "A" else 3]
    return random.choice(options[kind] if isinstance(options, dict) else options)


# ----------------------------------------------------------------------------- texts
TEXT = {
    "patch": dict(
        title="Monthly Windows patching - {ci}",
        desc="Apply the {month} cumulative updates via Azure Update Manager to {ci}.",
        just=["Monthly security baseline; {n} critical CVEs outstanding.", "Policy: servers are patched within 30 days of release."],
        impl="1. Confirm the last good backup in rsv-backup-prod.\n2. Take an OS disk snapshot of {ci}.\n"
             "3. Run the Update Manager deployment for {ci} (reboot if required).\n4. Confirm heartbeat in Log Analytics and services running.",
        backout="Restore the OS disk snapshot from step 2 (about 20 min). If only one update is faulty, uninstall it via Update Manager.",
        test="Heartbeat present for 15 min, RDP works, application services running, no new critical events."),
    "release": dict(
        title="Deploy release {ver} to {ci}",
        desc="Deploy release {ver} of the {svc} application to {ci} via the Azure DevOps pipeline. Includes {feature}.",
        just=["Business request {req}: {feature}.", "Fixes defect {req} reported by Customer Care."],
        impl="1. Pipeline deploys {ver} to the staging slot / standby instance.\n2. Run smoke tests against staging.\n"
             "3. Swap staging into production.\n4. Watch error rate and latency for 30 min.",
        backout="Swap back to the previous slot or redeploy release {prev} from the pipeline (under 10 min). No schema changes in this release.",
        test="Automated smoke suite (login, search, add to basket, checkout with a test card); 5xx rate below 0.5% for 30 min."),
    "config": dict(
        title="Configuration change on {ci}: {setting}",
        desc="Change {setting} on {ci} ({svc}).",
        just=["Performance tuning after incident review {req}.", "Requested by the product team ({req})."],
        impl="1. Export the current configuration.\n2. Apply the new setting via the pipeline variable group.\n3. Confirm the setting is live.",
        backout="Re-apply the configuration exported in step 1 (under 5 min).",
        test="Health endpoint green; error rate and latency unchanged for 30 min."),
    "nsg": dict(
        title="NSG rule update for {ci} subnet",
        desc="Add inbound deny rules per security review SR-{n} for the subnet hosting {ci}.",
        just=["Security review SR-{n} finding: overly broad inbound rules.", "Audit remediation SR-{n}."],
        impl="1. Export the current NSG rules.\n2. Add the new rules with priority {prio}.\n3. Verify effective rules on the {ci} NIC.",
        backout="Delete the new rules or re-import the exported rule set (under 5 min).",
        test="Connection tests from the app subnet and jump host to {ci} on required ports; Network Watcher IP flow verify."),
    "fwrule": dict(
        title="Azure Firewall policy update on {ci}",
        desc="Update rule collections on {ci} for {reason}.",
        just=["Needed for {reason}.", "Security review SR-{n}."],
        impl="1. Export the firewall policy.\n2. Apply the rule collection change via the IaC pipeline.\n3. Check firewall logs for new denies.",
        backout="Redeploy the previous policy version from the pipeline (about 10 min).",
        test="Test the flows listed in the change (site-to-site, partner APIs, outbound SaaS); no unexpected denies for 30 min."),
    "secret": dict(
        title="Rotate secrets in {ci}",
        desc="Quarterly rotation of secrets used by {consumers}, and access policy clean-up in {ci}.",
        just=["Quarterly rotation policy.", "Security review SR-{n}: secrets older than 90 days."],
        impl="1. Create new secret versions.\n2. Point {consumers} at the new versions.\n3. Restart {consumers} slot by slot.\n"
             "4. Disable (not delete) the old versions after 24 h.",
        backout="Re-enable the previous secret versions and point consumers back (under 10 min); old versions are kept for 24 h.",
        test="Consumers authenticate: health endpoints green, no 401/403 in Application Insights for 30 min."),
    "cert": dict(
        title="Renew TLS certificate on {ci}",
        desc="Replace the TLS certificate for {host} that expires on {date}.",
        just=["Certificate expires on {date}.", "Renewal ahead of expiry ({date})."],
        impl="1. Import the renewed certificate into Key Vault.\n2. Rebind on {ci}.\n3. Check the chain with an external SSL test.",
        backout="Rebind the existing certificate (valid until {date}).",
        test="HTTPS check from outside and from the application gateway; certificate shows the new expiry date."),
    "sqlmaint": dict(
        title="Index maintenance on {ci}",
        desc="Weekly index rebuild and statistics update on {ci}.",
        just=["Standard weekly maintenance.", "Fragmentation above 30% on key indexes."],
        impl="1. Run the index maintenance job with ONLINE=ON.\n2. Monitor DTU and blocking during the run.",
        backout="Stop the job; online rebuilds roll back automatically.",
        test="Query Store: top 10 queries no slower than baseline; no blocking chains over 30 s."),
    "schema": dict(
        title="Database schema change on {ci}: {what}",
        desc="Apply migration {mig} to {ci}: {what}.",
        just=["Needed by release {ver}.", "Performance fix from problem review ({req})."],
        impl="1. Create a restore point / copy-only backup.\n2. Run migration {mig} via the release pipeline.\n3. Validate row counts and application health.",
        backout="Run the down-migration script for {mig} (tested in pre-prod).",
        test="Application smoke test, row counts match, query duration within baseline."),
    "aksupgrade": dict(
        title="AKS node pool upgrade on {ci}",
        desc="Upgrade the user node pool on {ci} to Kubernetes {kver}.",
        just=["Version {kver} required before end of support.", "Security fixes in node image {kver}."],
        impl="1. Confirm PodDisruptionBudgets.\n2. Create a new node pool on {kver} and cordon the old one.\n3. Drain workloads and watch readiness.",
        backout="Uncordon the old node pool (kept for 24 h) and move workloads back.",
        test="All deployments ready, no CrashLoopBackOff, inventory and pricing APIs return 200."),
    "scale": dict(
        title="Resize {ci} to {sku}",
        desc="Change the size of {ci} to {sku} for {reason}.",
        just=["Capacity: {reason}.", "Cost optimisation: {reason}."],
        impl="1. Confirm the target size is available in the region.\n2. Resize (needs a restart for VMs).\n3. Check service health.",
        backout="Resize back to the previous size.",
        test="Service health check and performance counters normal for 30 min."),
    "backup": dict(
        title="Backup policy change on {ci}",
        desc="Change retention and schedule in backup policy {pol} on {ci}.",
        just=["Audit requirement: 35-day retention.", "Cost review of backup retention."],
        impl="1. Export the current policy.\n2. Apply the new policy.\n3. Trigger an ad-hoc backup of one protected item.",
        backout="Re-apply the exported policy.",
        test="Ad-hoc backup completes; next scheduled backup succeeds."),
}
IRREVERSIBLE_BACKOUT = "Restore from the backup / restore point taken before the change (estimated {h} h)."

IMPL_WEAK = ["Deploy via pipeline.", "As per standard process.", "Apply the change.", "Implement as discussed with the team.",
             "Follow the vendor document."]
BACKOUT_WEAK = ["Rollback if required.", "Revert.", "Restore if needed.", "Undo the change."]
TEST_WEAK = ["Owner to verify.", "Smoke test.", "Check it works.", "Will monitor.", "Business to confirm."]
IMPL_MISSING = ["", "TBD"]
BACKOUT_MISSING = ["", "N/A", "TBD", "Not applicable - low risk change."]
TEST_MISSING = ["", "N/A", "TBD"]


def fill_vars(kind, ci, svc, consumers):
    r = random.randint
    return {"ci": ci, "svc": svc, "n": r(100, 999), "month": random.choice(["October", "November"]),
            "ver": f"{r(3, 6)}.{r(1, 20)}.{r(0, 9)}", "prev": f"{r(3, 6)}.{r(1, 20)}.{r(0, 9)}", "req": f"REQ{r(1000, 9999)}",
            "feature": random.choice(["a fix for discount rounding", "the new delivery-slot API", "logging improvements",
                                      "a payment-retry change", "a .NET security update", "faster order search"]),
            "setting": random.choice(["connection pool size", "feature flag express-checkout", "retry policy",
                                      "request timeout", "autoscale rule", "cache expiry"]),
            "prio": r(200, 900), "reason": random.choice(["a new payroll SaaS endpoint", "decommissioned partner ranges",
                                                          "peak-season capacity", "lower utilisation after review"]),
            "consumers": ", ".join(consumers) or "dependent apps", "host": f"{ci}.shopkart-demo.example",
            "date": random.choice(["12 Dec 2026", "18 Dec 2026", "5 Jan 2027"]),
            "what": random.choice(["add a delivery-slot column", "new index for usp_GetOrderHistory",
                                   "partition the order history table", "widen the address columns"]),
            "mig": f"M{r(100, 400)}", "kver": random.choice(["1.31", "1.32"]),
            "sku": random.choice(["Standard_D4s_v5", "Standard_D8s_v5", "S4", "P2"]),
            "pol": random.choice(["vm-daily", "sql-weekly"]), "h": random.choice([2, 3, 4])}


def render(s, v):
    try:
        return s.format(**v)
    except (KeyError, IndexError):
        return s


# ----------------------------------------------------------------------------- outcome model
TIER = {"1 - most critical": 1, "2 - somewhat critical": 2, "3 - less critical": 3}
BANDS = (("low", 0.10), ("moderate", 0.18), ("high", 1.01))   # true failure probability -> band


def failure_logit(f):
    """f: feature dict built by the generator. Returns (logit, {factor: contribution})."""
    c = {"base": KINDS[f["kind"]]["base"]}
    c["tier"] = {1: 0.7, 2: 0.3, 3: 0.0}[f["tier"]]
    c["blast_radius"] = 0.25 * min(f["dependents"], 3)
    if f["type"] == "emergency":
        c["emergency"] = 0.5
    for part, w_missing, w_weak in (("implementation", 0.5, 0.4), ("backout", 1.0, 0.5), ("test", 0.7, 0.3)):
        q = f["quality"][part]
        if q != "ok":
            c[f"{part}_{q}"] = w_missing if q == "missing" else w_weak
    if f["window"] == "business":
        c["business_hours"] = 0.5 + (0.4 if f["tier"] == 1 else 0.0)
    elif f["window"] == "off_hours":
        c["outside_maintenance_window"] = 0.3
    if f["freeze"]:
        c["freeze"] = 0.7
    if f["conflict"]:
        c["conflict"] = 0.9 if f["conflict"] == "same_ci" else 0.5
    if f["open_problem"]:
        c["open_problem"] = 0.5
    if f["open_incident"]:
        c["open_incident"] = 0.4
    if f["short_lead"]:
        c["short_lead_time"] = 0.3
    for h in f["hidden"]:
        c[h] = HIDDEN[h][0]
    c["group_track_record"] = GROUP_SKILL.get(f["group"], 0.0)
    if f["ci"] in CI_FRAGILITY:
        c["ci_fragility"] = CI_FRAGILITY[f["ci"]]
    c["noise"] = f["noise"]
    return sum(c.values()), {k: round(v, 2) for k, v in c.items() if v}


def failure_probability(f):
    logit, contrib = failure_logit(f)
    return 1 / (1 + math.exp(-logit)), contrib


def band(p):
    return next(name for name, hi in BANDS if p < hi)


def recommendation(band_name, quality, freeze, ctype):
    """SKR change policy (also published to the agents as a KB article)."""
    if any(quality[p] == "missing" for p in ("implementation", "backout", "test")):
        return "send_back"
    if freeze and ctype != "emergency":
        return "send_back"
    return {"high": "cab", "moderate": "approve_with_conditions", "low": "approve"}[band_name]
