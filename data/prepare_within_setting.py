"""Build the normalized, offline evidence for the within-setting Kind evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


PHASES = (
    ("calibration", "ws23-cal", 12, "calibration-cluster"),
    ("evaluation_v2", "ws23-eva", 48, "evaluation-cluster"),
)
UNCHANGED_TRIAL_FILES = ("x-manifest.json", "signal.jsonl", "scheduler-events.json")
DELETE_KEYS = ("x_delete", "first_z_delete", "later_z_delete")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")


def copy(source: Path, target: Path) -> None:
    with target.open("xb") as f:
        f.write(source.read_bytes())


def fields(row: dict, *keys: str) -> dict:
    return {k: row[k] for k in keys if k in row}


def bind_projection(row: dict) -> dict:
    """Keep exactly the audit fields read by the frozen Bind verifier."""
    out = fields(row, "verb", "stage", "requestReceivedTimestamp", "stageTimestamp", "auditID")
    request = row.get("requestObject") or {}
    out.update(
        objectRef=fields(row.get("objectRef") or {}, "subresource", "name", "namespace"),
        requestObject={
            "metadata": fields(request.get("metadata") or {}, "uid"),
            "target": fields(request.get("target") or {}, "name"),
        },
        responseStatus=fields(row.get("responseStatus") or {}, "code"),
    )
    return out


def summary_projection(row: dict) -> dict:
    """Retain DELETE timings and UID receipts, not full Pod API responses."""
    for key in DELETE_KEYS:
        if key in row:
            entry = row[key]
            response = entry["response"]
            metadata = fields(
                response.get("metadata") or {}, "namespace", "name", "uid", "deletionTimestamp"
            )
            row[key] = {
                **fields(entry, "submit_s", "ack_s"),
                "response": {**fields(response, "kind"), "metadata": metadata},
            }
    return row


def validate_phase(path: Path, prefix: str, n: int) -> list[str]:
    plan_bytes = (path / "PLAN.json").read_bytes()
    plan = json.loads(plan_bytes)
    term = read(path / "TERMINAL.json")
    if (
        term["plan_sha256"] != hashlib.sha256(plan_bytes).hexdigest()
        or [term[k] for k in ("planned", "attempted", "complete")] != [n] * 3
        or not term["admitted"]
        or term["error"] is not None
    ):
        raise ValueError(f"{path.name}: expected a complete, admitted physical phase")
    ids = [a["trial_id"] for a in plan["trials"]]
    expected = [f"{prefix}-{i:02}" for i in range(n)]
    if ids != expected or {p.stem for p in path.glob("trial-*.json")} != {f"trial-{i}" for i in ids}:
        raise ValueError(f"{path.name}: planned and saved trial IDs do not match")
    return ids


def build(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents:
        raise ValueError("output must be separate from the frozen source")
    phase_ids = {name: validate_phase(source / name, prefix, n) for name, prefix, n, _ in PHASES}
    old = read(source / "evaluation" / "TERMINAL.json")
    if old["attempted"] != 0 or old["complete"] != 0 or old["admitted"]:
        raise ValueError("expected the preserved zero-trial storage stop")
    seal = source / "calibration" / "CALIBRATION_SEAL.json"
    eval_plan = read(source / "evaluation_v2" / "PLAN.json")
    if eval_plan["seal_sha256"] != hashlib.sha256(seal.read_bytes()).hexdigest():
        raise ValueError("evaluation does not use the archived calibration seal")
    stamp = (source / "ANALYSIS_V2.sha256").read_text(encoding="utf-8").split()
    if len(stamp) != 2 or not re.fullmatch("[0-9a-f]{64}", stamp[0]) or not stamp[1].endswith("/score_v2.py"):
        raise ValueError("invalid score_v2 checksum record")

    output.mkdir(parents=True, exist_ok=False)
    with (output / "ANALYSIS_V2.sha256").open("x", encoding="utf-8") as f:
        f.write(f"{stamp[0]}  ./score_v2.py\n")
    for phase, _, _, label in PHASES:
        root, target = source / phase, output / phase
        target.mkdir()
        for name in ("PLAN.json", "TERMINAL.json"):
            copy(root / name, target / name)
        if phase == "calibration":
            copy(seal, target / seal.name)
        write(target / "environment.json", {"node_id": label})
        for tid in phase_ids[phase]:
            ledger = f"trial-{tid}.json"
            write(target / ledger, summary_projection(read(root / ledger)))
            (target / tid).mkdir()
            for name in UNCHANGED_TRIAL_FILES:
                copy(root / tid / name, target / tid / name)
            binds = read(root / tid / "binding-audit.json")
            write(target / tid / "binding-audit.json", [bind_projection(x) for x in binds])

    old_target = output / "evaluation"
    old_target.mkdir()
    for name in ("PLAN.json", "TERMINAL.json"):
        copy(source / "evaluation" / name, old_target / name)
    write(
        output / "NORMALIZATION.json",
        {
            "format": "fgcs-within-setting-public-evidence/1",
            "scope": "12 calibration and 48 independent evaluation Kind trials; offline readback only",
            "previous_attempt": "evaluation/ contains plan and terminal for the zero-trial storage stop",
            "unchanged": [
                "plans, terminal records and the calibration seal, preserved byte for byte",
                "X manifests, handler signals and scoped scheduler-event records",
                "experiment timestamps, Pod UIDs, boot IDs, audit IDs, metric and trial values",
            ],
            "normalized": [
                "binding-audit records retain only the fields read by the Bind verifier",
                "DELETE responses retain command timings and scoped Pod identity receipts only",
                "environment contains two distinct synthetic cluster labels instead of host/Docker inventory",
                "analysis checksum record uses a portable relative scorer path",
            ],
            "not_included": [
                "kubeconfigs, host configuration, runtime addresses and credential metadata",
                "Docker inventory, capacity snapshots, unused manifests and runtime stderr",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="frozen local campaign root")
    parser.add_argument("--out", type=Path, required=True, help="new public evidence directory")
    args = parser.parse_args()
    build(args.source, args.out)


if __name__ == "__main__":
    main()
