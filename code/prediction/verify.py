"""Recompute normalized 56/96 Bind forecasts using external evidence only.

Prediction/scoring functions derive from fgcs-evidence/verify.py, SHA-256
ac33e707f25788f57b4d61db896729004d1616e76698125a5273fb2c250c3717.
The adapter adds explicit directory/ZIP input, not new measurements or formulas.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath


TOL = 1e-8  # seconds; same tolerance as the archived verifier
ARCHIVED_VERIFIER_SHA256 = "ac33e707f25788f57b4d61db896729004d1616e76698125a5273fb2c250c3717"


class Evidence:
    """Read an extracted evidence directory or ZIP without extracting files."""

    def __init__(self, source):
        self.archive = None
        if isinstance(source, (str, Path)) and Path(source).is_dir():
            self.root = Path(source)
        else:
            self.root = None
            self.archive = zipfile.ZipFile(source)
            names = self.archive.namelist()
            if len(names) != len(set(names)):
                self.archive.close()
                raise ValueError("Evidence ZIP contains duplicate member names")
            self.prefix = "fgcs-evidence/" if "fgcs-evidence/manifest.json" in names else ""

    def close(self):
        if self.archive is not None:
            self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read_bytes(self, name):
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or str(path) != name:
            raise ValueError("Evidence filenames must be safe relative paths")
        if self.archive is not None:
            return self.archive.read(self.prefix + name)
        return (self.root / name).read_bytes()

    def load_csv(self, name):
        return list(csv.DictReader(io.StringIO(self.read_bytes(name).decode("utf-8"), newline="")))

    def load_json(self, name):
        return json.loads(self.read_bytes(name).decode("utf-8"))

    def verify_manifest(self):
        files = self.load_json("manifest.json")["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("Nonempty archived manifest required")
        for name, expected in files.items():
            if hashlib.sha256(self.read_bytes(name)).hexdigest() != expected:
                raise ValueError("Evidence hash mismatch: " + name)
        return len(files)


def compute(row, terms, model):
    duration = float(row["duration"])
    s = float(row["s"])
    a = float(row["a"])
    b = float(row["b"])
    use_event = row["signal_used_by_model"] == "True"
    fixed = terms["fixed_return_s"]
    event = float(row["handler_input_s"]) + duration + terms["residual_s"] if use_event else duration
    r = {"M4-new": fixed, "M3-new": event, "M3+M4": event if use_event else fixed}[model]
    if row["capacity"] == "spare" or r <= s:
        pred = s + terms["new_overhead_s"]
    else:
        pred = max(s + terms["new_overhead_s"], r + terms["retry_overhead_s"])
    return r, pred, pred - a, (pred - b) * 1000


def percentile(sorted_values, p):
    j = p * (len(sorted_values) - 1)
    lo, hi = math.floor(j), math.ceil(j)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (j - lo)


def summarize(values):
    errors = sorted(abs(v) for v in values)
    return {
        "n": len(values), "mae_ms": statistics.mean(errors),
        "bias_ms": statistics.mean(values), "median_ae_ms": statistics.median(errors),
        "p95_ae_ms_linear": percentile(errors, .95),
    }


def paired(against, candidate, rows):
    gains = [abs(against[r["trial_id"]]) - abs(candidate[r["trial_id"]]) for r in rows]
    ranked = sorted(gains, reverse=True)
    return {
        "n": len(rows), "signal_available_n": sum(r["signal_used_by_model"] == "True" for r in rows),
        "baseline_mae_ms": statistics.mean(abs(against[r["trial_id"]]) for r in rows),
        "candidate_mae_ms": statistics.mean(abs(candidate[r["trial_id"]]) for r in rows),
        "gain_ms": statistics.mean(gains), "median_gain_ms": statistics.median(gains),
        "improved": sum(v > TOL * 1000 for v in gains),
        "equal": sum(abs(v) <= TOL * 1000 for v in gains),
        "worsened": sum(v < -TOL * 1000 for v in gains),
        "top3_share": sum(ranked[:3]) / sum(gains) if sum(gains) > 0 else None,
        "gain_excluding_top3_ms": statistics.mean(ranked[3:]) if len(gains) > 3 else None,
    }


def campaign(n, evidence):
    # Apart from explicit evidence readers, this follows the archived campaign().
    load_json, load_csv = evidence.load_json, evidence.load_csv
    terms = load_json(f"{n}-calibration.json")["terms_seconds"]
    cal = load_csv(f"{n}-calibration-events.csv")
    calib = {r["trial_id"] for r in cal}
    assert len(calib) == len(cal) == 24
    samples = defaultdict(list)
    for row in load_csv(f"{n}-calibration-samples.csv"):
        assert row["trial_id"] in calib
        samples[row["duration"], row["term"]].append(float(row["value_s"]))
    for d, values in terms.items():
        for key, expected in values.items():
            assert math.isclose(statistics.median(samples[d, key]), expected, abs_tol=TOL)
    events = load_csv(f"{n}-evaluation-events.csv")
    assert len(events) == n and not calib & {r["trial_id"] for r in events}
    assert not {r[k] for r in events for k in ("x_pod", "y_pod")} & {r[k] for r in cal for k in ("x_pod", "y_pod")}
    for r in events:
        assert r["x_pod"] != r["y_pod"]
        if n == 96:
            assert r["observer_x_pod"] == r["x_pod"]
            assert r["forecast_built_before_create_verified"] == "True"
            if r["signal_used_by_model"] == "True":
                assert r["signal_input_x_pod"] == r["x_pod"]
                assert float(r["received_ms"]) <= float(r["verified_ms"]) <= float(r["s"]) * 1000 + TOL
                assert float(r["delivered_ms"]) <= float(r["s"]) * 1000 + TOL
            else:
                assert not r["signal_input_x_pod"] and not r["handler_input_s"]
    rows = {r["trial_id"]: r for r in events}
    all_errors = defaultdict(dict)
    frozen = load_csv(f"{n}-frozen-predictions.csv")
    assert len(frozen) == n * (3 if n == 96 else 2)
    for p in frozen:
        r = rows[p["trial_id"]]
        result = compute(r, terms[str(float(r["duration"]))], p["model"])
        for k, got in zip(("predicted_return_s", "predicted_bind_s", "predicted_arrival_to_bind_s"), result[:3]):
            assert math.isclose(got, float(p[k]), rel_tol=0, abs_tol=TOL), (p["trial_id"], p["model"], k, got, p[k])
        assert p["trial_id"] not in all_errors[p["model"]]
        all_errors[p["model"]][p["trial_id"]] = result[3]
    grouped = defaultdict(list)
    for r in events:
        grouped[r["condition"]].append(r)
    if n == 96:
        grouped["natural_contended"] = grouped["natural_boundary"] + grouped["natural_early"]
        for r in events:
            i = r["trial_id"]
            model = "M3-new" if r["signal_used_by_model"] == "True" else "M4-new"
            assert all_errors["M3+M4"][i] == all_errors[model][i]
            if r["capacity"] == "spare":
                assert len({values[i] for values in all_errors.values()}) == 1
    model_stats = {g: {m: summarize([v[r["trial_id"]] for r in subset])
                       for m, v in sorted(all_errors.items())} for g, subset in sorted(grouped.items())}
    result = {"calibration_rows": len(calib), "evaluation_rows": len(rows), "forecasts_matched": len(frozen),
              "conditions": model_stats}
    if n == 96:
        primary = {}
        for g, model in (("natural_boundary", "M4-new"), ("natural_early", "M3-new"),
                         ("natural_contended", "M3-new"), ("natural_contended", "M4-new")):
            a, b, rs = all_errors[model], all_errors["M3+M4"], grouped[g]
            primary[g + "/" + model] = {
                "overall": paired(a, b, rs),
                "duration": {d: paired(a, b, [r for r in rs if float(r["duration"]) == d]) for d in (2, 5)},
                "session": {s: paired(a, b, [r for r in rs if r["session"] == s])
                            for s in sorted({r["session"] for r in rs})},
                "omit_session": {s: paired(a, b, [r for r in rs if r["session"] != s])
                                 for s in sorted({r["session"] for r in rs})},
            }
        result["primary_paired"] = primary
    return result


def rescore(evidence, campaigns=(56, 96)):
    if not __debug__:
        raise RuntimeError("Do not use Python -O: archived validation uses assertions")
    count = evidence.verify_manifest()
    results = {str(n): campaign(n, evidence) for n in campaigns}
    return {"manifest_files_verified": count, "campaigns": results,
            "scope": "normalized archived forecasts only; no acquisition or IBM replay",
            "archived_verifier_sha256": ARCHIVED_VERIFIER_SHA256}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True, help="external ZIP or extracted fgcs-evidence directory")
    parser.add_argument("--campaign", choices=("56", "96", "all"), default="all")
    parser.add_argument("--json", action="store_true", help="print detailed descriptive condition/session scores")
    args = parser.parse_args(argv)
    campaigns = (56, 96) if args.campaign == "all" else (int(args.campaign),)
    with Evidence(args.evidence) as evidence:
        result = rescore(evidence, campaigns)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("External manifest and archived calibration/forecast checks passed.")
        for n, report in result["campaigns"].items():
            print(f"{n}-round forecasts: {report['forecasts_matched']} matched.")
            for key, section in report.get("primary_paired", {}).items():
                s = section["overall"]
                print(f"{key}, n={s['n']}: {s['baseline_mae_ms']:.6f} -> {s['candidate_mae_ms']:.6f} ms, "
                      f"paired gain {s['gain_ms']:.6f} ms ({s['improved']}/{s['equal']}/{s['worsened']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
