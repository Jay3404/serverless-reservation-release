"""Recalculate the paper's Bind-time prediction results from archived inputs."""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal as D
import json
from math import isclose, isfinite
from pathlib import Path
from statistics import fmean, mean, median
import subprocess
import sys

from audit_boundary_rejections import audit as audit_rejections
from verify import Evidence, TOL, compute, paired, percentile, rescore


MODELS = ("M3-new", "M4-new", "M3+M4")


def need(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, message, tol=TOL):
    need(isclose(actual, expected, abs_tol=tol, rel_tol=0), message)


def fit_observed_terms(ev, n):
    """Compute medians/ranges directly from the distinct calibration event rows."""
    rows = ev.load_csv(f"{n}-calibration-events.csv")
    terms = ev.load_json(f"{n}-calibration.json")["terms_seconds"]
    need(len(rows) == len({r["trial_id"] for r in rows}) == 24, "bad calibration census")
    out = {}
    for duration, frozen in terms.items():
        sample = defaultdict(list)
        for r in rows:
            if r["duration"] != duration:
                continue
            # The earlier calibration ledger carries no condition label.
            need(r["condition"] in ({"calibration", ""} if n == 56 else {"calibration"})
                 and r["status"] == "complete",
                 "evaluation or failed row in calibration")
            a, s, b = (D(r[k]) for k in ("a", "s", "b"))
            need(0 <= a <= s <= b, "bad calibration timeline")
            if r["capacity"] == "spare":
                sample["new_overhead_s"].append(b-s)
            elif r["capacity"] == "contended":
                low, high, h = (D(r[k]) for k in ("r_lo", "r_hi", "h"))
                need(0 <= h <= low <= high <= b, "invalid observed cache bracket")
                mid = (low+high)/2
                sample["fixed_return_s"].append(mid)
                sample["residual_s"].append(mid-h-D(duration))
                sample["retry_overhead_s"].append(b-mid)
            else:
                raise ValueError("Unknown calibration condition")
        need(set(sample) == set(frozen) and all(len(x) == 6 for x in sample.values()),
             "incorrect six-trial calibration strata")
        for key in sample:
            close(float(median(sample[key])), frozen[key], f"original {n}-trial fit differs: {key}")
        out[duration] = {k: dict(n=len(v), median_s=float(median(v)), min_s=float(min(v)),
                                max_s=float(max(v))) for k, v in sample.items()}
    return out


def bind_optimum(rows, u, v):
    """Find the exact closed interval(s) minimizing the absolute Bind error."""
    need(u > v > 0 and rows, "invalid continuous piecewise fit")
    knots = sorted({D(0)} | {c for s, b in rows for c in (s, s+u-v, b-v) if c >= 0})
    errors = [sum(abs(max(s+u, r+v)-b) for s, b in rows) for r in knots]
    minimum = min(errors)
    intervals = []
    for i, r in enumerate(knots):
        if errors[i] == minimum:
            if i and errors[i-1] == minimum:
                intervals[-1][1] = r
            else:
                intervals.append([r, r])
    return intervals


def fit_sensitivity(ev):
    cal = ev.load_csv("96-calibration-events.csv")
    evaluation = ev.load_csv("96-evaluation-events.csv")
    terms = json.loads(ev.read_bytes("96-calibration.json"), parse_float=D)["terms_seconds"]
    prior = ev.load_json("96-bind-fixed-sensitivity-fit.json")["fit"]
    fits = {}
    for d, term in sorted(terms.items()):
        u, v = (term[k] for k in ("new_overhead_s", "retry_overhead_s"))
        pairs = [(D(r["s"]), D(r["b"])) for r in cal
                 if r["duration"] == d and r["capacity"] == "contended"]
        need(len(pairs) == 6, "fit must use six contended calibration rows")
        intervals = bind_optimum(pairs, u, v)
        reference = [[D(x) for x in line] for line in prior[d]["bind_loss_minimizer_intervals_s"]]
        need(len(reference) == len(intervals) and
             all(abs(a-b) <= D("0.000000001") for x, y in zip(intervals, reference) for a, b in zip(x, y)),
             "frozen optimal fixed-return interval not reproduced")
        # Score the complete calibration-optimal set against the separate
        # evaluation dataset; no constant is selected using the evaluation MAE.
        pairs = [(D(r["s"]), D(r["b"])) for r in evaluation
                 if r["duration"] == d and r["condition"] == "natural_boundary"]
        need(len(pairs) == 18, "bad sensitivity evaluation denominator")
        knots = {c for s, b in pairs for c in (s+u-v, b-v)}
        ms = [mean(abs(max(s+u, r+v)-b) for s, b in pairs) * 1000
              for low, high in intervals for r in {low, high} | {k for k in knots if low < k < high}]
        fits[d] = dict(calibration_optimum_s=[[str(x) for x in a] for a in intervals],
                       n_eval=len(pairs), min_ms=float(min(ms)), max_ms=float(max(ms)))
    total = {key: fmean(r[key] for r in fits.values()) for key in ("min_ms", "max_ms")}
    for key, expected in (("min_ms", 108.515), ("max_ms", 113.267)):
        close(total[key], expected, "paper fixed sensitivity mismatch", .0006)
    return {"post_hoc": True, "per_duration": fits, "boundary_mae_ms": total}


def historical(ev):
    """Use the common-overhead geometry on the two earlier 80-trial campaigns."""
    conf = ev.load_json("historical-calibration.json")
    fresh = [r["bind_s"] - r["submit_s"] for r in conf["old_uncontended_calibration"]]
    need(len(fresh) == 16, "historical baseline must use 16 spare calibration rows")
    overhead = median(fresh)
    close(overhead, conf["old_fresh_s"], "historical fresh-path overhead mismatch")
    preflight = conf["contemporary_calibration"]
    mids = [(float(r["cache_lo_s"])+float(r["cache_hi_s"]))/2 for r in preflight
            if r["capacity"] == "contended" and r["cache_lo_s"] and r["cache_hi_s"]]
    need(len(preflight) == 10 and len(mids) == 2, "invalid retrospective calibration sample count")
    recent = median(mids)
    close(recent, conf["reference_contemporary_return_s"], "retrospective fixed fit mismatch")
    out = {"calibration_spare_n": len(fresh), "spare_path_ms": overhead*1000,
           "spare_path_range_ms": [min(fresh)*1000, max(fresh)*1000],
           "retrospective_preflight_n": len(preflight), "usable_cache_brackets": len(mids),
           "retrospective_fixed_s": recent}
    output_rows = []
    for name in ("fixed", "signal"):
        rows = ev.load_csv(f"historical-{name}.csv")
        counts = Counter(r["capacity"] for r in rows)
        need(len({r["trial_id"] for r in rows}) == 80 and counts == {"contended": 40, "spare": 40},
             "missing 80-trial campaign")
        err = defaultdict(list)
        retry, tail = [], []
        for r in rows:
            a, s, b = (float(r[key]) for key in ("arrival_s", "submit_s", "bind_s"))
            need(all(map(isfinite, (a, s, b))) and 0 <= a <= s <= b, "historical timeline inconsistent")
            h = float(r["signal_input_s"]) if r["signal_input_s"] else 0.0
            need(0 <= h <= s, "handler input unavailable at submission")
            ret = {"M0": 0., "M1": 5., "M2": conf["old_return_s"]}
            if name == "signal":
                ret.update(M3=h+5, M4=recent)
            for m, x in ret.items():
                block = max(0., x-s) if r["capacity"] == "contended" else 0.
                forecast = s-a+overhead+block
                if m != "M4":
                    close(forecast, float(r[f"{m}_frozen_arrival_to_bind_s"]), "archived 80-trial forecast differs")
                e = abs(forecast-(b-a))*1000
                err[r["capacity"], m].append(e)
                output_rows.append(dict(campaign=name, trial=r["trial_id"], capacity=r["capacity"],
                                        model=m, absolute_error_ms=e, predicted_arrival_to_bind_s=forecast))
            if r["cache_lo_s"] and r["cache_hi_s"] and r["capacity"] == "contended":
                mid = (float(r["cache_lo_s"])+float(r["cache_hi_s"]))/2
                retry.append((b-mid)*1000)
                tail.append((mid-float(r["handler_source_s"])-5)*1000)
        out[name] = {c: {m: {"n": len(err[c, m]), "mae_ms": fmean(err[c, m])} for m in ret} for c in counts}
        if name == "signal":
            need(len(retry) == len(tail) == 16, "16 historical fit/requeue brackets expected")
            out["followup_phases"] = {"n": len(retry), "cache_to_bind_median_ms": median(retry),
                                      "handler_plus_cleanup_to_cache_median_ms": median(tail)}
    for name, expected in (("fixed", {"M1": 178.53, "M2": 198.03}),
                           ("signal", {"M1": 92.55, "M2": 165.42, "M3": 8.79, "M4": 42.69})):
        for model, value in expected.items():
            close(out[name]["contended"][model]["mae_ms"], value, f"reported historical {name} {model} mismatch", .006)
    return out, output_rows


def campaign56(ev):
    terms = ev.load_json("56-calibration.json")["terms_seconds"]
    rows = ev.load_csv("56-evaluation-events.csv")
    by_model = {m: {r["trial_id"]:compute(r, terms[r["duration"]], m)[-1] for r in rows}
                for m in MODELS}
    uses = {g: [r for r in rows if r["condition"] == g] for g in ("boundary", "early")}
    need([len(uses[x]) for x in ("boundary", "early")] == [24, 8], "56-round census")
    need(all(by_model["M3+M4"][r["trial_id"]] == by_model["M3-new"][r["trial_id"]] for r in uses["boundary"])
         and all(by_model["M3+M4"][r["trial_id"]] == by_model["M4-new"][r["trial_id"]] for r in uses["early"]),
         "retrospective fallback changed the assigned model")
    out = {}
    for group, samples in uses.items():
        ref, candidate = ("M4-new","M3-new") if group == "boundary" else ("M3-new","M4-new")
        a, b = by_model[ref], by_model[candidate]
        out[group] = {
            "original_reference": ref, "original_candidate": candidate,
            "paired": paired(a, b, samples),
            "session": {s:paired(a, b, [r for r in samples if r["session"]==s])
                        for s in sorted({r["session"] for r in samples})},
        }
    need(all(s["gain_ms"] > 0 for s in out["boundary"]["session"].values()),
         "not all four original boundary session means improved")
    for group, expected in (("boundary", (63.898, 3.529)), ("early", (346.131, 57.754))):
        for key, center in zip(("baseline_mae_ms", "candidate_mae_ms"), expected):
            close(out[group]["paired"][key], center, "56-round original value mismatch", .001)
    return out


def score_predictions(ev):
    rows = ev.load_csv("96-evaluation-events.csv")
    terms = ev.load_json("96-calibration.json")["terms_seconds"]
    pred, gain = [], []
    for r in rows:
        by_model = {}
        for m in MODELS:
            _, b, _, err = compute(r, terms[r["duration"]], m)
            by_model[m] = abs(err)
            pred.append(dict(trial_id=r["trial_id"], condition=r["condition"], model=m, scored="True",
                             bind_confirmed=r["bind_confirmed"], y_uid=r["y_pod"],
                             predicted_bind_s=b, absolute_error_ms=abs(err)))
        if r["condition"] == "natural_boundary":
            ref, hy = by_model["M4-new"], by_model["M3+M4"]
            gain.append(dict(trial=r["trial_id"], session=int(r["session"]), cleanup_s=int(float(r["duration"])),
                             target_minus_cleanup_s=str(D(r["offset"])-D(r["duration"])),
                             reference_abs_ms=ref, hybrid_abs_ms=hy, gain_ms=ref-hy))
    need(len(pred) == 288 and len(gain) == 36, "bad plot denominator")
    return rows, pred, gain


def clock_and_paths(ev, events, gains):
    cut = {r["trial_id"]:r["occupancy"] for r in ev.load_csv("96-cutoff-occupancy.csv")}
    trials = {r["trial_id"]:r for r in events}
    need(len(cut) == 96 and set(cut)==set(trials) and
         Counter(cut.values()) == {"VERIFIED_X_HELD":86,"UNIDENTIFIED":10},
         "measured and unidentified cache cases differ")
    bounds = [r for r in events if r["r_lo"] and r["r_hi"]]
    need(len(bounds) == 61, "bounded cache release sample count changed")
    fields = ("cutoff_to_create_command_ms", "cutoff_timestamp_capture_bracket_us",
              "host_wall_monotonic_disagreement_us", "app_alignment_probe_disagreement_us",
              "handler_realtime_monotonic_rel_intent_disagreement_us", "bind_log_minus_audit_response_ms",
              "scheduler_cache_bracket_width_us")
    def describe(vals):
        vals = sorted(vals)
        return dict(n=len(vals), min=vals[0], median=median(vals), max=vals[-1], p95_linear=percentile(vals,.95))
    clock = {key:describe([float(r[key]) for r in events if r[key]]) for key in fields}
    clock["controller_node_realtime_probe_bracket_ms"] = describe([
        v for r in events for v in json.loads(r["controller_node_realtime_probe_bracket_ms"])])
    chosen = sorted(gains, key=lambda r:r["gain_ms"], reverse=True)[:3]
    frozen = {(r["trial_id"],r["model"]):r for r in ev.load_csv("96-frozen-predictions.csv")}
    largest = []
    for row in chosen:
        i = row["trial"]
        a, b = (frozen[i,m] for m in ("M4-new","M3+M4"))
        largest.append(dict(trial=i, gain_ms=row["gain_ms"], capacity=trials[i]["capacity"],
                            fixed_path=a["predicted_branch"], hybrid_path=b["predicted_branch"],
                            scheduler_logged_memory_failure=trials[i]["nofit"]=="True"))
    return {"bind_scored_n": len(trials), "cache_return_brackets_n":len(bounds),
            "past_cutoff_x_reservation":dict(Counter(cut.values())), "observed_clock_descriptions":clock,
            "largest_boundary_gains":largest}


def endpoint(ev, evaluation, fresh_predictions):
    rows = ev.load_csv("bind-endpoint-ledger.csv")
    index = {r["trial_id"]:r for r in evaluation}
    pred = {(r["trial_id"], r["model"]):r["predicted_bind_s"] * 1000 for r in fresh_predictions}
    deltas = []
    out = defaultdict(lambda: defaultdict(list))
    need(len(rows) == len({r["y_uid_pseudonym"] for r in rows}) == 96 and
         set(index) == {r["trial_id"] for r in rows}, "bad endpoint census")
    for r in rows:
        i, a = r["trial_id"], index[r["trial_id"]]
        need(r["condition"] == a["condition"] and r["same_uid_successful_bind"] == "1", "endpoint trial mismatch")
        close(float(r["cutoff_ms"]), float(a["s"])*1000, "cutoff changed", TOL*1000)
        bind = float(r["scheduler_success_log_ms"])
        api = float(r["bind_audit_response_complete_ms"])
        close(bind, float(a["b"])*1000, "scheduler endpoint changed", TOL*1000)
        close(bind-api, float(r["log_minus_audit_complete_ms"]), "endpoint spacing changed", TOL*1000)
        need(float(r["bind_audit_request_received_ms"]) <= api <= bind, "matched Bind endpoint order impossible")
        deltas.append(bind-api)
        for key, model in zip(("m3","m4","hybrid"), MODELS):
            predicted = pred[i, model]
            close(predicted, float(r[key+"_predicted_bind_ms"]), "endpoint ledger forecast is not frozen", TOL*1000)
            out[r["condition"]]["log",key].append(abs(predicted-bind))
            out[r["condition"]]["api",key].append(abs(predicted-api))
    checks = {
        "natural_boundary": ("m4", 109.273, 3.110, 106.163),
        "natural_early": ("m3", 335.136, 83.700, 251.437),
    }
    summary = {}
    for group, keys in out.items():
        summary[group] = {}
        for source in ("log", "api"):
            a = {m: fmean(keys[source,m]) for m in ("m3","m4","hybrid")}
            if group in checks:
                ref, base, hybrid, gain = checks[group]
                a.update(paired_gain_ms=fmean(x-y for x,y in zip(keys[source,ref], keys[source,"hybrid"])))
                if source == "api":
                    for k, expected in ((ref, base), ("hybrid", hybrid), ("paired_gain_ms", gain)):
                        close(a[k], expected, "paper API endpoint sensitivity mismatch", .001)
            summary[group][source] = a
    return {"same_uid_n": len(rows), "scheduler_after_api_ms": [min(deltas), median(deltas), max(deltas)],
            "errors": summary, "is_absolute_clock_accuracy": False}


def write_csv(path, rows):
    with path.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def reproduce(evidence, out, figures=False, font_dir=None):
    with Evidence(evidence) as ev:
        result = rescore(ev)
        result["calibration_empirical"] = {str(n):fit_observed_terms(ev, n) for n in (56,96)}
        result["fixed_sensitivity"] = fit_sensitivity(ev)
        result["rejection_diagnostic"] = audit_rejections(ev)
        result["earlier_56_paired"] = campaign56(ev)
        result["historical_campaigns"], history = historical(ev)
        events, pred, gain = score_predictions(ev)
        result["clock_and_paths"] = clock_and_paths(ev, events, gain)
        result["endpoint"] = endpoint(ev, events, pred)
    out.mkdir(parents=True)
    (out/"prediction_report.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    write_csv(out/"predictions96.csv", pred)
    write_csv(out/"boundary_gains.csv", gain)
    write_csv(out/"historical_predictions.csv", history)
    if figures:
        plotter = Path(__file__).resolve().parents[1] / "figures/plot_prediction.py"
        args = [sys.executable, "-B", plotter, "--predictions", out/"predictions96.csv",
                "--boundary-gains", out/"boundary_gains.csv", "--out", out/"figures"]
        if font_dir is not None:
            args.extend(("--font-dir", font_dir))
        subprocess.run(args, check=True)
    print("Bind prediction: 56/96 and both historical 80-trial campaigns reproduced.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, required=True, help="normalized prediction evidence directory or ZIP")
    p.add_argument("--out", type=Path, required=True, help="new output directory")
    p.add_argument("--figures", action="store_true", help="rebuild Figures 1–4 (ReportLab required)")
    p.add_argument("--font-dir", type=Path, help="optional DejaVu Sans directory for figures")
    args = p.parse_args()
    reproduce(args.evidence, args.out, args.figures, args.font_dir)


if __name__ == "__main__":
    main()
