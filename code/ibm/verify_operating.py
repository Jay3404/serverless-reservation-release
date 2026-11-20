"""Recompute the archived IBM operating tables from event ledgers, optionally rerunning the cells."""

import argparse
from bisect import bisect_left
import csv
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from prepare import EXPECTED_GZIP, content_id, validate


CAMPAIGNS = (
    ("primary_1s", "operating", 1.0),
    ("exact_cap_buffer_1s", "exact_cap/cost_buffer", 1.0),
    ("exact_cap_largest_1s", "exact_cap/largest", 1.0),
    ("sensitivity_0p5s", "operating_sensitivity/horizon_0p5", 0.5),
    ("sensitivity_2s", "operating_sensitivity/horizon_2p0", 2.0),
)
OP_MODES = ("fixed", "calibrated", "oracle")
DAYS = ("day1", "day2")
SLO = 8.21376629075
BUDGET = 392845.20121905557
TRAIN_BUDGET_RECEIPT_SHA = "10038d3cca580df5b3f822d1b254446863f86bf33d0785f24a9aa6443d29473a"
RUNNER_SHA = "6b61866fe109a693d8db0e7ea340ed0a6018f1df3b59a2dc65e11ac1ba5c24d6"
KERNEL_SHA = "1cf3dfa0b9d05b8af835d3d53d529acd42aeafbaaccfbaa7e8a20fb472fed036"


def read(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def close(a, b, atol=1e-8):
    assert math.isclose(a, b, rel_tol=0, abs_tol=atol), (a, b)


def write(path, rows):
    assert rows
    with path.open("x", encoding="utf-8", newline="") as stream:
        fields = list(rows[0])
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def raw_cell(path, summary, bundle, batch, horizon, ttl):
    data = read(path)
    raw = data["summary"]
    rows = data["requests"]
    day, cap, cleanup, treatment, mode = summary["role"]
    assert treatment == "natural"
    assert raw["role"] == summary["role"]
    trace = bundle["trace"][day]
    expected = {str(rid): (str(app), at, service) for rid, app, at, service in trace["requests"]}
    assert len(rows) == len(expected) == raw["n"] == summary["n"] == trace["request_count"]
    actual_ids = {r["request_id"] for r in rows}
    assert actual_ids == set(expected)
    completed = [r for r in rows if r["completed"] is not None]
    assert len(completed) == raw["completed"] == summary["completed"] == len(rows)
    for r in rows:
        app, at, service = expected[r["request_id"]]
        assert (r["app_id"], r["arrival"]) == (app, at)
        close(r["completed"] - r["started"], service)
        close(r["completed"] - at, r["response"])
        close(r["started"] - at, r["initialization_wait"] + r["other_wait"])
    costs = {k: [] for k in raw["state_cost_gbs"]}
    instances = data["instances"]
    changes = Counter()
    lifetime_totals = []
    lifetime_tails = []
    by_id = {i["instance"]: i for i in instances}
    assert len(by_id) == len(instances) == raw["creations"]
    intents, returns = {}, {}
    for kind, ident, app, timestamp, reason in data["lifecycle"]:
        assert app == by_id[ident]["app"]
        key = intents if kind == "retirement_intent" else returns
        assert kind in ("retirement_intent", "confirmed_return") and ident not in key
        key[ident] = (timestamp, reason)
    assert set(by_id) == set(intents) == set(returns)
    for i in instances:
        ident, born, confirmed = i["instance"], i["born"], i["confirmed_return"]
        retired, _ = intents[ident]
        assert trace["start"] <= born <= retired <= confirmed == returns[ident][0]
        gb = bundle["configs"][i["app"]]["memory_gb"]
        changes[born] += gb
        changes[confirmed] -= gb
        lifetime = gb * (confirmed - born)
        lifetime_totals.append(lifetime)
        lifetime_tails.append(gb * max(0, confirmed - max(born, trace["end"])))
        close(math.fsum(i["cost_gbs"].values()), lifetime)
        close(i["cost_gbs"].get("retiring", 0), gb * (confirmed - retired))
        for state, value in i["cost_gbs"].items():
            costs[state].append(value)
    assert sum(reason == "capacity" for _, reason in intents.values()) == raw["reclaim"]
    reserved = peak = 0
    for at in sorted(changes):
        reserved += changes[at]
        peak = max(peak, reserved)
        assert -1e-9 <= reserved <= cap + 1e-9
    close(reserved, 0)
    close(peak, raw["stock_max"])
    totals = {state: math.fsum(values) for state, values in costs.items()}
    reserved_cost = math.fsum(lifetime_totals)
    tail_cost = math.fsum(lifetime_tails)
    for item in (raw, summary):
        close(reserved_cost, item["cost_gbs"], 1e-6)
        close(tail_cost, item["tail_gbs"], 1e-6)
        for state, cost in totals.items():
            close(cost, item["state_cost_gbs"][state], 1e-6)
        assert item["budget_gbs"] == BUDGET and item["slo_s"] == SLO
    assert raw["eligible"] == summary["eligible"] == (reserved_cost <= BUDGET)
    other_ms = math.fsum(r["other_wait"] for r in rows) / len(rows) * 1000
    response_ms = math.fsum(r["response"] for r in rows) / len(rows) * 1000
    close(other_ms, summary["waits"]["mean"])
    close(response_ms, summary["response"]["mean"])
    misses = sum(r["response"] > SLO for r in rows)
    cold = sum(r["cold"] for r in rows)
    positive = sum(r["other_wait"] > 1e-9 for r in rows)
    assert misses == raw["slo_misses"] == summary["slo_misses"]
    assert positive == raw["blocked"] == summary["blocked"]
    assert cold == raw["cold"] == summary["cold"]
    return {
        "batch": batch, "day": day, "cap_gb": cap, "cleanup_s": cleanup, "mode": mode,
        "waiting_horizon_s": horizon, "ttl_s": ttl, "planned": len(rows), "completed": len(completed),
        "eligible": reserved_cost <= BUDGET, "budget_gbs": BUDGET, "cost_gbs": reserved_cost,
        "excess_gbs": max(reserved_cost - BUDGET, 0), "tail_gbs": tail_cost, "peak_reserved_gb": peak,
        "other_mean_ms": other_ms, "positive_other_wait": positive,
        "other_p95_ms": raw["waits"]["p95"], "other_p99_ms": raw["waits"]["p99"],
        "response_mean_ms": response_ms, "cold": cold, "cold_fraction": cold / len(rows),
        "slo_s": SLO, "slo_misses": misses, "slo_miss_fraction": misses / len(rows),
        "reclaim": raw["reclaim"], "created": len(instances), "ledger": str(path),
        "ledger_sha256": sha(path),
    }


def check_selection(label, folder, base, seal, budget):
    train = folder if "train_summary_sha256" in seal else base / "operating"
    plan = read(train / "TRAIN_PLAN.json")
    training = read(train / "TRAIN_SUMMARY.json")
    assert len(training) == 192 and plan.get("budget", plan.get("train_budget_gbs")) == budget
    assert not seal["dev_opened_during_selection"]
    if train == folder:
        assert seal["train_plan_sha256"] == sha(train / "TRAIN_PLAN.json")
        assert seal["train_summary_sha256"] == sha(train / "TRAIN_SUMMARY.json")
    else:
        assert seal["primary_seal_sha256"] == sha(train / "EVALUATION_SEAL.json")
    indexed = {(tuple(r["role"]), r["ttl"]): r for r in training}
    assert len(indexed) == 192 and {x["ttl"] for x in training} == {0, 5, 15, 30, 45, 60}
    out = []
    for c in seal["choices"]:
        cap, cleanup = c["cap"], c["delay"]
        margin = plan.get("train_margin_fraction", plan.get("engineering_margin"))
        assert margin == .1
        threshold = budget if "rule" in seal else (1 - margin) * budget
        rule = seal.get("rule", "primary_90pct_largest")
        candidates = []
        for row in c["candidates"]:
            ttl = row["ttl"]
            modes = plan.get("modes", ("stock", *OP_MODES))
            sample = [indexed[(day, cap, cleanup, "natural", mode), ttl]
                      for day in plan["train_windows"] for mode in modes]
            assert len(sample) == 8
            for x in sample:
                assert x["n"] == plan["train_windows"][x["role"][0]]["request_count"]
            worst = max(r["cost_gbs"] for r in sample)
            full = all(r["n"] == r["completed"] for r in sample)
            close(row["max_cost_gbs"], worst)
            assert full == row["full_completion"]
            feasible = full and worst <= threshold
            candidates.append(dict(batch=label, rule=rule, cap_gb=cap, cleanup_s=cleanup,
                                   ttl_s=ttl, train_cells=len(sample), all_complete=full,
                                   worst_train_gbs=worst, ceiling_gbs=threshold,
                                   eligible=feasible, selected=c["ttl"] == ttl))
        feasible = [r for r in candidates if r["eligible"]]
        selected = None
        if feasible:
            if rule == "cost_buffer":
                selected = min(feasible, key=lambda r: (r["worst_train_gbs"], -r["ttl_s"]))
            else:
                selected = max(feasible, key=lambda r: r["ttl_s"])
        assert (None if selected is None else selected["ttl_s"]) == c["ttl"]
        if "worst_train_gbs" in c and selected is not None:
            assert selected["worst_train_gbs"] == c["worst_train_gbs"]
        out.extend(candidates)
    return out


def audit_wait_receipts(ledgers):
    """Check oracle waits against modeled return times and slot claims."""
    eps = 1e-8
    results = []
    for role, path in sorted(ledgers.items()):
        if role[-1] != "oracle":
            continue
        data = read(path)
        requests = {(r["app_id"], r["request_id"]): r for r in data["requests"]}
        returns = sorted(i["confirmed_return"] for i in data["instances"])
        row = dict(role=list(role), calls=len(data["decisions"]), wait_calls=0,
                   at_return=0, later=0, earlier=0)
        for decision in data["decisions"]:
            eta = decision["wait_until"]
            if eta is None:
                continue
            i = bisect_left(returns, eta - eps)
            assert not decision["victims"] and 0 < eta - decision["t"] <= 1 + eps
            assert i < len(returns) and abs(returns[i] - eta) <= eps
            r = requests[decision["app"], decision["head"]]
            gap = (r["started"] - r["initialization_wait"]) - eta
            row["wait_calls"] += 1
            row["at_return" if abs(gap) <= eps else "later" if gap > 0 else "earlier"] += 1
        results.append(row)
    totals = {key: sum(r[key] for r in results)
              for key in ("calls", "wait_calls", "at_return", "later", "earlier")}
    assert len(results) == 4 and tuple(totals.values()) == (2909, 1183, 1171, 12, 0)
    return dict(rows=results, totals=totals, comparison_tolerance_s=eps)


def set_replay_bundle(bundle):
    global REPLAY_BUNDLE
    REPLAY_BUNDLE = bundle


def rerun_cell(role, ttl, horizon, expected_path, output):
    import rebudget
    import run

    # The experimental waiting horizon varies; the replay watchdog does not.
    assert run.WATCHDOG == 1.0
    run.MAX_WAIT = horizon
    result = rebudget.run_with_ttl(REPLAY_BUNDLE, role, ttl, output)
    name = "_".join(map(str, role)) + ".json.gz"
    expected, actual = read(expected_path), read(output / name)
    for data in (expected, actual):
        data["summary"].pop("duration_s")
    assert expected == actual, (role, ttl, horizon, "simulation changed archived results")
    return dict(role=list(role), ttl_s=ttl, horizon_s=horizon,
                completed=result["completed"], matches_ledger=True)


def main():
    if not __debug__:
        raise SystemExit("Run without -O: verification depends on assertions")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path, help="the ibm/operating evidence directory")
    parser.add_argument("--bundle", required=True, type=Path, help="prepared inputs.json.gz")
    parser.add_argument("--out", required=True, type=Path, help="new output directory")
    parser.add_argument("--rerun", choices=("selected", "all"), nargs="?", const="all",
                        help="also rerun 12 selected TTL5 cells and 4 TTL60 oracle cells, or all 84")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    base = args.evidence / "batches"
    original = args.evidence / "ttl60"
    verifier = read(base / "operating_verified_v2/verified.json")
    bundle = read(args.bundle)
    validate(bundle)
    content_hash = content_id(bundle)
    bundle_hash = sha(args.bundle)
    assert bundle["physics"]["train_budget_24h_gbs"] == BUDGET
    calibration_path = args.evidence / "train-budget.json"
    pins = read(base / "operating/TRAIN_PLAN.json")["train_source_pins"]
    calibrated_budget_hashes = {digest for filename, digest in pins.items()
                                if filename.endswith("/calibration_native600/calibration.json")}
    assert calibrated_budget_hashes == {TRAIN_BUDGET_RECEIPT_SHA} == {sha(calibration_path)}
    calibration = read(calibration_path)
    assert calibration["fit_split"] == "TRAIN" and calibration["budget_gb"] * 86_400 == BUDGET
    all_rows, training_rows, batch_meta = [], [], {}
    jobs = []
    oracle_wait_paths = {}
    for label, dirname, horizon in CAMPAIGNS:
        folder = base / dirname
        seal_path, terminal_path = (folder / "EVALUATION_SEAL.json", folder / "EVALUATION_TERMINAL.json")
        seal, terminal = read(seal_path), read(terminal_path)
        assert seal["known_dev_sha256"] == EXPECTED_GZIP
        original_verified = verifier[label]
        assert sha(seal_path) == original_verified["seal_sha256"]
        assert sha(terminal_path) == original_verified["terminal_sha256"]
        assert terminal["exit"] == "complete" and terminal["budget"] == BUDGET
        if horizon == 1:
            start = read(folder / "EVALUATION_START.json")
            assert start["seal_sha256"] == terminal["selected_roles_sha256"] == sha(seal_path)
            assert start["bundle_sha256"] == EXPECTED_GZIP and start["jobs"] == seal["roles"]
        else:
            assert seal["waiting_horizon_s"] == terminal["waiting_horizon_s"] == horizon
            assert seal["watchdog_s"] == terminal["watchdog_s"] == 1.0
        assert terminal["dev_summary_sha256"] == sha(folder / "DEV_SUMMARY.json")
        assert terminal["dev_pairs_sha256"] == sha(folder / "DEV_PAIRS.json")
        summary = read(folder / "DEV_SUMMARY.json")
        expected = {(tuple(role), ttl) for role, ttl in seal["roles"]}
        assert {(tuple(r["role"]), r["ttl"]) for r in summary} == expected
        assert len(summary) == len(expected) == 16
        training_rows.extend(check_selection(label, folder, base, seal, BUDGET))
        batch_rows = []
        for row in summary:
            role = tuple(row["role"])
            expected_rel = "ledgers/" + "_".join(map(str, role)) + ".json.gz"
            assert row["raw_file"] == expected_rel
            path = folder / expected_rel
            checked = raw_cell(path, row, bundle, label, horizon, row["ttl"])
            assert checked["ledger_sha256"] == row["raw_sha256"] == terminal["raw_ledger_hashes"][row["raw_file"]]
            batch_rows.append(checked)
            main_cell = role[1] == 8 and role[-1] in OP_MODES and (
                (label, role[2]) in (("primary_1s", 2), ("exact_cap_buffer_1s", 5)))
            if main_cell and role[-1] == "oracle":
                oracle_wait_paths[role] = path
            if args.rerun == "all" or (args.rerun == "selected" and main_cell):
                jobs.append((label, role, row["ttl"], horizon, path))
        eligible = sum(r["eligible"] for r in batch_rows)
        assert eligible == original_verified["eligible"] == terminal["eligible"]
        batch_meta[label] = dict(completed=len(batch_rows), eligible=eligible,
                                 seal_sha256=sha(seal_path), terminal_sha256=sha(terminal_path))
        all_rows.extend(batch_rows)

    main = [r for r in all_rows if r["cap_gb"] == 8 and r["mode"] in OP_MODES
            and (r["batch"], r["cleanup_s"]) in (("primary_1s", 2), ("exact_cap_buffer_1s", 5))]
    main.sort(key=lambda r: (r["cleanup_s"], r["day"], OP_MODES.index(r["mode"])))
    assert len(main) == 12 and all(r["eligible"] and r["ttl_s"] == 5 for r in main)
    archived_hashes = read(original / "checksums.json")
    assert len(archived_hashes) == 5
    old_summary = original / "results/summary.json"
    assert archived_hashes["results/summary.json"] == sha(old_summary)
    old = []
    for row in read(old_summary):
        day, cap, cleanup, treatment, mode = row["role"]
        if cap == 8 and treatment == "natural" and mode == "oracle":
            name = f"results/{day}_{cap}_{cleanup}_{treatment}_{mode}.json.gz"
            checked = raw_cell(original / name, row, bundle, "archived_ttl60", 1.0, 60)
            assert checked["ledger_sha256"] == archived_hashes[name] and not checked["eligible"]
            old.append(checked)
            if args.rerun:
                jobs.append(("archived_ttl60", tuple(row["role"]), 60, 1.0, original / name))
    assert len(old) == 4
    assert not any(r["eligible"] for r in all_rows if r["batch"] == "exact_cap_largest_1s")
    pairs = []
    for label, _, _ in CAMPAIGNS:
        rows = [r for r in all_rows if r["batch"] == label]
        fixed = {(r["day"], r["cap_gb"], r["cleanup_s"]): r for r in rows if r["mode"] == "fixed"}
        for r in rows:
            if r["mode"] not in ("calibrated", "oracle"):
                continue
            ref = fixed[r["day"], r["cap_gb"], r["cleanup_s"]]
            assert ref["ttl_s"] == r["ttl_s"] and ref["planned"] == r["planned"]
            pairs.append({
                **{k: r[k] for k in ("batch", "day", "cap_gb", "cleanup_s", "mode", "waiting_horizon_s", "ttl_s")},
                "fixed_eligible": ref["eligible"], "mode_eligible": r["eligible"],
                "delta_other_ms": r["other_mean_ms"] - ref["other_mean_ms"],
                "delta_cost_gbs": r["cost_gbs"] - ref["cost_gbs"],
                "delta_slo_misses": r["slo_misses"] - ref["slo_misses"],
            })
    waits = audit_wait_receipts(oracle_wait_paths)
    args.out.mkdir(parents=True, exist_ok=False)
    write(args.out / "ibm_ttl5_8gb_1s.csv", main)
    write(args.out / "ibm_all_operating_points.csv", all_rows)
    write(args.out / "ibm_pairs_vs_fixed.csv", pairs)
    write(args.out / "ibm_ttl60_oracle_8gb.csv", old)
    write(args.out / "ibm_train_selection.csv", training_rows)
    rerun = []
    if args.rerun:
        assert len(jobs) == (84 if args.rerun == "all" else 16)
        here = Path(__file__).resolve().parent
        assert sha(here / "run.py") == RUNNER_SHA and sha(here / "kernel_v1.py") == KERNEL_SHA
        rerun_root = args.out / "rerun"
        rerun_root.mkdir()
        with ProcessPoolExecutor(max_workers=args.workers,
                                 initializer=set_replay_bundle, initargs=(bundle,)) as pool:
            futures = []
            for batch, role, ttl, horizon, path in jobs:
                folder = rerun_root / batch
                folder.mkdir(exist_ok=True)
                futures.append(pool.submit(rerun_cell, role, ttl, horizon, path, folder))
            rerun = [future.result(timeout=120) for future in futures]
    metadata = dict(batches=batch_meta, bundle_sha256=bundle_hash, budget_gbs=BUDGET,
                    bundle_content_sha256=content_hash,
                    budget_train_gb=calibration["budget_gb"], budget_train_source=str(calibration_path),
                    total_cells=len(all_rows), total_request_rows=sum(r["planned"] for r in all_rows),
                    main_cells=len(main), legacy_ttl60_oracle_cells=len(old),
                    oracle_wait_timing=waits, rerun=rerun)
    with (args.out / "ibm_audit.json").open("x", encoding="utf-8") as stream:
        json.dump(metadata, stream, indent=2)
        stream.write("\n")
    print(f"verified {len(all_rows)} operating cells, {len(main)} main and {len(old)} legacy TTL60 oracle cells; "
          f"reran {len(rerun)} cells")


if __name__ == "__main__":
    main()
