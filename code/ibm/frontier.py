"""Report all existing TTL candidates; do not select an operating point on DEV."""
from concurrent.futures import ProcessPoolExecutor
import argparse
import gzip
import json
from pathlib import Path

import run
from rebudget import TTLS, put, run_with_ttl, sha

MODES = ("stock", "fixed", "calibrated")
REPORT_ANCHOR_TTL = 30


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--archived-ttl60", type=Path, required=True)
    p.add_argument("--train", type=Path, required=True)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    assert 1 <= a.workers <= 8
    a.out.mkdir(exist_ok=False)
    with gzip.open(a.bundle, "rt", encoding="utf-8") as f:
        bundle = json.load(f)
    archived = json.loads(a.archived_ttl60.read_text())
    train = json.loads((a.train/"EVALUATION_SEAL.json").read_text())
    assert len(archived) == 58 and not any(x["eligible"] for x in archived)
    assert len(train["choices"]) == 4 and all(x["ttl"] == 60 for x in train["choices"])
    roles = [(role, ttl) for role in run.roles()
             if role[3] == "natural" and role[4] in MODES for ttl in TTLS]
    assert len(roles) == 144 and len(set(roles)) == 144
    put(a.out/"seal.json", dict(schema="frontier-v1", roles=roles,
        reason="TRAIN-selected TTL60 transferred above budget; exhaust the already-declared finite TTL grid",
        selection="NO DEV selection, paired rows at all TTLs, TTL30 is fixed reporting anchor (half the prior 60 s)",
        anchor=REPORT_ANCHOR_TTL, budget_gbs=bundle["physics"]["train_budget_24h_gbs"],
        prior_sha256={"archived_ttl60":sha(a.archived_ttl60),
                      "train":sha(a.train/"EVALUATION_SEAL.json")},
        hashes={p.name:sha(p) for p in (a.bundle, Path(__file__),
                 Path(run.__file__), Path(run.__file__).with_name("rebudget.py"),
                 Path(run.__file__).with_name("kernel_v1.py"))}))
    directories = {ttl:a.out/("ttl"+str(ttl)) for ttl in TTLS}
    for folder in directories.values():
        folder.mkdir()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures = [pool.submit(run_with_ttl, bundle, role, ttl, directories[ttl])
                   for role, ttl in roles]
        rows = [f.result(timeout=120) for f in futures]
    put(a.out/"summary.json", rows)
    print("all",len(rows),"eligible",sum(r["eligible"] for r in rows),
          "anchor eligible",sum(r["ttl"]==REPORT_ANCHOR_TTL and r["eligible"] for r in rows),
          flush=True)


if __name__ == "__main__":
    main()
