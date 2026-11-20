"""Verify the separate, externally supplied IBM release-sensitivity tables."""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def read_verified_csv(directory, manifest, name):
    path = directory / name
    require(hashlib.sha256(path.read_bytes()).hexdigest() == manifest[name],
            f"SHA-256 mismatch: {name}")
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_ibm(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["files"]
    cells, pairs, rankings = (
        read_verified_csv(directory, manifest, f"ibm-all-{table}.csv")
        for table in ("cells", "pairs", "rankings")
    )
    require((len(cells), len(pairs), len(rankings)) == (210, 174, 70),
            "unexpected archived table census")

    def scenario(row):
        return tuple(row[field] for field in ("batch", "day", "H_gb", "endpoint"))

    groups = defaultdict(list)
    for row in cells:
        groups[scenario(row)].append(row)
        require(math.isclose(float(row["other_ms_per_arrival"]),
                             1000 * float(row["other_s"]) / int(row["arrivals"]),
                             abs_tol=1e-8), (row["role_id"], "other wait"))
        cost, target = float(row["cost_gbs"]), float(row["target_gbs"])
        require(math.isclose(float(row["over_gbs"]), max(0.0, cost - target),
                             abs_tol=1e-5), (row["role_id"], "budget excess"))
        require(row["within_budget"] == str(cost <= target),
                (row["role_id"], "budget eligibility"))
    require(len(groups) == len(rankings) and set(groups) == {scenario(row) for row in rankings},
            "ranking keys differ")
    for row in rankings:
        key = scenario(row)
        group = groups[key]
        require(len(group) == 3, (key, "policy census"))
        rank = [c["policy"] for c in sorted(group, key=lambda c: float(c["total_wait_s"]))]
        require(rank == row["rank_total_wait"].replace(" ", "").split("<"),
                (key, "total wait ranking"))
        eligible = {c["policy"] for c in group if c["within_budget"] == "True"}
        reported = row["eligible"].replace(" ", "").replace("|", ",").split(",")
        require(eligible == set(filter(None, reported)), (key, "eligible policies"))

    return dict(cells=len(cells), contrast_rows=len(pairs), ranking_scenarios=len(rankings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="External evidence directory with the manifest and IBM CSVs.")
    args = parser.parse_args()
    print(json.dumps(verify_ibm(args.directory), indent=2))


if __name__ == "__main__":
    main()
