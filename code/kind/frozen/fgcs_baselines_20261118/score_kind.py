#!/usr/bin/env python3
"""Score the separate four-arm Kind baseline campaign, never the old forced-action arms."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import runpy

ARMS = ("kubernetes", "instant", "fixed", "signal")
CONDITIONS = ("fast", "slow")
WINDOW_S = 8.5
PREDICTION_HORIZON_S = 1.
BIND_TOLERANCE_S = .05
METRICS = (
    "y_arrival_to_bind_s", "y_submit_to_bind_s", "y_arrival_to_ready_s",
    "y_http_response_s", "z_http_response_s", "z_delete_api_count",
    "z_recreations", "http_completed_by_window", "http_completed_observed",
)
PREVIOUS_ANALYZER = Path(__file__).resolve().parents[1] / "fgcs_retirement_action_20261117" / "analyze_kind.py"
PREVIOUS_ANALYZER_SHA = "1fc89a0945288b80a564cee2e93f1032333e6fbf3c6c8254e6dd27c830cb8a28"


def load(path):
    return json.loads(path.read_text())


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


if checksum(PREVIOUS_ANALYZER) != PREVIOUS_ANALYZER_SHA:
    raise ValueError("immutable previous analyzer SHA-256 mismatch")
_old = runpy.run_path(str(PREVIOUS_ANALYZER))
need, num, elapsed, desc, write_csv, fit_witness, check_runner_cost = (
    _old[k] for k in ("need", "num", "elapsed", "desc", "write_csv", "fit_witness", "check_runner_cost")
)


def valid_timestamp(v):
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def close(a, b, atol=1e-6):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=0, abs_tol=atol)


def checked_plan(plan):
    need(plan.get("phase") == "evaluation", "not a new physical evaluation campaign")
    need(num(plan.get("window_s")) == WINDOW_S and num(plan.get("decision_horizon_s")) == PREDICTION_HORIZON_S,
         "cost window or decision horizon differs from the sealed contract")
    need(isinstance(plan.get("trials"), list), "missing sealed trial grid")
    trials = plan["trials"]
    need(len(trials) == 48 and all(isinstance(t, dict) for t in trials), "expected 48 assignments")
    need(num(plan.get("bind_tolerance_s")) == BIND_TOLERANCE_S, "wrong Bind descriptive tolerance")
    ids, blocks, assignments = set(), set(), set()
    for t in trials:
        i, b = t["trial_id"], t["block"]
        need(isinstance(i, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", i)) and i not in ids, "invalid/duplicate trial ID")
        need(type(b) is int, "block must be integer")
        ids.add(i)
        blocks.add(b)
        assignments.add((t["condition"], b, t["arm"]))
    need(len(blocks) == 6 and assignments == {(c, b, a) for c in CONDITIONS for b in blocks for a in ARMS},
         "each of six blocks must contain the two conditions crossed with all four arms")
    for b in blocks:
        order = [t["order"] for t in trials if t["block"] == b]
        need(len(set(order)) == 8 and all(type(x) is int for x in order), "invalid within-block order")
    return trials


def load_trial_records(root, planned, checksums):
    found, uids = {}, set()
    for p in sorted(root.glob("trial-*.json")):
        v = load(p)
        i = v["trial_id"]
        need(i in planned and i not in found, f"unknown/duplicate trial {i}")
        t = planned[i]
        need(all(v.get(k) == t[k] for k in ("condition", "arm", "block", "order")), f"assignment mismatch {i}")
        need(v.get("status") in {"complete", "incomplete"}, f"unrecognized terminal status {i}")
        for obj in (v.get("pod_creates") or {}).values():
            u = obj.get("uid")
            need(isinstance(u, str) and u and u not in uids, f"missing/nonunique Pod UID in {i}")
            uids.add(u)
        checksums[p.name] = checksum(p)
        found[i] = v
    return found, uids


def read_receipt(root, tid, name, checksums):
    p = root / tid / name
    if not p.is_file():
        return None
    checksums[str(p.relative_to(root))] = checksum(p)
    v = load(p)
    need(isinstance(v, list), f"invalid receipt {tid}/{name}")
    return v


def independent_bind(raw, audit, logs, problems):
    b = num(raw.get("y_bind_s"))
    if raw.get("y_bind_uid_verified") is not True or b is None:
        return None, "not_claimed"
    if audit is None or logs is None:
        return None, "missing_raw_bind_receipt"
    v = dict(raw)
    # Require exactly one joined API ResponseComplete with the expected UID,
    # node and timestamp, and independently rejoin the scheduler success log.
    py = (v.get("pod_creates") or {}).get("y") or {}
    uid, name = py.get("uid"), py.get("name")
    evidence = v.get("y_binding") or {}
    witness = v.get("y_bind_witness_id")
    matched = [
        e for e in audit if uid and witness and e.get("auditID") == witness
        and e.get("verb") == "create" and e.get("stage") == "ResponseComplete"
        and e.get("objectRef", {}).get("name") == name
        and e.get("objectRef", {}).get("subresource") == "binding"
        and e.get("requestObject", {}).get("metadata", {}).get("uid") == uid
        and e.get("requestObject", {}).get("target", {}).get("name")
        and e.get("responseStatus", {}).get("code") in (200, 201, 202)
    ]
    if len(matched) != 1 or uid != v.get("y_uid") or not close(valid_timestamp(matched[0].get("stageTimestamp")), b):
        problems.append("raw_y_bind_audit_mismatch")
        return None, "audit_mismatch"
    if not any(
        e.get("msg") == "Successfully bound pod to node"
        and ref_matches(e.get("pod"), matched[0]["objectRef"].get("namespace"), name)
        and (not e.get("node") or e.get("node") == matched[0]["requestObject"]["target"]["name"])
        and close(num(e.get("ts")), num(evidence.get("scheduler_bind_s")))
        for e in logs
    ):
        problems.append("raw_y_scheduler_bind_mismatch")
        return None, "scheduler_mismatch"
    return b, "uid_api_and_scheduler_match"


def ref_matches(ref, namespace, name):
    return (ref == f"{namespace}/{name}" or
            isinstance(ref, dict) and ref.get("namespace") == namespace and ref.get("name") == name)


def raw_scheduler_events(logs, v):
    name = ((v.get("pod_creates") or {}).get("y") or {}).get("name")
    if logs is None or not name:
        return None, None
    matches = [e for e in logs if isinstance(e.get("pod"), dict) and e["pod"].get("name") == name
               or isinstance(e.get("pod"), str) and e["pod"].rsplit("/", 1)[-1] == name]
    memory = sum("Insufficient memory" in str(e.get("err", "")) and
                 e.get("msg") == "Unable to schedule pod; no fit; waiting" for e in matches)
    attempts = sum(e.get("msg") == "Attempting to schedule pod" for e in matches)
    return memory, attempts


def raw_cache_bracket(logs, v, key):
    """Rejoin the log pair on exact created Pod names; these are bounds, not release timestamps."""
    births = v.get("pod_creates") or {}
    name = (births.get(key) or {}).get("name")
    dst = {(births.get(k) or {}).get("name") for k in ("y", "zn")} - {None}
    if logs is None or not name:
        return None, None

    def pod_name(ref):
        return ref.get("name") if isinstance(ref, dict) else ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None

    deleted = [e for e in logs if pod_name(e.get("pod")) == name]
    if any(e.get("msg") == "Scheduler cache RemovePod failed" for e in deleted):
        return None, None
    starts = [num(e.get("ts")) for e in deleted if e.get("msg") == "Delete event for scheduled pod"]
    if len(starts) != 1 or starts[0] is None:
        return None, None
    lo = starts[0]
    hints = [e["ts"] for e in logs if e.get("msg") == "another scheduled pod was deleted, and it may make the unscheduled pod schedulable"
             and pod_name(e.get("deletedPod")) == name and pod_name(e.get("pod")) in dst
             and num(e.get("ts")) is not None and e["ts"] >= lo]
    return lo, min(hints, default=None)


def uid_delete(row, phase, end, problems):
    prefix = "first" if phase == "first" else "later"
    actual = row.get(prefix + "_retire_applied")
    receipt = row.get(prefix + "_z_delete")
    if not isinstance(receipt, dict):
        related_error = any("Z DELETE" in str(e) for e in row.get("errors", []))
        if actual is True or related_error:
            problems.append("unresolved_" + prefix + "_z_delete")
            return None
        return False if actual is False else None
    ti, ta = num(receipt.get("submit_s")), num(receipt.get("ack_s"))
    meta = (receipt.get("response") or {}).get("metadata") or {}
    if None in (ti, ta, end) or ti > ta or not row.get("z_uid") or row["z_uid"] != meta.get("uid") or not meta.get("deletionTimestamp"):
        problems.append("unverified_" + prefix + "_z_delete")
        return None
    if actual is not True:
        problems.append("applied_flag_conflicts_with_" + prefix + "_z_delete_receipt")
        return None
    return ti <= end


def http_score(v, key, end, problems):
    q = v.get(key + "_client") or {}
    code = q.get("status")
    t, a = num(v.get(key + "_done_s")), num(v.get(key + "_arrival_s"))
    if not q:
        return ("timeout" if any("timeout" in str(e).lower().replace(" ", "") for e in v.get("errors", []))
                else "unscored"), None
    if code != 200:
        body = (str(q.get("body", "")) + " " + str(q.get("error", ""))).lower()
        return ("timeout" if code in (408, 504) or "timeout" in body or "timed out" in body else "http_failure"), None
    body, upstream = q.get("body"), v.get(key + "_upstream")
    pod = v.get("pod_creates") or {}
    initial_z, replacement_z = (pod.get("z") or {}).get("uid"), (pod.get("zn") or {}).get("uid")
    served = v.get("z_serving_uid")
    linked = bool(
        isinstance(body, dict) and body == upstream and body.get("pid") == 1 and body.get("user_traffic") is True
        and body.get("request_id") == v["trial_id"] + "-" + key and body.get("app_id") == (1 if key == "y" else 2)
        and body.get("boot_id")
    )
    if key == "y":
        linked = linked and bool(v.get("y_uid")) and (pod.get("y") or {}).get("uid") == v["y_uid"]
    else:
        linked = linked and bool(served) and (
            (served == initial_z and body.get("boot_id") == v.get("z_boot_id")) or
            (served == replacement_z and body.get("boot_id") != v.get("z_boot_id"))
        )
    client_done_ns = num((q.get("client_done") or {}).get("epoch_ns"))
    linked = linked and client_done_ns is not None and close(t, client_done_ns / 1e9)
    delay = elapsed(t, a, problems, key + "_http_response")
    if not linked or delay is None or end is None:
        problems.append("unverified_" + key + "_http_200")
        return "unscored", None
    return ("completed_by_window" if t <= end else "completed_after_window"), delay


def decisions(v, problems):
    raw = v.get("controller_decisions")
    if isinstance(raw, dict):
        raw = [dict(r, phase=k) for k, r in raw.items() if isinstance(r, dict)]
    out = {}
    for d in raw or []:
        if not isinstance(d, dict) or d.get("phase") not in ("first", "recheck") or d["phase"] in out:
            problems.append("malformed_or_duplicate_controller_phase")
            continue
        out[d["phase"]] = d
    return out


def score_trial(grid, raw, audit=None, logs=None):
    v = raw or {}
    p = []
    r = dict(grid, status=v.get("status", "not_run"), attempted=raw is not None)
    r["raw_run_errors"] = json.dumps(v.get("errors") or [])
    start, end = num(v.get("cost_window_start_s")), num(v.get("cost_window_end_s"))
    intent, a, s = num(v.get("x_retire_intent_s")), num(v.get("y_arrival_s")), num(v.get("y_submit_s"))
    r.update(window_start_s=start, window_end_s=end, x_retire_intent_s=intent)
    window_ok = close(start, intent) and end is not None and close(end - start, WINDOW_S)
    if raw and not window_ok:
        p.append("unverified_8_5s_cost_window")
    b, r["bind_evidence"] = independent_bind(v, audit, logs, p)
    for k in ("x_uid", "y_uid", "z_uid", "z_serving_uid", "y_bind_witness_id"):
        r[k] = v.get(k)
    r.update(y_arrival_s=a, y_submit_s=s, y_bind_s=b,
             y_bind_inside_window=b <= end if b is not None and end is not None else None)
    create = ((v.get("pod_creates") or {}).get("y") or {})
    r["y_create_uid_joined"] = (bool(v.get("y_uid")) and v["y_uid"] == create.get("uid")
        and close(s, num(create.get("create_submit_s"))) and num(create.get("create_ack_s")) is not None) if create else None
    r["y_arrival_to_submit_s"] = elapsed(s, a, p, "y_arrival_to_submit")
    r["y_submit_to_bind_s"] = elapsed(b, s, p, "y_submit_to_bind")
    r["y_arrival_to_bind_s"] = elapsed(b, a, p, "y_arrival_to_bind") if r["y_submit_to_bind_s"] is not None else None
    ready = num(v.get("y_ready_s"))
    r["y_arrival_to_ready_s"] = elapsed(ready, a, p, "y_arrival_to_ready") if b is not None and ready is not None and ready >= b else None
    for key in ("y", "z"):
        status, value = http_score(v, key, end if window_ok else None, p) if raw else ("not_run", None)
        r[key + "_arrival_s"] = num(v.get(key + "_arrival_s"))
        r[key + "_http_done_s"] = num(v.get(key + "_done_s"))
        r[key + "_http_status"], r[key + "_http_response_s"] = status, value
    r["http_completed_observed"] = (sum(r[k + "_http_response_s"] is not None for k in ("y", "z")) if raw else None)
    r["http_completed_by_window"] = (sum(r[k + "_http_status"] == "completed_by_window" for k in ("y", "z")) if raw else None)

    phases = decisions(v, p)
    for phase in ("first", "recheck"):
        d = phases.get(phase, {})
        for key in ("t_s", "proposed_action", "actual_free_gb", "predicted_returnable_gb", "x_remaining_s",
                    "x_reserved_actual", "y_pending", "signal_used", "signal_available", "signal_received_s",
                    "signal_validated_s", "skip_reason", "controller_free_input_gb", "controller_x_still_counted",
                    "capacity_snapshot", "fixed_predicted_return_s", "signal_predicted_return_s"):
            r[phase + "_" + key] = d.get(key)
        r[phase + "_ledger_present"] = phase in phases
        event_times = [num(v.get("x_retire_intent_s")), num(v.get("x_signal_event_s")),
                       num(v.get("x_signal_received_s")), num(v.get("x_signal_validated_s")), num(d.get("t_s"))]
        available = bool(
            v.get("x_uid") and v.get("x_signal_uid") == v["x_uid"] and
            None not in event_times and event_times == sorted(event_times))
        r[phase + "_signal_available_rechecked"] = available if d else None
        if d.get("signal_available") is not None and bool(d["signal_available"]) != available:
            p.append(phase + "_signal_cutoff_mismatch")
        if d.get("signal_used") is True and (not available or grid["arm"] != "signal"):
            p.append(phase + "_invalid_signal_use")
        instant_before_pending = (
            grid["arm"] == "instant" and d.get("x_reserved_actual") is True and
            (d.get("y_pending") is True or phase == "first" and d.get("y_pending") is None)
        )
        if instant_before_pending and d.get("proposed_action") in ("wait", "reclaim"):
            if num(d.get("x_remaining_s")) != 0.:
                p.append(phase + "_instant_remaining_nonzero")
            if None not in (num(d.get("actual_free_gb")), num(d.get("predicted_returnable_gb")), num(v.get("y_request_gb"))):
                fits = float(d["actual_free_gb"]) + float(d["predicted_returnable_gb"]) >= float(v["y_request_gb"]) - 1e-9
                if fits and d["proposed_action"] == "reclaim":
                    p.append(phase + "_instant_reclaims_despite_predicted_fit")
    r["first_delete_confirmed"], r["recheck_delete_confirmed"] = (
        uid_delete(v, phase, end, p) for phase in ("first", "recheck"))
    for phase, prefix in (("first", "first"), ("recheck", "later")):
        receipt = v.get(prefix + "_z_delete") or {}
        r[phase + "_z_delete_submit_s"], r[phase + "_z_delete_ack_s"] = (
            num(receipt.get("submit_s")), num(receipt.get("ack_s")))
    delete_count = [r["first_delete_confirmed"], r["recheck_delete_confirmed"]]
    r["z_delete_api_count"] = sum(delete_count) if None not in delete_count else None
    r["z_route_disabled_seen"] = (any(num(v.get(k)) is not None and num(v.get(k)) <= end for k in
                                   ("z_first_route_disabled_s", "z_later_route_disabled_s")) if end is not None else None)
    if grid["arm"] == "kubernetes" and (any(x is True for x in delete_count) or r["z_route_disabled_seen"]):
        p.append("kubernetes_arm_additional_reclamation")
    if r["z_http_response_s"] is not None:
        r["z_recreations"] = int(v.get("z_serving_uid") == ((v.get("pod_creates") or {}).get("zn") or {}).get("uid"))
    else:
        r["z_recreations"] = None
    r["z_replacement_created"] = bool(((v.get("pod_creates") or {}).get("zn") or {}).get("uid")) if raw else None

    memfail, attempts = raw_scheduler_events(logs, v)
    r.update(y_memory_no_fit_events=memfail, y_scheduler_attempts=attempts)
    input_free, expected, actual_free, need_gb, x_gb = [
        num(q) for q in (r["first_controller_free_input_gb"], r["first_predicted_returnable_gb"],
                         r["first_actual_free_gb"], v.get("y_request_gb"), v.get("x_request_gb"))]
    r["instant_pending_fit_witness"] = bool(
        r["first_x_reserved_actual"] is True and r["first_x_remaining_s"] == 0.
        and None not in (input_free, expected, actual_free, need_gb, x_gb)
        and actual_free < need_gb and close(expected, x_gb) and input_free + expected >= need_gb
        and r["first_proposed_action"] == "wait" and r["first_delete_confirmed"] is False
        and (memfail or 0) >= 1 and (attempts or 0) >= 2 and b is not None
    ) if grid["arm"] == "instant" and raw else None
    final_v = dict(v, y_bind_s=b, first_z_delete_applied=r["first_delete_confirmed"],
                   later_z_delete_applied=r["recheck_delete_confirmed"])
    r["x_release_lo_s"], r["x_release_hi_s"], r["z_release_lo_s"], r["z_release_hi_s"] = [
        num(v.get(f"{k}_release_{bound}_s")) for k in ("x", "z") for bound in ("lo", "hi")]
    for key in ("x", "z"):
        names = (key + "_release_lo_s", key + "_release_hi_s")
        endpoints = tuple(r[k] for k in names)
        checked = raw_cache_bracket(logs, v, key)
        matches = all((a is None and b is None) or close(a, b) for a, b in zip(endpoints, checked))
        r[key + "_release_log_match"] = matches if logs is not None and raw else None
        if logs is not None and raw and not matches:
            p.append(key + "_release_log_mismatch")
        if not matches:
            final_v.update({n: None for n in names})
    r["fit_source"], r["fit_lo_s"], r["fit_hi_s"] = (
        fit_witness(final_v, p) if b is not None and memfail is not None and memfail >= 1 else (None, None, None))
    xl, xu, zl, zu = (r[k] for k in ("x_release_lo_s", "x_release_hi_s", "z_release_lo_s", "z_release_hi_s"))
    r["release_order_both_observed"] = (
        ("X_first" if xu < zl else "Z_first" if zu < xl else "overlap_or_tie")
        if None not in (xl, xu, zl, zu) and xl <= xu and zl <= zu
        and r["x_release_log_match"] and r["z_release_log_match"] else None)

    r["reservation_scope"], r["reservation_time_basis"] = v.get("reservation_scope"), v.get("reservation_time_basis")
    cl, cu = num(v.get("cost_lower_gbs")), num(v.get("cost_upper_gbs"))
    r["reservation_integral_verified"] = (
        check_runner_cost(v, cl, cu, p) is True if window_ok and v.get("reservation_window_complete") is True
        and bool(r["reservation_scope"]) and bool(r["reservation_time_basis"]) and None not in (cl, cu) and 0 <= cl <= cu
        else False if raw else None)
    r["cost_lower_gbs"], r["cost_upper_gbs"] = (cl, cu) if r["reservation_integral_verified"] else (None, None)
    r["anomalies"] = ";".join(p)
    return r


def compare(sig, base):
    r = dict(condition=sig["condition"], block=sig["block"], baseline_arm=base["arm"],
             signal_trial_id=sig["trial_id"], baseline_trial_id=base["trial_id"])
    columns = (
        "status", "first_proposed_action", "recheck_proposed_action", "first_delete_confirmed",
        "recheck_delete_confirmed", "fit_source", "release_order_both_observed", "y_http_status",
        "z_http_status", "y_memory_no_fit_events", "y_scheduler_attempts", "first_signal_used",
        "recheck_signal_used", "z_replacement_created", *METRICS, "cost_lower_gbs", "cost_upper_gbs",
    )
    for k in columns:
        r["signal_" + k], r["baseline_" + k] = sig[k], base[k]
    for k in METRICS:
        x, y = sig[k], base[k]
        r["delta_signal_minus_baseline_" + k] = x - y if x is not None and y is not None else None
    for phase in ("first", "recheck"):
        k = phase + "_delete_confirmed"
        r[phase + "_applied_action_changed"] = sig[k] != base[k] if None not in (sig[k], base[k]) else None
        k = phase + "_proposed_action"
        r[phase + "_proposed_action_changed"] = sig[k] != base[k] if None not in (sig[k], base[k]) else None
    same_scope = all(sig[k] and sig[k] == base[k] for k in ("reservation_scope", "reservation_time_basis"))
    sl, su, bl, bu = (x[k] for x in (sig, base) for k in ("cost_lower_gbs", "cost_upper_gbs"))
    dlo, dhi = (sl - bu, su - bl) if same_scope and None not in (sl, su, bl, bu) else (None, None)
    r["delta_signal_minus_baseline_cost_lower_gbs"], r["delta_signal_minus_baseline_cost_upper_gbs"] = dlo, dhi
    r["cost_direction"] = ("signal_cost_lower" if dhi < 0. else "signal_cost_higher" if dlo > 0. else "bounds_include_zero"
                           ) if dlo is not None else "not_scored"
    d = r["delta_signal_minus_baseline_y_arrival_to_bind_s"]
    r["bind_direction"] = ("signal_earlier" if d < 0 else "signal_later" if d > 0 else "identical") if d is not None else "not_scored"
    r["bind_direction_50ms_descriptive"] = ("signal_earlier" if d < -BIND_TOLERANCE_S else
        "signal_later" if d > BIND_TOLERANCE_S else "within_50ms") if d is not None else "not_scored"
    return r


def table(rows, pairs):
    groups, comparisons = [], []
    for condition in CONDITIONS:
        for arm in ARMS:
            part = [r for r in rows if r["condition"] == condition and r["arm"] == arm]
            acc = dict(condition=condition, arm=arm, planned=len(part), attempted=sum(r["attempted"] for r in part),
                       complete=sum(r["status"] == "complete" for r in part),
                       bind_scored=sum(r["y_arrival_to_bind_s"] is not None for r in part),
                       bind_after_cost_window=sum(r["y_bind_inside_window"] is False for r in part),
                       instant_pending_fit_witnesses=sum(r["instant_pending_fit_witness"] is True for r in part),
                       cost_verified=sum(r["reservation_integral_verified"] is True for r in part),
                       y_http_outcomes=dict(Counter(r["y_http_status"] for r in part)),
                       z_http_outcomes=dict(Counter(r["z_http_status"] for r in part)),
                       fit_sources=dict(Counter(r["fit_source"] or "unconfirmed" for r in part)))
            for m in (*METRICS, "cost_lower_gbs", "cost_upper_gbs"):
                summary = desc(r[m] for r in part)
                acc.update({m + "_" + k: v for k, v in summary.items()})
            groups.append(acc)
        for base in ARMS[:-1]:
            part = [r for r in pairs if r["condition"] == condition and r["baseline_arm"] == base]
            acc = dict(condition=condition, baseline_arm=base, planned_pairs=len(part),
                       bind_direction=dict(Counter(r["bind_direction"] for r in part)),
                       bind_direction_50ms_descriptive=dict(Counter(r["bind_direction_50ms_descriptive"] for r in part)),
                       cost_direction=dict(Counter(r["cost_direction"] for r in part)),
                       first_action_changed=sum(r["first_applied_action_changed"] is True for r in part),
                       recheck_action_changed=sum(r["recheck_applied_action_changed"] is True for r in part))
            for m in (*METRICS, "cost_lower_gbs", "cost_upper_gbs"):
                summary = desc(r["delta_signal_minus_baseline_" + m] for r in part)
                acc.update({"delta_signal_minus_baseline_" + m + "_" + k: v for k, v in summary.items()})
            comparisons.append(acc)
    return groups, comparisons


def score(root, calibration=None):
    plan_path, terminal_path = root / "PLAN.json", root / "TERMINAL.json"
    plan = load(plan_path)
    trials = checked_plan(plan)
    code_hash = checksum(Path(__file__))
    scoring_hash = checksum(Path(__file__).with_name("SCORING.md"))
    source = plan.get("source_hashes") or {}
    need(source.get("score_kind.py") == code_hash and source.get("SCORING.md") == scoring_hash,
         "evaluation plan does not pin these scoring inputs")
    planned = {t["trial_id"]: t for t in trials}
    psha = checksum(plan_path)
    checksums = {"PLAN.json": psha}
    terminal = load(terminal_path) if terminal_path.is_file() else None
    if terminal is not None:
        checksums["TERMINAL.json"] = checksum(terminal_path)
        need(terminal.get("plan_sha256") == psha and terminal.get("planned") == 48, "terminal/plan mismatch")
    actual, uids = load_trial_records(root, planned, checksums)
    provenance = None
    if calibration is not None:
        cal_plan, seal_path = calibration / "PLAN.json", calibration / "CALIBRATION_SEAL.json"
        if not seal_path.is_file():
            seal_path = calibration / "MODEL_SEAL.json"
        cal_ids = {t["trial_id"]: t for t in load(cal_plan)["trials"]}
        need(not set(cal_ids) & set(planned), "calibration and evaluation trial IDs overlap")
        cal_uids = {
            pod["uid"] for f in calibration.glob("trial-*.json")
            for pod in (load(f).get("pod_creates") or {}).values() if pod.get("uid")
        }
        need(not cal_uids & uids, "calibration and evaluation Pod UIDs overlap")
        seal_sha = checksum(seal_path)
        seal = load(seal_path)
        need(plan.get("seal_sha256") == seal_sha and plan.get("model") == seal and seal.get("admitted") is True,
             "evaluation plan calibration seal mismatch or failed admission")
        provenance = dict(seal_sha256=seal_sha, calibration_plan_sha256=checksum(cal_plan),
                          calibration_uids=len(cal_uids), evaluation_uids=len(uids), no_overlap=True)
    if terminal is not None:
        need(terminal.get("attempted") == len(actual) and terminal.get("complete") ==
             sum(r.get("status") == "complete" for r in actual.values()), "terminal/ledger denominator mismatch")
    rows = [score_trial(t, actual.get(t["trial_id"]),
                        read_receipt(root, t["trial_id"], "binding-audit.json", checksums),
                        read_receipt(root, t["trial_id"], "scheduler-events.json", checksums)) for t in trials]
    keys = {(r["condition"], r["block"], r["arm"]): r for r in rows}
    pairs = [compare(keys[c, b, "signal"], keys[c, b, base])
             for c in CONDITIONS for b in sorted({t["block"] for t in trials}) for base in ARMS[:-1]]
    groups, comparisons = table(rows, pairs)
    manifest = dict(protocol="SCORING.md", scoring_protocol_sha256=scoring_hash,
                    plan_sha256=psha, runner_terminal=terminal,
                    scorer_sha256=code_hash, prior_analyzer_sha256=PREVIOUS_ANALYZER_SHA,
                    raw_input_sha256=checksums, calibration=provenance,
                    n_planned=48, n_attempted=len(actual), n_pairs_planned=len(pairs),
                    n_bind_scored=sum(r["y_arrival_to_bind_s"] is not None for r in rows),
                    n_rows_with_anomalies=sum(bool(r["anomalies"]) for r in rows))
    return rows, pairs, groups, comparisons, manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evaluation", type=Path, required=True)
    p.add_argument("--calibration", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    rows, pairs, groups, comparisons, manifest = score(a.evaluation, a.calibration)
    a.output.mkdir(parents=True, exist_ok=False)
    for name, values in (("trial_scores", rows), ("block_pairs", pairs), ("arm_results", groups), ("pair_results", comparisons)):
        write_csv(a.output / (name + ".csv"), values)
    with (a.output / "scoring_manifest.json").open("x") as dest:
        json.dump(manifest, dest, indent=2, sort_keys=True, allow_nan=False)
        dest.write("\n")
    print(json.dumps({k: v for k, v in manifest.items() if k.startswith("n_")}, sort_keys=True))


if __name__ == "__main__":
    main()
