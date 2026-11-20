"""Export only the relative times and experiment fields used by the scorer.

This is an offline adapter for an authorized archived research tree. It does
not retrieve measurements, consult a cluster, or execute bytes from the source
archive. Publish the resulting evidence only after checking release rights.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from verify import Evidence


def write_csv(path, rows):
    with path.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path, expected_sha=None):
    if expected_sha is not None and fingerprint(path) != expected_sha:
        raise ValueError(f"{path.name} does not match the archived receipt")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


OLD = "c192f6e011daa20fe9f52080509505c65267afbf1aaa9e13a5657d476e8d7058"
HISTORICAL_RECEIPT = {
    "fixed": "4dd5a7bd846588c0cdcb0aa9544cbfe0fe07561a94434234a5633918c047400c",
    "signal": "c037d5fe8efd8e8db0df71295ca2cb4fe9ce50072809d7a12fa2b8fc59346804",
}
REFIT_RECEIPT = "d56ed7e0481617b4edccf9cea3597bfe9ff143d3e8319cb210e63112844d2c0d"
CLOCK_RECEIPT = "dd1cc71a9c71463a9327832b9011bf5e9aadc9bd87fb11c10267127f03f41f8d"
ENDPOINT_LEDGER = "6e2548d4082b8bd28b162b56c5e924e2c9be85aa549467ca8ba83db46b4b18b6"
ORIGINAL_NORMALIZED = (
    "56-calibration-events.csv",
    "56-calibration-samples.csv",
    "56-calibration.json",
    "56-evaluation-events.csv",
    "56-frozen-predictions.csv",
    "96-bind-fixed-sensitivity-fit.json",
    "96-calibration-events.csv",
    "96-calibration-samples.csv",
    "96-calibration.json",
    "96-evaluation-events.csv",
    "96-frozen-predictions.csv",
)


def recorded_rows(name, folder, seal_key):
    receipt_path = folder / "receipt.json"
    if fingerprint(receipt_path) != HISTORICAL_RECEIPT[name]:
        raise ValueError("Unexpected historical scoring receipt")
    receipt = json.loads(receipt_path.read_bytes())
    if receipt[seal_key] != OLD:
        raise ValueError("Historical campaign did not use the pinned model")
    files = receipt["outputs_sha256"]
    events = read_csv(folder / "event_ledger.csv", files["event_ledger.csv"])
    predictions = read_csv(folder / "request_predictions.csv", files["request_predictions.csv"])
    index = {r["trial_id"]: r for r in predictions}
    rows = []
    for i, r in enumerate(events, 1):
        p = index[r["trial_id"]]
        if r["analysis_status"] != "SCORED" or r["competition"] != p["competition"]:
            raise ValueError("Historical trial not fully scored")
        signal = name == "signal"
        bind = r["bind_lo_s"] if signal else r["y_binding_success_native_s"]
        if signal and bind != r["bind_hi_s"]:
            raise ValueError("Historical signal endpoint is not the exact matched Bind")
        rows.append(dict(
            trial_id=f"{name}-{i:03}", block=r["block"], session=r.get("session", ""),
            capacity=r["competition"], offset_s=r["planned_offset_s"],
            arrival_s=r["y_ingress_s"], submit_s=r["y_submit_s"], bind_s=bind,
            signal_input_s=r.get("online_handler_rel_intent_s", ""),
            observed_no_fit=int(r["y_same_uid_no_fit_count"]) > 0,
            cache_lo_s=r.get("cache_exact_lo_s", ""), cache_hi_s=r.get("cache_exact_hi_s", ""),
            handler_source_s=r.get("handler_lo_s", ""),
            **{f"M{i}_frozen_arrival_to_bind_s": p[f"M{i}_predict_W_s"] if i < 3 or signal else ""
               for i in range(4)},
        ))
    if len(rows) != len(index) or len(rows) != 80:
        raise ValueError("Missing historical trial")
    return rows


def export(root, archive, out):
    workspace = root / "outputs"
    older = workspace / "platform_return_20261110"
    seal_path = older / "MODEL_SEAL_V3.json"
    if fingerprint(seal_path) != OLD:
        raise ValueError("Different historical calibration seal")
    seal = json.loads(seal_path.read_bytes())
    stage = older / "calibration_normalized_v3.json"
    if fingerprint(stage) != seal["calibration_normalized_sha256"]:
        raise ValueError("Different historical calibration ledger")
    original_cal = json.loads(stage.read_bytes())["trials"]

    refit_path = workspace / "professor_m4_followup_20261111/ARCHIVED_M4_SEAL_V2.json"
    if fingerprint(refit_path) != REFIT_RECEIPT:
        raise ValueError("Unexpected retrospective calibration seal")
    refit = json.loads(refit_path.read_bytes())
    if refit["prior_model_sha256"] != OLD or not refit["no_evaluation_input_opened_by_calibration"]:
        raise ValueError("Invalid retrospective calibration lineage")
    signal_root = workspace / "platform_return_signal_20261110"
    preflight = read_csv(signal_root / "instrument_checks_v2/event_ledger.csv", refit["calibration_ledger_sha256"])
    historical_terms = {
        "old_return_s": seal["return_delay_s"]["B"],
        "old_fresh_s": seal["learned_terms"]["ordinary_submit_to_bind_s"],
        "old_uncontended_calibration": [
            {"submit_s": t["submit"], "bind_s": t["bind"]}
            for t in original_cal if t["competition"] == "spare"
        ],
        "contemporary_calibration": [
            {"capacity": t["competition"], "cache_lo_s": t["cache_exact_lo_s"],
             "cache_hi_s": t["cache_exact_hi_s"], "submit_s": t["submit_lo_s"], "bind_s": t["bind_lo_s"]}
            for t in preflight
        ],
        "reference_contemporary_return_s": refit["m4_s"],
    }
    fixed = recorded_rows("fixed", workspace / "platform_return_boundary_20261110/score_v1", "frozen_model_sha256")
    signal = recorded_rows("signal", signal_root / "evaluation_scores_v2", "original_model_sha256")
    audited_path = root / "paper/fgcs_retirement/revision2/results/clock_audit.json"
    if fingerprint(audited_path) != CLOCK_RECEIPT:
        raise ValueError("Unexpected audited clock observations")
    audited = json.loads(audited_path.read_bytes())
    cutoff = [{"trial_id": r["trial_id"], "occupancy": r["cache_occupancy_at_cutoff"]}
              for r in audited["rows"]]
    if len(cutoff) != len({r["trial_id"] for r in cutoff}) or any(
            r["occupancy"] not in ("VERIFIED_X_HELD", "UNIDENTIFIED") for r in cutoff):
        raise ValueError("unexpected cutoff occupancy witness")
    out.mkdir(parents=True, exist_ok=False)
    with Evidence(archive) as source:
        source.verify_manifest()
        for name in ORIGINAL_NORMALIZED:
            (out / name).write_bytes(source.read_bytes(name))
        endpoint = source.read_bytes("endpoint/bind-endpoint-ledger.csv")
        if hashlib.sha256(endpoint).hexdigest() != ENDPOINT_LEDGER:
            raise ValueError("Unexpected alternate endpoint observations")
        (out / "bind-endpoint-ledger.csv").write_bytes(endpoint)
    write_csv(out / "historical-fixed.csv", fixed)
    write_csv(out / "historical-signal.csv", signal)
    write_csv(out / "96-cutoff-occupancy.csv", cutoff)
    (out / "historical-calibration.json").write_text(json.dumps(historical_terms, indent=2) + "\n", encoding="utf-8")
    files = {p.name: fingerprint(p) for p in sorted(out.iterdir())}
    (out / "manifest.json").write_text(json.dumps(
        {"schema": "PREDICTION_OFFLINE_EVIDENCE_V1", "files": files}, indent=2) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--research-root", type=Path, required=True, help="local source tree with historical outputs/")
    p.add_argument("--normalized", type=Path, required=True, help="separate review evidence ZIP or directory")
    p.add_argument("--out", type=Path, required=True, help="new normalized evidence directory")
    a = p.parse_args()
    export(a.research_root, a.normalized, a.out)


if __name__ == "__main__":
    main()
