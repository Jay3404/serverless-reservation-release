#!/usr/bin/env python3
"""Export offline Kind evidence from authorized local experiment archives.

Export only fields needed to audit experimental assignments, capacity, UID
links, timing, action, and fixed-window cost. No original host logs or full
Kubernetes objects are copied. The HTTP inputs are already normalized and their
byte seals are intentionally retained. Neither evidence nor raw archives is used
as an executable input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from shutil import copyfile


TRIAL = """
arm block calibration capacity_ledger_complete condition cost_lower_gbs
cost_upper_gbs cost_window_end_s cost_window_start_s decision_s errors
first_applied_action first_proposed_action first_retire_applied
first_z_delete_receipt_uid fixed_predicted_return_s fixed_shadow_action
free_gb_at_decision initial_action later_retire_applied order
reservation_intervals reservation_scope reservation_time_basis
reservation_window_complete signal_observer_complete signal_predicted_return_s
signal_shadow_action signal_used status trial_id x_boot_id x_release_hi_s
x_release_lo_s x_request_gb x_retire_intent_s x_signal_event_s
x_signal_received_s x_signal_uid x_signal_used_in_policy x_signal_validated_s x_uid
y_arrival_s y_bind_s y_bind_uid_verified y_bind_witness_id y_create_ack_s y_done_s
y_http_ok y_memory_failure_count y_ready_s y_request_gb y_submit_s y_uid
z_arrival_s z_bind_s z_boot_id z_done_s z_first_delete_ack_s z_first_delete_s
z_first_route_disabled_s z_http_ok z_later_route_disabled_s z_ready_s
z_recreations z_release_hi_s z_release_lo_s z_request_gb z_serving_uid z_submit_s z_uid
"""
BIND_HINT = "another scheduled pod was deleted, and it may make the unscheduled pod schedulable"
SCHEDULER_EVENTS = {
    "Add event for unscheduled pod", "Attempting to schedule pod",
    "Unable to schedule pod; no fit; waiting", "Delete event for scheduled pod",
    "Successfully bound pod to node", "Scheduler cache RemovePod failed", BIND_HINT,
}
DECISION = """
actual_free_gb capacity_snapshot controller_free_input_gb controller_x_still_counted
fixed_predicted_return_s later_memory_fit_failure_witness phase
predicted_returnable_gb proposed_action reason result signal_available
signal_predicted_return_s signal_received_s signal_used signal_validated_s
skip_reason t_s x_remaining_s x_reserved_actual y_pending
"""


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keep(obj, columns):
    return {key: obj[key] for key in columns.split() if key in obj}


def dump(path, obj):
    with path.open("x", encoding="utf-8") as dest:
        json.dump(obj, dest, sort_keys=True, separators=(",", ":"), allow_nan=False)
        dest.write("\n")


def decisions(obj):
    if obj is None:
        return None
    if obj.get("capacity_snapshot") not in (
        None, "prepacked X and Z before Y CREATE",
        "Y and X owned Pod API status; X presence is conservative, not exact cache state",
    ):
        raise ValueError("Unexpected capacity-snapshot label")
    return keep(obj, DECISION)


def minimal_delete(obj):
    payload = obj["response"]
    return {**keep(obj, "submit_s ack_s"), "response": {
        "metadata": keep(payload["metadata"], "uid deletionTimestamp"),
        "spec": keep(payload["spec"], "preemptionPolicy"),
    }}


def minimal_audit(row):
    return {**keep(row, "auditID requestReceivedTimestamp stage stageTimestamp verb"),
            "objectRef": keep(row["objectRef"], "name namespace subresource"),
            "requestObject": {"metadata": keep(row["requestObject"]["metadata"], "uid"),
                              "target": keep(row["requestObject"]["target"], "name")},
            "user": keep(row["user"], "username"),
            "responseStatus": keep(row["responseStatus"], "code")}


def minimal_log(row):
    out = keep(row, "msg ts node event queue")
    for k in ("pod", "deletedPod"):
        if k in row:
            out[k] = keep(row[k], "name namespace") if isinstance(row[k], dict) else row[k]
    if "err" in row:
        out["err"] = " ".join(s for s in ("Insufficient memory", "preemptionPolicy=Never")
                               if s in row["err"])
    return out


def minimal_manifest(path):
    source = read(path)
    spec = source["spec"]
    container, = spec["containers"]
    relevant = {"TERMINATION_MODE", "CLEANUP_S"}
    selected = {"env": [keep(x, "name value") for x in container["env"] if x["name"] in relevant]}
    stop = container.get("lifecycle", {}).get("preStop", {}).get("exec", {}).get("command")
    if stop is not None:
        if stop != ["python", "-B", "-c", "import time;time.sleep(1.6)"]:
            raise ValueError("Unexpected calibrated preStop command")
        # Historical setting only; this command is never executed.
        selected["lifecycle"] = {"preStop": {"exec": {"command": stop}}}
    return {"metadata": keep(source["metadata"], "name namespace"), "spec": {
        **keep(spec, "terminationGracePeriodSeconds schedulerName"), "containers": [selected],
    }}


def extract(row, folder, include_calibration):
    out = keep(row, TRIAL)
    if out["errors"] or row["status"] != "complete":
        raise ValueError("Complete archived fixture receipts are required for this export")
    out["pod_creates"] = {k: keep(v, "name uid create_ack_s create_submit_s")
                          for k, v in row["pod_creates"].items()}
    for k in ("x_delete", "first_z_delete", "later_z_delete"):
        if isinstance(row.get(k), dict):
            out[k] = minimal_delete(row[k])
    out["y_binding"] = keep(row["y_binding"], "scheduler_bind_s")
    if "controller_decisions" in row:
        out["controller_decisions"] = [decisions(x) for x in row["controller_decisions"]]
    if "recheck" in row:
        out["recheck"] = decisions(row["recheck"])
    for target in ("y", "z"):
        body = "pid user_traffic request_id app_id boot_id"
        client = row[f"{target}_client"]
        out[f"{target}_client"] = {"status": client["status"], "body": keep(client["body"], body),
                                   "client_done": keep(client["client_done"], "epoch_ns")}
        out[f"{target}_upstream"] = keep(row[f"{target}_upstream"], body)
    for boundary, clock in (("before", "controller_start"), ("after", "controller_end")):
        out[f"clock_{boundary}"] = {clock: keep(row[f"clock_{boundary}"][clock], "realtime_ns")}
    result = {"trial": out,
              "audit": [minimal_audit(r) for r in read(folder / "binding-audit.json")],
              "scheduler": [minimal_log(r) for r in read(folder / "scheduler-events.json")
                            if r["msg"] in SCHEDULER_EVENTS or r.get("event") == "AssignedPodDelete"]}
    if include_calibration:
        result["x_manifest"] = minimal_manifest(folder / "x-manifest.json")
        event, = [item for item in (json.loads(x) for x in (folder / "signal.jsonl").read_text().splitlines())
                  if item["event"]["event"] == "sigterm_received"]
        result["termination"] = {"event": keep(event["event"], "wall_ns boot_id cleanup_seconds"),
                                 "received": keep(event["received"], "epoch_ns"),
                                 "validated": keep(event["validated"], "epoch_ns"),
                                 "x_uid": event["x_uid"]}
    else:
        spec = read(folder / "y-manifest.json")["spec"]
        result["y_manifests"] = {"scheduler": spec["schedulerName"], "node": spec.get("nodeName"),
                                 "priority": spec["priorityClassName"], "gates": spec.get("schedulingGates"),
                                 "sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                                 "replacement_written": (folder / "zn-manifest.json").is_file()}
    return result


def stage(root, normalized, include_calibration, archived_score=None):
    plan = read(root / "PLAN.json")
    terminal = read(root / "TERMINAL.json")
    if terminal["plan_sha256"] != digest(root / "PLAN.json") or terminal["complete"] != len(plan["trials"]):
        raise ValueError("Original Kind plan and terminal do not match")
    if archived_score:
        known = read(archived_score)["raw_input_sha256"]
        for name, sha in known.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()) or digest(target) != sha:
                raise ValueError("Original Kind evaluation changed after scoring")
    normalized_plan = keep(plan, "phase window_s decision_horizon_s bind_tolerance_s slo_s cluster")
    normalized_plan["trials"] = [keep(x, "trial_id block order condition arm") for x in plan["trials"]]
    receipts, fingerprints = [], {}
    for role in normalized_plan["trials"]:
        ident = role["trial_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
            raise ValueError("Invalid trial identifier")
        file = root / f"trial-{ident}.json"
        data = read(file)
        if any(data[k] != v for k, v in role.items()):
            raise ValueError(f"Original trial role mismatch: {ident}")
        receipts.append(extract(data, root / ident, include_calibration))
        fingerprints[ident] = {"trial": digest(file), **{
            name: digest(root / ident / name) for name in
            ("binding-audit.json", "scheduler-events.json", *(
                ("x-manifest.json", "signal.jsonl") if include_calibration else ("y-manifest.json",)
            ))}}
    dump(normalized, {"schema": "FGCS_KIND_MINIMAL_V1", "plan": normalized_plan, "trials": receipts,
                      "source_hashes": {"plan": digest(root / "PLAN.json"),
                                        "terminal": digest(root / "TERMINAL.json"), "trial": fingerprints}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("baseline", "forced", "http", "prior-a", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    stage(args.baseline / "calibration", args.out / "baseline-calibration.json", True)
    stage(args.baseline / "evaluation", args.out / "baseline-evaluation.json", True,
          args.baseline / "scored" / "scoring_manifest.json")
    stage(args.forced, args.out / "forced-evaluation.json", False)
    seal = args.baseline / "calibration" / "CALIBRATION_SEAL.json"
    if read(args.baseline / "evaluation" / "PLAN.json")["seal_sha256"] != digest(seal):
        raise ValueError("Original Kind calibration seal mismatch")
    dump(args.out / "baseline-calibration-seal.json", keep(read(seal),
         "usable_n calibration_plan_n admitted calibration fixed_s residual_s x_cleanup_s prestop_slow_s"))
    for key in ("calibration", "evaluation", "confirmation"):
        copyfile(args.http / f"{key}_normalized_v3.json", args.out / f"http-{key}.json")
    copyfile(args.http / "EXPERIMENT_CONTRACT_KO.md", args.out / "http-contract.md")
    copyfile(args.prior_a, args.out / "http-prior-a.json")


if __name__ == "__main__":
    main()
