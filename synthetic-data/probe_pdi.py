#!/usr/bin/env python3
"""
Read-only check of a ServiceNow PDI before loading the change-management dataset.

Answers, without writing anything:
  * which change_request fields and choices exist (plans, risk, close codes, ...), and whether the AI fields exist
  * whether incidents have caused_by or the v1 u_caused_by field
  * how schedules are modelled (cmn_schedule types, cmn_schedule_span fields)
  * what the v1 load already created (groups, CIs, services, KB, problems, incidents, changes)
  * which of this dataset's user names already exist
  * which business rules run on change_request (they can override inserted states)

Credentials come from SN_INSTANCE / SN_USER / SN_PASSWORD, as for load_to_snow.py.

  python3 probe_pdi.py            # writes data/pdi_probe.json and prints a summary
  python3 probe_pdi.py --trial    # ALSO inserts 3 test changes, reads back what the instance stored,
                                  # tries to correct the state, then deletes them -> data/pdi_trial.json
"""
import argparse
import json
from pathlib import Path

from load_to_snow import Snow

DATA = Path(__file__).resolve().parent / "data"
CHANGE_FIELDS = ["justification", "implementation_plan", "backout_plan", "test_plan", "risk", "impact", "type",
                 "category", "start_date", "end_date", "work_start", "work_end", "close_code", "close_notes",
                 "business_service", "requested_by", "cmdb_ci", "assignment_group", "assigned_to", "opened_at",
                 "correlation_id", "work_notes", "state", "conflict_status", "u_ai_risk", "u_ai_recommendation",
                 "u_ai_close_code"]


READ_BACK = ("number,state,type,risk,impact,start_date,end_date,work_start,work_end,close_code,close_notes,"
             "justification,implementation_plan,backout_plan,test_plan,opened_at,correlation_id,cmdb_ci,"
             "assignment_group,business_service,chg_model,approval,conflict_status")


def v1_change_summary(sn):
    """How the v1 load's change_request records ended up (inserted with state Closed + close codes)."""
    rows = sn._get("change_request", "correlation_id=SYNTH-SKR", "state,risk,close_code,work_start,start_date,type,chg_model")
    from collections import Counter
    return {"n": len(rows),
            "state": dict(Counter(str(r.get("state")) for r in rows)),
            "risk": dict(Counter(str(r.get("risk")) for r in rows)),
            "close_code": dict(Counter(str(r.get("close_code")) for r in rows)),
            "work_start_set": sum(1 for r in rows if r.get("work_start")),
            "type": dict(Counter(str(r.get("type")) for r in rows)),
            "chg_model_set": sum(1 for r in rows if r.get("chg_model"))}


def trial(sn, plan):
    """Insert one closed, one Assess and one Review change; record what sticks; delete them."""
    changes = {s["stage"]: s["records"] for s in plan["stages"]}["changes"]
    picks = [next(r for r in changes if r["state"] == st) for st in ("3", "-4", "0")]
    lookup = {}

    def ref(v):
        if not (isinstance(v, str) and v.startswith("@")):
            return v
        kind, name = v[1:].split(":", 1)
        table, field, value = {"ci": ("cmdb_ci", "name", name), "svc": ("cmdb_ci_service", "name", f"SKR {name}"),
                               "group": ("sys_user_group", "name", f"SKR {name}")}.get(kind, (None, None, None))
        if not table:
            return None                                   # users: not needed for the trial
        if v not in lookup:
            lookup[v] = sn.query_one(table, field, value)
        return lookup[v]

    results = []
    for rec in picks:
        body = {k: ref(v) for k, v in rec.items() if not k.startswith("_")}
        body = {k: v for k, v in body.items() if v not in (None, "")}
        body["short_description"] = "[PROBE - deleted automatically] " + body["short_description"]
        body["correlation_id"] = "SYNTH-SKR-CHG-PROBE"
        sys_id, info = sn.insert("change_request", body)
        res = {"key": rec["_key"], "intended": {k: rec.get(k) for k in ("state", "type", "risk", "start_date", "end_date",
                                                                           "work_start", "work_end", "close_code")},
               "insert_error": None if sys_id else info}
        if not sys_id:
            sys_id, info = sn.insert("change_request", {k: v for k, v in body.items() if k not in rec.get("_retry_drop", [])})
            res["retry_without"] = rec.get("_retry_drop")
            res["retry_error"] = None if sys_id else info
        if sys_id:
            res["stored_after_insert"] = (sn._get("change_request", f"sys_id={sys_id}", READ_BACK) or [{}])[0]
            fix = {k: rec[k] for k in ("state", "risk", "close_code", "close_notes", "work_start", "work_end")
                   if rec.get(k) and str(res["stored_after_insert"].get(k)) != str(rec[k])}
            fix.update(rec.get("_post_patch", {}))
            res["patch_body"] = fix
            res["patch_error"] = sn.patch("change_request", sys_id, fix)
            res["stored_after_patch"] = (sn._get("change_request", f"sys_id={sys_id}", READ_BACK) or [{}])[0]
            res["deleted"] = sn.delete("change_request", sys_id)
        results.append(res)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trial", action="store_true", help="insert, read back and delete 3 test changes")
    a = ap.parse_args()
    sn = Snow(False)
    sn.check()
    out = {"instance": sn.base}

    def dictionary(tables, elements):
        rows = sn._get("sys_dictionary", f"nameIN{','.join(tables)}^elementIN{','.join(elements)}",
                       "name,element,internal_type,reference,max_length,mandatory")
        found = {}
        for r in rows:
            found.setdefault(r["element"], {"table": r["name"], "type": r["internal_type"].get("value")
                                            if isinstance(r["internal_type"], dict) else r["internal_type"],
                                            "reference": r.get("reference"), "mandatory": r.get("mandatory")})
        return found

    def choices(table, element):
        return [(c["value"], c["label"]) for c in
                sn._get("sys_choice", f"name={table}^element={element}^inactive=false^language=en", "value,label")]

    def count(table, query):
        r = sn._req("GET", f"{sn.base}/api/now/stats/{table}", params={"sysparm_query": query, "sysparm_count": "true"})
        try:
            return int(r.json()["result"]["stats"]["count"])
        except Exception:
            return f"HTTP {r.status_code}"

    out["change_request_fields"] = dictionary(["change_request", "task"], CHANGE_FIELDS)
    out["change_request_missing_fields"] = [f for f in CHANGE_FIELDS if f not in out["change_request_fields"]]
    out["change_choices"] = {e: choices("change_request", e) for e in ("type", "risk", "impact", "state", "close_code",
                                                                       "category")}
    out["incident_link_fields"] = dictionary(["incident", "task"], ["caused_by", "u_caused_by", "problem_id",
                                                                    "u_problem_id"])
    out["tables_exist"] = {t: bool(sn._get("sys_db_object", f"name={t}", "name"))
                           for t in ("cmn_schedule", "cmn_schedule_span", "cmn_schedule_condition", "change_conflict",
                                     "cmn_schedule_blackout", "cmn_schedule_maintenance")}
    out["cmn_schedule_fields"] = dictionary(["cmn_schedule"], ["name", "type", "time_zone", "description",
                                                               "u_applies_to"])
    out["cmn_schedule_type_choices"] = choices("cmn_schedule", "type")
    out["cmn_schedule_span_fields"] = dictionary(["cmn_schedule_span"], ["schedule", "name", "start_date_time",
                                                                         "end_date_time", "repeat_type", "days_of_week",
                                                                         "all_day", "type"])
    out["existing"] = {
        "users synth.*": count("sys_user", "user_nameSTARTSWITHsynth."),
        "groups SKR*": count("sys_user_group", "nameSTARTSWITHSKR "),
        "services SKR*": count("cmdb_ci_service", "nameSTARTSWITHSKR "),
        "CIs tagged": count("cmdb_ci", "short_descriptionLIKESYNTH-SKR"),
        "relationships Depends on": count("cmdb_rel_ci", "parent.short_descriptionLIKESYNTH-SKR"),
        "KB tagged": count("kb_knowledge", "metaLIKESYNTH-SKR"),
        "problems tagged": count("problem", "correlation_idSTARTSWITHSYNTH-SKR"),
        "incidents SYNTH-SKR": count("incident", "correlation_id=SYNTH-SKR"),
        "incidents SYNTH-SKR-LIVE": count("incident", "correlation_id=SYNTH-SKR-LIVE"),
        "incidents SYNTH-SKR-CHG": count("incident", "correlation_id=SYNTH-SKR-CHG"),
        "changes SYNTH-SKR": count("change_request", "correlation_id=SYNTH-SKR"),
        "changes SYNTH-SKR-CHG": count("change_request", "correlation_id=SYNTH-SKR-CHG"),
        "schedules SKR*": count("cmn_schedule", "nameSTARTSWITHSKR "),
        "all schedules by type": {t: count("cmn_schedule", f"type={t}") for t in ("blackout", "maintenance")},
    }
    plan = json.loads((DATA / "load_plan.json").read_text())
    stage = {s["stage"]: s["records"] for s in plan["stages"]}
    names = [r["user_name"] for r in stage["users"]]
    taken = []
    for i in range(0, len(names), 40):
        taken += [r["user_name"] for r in sn._get("sys_user", "user_nameIN" + ",".join(names[i:i + 40]), "user_name")]
    out["dataset_user_names_already_on_pdi"] = sorted(taken)
    kb_titles = [r["short_description"] for r in stage["kb"]]
    found = [r["short_description"] for r in sn._get("kb_knowledge", "metaLIKESYNTH-SKR", "short_description")]
    out["dataset_kb_titles_missing_on_pdi"] = [t for t in kb_titles if t not in found]
    out["rel_type_depends_on"] = bool(sn._get("cmdb_rel_type", "name=Depends on::Used by", "sys_id"))
    out["change_business_rules"] = sorted(r["name"] for r in
                                          sn._get("sys_script", "collection=change_request^active=true", "name"))
    out["change_state_model_props"] = {r["name"]: r["value"] for r in
                                       sn._get("sys_properties", "nameLIKEchange_request^nameLIKEstate", "name,value")}

    out["v1_changes"] = v1_change_summary(sn)
    if a.trial:
        out["trial"] = trial(sn, plan)
        (DATA / "pdi_trial.json").write_text(json.dumps(out["trial"], indent=1))
        for t in out["trial"]:
            ins, pat = t.get("stored_after_insert", {}), t.get("stored_after_patch", {})
            print(f"trial {t['key']}: intended state {t['intended']['state']} -> after insert {ins.get('state')}, "
                  f"after patch {pat.get('state')}; risk {t['intended']['risk']} -> {pat.get('risk')}; "
                  f"close_code -> {pat.get('close_code')!r}; deleted={t.get('deleted')}"
                  + (f"; insert error {t['insert_error']}" if t.get("insert_error") else ""))
    print(f"v1 changes on the PDI: {out['v1_changes']}")
    path = DATA / "pdi_probe.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"\nMissing change_request fields: {out['change_request_missing_fields']}")
    print(f"Incident link fields: {sorted(out['incident_link_fields'])}")
    print(f"Tables: {out['tables_exist']}")
    print("Existing records: " + json.dumps(out["existing"]))
    print(f"Dataset user names already on the PDI: {len(taken)}")
    print(f"Business rules on change_request: {len(out['change_business_rules'])}")
    print(f"\nWrote {path} (read-only probe; nothing was changed on the instance).")


if __name__ == "__main__":
    main()
