"""After archived-TTL failure, separately calibrate one shared TTL on TRAIN48."""
from concurrent.futures import ProcessPoolExecutor
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

import run

TTLS = (0, 5, 15, 30, 45, 60)
CALIBRATION_MODES = ("stock", "fixed", "calibrated")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def put(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, allow_nan=False)


def run_with_ttl(bundle, role, ttl, output):
    original = run.Loop
    class TrainTTL(original):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.default_ttl = float(ttl)
    run.Loop = TrainTTL
    try:
        row = run.run_cell(bundle, role, output)
        return {**row, "ttl": ttl}
    finally:
        run.Loop = original


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    assert 1 <= args.workers <= 8
    args.out.mkdir(exist_ok=False)
    with gzip.open(args.bundle, "rt", encoding="utf-8") as f:
        bundle = json.load(f)
    assert bundle["trace"]["train"]["end"] - bundle["trace"]["train"]["start"] == 172800
    train = {**bundle, "trace": {"train": bundle["trace"]["train"]},
             "physics": {**bundle["physics"], "train_budget_24h_gbs":
                         2 * bundle["physics"]["train_budget_24h_gbs"]}}
    evaluation = {**bundle, "trace": {k: bundle["trace"][k] for k in run.DAYS}}
    train_roles = [(("train", cap, d, "natural", mode), ttl) for cap in run.CAPS
                   for d in run.DURATIONS for ttl in TTLS for mode in CALIBRATION_MODES]
    here = Path(__file__).parent
    put(args.out/"CALIBRATION_PLAN.json",
        dict(schema="rebudget-v1",
             reason="All 58 new archived-TTL60 cells above the inherited budget; preserve them.",
             ttl_grid_s=TTLS, train_roles=train_roles, evaluation_candidates=list(run.roles()),
             selection="Largest common TRAIN-feasible TTL for stock, fixed, calibrated; none means no evaluation",
             budget_48h=train["physics"]["train_budget_24h_gbs"],
             budget_24h=bundle["physics"]["train_budget_24h_gbs"], workers=args.workers,
             files={p.name:sha(p) for p in (args.bundle, here/"run.py",
                                           here/"kernel_v1.py", here/"rebudget.py")}))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_with_ttl, train, role, ttl, None)
                   for role, ttl in train_roles]
        summaries = [f.result(timeout=120) for f in futures]
    put(args.out/"train_summary.json", summaries)
    by_cell = defaultdict(list)
    for row in summaries:
        _, cap, d, _, _ = row["role"]
        by_cell[cap, d, row["ttl"]].append(row)
    chosen = {}
    for cap in run.CAPS:
        for d in run.DURATIONS:
            feasible = [ttl for ttl in TTLS if len(by_cell[cap,d,ttl]) == 3
                        and all(r["eligible"] and r["completed"] == r["n"]
                                for r in by_cell[cap,d,ttl])]
            chosen[cap,d] = max(feasible) if feasible else None
    plan = [(role, chosen[role[1],role[2]]) for role in run.roles()
            if chosen[role[1],role[2]] is not None]
    put(args.out/"EVALUATION_SEAL.json",
        dict(train_summary_sha256=sha(args.out/"train_summary.json"),
             calibration_plan_sha256=sha(args.out/"CALIBRATION_PLAN.json"),
             choices=[{"cap":c,"d":d,"ttl":ttl} for (c,d),ttl in chosen.items()],
             jobs=plan))
    print("TRAIN selection:", chosen, flush=True)
    ledgers = args.out/"ledgers"
    ledgers.mkdir()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_with_ttl, evaluation, role, ttl, ledgers)
                   for role, ttl in plan]
        summaries = [f.result(timeout=120) for f in futures]
    put(args.out/"summary.json", summaries)
    print("scored", len(summaries), "within budget", sum(x["eligible"] for x in summaries),
          flush=True)


if __name__ == "__main__":
    main()
