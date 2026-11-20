"""Compare freshly replayed IBM grids to archived summaries, excluding wall runtime."""
import argparse
import json
import math
from pathlib import Path


def read(folder, name, count):
    rows = json.loads((folder / name).read_bytes())
    if len(rows) != count:
        raise ValueError(f"Wrong role count in {name}: {len(rows)} != {count}")
    keyed = {(tuple(row["role"]), row.get("ttl")): {k: v for k, v in row.items() if k != "duration_s"}
             for row in rows}
    if len(keyed) != count:
        raise ValueError(f"Duplicate roles in {name}")
    return keyed


def compare(report, results, reference, specs):
    for name, count in specs:
        actual = read(results, name, count)
        expected = read(reference, name, count)
        if actual.keys() != expected.keys():
            raise ValueError(f"{name}: role census changed")
        # TRAIN accumulation can vary by a few ULP across platforms. All roles,
        # classification/counts, and non-cost values still compare exactly.
        allowed = {"cost_gbs", "tail_gbs"} if name == "train_summary.json" else set()
        delta = 0.0
        rounded_roles = 0
        for role, row in actual.items():
            ref = expected[role]
            if row == ref:
                continue
            rounded_roles += 1
            if row.keys() != ref.keys():
                raise ValueError(f"{name}: unexpected keys: {role}")
            for key in row:
                if row[key] == ref[key]:
                    continue
                if key not in allowed or not math.isclose(row[key], ref[key], rel_tol=0, abs_tol=2e-9):
                    raise ValueError(f"{name}: non-wall result changed: {role} {key}")
                delta = max(delta, abs(row[key] - ref[key]))
        report[name] = {"cells": count, "eligible": sum(r["eligible"] for r in actual.values()),
                        "rows_with_train_rounding": rounded_roles, "max_train_rounding_gbs": delta}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True, help="ibm/grid-reference evidence directory")
    for option in ("ttl60", "rebudget", "frontier"):
        p.add_argument("--" + option, type=Path)
    args = p.parse_args()
    if not (args.ttl60 or args.rebudget or args.frontier):
        p.error("pass at least one fresh result directory")
    report = {}
    for result, folder, specs in (
        (args.ttl60, "results", (("summary.json", 58),)),
        (args.rebudget, "rebudget", (("train_summary.json", 72), ("summary.json", 58))),
        (args.frontier, "frontier", (("summary.json", 144),)),
    ):
        if result is not None:
            report[folder] = {}
            compare(report[folder], result, args.reference / folder, specs)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
