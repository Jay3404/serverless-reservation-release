"""Export minimal request and instance ledgers from the archived PPO evaluation."""

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


RUNS = {**{f"D{s}": f"dev_D_s{s}" for s in (11, 22, 33, 44, 55)},
        "Planner": "ref_planner_h120_lambda2p0"}
REQUESTS = ("request_id", "arrival", "started", "completed", "cold",
            "initialization_wait", "other_wait")


def sha(path):
    with path.open("rb") as stream:
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
        return h.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def export(study, out):
    import numpy as np

    inventory = {x["relative_path"]: x["sha256"] for x in
                 json.loads((study / "terminal_inventory_v2/inventory.json").read_bytes())["files"]}

    def check(path):
        rel = path.relative_to(study).as_posix()
        digest = sha(path)
        require(inventory.get(rel) == digest, f"Run inventory hash mismatch: {rel}")
        return digest

    out.mkdir(parents=True, exist_ok=False)
    for label, name in RUNS.items():
        orig = study / "jobs" / name
        source_hashes = {n: check(orig / n) for n in (
            "status.json", "result.json", "pass1_requests.npz",
            "pass1_request_instance_links.npz", "pass1_instances.json.gz")}
        status = json.loads((orig / "status.json").read_bytes())
        result = json.loads((orig / "result.json").read_bytes())
        require(status["status"] == "completed" and status["exit_code"] == 0
                and not status["TEST_opened"] and status["result_sha256"] == source_hashes["result.json"],
                f"Invalid completion contract: {label}")
        ep, = result["episodes"]
        receipt = {k: ep[k] for k in ("evaluation_start", "evaluation_end", "target_gbs",
                                      "reservation_gb_seconds", "cost_through_T_by_state_gbs",
                                      "cold_requests", "completed_requests", "initialization_wait", "other_wait")}
        receipt["source_sha256"] = source_hashes
        if label != "Planner":
            seed = int(label[1:])
            checkpoint = study / "jobs" / f"train_D_s{seed}" / "checkpoint_pass3.pt"
            sha_checkpoint = check(checkpoint)
            require(status["job"]["seed"] == seed and status["job"]["checkpoint_sha256"] == sha_checkpoint
                    and result["evaluation_weight_before"] == result["evaluation_weight_after"]
                    and result["evaluation_optimizer_updates"] == 0, f"Bad frozen weights: {label}")
            receipt.update(checkpoint_sha256=sha_checkpoint, weight_sha256=result["evaluation_weight_before"])
        dest = out / label
        dest.mkdir()
        (dest / "receipt.json").write_text(json.dumps(receipt, allow_nan=False) + "\n", encoding="utf-8")

        with np.load(orig / "pass1_requests.npz", allow_pickle=False) as requests, np.load(
                orig / "pass1_request_instance_links.npz", allow_pickle=False) as links, gzip.open(
                dest / "requests.csv.gz", "wt", newline="", encoding="utf-8") as stream:
            require(np.array_equal(requests["request_id"], links["request_id"]), f"Link order: {label}")
            w = csv.writer(stream)
            w.writerow((*REQUESTS, "instance"))
            values = [requests[k].tolist() for k in REQUESTS] + [links["instance_id"].tolist()]
            w.writerows(zip(*values, strict=True))

        with gzip.open(orig / "pass1_instances.json.gz", "rt", encoding="utf-8") as stream:
            instances = json.load(stream)
        # Preserve per-instance accounting and all observed TTL/idle episodes,
        # but do not distribute training checkpoints, logs or external app identifiers.
        rows = []
        for x in instances:
            row = {k: x[k] for k in ("instance", "born", "confirmed_return", "cost_gbs")}
            life = x["observed_lifecycle"]
            row.update({k: life[k] for k in ("retirement", "ttl_assignments", "idle_episodes")})
            rows.append(row)
        with gzip.open(dest / "instances.json.gz", "wt", encoding="utf-8") as stream:
            json.dump(rows, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--study", type=Path, required=True, help="original sealed experiment directory")
    cli.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    export(args.study, args.out)


if __name__ == "__main__":
    main()
