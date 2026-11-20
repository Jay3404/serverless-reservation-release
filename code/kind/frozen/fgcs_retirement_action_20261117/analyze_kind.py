"""Offline normalized-ledger scorer for the two-condition, six-block Kind study.

The plan contains `trials` (trial_id, block, condition, arm), a sealed
`bind_tolerance_s`, and optionally `slo_s`. The ledger is JSON, JSONL, or CSV;
see `derive` for the normalized column names. Event timestamps are seconds on the
same aligned clock within a trial. Without a measurement/witness the scorer
leaves a value missing, and retains every planned row. Segments describe measured
scheduler reservation occupancy, never RSS; they may be supplied as a JSON list.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from statistics import mean, median


ARMS = ("fixed", "signal", "force_wait", "force_reclaim")
ALIASES = {
    "fixed": "fixed", "signal": "signal", "hybrid": "signal", "calibrated": "signal",
    "force_wait": "force_wait", "forced_wait": "force_wait", "wait": "force_wait",
    "force_reclaim": "force_reclaim", "forced_reclaim": "force_reclaim", "reclaim": "force_reclaim",
}
CONTRASTS = {"action": ("force_wait", "force_reclaim"), "information": ("fixed", "signal")}
METRICS = ("y_arrival_to_bind_s", "y_submit_to_bind_s", "y_arrival_to_ready_s", "y_http_response_s",
           "z_http_response_s", "z_http_wait_to_ready_s", "reserved_gbs", "z_recreations",
           "slo_violations", "http_completions", "reclamation_count")

# Short names used by the raw Kind trial runner; normalization preserves the
# underlying documents as well as the normalized event rows.
FIELDS = {
    "y_submit_s": ("y_created_s", "y_create_start_s"),
    "y_http_done_s": ("y_done_s",), "z_http_done_s": ("z_done_s",),
    "z_http_arrival_s": ("z_arrival_s",), "z_replacement_ready_s": ("z_ready_s",),
    "x_handler_s": ("x_signal_event_s",), "fixed_eta_s": ("fixed_predicted_return_s",),
    "signal_eta_s": ("signal_predicted_return_s",),
    "first_proposed_action": ("initial_action",), "first_z_delete_applied": ("first_retire_applied",),
    "later_z_delete_applied": ("later_retire_applied",), "x_retire_intent_s": ("x_intent_s",),
}


def need(ok, why):
    if not ok:
        raise ValueError(why)


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def num(x):
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def flag(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


def alias(x):
    need(x in ALIASES, f"unknown assignment arm {x!r}")
    return ALIASES[x]


def load_records(path):
    if path.is_dir():
        rows=[]
        for p in sorted(path.glob("trial-*.json")):
            row=json.loads(p.read_text())
            independently_verify_audit(path,row)
            rows.append(row)
        return rows
    if path.suffix == ".csv":
        with path.open(newline="") as inp:
            return list(csv.DictReader(inp))
    if path.suffix in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    x = json.loads(path.read_text())
    return x.get("trials", x.get("rows")) if isinstance(x, dict) else x


def as_array(v):
    if isinstance(v, str) and v:
        return json.loads(v)
    return v


def independently_verify_audit(root, row):
    """Reread the raw same-UID Binding ResponseComplete when the receipt exists."""
    row["independent_bind_check"] = "not_claimed"
    if flag(row.get("y_bind_uid_verified")) is not True:
        return
    ident=row.get("trial_id")
    if not ident or Path(ident).name != ident:
        return
    path=root/ident/"binding-audit.json"
    if not path.is_file():
        row["independent_bind_check"] = "raw_audit_not_copied"
        return
    doc=json.loads(path.read_text())
    pod=(row.get("pod_creates") or {}).get("y") or {}
    witness=row.get("y_bind_witness_id")
    hits=[x for x in doc if x.get("auditID")==witness and witness and
          x.get("verb")=="create" and x.get("stage")=="ResponseComplete"
          and x.get("objectRef",{}).get("subresource")=="binding"
          and x.get("objectRef",{}).get("name")==pod.get("name") and
          x.get("requestObject",{}).get("metadata",{}).get("uid")==row.get("y_uid") and
          x.get("requestObject",{}).get("target",{}).get("name") and
          x.get("responseStatus",{}).get("code") in (200,201,202)]
    if len(hits)==1:
        iso=re.sub(r"(\.\d{6})\d+",r"\1",hits[0]["stageTimestamp"]).replace("Z","+00:00")
        timestamp=datetime.fromisoformat(iso).timestamp()
        if num(row.get("y_bind_s")) is not None and math.isclose(timestamp,float(row["y_bind_s"]),abs_tol=1e-6,rel_tol=0):
            row["independent_bind_check"]="UID_and_response_complete_match"
            return
    row["independent_bind_check"]="raw_audit_mismatch"
    row["y_bind_uid_verified"]=False


def elapsed(later, earlier, problems, label):
    if later is None or earlier is None:
        return None
    if later < earlier:
        problems.append(f"reversed_{label}")
        return None
    return later - earlier


def reserve_cost(v, problems):
    """Integrate exact occupancy intervals or keep cost missing.

    'reservation_window_complete' attests that all Pods in the declared
    campaign scope were followed to release or window end. A null end is
    admissible only with an explicit segment observation extending to that end.
    A precomputed cost is accepted only when the verified producer includes
    temporal bounds and explicitly marks that value as independently verified.
    """
    lo, hi = num(v.get("cost_window_start_s")), num(v.get("cost_window_end_s"))
    scope, basis = v.get("reservation_scope"), v.get("reservation_time_basis")
    if lo is None or hi is None or hi <= lo or not scope or not basis:
        return None, None
    if flag(v.get("reservation_window_complete")) is not True:
        return None, hi-lo
    segments = as_array(v.get("reservations"))
    if not isinstance(segments, list):
        verified, supplied = flag(v.get("reservations_independently_verified")), num(v.get("reserved_gbs"))
        return ((supplied if verified and supplied is not None and supplied >= 0 else None), hi-lo)
    totals, spans = [], {}
    for s in segments:
        uid = s.get("uid")
        since, until, gb = num(s.get("since_s")), num(s.get("until_s")), num(s.get("gb"))
        if until is None:
            covered = num(s.get("observed_through_s"))
            if covered is not None and covered >= hi:
                until = max(hi, since or hi)
        if not uid or since is None or until is None or until < since or gb is None or gb <= 0:
            problems.append("incomplete_reservation_segment")
            return None, hi-lo
        spans.setdefault(uid, []).append((since, until))
        totals.append(max(0.0, min(hi, until)-max(lo, since))*gb)
    for ss in spans.values():
        ss.sort()
        if any(a[1] > b[0] for a,b in zip(ss,ss[1:])):
            problems.append("overlapping_reservation_segments_same_uid")
            return None, hi-lo
    return math.fsum(totals), hi-lo


def check_runner_cost(v, lower, upper, problems):
    """Independently recalculate the interval integrals encoded by run_kind.py."""
    group=as_array(v.get("reservation_intervals"))
    if group is None:
        return None
    births = v.get("pod_creates")
    k = num(v.get("y_request_gb"))
    lo,hi = num(v.get("cost_window_start_s")),num(v.get("cost_window_end_s"))
    if (not isinstance(group,list) or not isinstance(births,dict) or len(group)!=len(set(births)-{"f"}) or
            None in (lo,hi,k,lower,upper) or k <= 0 or
            not all(num(v.get(a+"_request_gb"))==k for a in ("x","z"))):
        problems.append("incomplete_runner_cost_linkage")
        return False
    l,h=[],[]
    for rec in group:
        endpoints = [num(t) for t in rec] if isinstance(rec,(tuple,list)) and len(rec)==4 else [None]
        if any(t is None for t in endpoints):
            problems.append("incomplete_runner_cost_interval")
            return False
        start_lo,start_hi,stop_lo,stop_hi=endpoints
        if not (start_lo<=start_hi and stop_lo<=stop_hi):
            problems.append("invalid_runner_cost_interval")
            return False
        l.append(k*max(0.,min(hi,stop_lo)-max(lo,start_hi)))
        h.append(k*max(0.,min(hi,stop_hi)-max(lo,start_lo)))
    if not (math.isclose(math.fsum(l),lower,abs_tol=1e-8,rel_tol=0) and
            math.isclose(math.fsum(h),upper,abs_tol=1e-8,rel_tol=0)):
        problems.append("runner_cost_interval_total_does_not_match")
        return False
    return True


def fit_witness(v, problems):
    """Classify the first sufficient X/Z release from joined same-clock receipts."""
    free, ygb = num(v.get("free_gb_at_decision")), num(v.get("y_request_gb"))
    decision, bind = num(v.get("decision_s")), num(v.get("y_bind_s"))
    if free is None or ygb is None or free < 0 or ygb <= 0 or decision is None:
        return None, None, None
    if flag(v.get("capacity_ledger_complete")) is not True:
        return None, None, None
    records = as_array(v.get("capacity_events"))
    if records is None:
        # When the producer verifies the only intervening changes in available
        # memory are the preexisting X/Z releases, reconstruct that list from
        # the matching cache-removal bounds.
        records = []
        z_seen = flag(v.get("first_z_delete_applied")) is not False or flag(v.get("later_z_delete_applied")) is not False
        uncertain = []
        for a in ("x","z"):
            lo,hi,mem = (v.get(a+"_release_lo_s"),v.get(a+"_release_hi_s"),v.get(a+"_request_gb"))
            if a == "z" and lo is None and hi is None and not z_seen:
                continue
            if lo is not None and hi is not None:
                records.append(dict(lo_s=lo,hi_s=hi,delta_free_gb=mem,uid=v.get(a+"_uid")))
            else:
                command_s = (v.get("x_retire_intent_s") if a=="x" else
                             v.get("z_first_delete_s") or (v.get("later_z_delete") or {}).get("submit_s"))
                uncertain.append(num(lo) if num(lo) is not None else num(command_s))
        if uncertain:
            earliest=min(t if t is not None else -math.inf for t in uncertain)
            records=[r for r in records if num(r["hi_s"]) is not None and float(r["hi_s"])<earliest]
            if not records:
                problems.append("unresolved_censored_release_order")
                return None,None,None
    if not isinstance(records, list):
        return None, None, None
    if free >= ygb:
        problems.append("Y_fits_at_decision")
        return "preexisting", decision, decision
    if any(num(e.get("lo_s")) is None or num(e.get("hi_s")) is None or
           num(e.get("delta_free_gb")) is None or not e.get("uid") for e in records):
        problems.append("incomplete_capacity_ledger")
        return None, None, None
    if v.get("x_uid") and v.get("x_uid") == v.get("z_uid"):
        return None, None, None
    events = sorted((float(e["lo_s"]), float(e["hi_s"]), float(e["delta_free_gb"]), e["uid"]) for e in records)
    if any(a > b for a,b,_,_ in events):
        problems.append("invalid_capacity_event_bounds")
        return None, None, None
    for lo, hi, delta, uid in events:
        if hi < decision or (bind is not None and lo > bind):
            continue
        if lo < decision:
            problems.append("capacity_event_straddles_decision")
            return None, None, None
        if any((a[0] != lo and lo <= a[0] < hi) or (a[0] == lo and a[3] != uid) for a in events):
            problems.append("overlapping_capacity_event_bounds")
            return None, None, None
        if uid == v.get("y_uid") and delta < 0:
            continue
        free += delta
        if free >= ygb - 1e-9:
            if bind is None or hi > bind:
                problems.append("fit_not_confirmed_before_bind")
                return None,None,None
            provider = "X" if uid == v.get("x_uid") else "Z" if uid == v.get("z_uid") else "other"
            return provider, lo, hi
    return None, None, None


def derive(grid, raw, config):
    row = dict(grid)
    problems = []
    status = (raw.get("status") or "started") if raw is not None else "not_run"
    v = dict(raw or {})
    for canonical, aliases in FIELDS.items():
        if canonical not in v:
            v[canonical] = next((v[a] for a in aliases if a in v),None)
    row.update(status=status, attempted=status != "not_run")
    timings = (
        "x_retire_intent_s", "x_handler_s", "x_signal_received_s", "x_signal_validated_s", "x_release_lo_s",
        "x_release_hi_s", "decision_s", "z_first_delete_s", "z_first_delete_ack_s", "z_release_lo_s",
        "z_release_hi_s", "y_arrival_s", "y_submit_s", "y_bind_s", "y_ready_s", "y_http_done_s",
        "z_http_arrival_s", "z_replacement_ready_s", "z_http_done_s",
        "fixed_eta_s", "signal_eta_s", "free_gb_at_decision", "y_request_gb",
        "cost_window_start_s", "cost_window_end_s", "z_recreations",
        "cost_lower_gbs", "cost_upper_gbs", "z_bind_s")
    row.update({k:v.get(k) for k in ("x_uid", "x_signal_uid", "y_uid", "z_uid", "z_serving_uid",
               "fixed_shadow_action", "signal_shadow_action", "first_proposed_action", "first_applied_action",
               "reservation_scope", "reservation_time_basis", "run_error", "first_z_delete_receipt_uid",
               "y_bind_witness_id", "independent_bind_check")})
    if not row["run_error"] and v.get("errors"):
        row["run_error"] = json.dumps(v["errors"])
    row.update({k:num(v.get(k)) for k in timings})
    if row["x_uid"] is not None and row["z_uid"] is not None and row["x_uid"] == row["z_uid"]:
        problems.append("shared_x_z_uid")
    a,d,s,b,r,h = (row[k] for k in ("y_arrival_s","decision_s","y_submit_s","y_bind_s","y_ready_s","y_http_done_s"))
    if d is not None and a is not None and d < a:
        problems.append("decision_precedes_HTTP_arrival")
    y_bind_confirmed = bool(row["y_uid"]) and flag(v.get("y_bind_uid_verified")) is True
    if b is not None and not y_bind_confirmed:
        problems.append("unverified_y_bind_uid")
    row["y_submit_to_bind_s"] = elapsed(b,s,problems,"y_submit_to_bind") if y_bind_confirmed else None
    row["y_arrival_to_bind_s"] = (elapsed(b,a,problems,"y_arrival_to_bind")
                                  if row["y_submit_to_bind_s"] is not None else None)
    row["y_arrival_to_ready_s"] = (elapsed(r,a,problems,"y_arrival_to_ready")
                                   if r is not None and b is not None and r >= b and y_bind_confirmed else None)
    y_ok, z_ok = flag(v.get("y_http_ok")), flag(v.get("z_http_ok"))
    row["y_http_ok"], row["z_http_ok"] = y_ok, z_ok
    row["y_http_response_s"] = elapsed(h,a,problems,"y_http_response") if y_ok else None
    row["z_http_response_s"] = elapsed(row["z_http_done_s"],row["z_http_arrival_s"],problems,"z_http_response") if z_ok else None
    z_ready = row["z_replacement_ready_s"]
    z_arrival = row["z_http_arrival_s"]
    row["z_http_wait_to_ready_s"] = (max(0.,z_ready-z_arrival) if z_ready is not None and z_arrival is not None
                                    else 0. if z_ok and row["z_uid"] and row["z_serving_uid"] == row["z_uid"]
                                    else None)
    row["http_completions"] = (int(y_ok and row["y_http_response_s"] is not None) +
                               int(z_ok and row["z_http_response_s"] is not None)) if raw is not None else 0
    slo = num(config.get("slo_s"))
    row["slo_violations"] = (sum(row[k] > slo for k in ("y_http_response_s","z_http_response_s"))
                             if row["http_completions"] == 2 and slo is not None else None)
    x_intent,happened,recv,valid = (row[k] for k in ("x_retire_intent_s","x_handler_s","x_signal_received_s","x_signal_validated_s"))
    signal_census = flag(v.get("signal_observer_complete"))
    row["signal_available"] = None
    if d is not None and signal_census is True:
        row["signal_available"] = bool(row["x_uid"] and row["x_signal_uid"] == row["x_uid"] and
            None not in (x_intent,happened,recv,valid) and x_intent <= happened <= recv <= valid <= d)
        if flag(v.get("x_signal_used_in_policy",v.get("signal_used"))) is True and not row["signal_available"]:
            problems.append("signal_used_without_cutoff_witness")
    row["signal_used"] = flag(v.get("x_signal_used_in_policy",v.get("signal_used")))
    row["same_state_eta_changed"] = (abs(row["fixed_eta_s"] - row["signal_eta_s"]) > 1e-9
                                     if None not in (row["fixed_eta_s"], row["signal_eta_s"]) else None)
    row["same_state_actions_differ"] = (row["fixed_shadow_action"] != row["signal_shadow_action"]
                                        if row["fixed_shadow_action"] and row["signal_shadow_action"] else None)
    row["within_1s_fixed"] = d < row["fixed_eta_s"] <= d+1 if None not in (d,row["fixed_eta_s"]) else None
    row["within_1s_signal"] = d < row["signal_eta_s"] <= d+1 if None not in (d,row["signal_eta_s"]) else None
    for prediction in ("fixed","signal"):
        branch=row["within_1s_"+prediction]; shadow=row[prediction+"_shadow_action"]
        if branch is not None and shadow is not None and shadow != ("wait" if branch else "reclaim"):
            problems.append("shadow_threshold_mismatch_"+prediction)
    row["first_z_delete_applied"] = flag(v.get("first_z_delete_applied"))
    if row["first_z_delete_applied"] is False and row["first_proposed_action"]=="reclaim" and any(
            str(e).startswith("Z DELETE") for e in (v.get("errors") or [])):
        problems.append("first_Z_delete_attempt_not_resolved")
        row["first_z_delete_applied"]=None
        v["first_z_delete_applied"]=None
    if row["first_z_delete_applied"] is True and (not row["z_uid"] or
            row["z_first_delete_s"] is None or row["z_first_delete_ack_s"] is None or
            row["z_first_delete_ack_s"] < row["z_first_delete_s"] or
            (row["first_z_delete_receipt_uid"] is not None and row["first_z_delete_receipt_uid"] != row["z_uid"])):
        problems.append("unverified_first_z_delete")
        row["first_z_delete_applied"] = None
    row["later_z_delete_applied"] = flag(v.get("later_z_delete_applied"))
    row["reclamation_count"] = (int(row["first_z_delete_applied"])+int(row["later_z_delete_applied"])
                                if None not in (row["first_z_delete_applied"],row["later_z_delete_applied"])
                                else None)
    row["fit_source"],row["fit_lo_s"],row["fit_hi_s"] = fit_witness(v,problems) if y_bind_confirmed else (None,None,None)
    if row["fit_hi_s"] is not None and b is not None and row["fit_hi_s"] > b:
        problems.append("fit_upper_bound_after_bind")
    xl,xu,zl,zu = (row[k] for k in ("x_release_lo_s","x_release_hi_s","z_release_lo_s","z_release_hi_s"))
    row["observed_order"] = ("X_first" if xu < zl else "Z_first" if zu < xl else "overlap_or_tie"
                             ) if None not in (xl,xu,zl,zu) and xl <= xu and zl <= zu else None
    row["reserved_gbs"],row["cost_window_duration_s"] = reserve_cost(v,problems)
    lo,hi=row["cost_lower_gbs"],row["cost_upper_gbs"]
    if (flag(v.get("reservation_window_complete")) is not True or row["cost_window_duration_s"] is None or
            None in (lo,hi) or lo<0 or hi<lo):
        row["cost_lower_gbs"]=row["cost_upper_gbs"]=None
    elif row["reserved_gbs"] is not None and not lo-1e-6 <= row["reserved_gbs"] <= hi+1e-6:
        problems.append("point_cost_outside_reported_bounds")
    row["reservation_integral_rechecked"] = check_runner_cost(v,row["cost_lower_gbs"],row["cost_upper_gbs"],problems)
    if row["reservation_integral_rechecked"] is False:
        row["cost_lower_gbs"]=row["cost_upper_gbs"]=None
    row["anomalies"] = ";".join(problems)
    return row


def compare(condition, block, contrast, lhs, rhs, tol):
    delta = dict(condition=condition, block=block, contrast=contrast, lhs_arm=lhs["arm"], rhs_arm=rhs["arm"],
                 lhs_trial_id=lhs["trial_id"], rhs_trial_id=rhs["trial_id"],
                 lhs_status=lhs["status"], rhs_status=rhs["status"], both_attempted=lhs["attempted"] and rhs["attempted"],
                 lhs_signal_available=lhs["signal_available"], rhs_signal_available=rhs["signal_available"],
                 lhs_proposed_action=lhs["first_proposed_action"], rhs_proposed_action=rhs["first_proposed_action"],
                 lhs_first_z_delete_applied=lhs["first_z_delete_applied"], rhs_first_z_delete_applied=rhs["first_z_delete_applied"],
                 lhs_fit_source=lhs["fit_source"],rhs_fit_source=rhs["fit_source"],
                 lhs_observed_order=lhs["observed_order"],rhs_observed_order=rhs["observed_order"])
    actions = (lhs["first_z_delete_applied"],rhs["first_z_delete_applied"])
    delta["applied_action_changed"] = actions[0] != actions[1] if None not in actions else None
    delta["lhs_same_state_eta_changed"] = lhs["same_state_eta_changed"]
    delta["rhs_same_state_eta_changed"] = rhs["same_state_eta_changed"]
    delta["lhs_same_state_actions_differ"] = lhs["same_state_actions_differ"]
    delta["rhs_same_state_actions_differ"] = rhs["same_state_actions_differ"]
    delta["proposal_changed"] = (lhs["first_proposed_action"] != rhs["first_proposed_action"]
                                  if lhs["first_proposed_action"] and rhs["first_proposed_action"] else None)
    delta["forced_action_adherent"] = (actions == (False,True)) if contrast == "action" and None not in actions else None
    same_window=(lhs["reservation_scope"] == rhs["reservation_scope"] and
               lhs["reservation_time_basis"] == rhs["reservation_time_basis"] and
               None not in (lhs["cost_window_duration_s"],rhs["cost_window_duration_s"]) and
               math.isclose(lhs["cost_window_duration_s"],rhs["cost_window_duration_s"],abs_tol=1e-6,rel_tol=0.))
    for m in METRICS:
        x,y = lhs[m],rhs[m]
        if m == "reserved_gbs" and not same_window:
            x=y=None
        delta["lhs_"+m],delta["rhs_"+m]=x,y
        delta["delta_lhs_minus_rhs_"+m] = x - y if None not in (x,y) else None
    for end in ("lower","upper"):
        delta["lhs_cost_"+end+"_gbs"]=lhs["cost_"+end+"_gbs"]
        delta["rhs_cost_"+end+"_gbs"]=rhs["cost_"+end+"_gbs"]
    bounds = tuple(delta[k] for k in ("lhs_cost_lower_gbs","lhs_cost_upper_gbs","rhs_cost_lower_gbs","rhs_cost_upper_gbs"))
    delta["delta_lhs_minus_rhs_cost_lower_gbs"],delta["delta_lhs_minus_rhs_cost_upper_gbs"]=(
        (bounds[0]-bounds[3],bounds[1]-bounds[2]) if same_window and None not in bounds else (None,None))
    val = delta["delta_lhs_minus_rhs_y_arrival_to_bind_s"]
    delta["bind_direction"] = ("rhs_earlier" if val > tol else "rhs_later" if val < -tol else "within_tolerance"
                               ) if val is not None else "not_scored"
    return delta


def desc(x):
    z=[v for v in x if v is not None]
    return dict(n=len(z),mean=mean(z) if z else None,median=median(z) if z else None,
                min=min(z) if z else None,max=max(z) if z else None)


def paired_summary(rows):
    labels = ("rhs_earlier","rhs_later","within_tolerance","not_scored")
    directions=Counter(x["bind_direction"] for x in rows)
    cross = {s:{k:sum(r["bind_direction"]==k and r["applied_action_changed"] is v for r in rows) for k in labels}
             for s,v in (("first_action_changed",True),("same_first_action",False),("first_action_unconfirmed",None))}
    cost_direction = Counter(
        "rhs_cheaper" if r["delta_lhs_minus_rhs_cost_lower_gbs"] > 0.
        else "rhs_more_expensive" if r["delta_lhs_minus_rhs_cost_upper_gbs"] < 0.
        else "contains_zero"
        for r in rows if r["delta_lhs_minus_rhs_cost_lower_gbs"] is not None)
    return dict(condition=rows[0]["condition"],contrast=rows[0]["contrast"],planned_blocks=len(rows),
                both_attempted_blocks=sum(r["both_attempted"] for r in rows),
                applied_actions_observed_blocks=sum(r["applied_action_changed"] is not None for r in rows),
                action_changed_blocks=sum(r["applied_action_changed"] is True for r in rows),
                action_adherent_blocks=sum(r["forced_action_adherent"] is True for r in rows)
                      if rows[0]["contrast"] == "action" else None,
                directions={k:directions[k] for k in labels},
                adherent_forced_bind_directions={k:sum(r["bind_direction"]==k and r["forced_action_adherent"] is True
                    for r in rows) for k in labels} if rows[0]["contrast"] == "action" else None,
                bind_direction_by_first_action=cross,
                cost_bound_direction={k:cost_direction[k] for k in (
                    "rhs_cheaper","rhs_more_expensive","contains_zero")},
                deltas={m:desc([r["delta_lhs_minus_rhs_"+m] for r in rows])
                        for m in (*METRICS,"cost_lower_gbs","cost_upper_gbs")})


def arm_summary(rows):
    return dict(condition=rows[0]["condition"], arm=rows[0]["arm"], planned=len(rows),
                attempted=sum(r["attempted"] for r in rows),status=dict(Counter(r["status"] for r in rows)),
                signal_observed=sum(r["signal_available"] is not None for r in rows),
                signal_usable=sum(r["signal_available"] is True for r in rows),
                signal_used=sum(r["signal_used"] is True for r in rows),
                same_state_eta_changed=sum(r["same_state_eta_changed"] is True for r in rows),
                same_state_eta_only=sum(r["same_state_eta_changed"] is True and r["same_state_actions_differ"] is False for r in rows),
                same_state_actions_differ=sum(r["same_state_actions_differ"] is True for r in rows),
                same_state_actions_observed=sum(r["same_state_actions_differ"] is not None for r in rows),
                first_delete_observed=sum(r["first_z_delete_applied"] is not None for r in rows),
                first_delete_applied=sum(r["first_z_delete_applied"] is True for r in rows),
                fit_sources=dict(Counter(r["fit_source"] or "unconfirmed" for r in rows)),
                observed_order=dict(Counter(r["observed_order"] or "unknown" for r in rows)),
                y_http_completed=sum(r["y_http_response_s"] is not None for r in rows),
                z_http_completed=sum(r["z_http_response_s"] is not None for r in rows),
                metrics={m:desc([r[m] for r in rows]) for m in (*METRICS,"cost_lower_gbs","cost_upper_gbs")})


def write_csv(path, records):
    fields=list(dict.fromkeys(k for row in records for k in row))
    with path.open("x",newline="") as output:
        writer=csv.DictWriter(output,fieldnames=fields)
        writer.writeheader()
        writer.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,dict) else v for k,v in r.items()} for r in records)


def summarize(plan, ledger):
    tol = num(plan.get("bind_tolerance_s"))
    need(tol is not None and tol >= 0.,"missing sealed bind_tolerance_s")
    need(isinstance(ledger,list), "ledger must have trial rows")
    trials = plan.get("trials")
    need(isinstance(trials,list) and len(trials)==48, "plan must seal 48 trials")
    grid = {}
    for tr in trials:
        tid = tr["trial_id"]
        need(tid not in grid,f"duplicate plan trial {tid!r}")
        grid[tid]=dict(trial_id=tid,block=int(tr["block"]),condition=tr["condition"],arm=alias(tr["arm"]))
    cond = sorted({x["condition"] for x in grid.values()})
    blocks=sorted({x["block"] for x in grid.values()})
    assignments={(p["condition"],p["block"],p["arm"]) for p in grid.values()}
    need(len(cond)==2 and len(blocks)==6 and assignments=={(c,b,a) for c in cond for b in blocks for a in ARMS},
         "must have a complete crossed assignment within each of six blocks")
    actual={}
    for r in ledger:
        tid = r.get("trial_id")
        need(tid in grid,f"result outside sealed plan: {tid!r}")
        need(tid not in actual,f"duplicate ledger trial: {tid!r}")
        g=grid[tid]
        need(int(r.get("block",g["block"]))==g["block"] and r.get("condition",g["condition"])==g["condition"]
             and alias(r.get("arm",g["arm"]))==g["arm"],f"ledger assignment differs from plan for {tid!r}")
        actual[tid]=r
    rows=[derive(g,actual.get(tid),plan) for tid,g in grid.items()]
    pair_index={(r["condition"],r["block"],r["arm"]):r for r in rows}
    pairs=[compare(c,b,contrast,pair_index[c,b,lhs],pair_index[c,b,rhs],tol)
           for c in cond for b in blocks for contrast,(lhs,rhs) in CONTRASTS.items()]
    groups=[arm_summary([pair_index[c,b,a] for b in blocks]) for c in cond for a in ARMS]
    across=[paired_summary([r for r in pairs if r["condition"]==c and r["contrast"]==contrast])
            for c in cond for contrast in CONTRASTS]
    ix=Counter(r["y_uid"] for r in rows if r["y_uid"])
    need(all(n==1 for n in ix.values()),"Y Pod UIDs are not unique across trials")
    status=Counter(r["status"] for r in rows)
    return rows,pairs,dict(planned_trials=len(grid),ledger_rows=len(actual),status=dict(status),paired_blocks=len(blocks),
                           bind_tolerance_s=tol, effect_sign="left minus right; positive means right is earlier for waiting",
                           strata=groups,conditions=across)


def run():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("plan",type=Path)
    p.add_argument("ledger",type=Path)
    p.add_argument("out",type=Path)
    args=p.parse_args()
    plan=json.loads(args.plan.read_text())
    rows,pairs,summary=summarize(plan,load_records(args.ledger))
    ledger_hashes = ({str(p.relative_to(args.ledger)):sha(p)
                      for p in sorted([*args.ledger.glob("trial-*.json"),*args.ledger.glob("*/binding-audit.json")])}
                     if args.ledger.is_dir() else sha(args.ledger))
    summary["inputs_sha256"]={"plan":sha(args.plan),"ledger":ledger_hashes,"analyzer":sha(Path(__file__))}
    args.out.mkdir(exist_ok=False,parents=True)
    write_csv(args.out/"trial_results.csv",rows); write_csv(args.out/"block_pairs.csv",pairs)
    write_csv(args.out/"condition_comparisons.csv",summary["conditions"])
    with (args.out/"study_summary.json").open("x") as out:
        json.dump(summary,out,indent=2,allow_nan=False)


if __name__ == "__main__":
    run()
