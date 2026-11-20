"""Recalculate the archived first-action and overdue-forecast checks."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path

from run import phys


def need(ok, msg):
    if not ok:
        raise ValueError(msg)


def read(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    close_dir = root / "fgcs_closed_loop_20261117"
    study = root / "fgcs_conditions_20261117"
    branch = study / "branch"
    pins = read(study / "branch_verified/findings.json")
    need(pins["branch_seal_sha256"] == digest(branch / "seal.json"), "Branch seal hash mismatch")
    need(read(branch / "seal.json")["input_sha256"] == digest(close_dir / "inputs.json.gz"),
         "Branch bundle does not match seal")
    fits, swaps = [], []
    files = sorted((branch / "pairs").glob("*.json.gz"))
    need({p.name for p in files} == set(pins["receipt_hashes"]), "Branch receipt census changed")
    for path in files:
        need(digest(path) == pins["receipt_hashes"][path.name], f"Branch hash mismatch: {path.name}")
        rec = read(path)
        pair = rec["pair"]
        need(pair["parity"] == "exact" and len(rec["hybrid_states"]) == pair["hybrid_decisions"], "Branch parity")
        if pair["eligibility"] == "both" and pair["key"][3] == "natural":
            # Only the columns needed for the deterministic state audit.
            fits.append(dict(key=pair["key"], states=[[
                s["both_choice"], s["visible_count"], s["fixed_fit_s"], s["hybrid_fit_s"],
                s["fixed_victims"] != s["hybrid_victims"]] for s in rec["hybrid_states"]]))
        if pair["first"] is not None:
            w = pair["single_swap"]
            physical, alternate = (w[k] for k in ("actual_live_fit_fixed", "actual_live_fit_variant"))
            pending = [p for p in pair["first"]["pending_instances"] if p["i"] == physical["returning_instance"]]
            returning, = pending
            swaps.append(dict(key=pair["key"], decision_s=pair["first"]["t"],
                              return_intent_s=returning["intent"], return_s=returning["actual_return"],
                              fixed_fit=physical, variant_fit=alternate,
                              fixed_claim_s=w["head_fixed"]["slot_claim_s"],
                              variant_claim_s=w["head_variant"]["slot_claim_s"]))
    overdue = []
    for entry in read(root / "fgcs_retirement_action_20261117/ibm28_v2/rows.json"):
        overdue.append({**{k: entry[k] for k in ("t", "free_gb", "required_gb", "wait_until_s")},
                        "pending": [{k: p[k] for k in ("reserved_gb", "actual_return_s", "fixed_return_s",
                                                       "hybrid_return_s")} for p in entry["pending"]],
                        "dispatch": [{k: s[k] for k in ("trigger", "t", "request_claimed_or_started")}
                                     for s in entry["dispatch_attempts"]],
                        "slot": {k: entry["slot"][k] for k in ("trigger", "t")}})

    pair_receipts = read(study / "archive/summary.json")["pair_rows"]
    pairs = [{k: x[k] for k in ("dataset", "day", "cap_gb", "cleanup_s", "condition", "ttl_s",
                              "n", "fixed_cost_gbs", "hybrid_cost_gbs", "fixed_slo", "hybrid_slo",
                              "fixed_mean_other_wait_ms", "hybrid_mean_other_wait_ms",
                              "exact_other_wait_sign_counts")} for x in pair_receipts]
    refs = []
    ref_dir = close_dir / "refs"
    for row in read(ref_dir / "summary.json"):
        day, cap, duration, policy = row["role"]
        path = ref_dir / f"ttl{row['ttl']}/{day}_{cap}_{duration}_{policy}.json.gz"
        need(digest(path) == row["raw_sha256"], f"Reference hash mismatch: {path.name}")
        raw = read(path)
        refs.append(dict(role=row["role"], ttl=row["ttl"], declared_cost=row["cost_gbs"],
                         requests=len(raw["requests"]), completed=sum(r["completed"] is not None for r in raw["requests"]),
                         instance_costs=[math.fsum(i["cost_gbs"].values()) for i in raw["instances"]]))
    data = dict(fits=fits, swaps=swaps, overdue=overdue, pairs=pairs, references=refs)
    with out.open("xb") as f, gzip.GzipFile(mode="wb", fileobj=f, filename="", mtime=0) as g:
        g.write(json.dumps(data, separators=(",", ":"), allow_nan=False).encode())


def score(evidence, bundle):
    data = read(evidence)
    budget = bundle["physics"]["train_budget_24h_gbs"]
    pairs = [x for x in data["pairs"] if x["dataset"] == "full_ttl_frontier"
             and x["fixed_cost_gbs"] <= budget and x["hybrid_cost_gbs"] <= budget]
    need(len(pairs) == len(data["fits"]) == 23, "Jointly eligible pair count changed")
    directions = Counter()
    for p in pairs:
        wait = p["hybrid_mean_other_wait_ms"] - p["fixed_mean_other_wait_ms"]
        cost = p["hybrid_cost_gbs"] - p["fixed_cost_gbs"]
        directions["better" if wait < -1e-6 else "worse" if wait > 1e-6 else "tie"] += 1
        need(p["fixed_slo"] == p["hybrid_slo"] and not (cost < 0 and wait < -1e-6), "Pair result changed")
    need(directions == {"better": 1, "worse": 5, "tie": 17}, "Eligibility wait comparison changed")

    checked = [s for group in data["fits"] for s in group["states"]]
    flip = [s for s in checked if s[0] and s[1] and s[4]]
    need(len(checked) == 10_535 and len(flip) == sum(bool(s[4]) for s in checked) == 42,
         "Same-state decisions changed")
    one_missing = sum((a is None) != (b is None) for _, _, a, b, _ in flip)
    need(one_missing == len(data["overdue"]) == 28, "Missing forecast count changed")
    trigger_counts = Counter()
    for x in data["overdue"]:
        now, free, required = (x[k] for k in ("t", "free_gb", "required_gb"))
        need(free < required and all(p["actual_return_s"] > now for p in x["pending"]),
             "Reservation unexpectedly absent at decision")
        total = sum(p["reserved_gb"] for p in x["pending"])
        fixed_future = sum(p["reserved_gb"] for p in x["pending"] if p["fixed_return_s"] > now)
        need(free + total >= required and free + fixed_future < required, "Missing projected fit is not overdue")
        first, *rest = x["dispatch"]
        trigger_counts[first["trigger"]] += 1
        need(x["slot"]["trigger"] == "returned" and x["slot"]["t"] > now and
             any(d["request_claimed_or_started"] and d["t"] == x["slot"]["t"] for d in (first, *rest)),
             "Slot not claimed on an observed physical return")
    need(trigger_counts == {"returned": 25, "arrival": 3}, "Next dispatch trigger count changed")

    minima = {d: min(s[2] for s in phys(bundle, d)[0]) for d in (2, 5)}
    need(len(data["swaps"]) == 23, "First-action swap census changed")
    dt = []
    for swap in data["swaps"]:
        fit = swap["fixed_fit"]
        need(fit == swap["variant_fit"] and fit["t"] == swap["return_s"] ==
             swap["fixed_claim_s"] == swap["variant_claim_s"], "Trigger receives capacity from different event")
        lag = swap["return_s"] - swap["decision_s"]
        need(0 < lag < minima[swap["key"][2]] and swap["return_intent_s"] <= swap["decision_s"],
             "An additional retirement could overtake the first return")
        dt.append(lag)

    references = data["references"]
    expected_roles = {(day, cap, d, mode, ttl) for day in ("day1", "day2") for cap in (8, 16)
                      for d in (2, 5) for mode in ("planner", "rule") for ttl in (30, 60)}
    need(len(references) == len(expected_roles) and
         {(*x["role"], x["ttl"]) for x in references} == expected_roles, "Reference cell census changed")
    for row in references:
        cost = math.fsum(row["instance_costs"])
        need(row["completed"] == row["requests"] and cost > budget and
             math.isclose(cost, row["declared_cost"], rel_tol=0, abs_tol=1e-6), "Reference cost/completion disagrees")
    return dict(scope="offline ledger accounting, not policy re-execution", eligible_pairs=len(pairs),
                pair_wait_direction=dict(directions), observed_states=len(checked), changed_actions=len(flip),
                expired_forecasts=one_missing, next_dispatch_triggers=dict(trigger_counts),
                action_swaps_same_claim=len(dt), first_fit_lag_s=[min(dt), max(dt)],
                minimum_new_retirement_s=minima, reference_cells=len(references), reference_eligible=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="mode", required=True)
    export = commands.add_parser("prepare", help="curator: normalize the sealed local result archives")
    export.add_argument("--archives", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    check = commands.add_parser("score", help="recompute reported decision-audit and reference numbers")
    check.add_argument("--evidence", type=Path, required=True)
    check.add_argument("--bundle", type=Path, required=True)
    check.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.mode == "prepare":
        prepare(a.archives, a.out)
    else:
        result = score(a.evidence, read(a.bundle))
        with a.out.open("x", encoding="utf-8") as f:
            json.dump(result, f, indent=2, allow_nan=False)
            f.write("\n")
        print(f"Verified IBM decision and reference ledgers: {a.out}")


if __name__ == "__main__":
    main()
