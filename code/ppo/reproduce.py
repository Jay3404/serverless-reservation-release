"""Recompute the supplementary PPO D comparison from normalized archived ledgers."""

import argparse
from collections import Counter
import csv
import gzip
import json
from math import fsum, isclose, isfinite
from pathlib import Path
import re


SEEDS = (11, 22, 33, 44, 55)
ARMS = ("Planner", *(f"D{s}" for s in SEEDS))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def close(a, b, label, tolerance=1e-6):
    require(isclose(a, b, rel_tol=0.0, abs_tol=tolerance), f"{label}: {a} != {b}")


def read_arm(path):
    receipt = json.loads((path / "receipt.json").read_bytes())
    with gzip.open(path / "requests.csv.gz", "rt", newline="", encoding="utf-8") as stream:
        requests = list(csv.DictReader(stream))
    with gzip.open(path / "instances.json.gz", "rt", encoding="utf-8") as stream:
        rows = json.load(stream)
    instances = {x["instance"]: x for x in rows}
    require(len(instances) == len(rows) and len(requests) == 25_319, "Unexpected ledger census")
    start, end = receipt["evaluation_start"], receipt["evaluation_end"]
    keys = set()
    for x in requests:
        q = int(x["request_id"])
        arrival, begun, completed = (float(x[k]) for k in ("arrival", "started", "completed"))
        require(q not in keys and start <= arrival < end and arrival <= begun <= completed
                and isfinite(completed) and x["cold"] in {"True", "False"}
                and int(x["instance"]) in instances, f"Invalid row {q} in {path.name}")
        for field in ("initialization_wait", "other_wait"):
            require(isfinite(float(x[field])) and float(x[field]) >= 0, f"Invalid wait in {path.name}")
        keys.add(q)
    require(len(keys) == receipt["completed_requests"], "Request completion differs from archive")
    cold = sum(x["cold"] == "True" for x in requests)
    wait_init = fsum(float(x["initialization_wait"]) for x in requests)
    wait_other = fsum(float(x["other_wait"]) for x in requests)
    require(cold == receipt["cold_requests"], "Cold count differs from archive")
    close(wait_init, receipt["initialization_wait"], "Initialization wait")
    close(wait_other, receipt["other_wait"], "Other assignment wait")

    total, tail, reasons = [], [], Counter()
    for x in instances.values():
        lo, hi = x["born"], x["confirmed_return"]
        require(start <= lo < hi and set(x["cost_gbs"]) == {"init", "idle", "busy", "retiring"},
                f"Invalid lifetime on {x['instance']}")
        gb_s = fsum(x["cost_gbs"].values())
        require(gb_s >= 0 and isfinite(gb_s), "Invalid reservation cost")
        total.append(gb_s)
        # A constant reservation spans an instance's recorded lifetime.
        size = gb_s / (hi - lo)
        tail.append(max(0.0, hi - max(end, lo)) * size)
        reasons[x["retirement"]["reason"]] += 1
    cost, after_end = fsum(total), fsum(tail)
    close(cost, receipt["reservation_gb_seconds"], "Lifetime reservation GB-s")
    close(after_end, cost - fsum(receipt["cost_through_T_by_state_gbs"].values()), "Tail GB-s")
    summary = dict(requests=len(keys), cold=cold, wait_init_s=wait_init, wait_other_s=wait_other,
                   cost_gbs=cost, tail_gbs=after_end, retirement_reasons=dict(reasons),
                   budget_gbs=receipt["target_gbs"], budget_eligible=cost <= receipt["target_gbs"])
    return receipt, requests, instances, summary


def run(evidence):
    results = {name: read_arm(evidence / name) for name in ARMS}
    receipt = {name: results[name][0] for name in ARMS}
    hashes = ("checkpoint_sha256", "weight_sha256")
    for k in hashes:
        found = {receipt[f"D{s}"][k] for s in SEEDS}
        require(len(found) == len(SEEDS) and all(re.fullmatch(r"[0-9a-f]{64}", v) for v in found),
                f"Expected five archived {k} values")
    for s in (22, 33, 44):
        require(results[f"D{s}"][1] == results["D11"][1], f"D{s} requests or assignments changed")

    left, right = (results[n][2] for n in ("D11", "D33"))
    require(left.keys() == right.keys(), "D11/D33 instance census differs")
    ttl_outcomes = Counter()
    for key, x in left.items():
        y = right[key]
        require({k: v for k, v in x.items() if k != "ttl_assignments"} ==
                {k: v for k, v in y.items() if k != "ttl_assignments"}, "D11/D33 physical lifetime differs")
        episodes = {v["opened_at"]: v for v in y["idle_episodes"]}
        for a, b in zip(x["ttl_assignments"], y["ttl_assignments"], strict=True):
            if a == b:
                continue
            require(a["at"] == b["at"] and a["idle_since"] == b["idle_since"]
                    and (a["ttl"], b["ttl"]) == (60, 600), "Non-TTL difference")
            episode = episodes[a["idle_since"]]
            require(0 <= episode["closed_at"] - episode["opened_at"] < 60, "TTL expires before idle outcome")
            ttl_outcomes[episode["outcome"]] += 1
    require(ttl_outcomes == {"reused": 318, "reclaimed": 1}, "TTL differences disagree with the appendix")

    arms = {name: results[name][3] for name in ARMS}
    tails = {name: arms[name]["tail_gbs"] - arms["D11"]["tail_gbs"] for name in ("D22", "D44")}
    for name, amount in tails.items():
        close(amount, 2160, f"{name} tail increment")
    p, d = arms["Planner"], arms["D55"]
    require(len({x["budget_gbs"] for x in arms.values()}) == 1 and p["budget_eligible"]
            and [f"D{s}" for s in SEEDS if arms[f"D{s}"]["budget_eligible"]] == ["D55"]
            and p["cold"] == 622 and d["cold"] == 691, "Published budget/cold comparison changed")
    close(p["cost_gbs"], 388558.307, "Published Planner cost", 0.00051)
    close(d["cost_gbs"], 376476.935, "Published D55 cost", 0.00051)
    total_wait = lambda row: row["wait_init_s"] + row["wait_other_s"]
    gain = (total_wait(p) - total_wait(d)) / total_wait(p) * 100
    close(gain, 0.85, "Published total-wait gain in percent", 0.005)
    require(d["retirement_reasons"].get("voluntary") == 471, "D55 voluntary retirements changed")
    return dict(arms=arms, identical_request_ledgers=["D11", "D22", "D33", "D44"],
                distinct_checkpoint_digests=5, distinct_weight_digests=5,
                d11_d33_changed_ttls=dict(ttl_outcomes), tail_difference_gbs=tails,
                d55_total_wait_reduction_percent=gain, offline_measurement_reanalysis_only=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--evidence", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    result = run(args.evidence)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Verified supplementary PPO comparison: {args.out}")


if __name__ == "__main__":
    main()
