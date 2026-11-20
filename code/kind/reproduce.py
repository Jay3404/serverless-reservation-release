#!/usr/bin/env python3
"""Reproduce the manuscript's Kind analyses from verified offline evidence.

This script does not create a cluster, submit a resource, issue a request, or
extrapolate response times for an unobserved action. Input records are normalized
exports of the completed trials. See README.md for the evidence boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import runpy

from build_evidence import BIND_HINT, digest, read


FROZEN = Path(__file__).resolve().parent / "frozen"
BASE = FROZEN / "fgcs_baselines_20261118" / "score_kind.py"
LEGACY = FROZEN / "fgcs_retirement_action_20261117" / "analyze_kind.py"
REPLAY = FROZEN / "fgcs_configaware_20261119" / "replay_config.py"
HTTP = FROZEN / "platform_return_20261110" / "heldout_models.py"


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def timestamp(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def same_ref(ref, ns, name):
    return (ref.get("namespace") == ns and ref.get("name") == name
            if isinstance(ref, dict) else ref == f"{ns}/{name}")


def stage(path, phase, arms, blocks):
    data = read(path)
    plan, records = data["plan"], data["trials"]
    assignments, seen, uids = {}, {}, set()
    grid = {(block, condition, arm) for block in range(1, blocks + 1)
            for condition in ("fast", "slow") for arm in arms}
    need(data["schema"] == "FGCS_KIND_MINIMAL_V1" and plan["phase"] == phase
         and len(plan["trials"]) == len(records) == len(grid), f"{path.name}: trial denominator")
    for role in plan["trials"]:
        trial_id = role["trial_id"]
        configs = role["block"], role["condition"], role["arm"]
        need(trial_id not in assignments and configs in grid, f"{trial_id}: unmatched assignment")
        assignments[trial_id] = role
        grid.remove(configs)
    need(not grid, f"{path.name}: incomplete condition crossing")
    for record in records:
        row = record["trial"]
        name = row["trial_id"]
        need(name not in seen and all(row[k] == val for k, val in assignments[name].items())
             and row["status"] == "complete" and not row["errors"]
             and row["calibration"] is (phase == "calibration"), f"{name}: trial receipt")
        seen[name] = record
        for label, pod in row["pod_creates"].items():
            uid = pod["uid"]
            need(uid and uid not in uids, f"{name}: reused Pod UID")
            need(label not in ("x", "y", "z") or uid == row[f"{label}_uid"], f"{name}: inconsistent Pod UID")
            uids.add(uid)
    return plan, [seen[role["trial_id"]] for role in plan["trials"]], uids


def configuration_witness(record, plan, scores, replay):
    row = record["trial"]
    event = record["termination"]
    src, rec, val = (event["event"]["wall_ns"] / 1e9, event["received"]["epoch_ns"] / 1e9,
                     event["validated"]["epoch_ns"] / 1e9)
    name = row["trial_id"]
    t0 = row["x_retire_intent_s"]
    need(event["event"]["cleanup_seconds"] == replay["X_CLEANUP"]
         and event["event"]["boot_id"] == row["x_boot_id"]
         and event["x_uid"] == row["x_signal_uid"] == row["x_uid"]
         and t0 <= src <= rec <= val
         and (src, rec, val) == (row["x_signal_event_s"], row["x_signal_received_s"], row["x_signal_validated_s"]),
         f"{name}: terminated Pod identity or signal timestamps")
    x_manifest = record["x_manifest"]
    setting = replay["config_from_manifest"](x_manifest)
    need(setting == replay["CONDITIONS"][row["condition"]]
         and x_manifest["metadata"]["name"] == row["pod_creates"]["x"]["name"]
         and x_manifest["metadata"]["namespace"] == plan["cluster"], f"{name}: X configuration")
    for key in ("x", "z"):
        bound = scores["raw_cache_bracket"](record["scheduler"], row, key)
        need(bound == (row[f"{key}_release_lo_s"], row[f"{key}_release_hi_s"]), f"{name}: release bracket")
    b, witness = scores["independent_bind"](row, record["audit"], record["scheduler"], [])
    need(b is not None and witness == "uid_api_and_scheduler_match", f"{name}: Bind evidence")
    need(scores["raw_scheduler_events"](record["scheduler"], row)[0] == row["y_memory_failure_count"] >= 1
         and src + replay["X_CLEANUP"] <= row["x_release_lo_s"] <= row["x_release_hi_s"],
         f"{name}: scheduler fit/release witness")
    return dict(row, _prestop_s=setting)


def baseline(evidence, out, scores):
    replay = runpy.run_path(str(REPLAY))
    cp, calibration, cal_uids = stage(evidence / "baseline-calibration.json", "calibration", ("force_reclaim",), 4)
    cal_ids = {x["trial"]["trial_id"] for x in calibration}
    rows = [configuration_witness(r, cp, scores, replay) for r in calibration]
    cal, pooled, residual, configured = replay["calibrate"](rows, read(evidence / "baseline-calibration-seal.json"))
    # Held-out events are not loaded until all forecaster constants are fit.
    ep, evaluation, eval_uids = stage(evidence / "baseline-evaluation.json", "evaluation", scores["ARMS"], 6)
    parsed = [configuration_witness(r, ep, scores, replay) for r in evaluation]
    need(not cal_uids.intersection(eval_uids) and not cal_ids.intersection(r["trial_id"] for r in parsed)
         and max(r["clock_after"]["controller_end"]["realtime_ns"] for r in rows)
         < min(r["clock_before"]["controller_start"]["realtime_ns"] for r in parsed),
         "calibration/evaluation overlap or chronology")
    need(ep["window_s"] == scores["WINDOW_S"]
         and ep["decision_horizon_s"] == scores["PREDICTION_HORIZON_S"]
         and ep["bind_tolerance_s"] == scores["BIND_TOLERANCE_S"], "experimental comparison contract")

    trial_scores = [scores["score_trial"](role, rec["trial"], rec["audit"], rec["scheduler"])
                    for role, rec in zip(ep["trials"], evaluation)]
    trial_scores_index = {(r["condition"], r["block"], r["arm"]): r for r in trial_scores}
    pairs = [scores["compare"](trial_scores_index[c, b, "signal"], trial_scores_index[c, b, a])
             for c in scores["CONDITIONS"] for b in range(1, 7) for a in scores["ARMS"][:-1]]
    grouped, comparisons = scores["table"](trial_scores, pairs)
    need(len(pairs) == 36 and all(r["y_arrival_to_bind_s"] is not None and r["http_completed_by_window"] == 2
                                  and r["reservation_integral_verified"] and not r["anomalies"] for r in trial_scores),
         "baseline result has unverified trial results")
    base = out / "baseline"
    base.mkdir()
    for tag, rs in (("trial_scores", trial_scores), ("block_pairs", pairs), ("arm_results", grouped), ("pair_results", comparisons)):
        scores["write_csv"](base / f"{tag}.csv", rs)
    first, second = replay["replay"](parsed, pooled, residual, configured)
    results = replay["summarize"](cal, pooled, residual, configured, first, second)
    subset = out / "replay"
    subset.mkdir()
    for name, table in (("calibration_by_config", cal), ("first_decisions", first), ("rechecks", second)):
        replay["put_csv"](subset / f"{name}.csv", table)
    replay["put_json"](subset / "summary.json", results)
    return results


def wait_path(row, rec, ns):
    """Verify the original forced wait intervention and UID-matched retry chain."""
    estimate = row["recheck"]
    now, end = row["cost_window_start_s"], row["cost_window_end_s"]
    name = row["trial_id"]
    x, y, z = (row["pod_creates"][k]["name"] for k in ("x", "y", "z"))
    deleted = row["x_delete"]
    need(now <= deleted["submit_s"] <= deleted["ack_s"] < row["decision_s"]
         and deleted["response"]["metadata"]["uid"] == row["x_uid"]
         and deleted["response"]["metadata"]["deletionTimestamp"]
         and deleted["response"]["spec"]["preemptionPolicy"] == "Never", f"{name}: X delete")
    need(now == row["x_retire_intent_s"] and abs(end - now - 8.5) < 1e-6 and row["reservation_window_complete"]
         and not row["first_retire_applied"] and not row["later_retire_applied"]
         and all(row[k] == "wait" for k in ("initial_action", "first_proposed_action", "first_applied_action"))
         and all(row.get(k) is None for k in ("z_first_route_disabled_s", "z_first_delete_s", "first_z_delete", "later_z_delete", "z_submit_s"))
         and row["z_serving_uid"] == row["z_uid"] and row["z_recreations"] == 0
         and row["z_upstream"]["boot_id"] == row["z_boot_id"], f"{name}: forced wait was not followed")
    y_birth = row["pod_creates"]["y"]
    need(row["y_submit_s"] == y_birth["create_submit_s"] and row["y_create_ack_s"] == y_birth["create_ack_s"]
         and row["y_arrival_s"] <= row["decision_s"] <= row["y_submit_s"] < row["y_create_ack_s"]
         and not rec["y_manifests"]["replacement_written"], f"{name}: Y submission")
    t_re = estimate["t_s"]
    expected = "no_pending_Y" if row["condition"] == "fast" else "protected, already retiring, or still predicts within horizon"
    need(estimate["result"] == expected and estimate["reason"] == "common fixed forecast at ingress+1s"
         and 1 <= t_re - row["y_arrival_s"] < 1.1, f"{name}: recheck decision")

    logs = [v for v in rec["scheduler"] if now <= v["ts"] <= end]
    removal, = [e for e in logs if e["msg"] == "Delete event for scheduled pod"]
    y_logs = [e for e in logs if same_ref(e.get("pod"), ns, y)]
    attempt, retry = [e["ts"] for e in y_logs if e["msg"] == "Attempting to schedule pod"]
    failure, = [e for e in y_logs if e["msg"] == "Unable to schedule pod; no fit; waiting"]
    hint, = [e["ts"] for e in y_logs if e["msg"] == BIND_HINT and same_ref(e.get("deletedPod"), ns, x)]
    requeue, = [e["ts"] for e in y_logs if e.get("event") == "AssignedPodDelete" and e.get("queue") in ("Active", "Backoff")]
    success, = [e["ts"] for e in y_logs if e["msg"] == "Successfully bound pod to node"]
    match, = [v for v in rec["audit"] if v["auditID"] == row["y_bind_witness_id"] and v["stage"] == "ResponseComplete"
              and v["user"]["username"] == "system:kube-scheduler"]
    bound = row["y_bind_s"]
    need(same_ref(removal.get("pod"), ns, x) and "Insufficient memory" in failure["err"]
         and "preemptionPolicy=Never" in failure["err"]
         and row["x_release_lo_s"] == removal["ts"] and row["x_release_hi_s"] == hint
         and attempt <= failure["ts"] < removal["ts"] <= hint <= requeue <= retry
         <= timestamp(match["requestReceivedTimestamp"]) <= bound <= success + .005,
         f"{name}: fit/release/retry path")
    need((bound < t_re) if row["condition"] == "fast" else (failure["ts"] < t_re < retry), f"{name}: recheck context")
    need(row["y_http_ok"] and row["z_http_ok"] and all(row[f"{app}_client"]["status"] == 200
         and row[f"{app}_done_s"] <= end for app in ("y", "z")), f"{name}: HTTP completion")
    return dict(trial_id=name, block=row["block"], condition=row["condition"], recheck_result=estimate["result"],
                y_arrival_to_bind_ms=round((bound - row["y_arrival_s"]) * 1000, 6),
                decision_to_submit_ms=round((row["y_submit_s"] - row["decision_s"]) * 1000, 6),
                z_delete_api_count=0, original_z_served=True, y_retry_after_x_release=True)


def forced(evidence, out, scores):
    old = runpy.run_path(str(LEGACY))
    plan, records, _ = stage(evidence / "forced-evaluation.json", "evaluation", old["ARMS"], 6)
    rows, waits = [], []
    need(len({r["y_manifests"]["sha256"] for r in records}) == 1, "Y manifest specification changed across arms")
    for record in records:
        row = record["trial"]
        bind, witness = scores["independent_bind"](row, record["audit"], record["scheduler"], [])
        need(bind is not None and witness == "uid_api_and_scheduler_match", "forced baseline Bind not verified")
        mf = record["y_manifests"]
        need(mf["scheduler"] == "default-scheduler" and mf["priority"] == "fgcs-retirement-nonpreempting"
             and not mf["node"] and not mf["gates"], "scheduler/priority surprise")
        row = dict(row, independent_bind_check="UID_and_response_complete_match")
        if row["arm"] == "force_wait":
            waits.append(wait_path(row, record, plan["cluster"]))
        rows.append(row)
    need(Counter(r["condition"] for r in waits) == {"fast": 6, "slow": 6}, "forced wait crossed denominator")
    results, pairs, summary = old["summarize"](plan, rows)
    folder = out / "forced"
    folder.mkdir()
    for name, table in (("trial_results", results), ("block_pairs", pairs), ("condition_comparisons", summary["conditions"]),
                        ("forced_wait", waits)):
        old["write_csv"](folder / f"{name}.csv", table)
    runpy.run_path(str(REPLAY))["put_json"](folder / "study_summary.json", summary)


def http(evidence, out):
    runner = runpy.run_path(str(HTTP))
    folder = out / "http"
    folder.mkdir()
    contract = evidence / "http-contract.md"
    seal, ev = folder / "seal.json", folder / "evaluation.json"
    runner["fit"](evidence / "http-calibration.json", evidence / "http-prior-a.json", seal, contract)
    data = runner["evaluate"](evidence / "http-evaluation.json", seal, digest(seal), contract, "evaluation", ev)
    confirm = runner["evaluate"](evidence / "http-confirmation.json", seal, digest(seal), contract, "confirmation",
                                 folder / "confirmation.json", previous_evaluation=ev)
    print("HTTP arrival→Bind interaction (seconds):", data["platform_early_DID_s"]["mean"],
          confirm["platform_early_DID_s"]["mean"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True, help="trusted normalized Kind evidence directory")
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    scores = runpy.run_path(str(BASE))
    summary = baseline(args.evidence, args.out, scores)
    forced(args.evidence, args.out, scores)
    http(args.evidence, args.out)
    print(f"Kind: 48 baseline trials, {summary['initial_states_n']} replayed first decisions, 48 historical action trials")


if __name__ == "__main__":
    main()
