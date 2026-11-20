"""Descriptive post-hoc boundary diagnostic; never a replacement primary analysis.

Scoring follows fgcs-evidence/audit_boundary_rejections.py (SHA-256
c2372adc4ee045dec7524488e657b5b2789ce8f9f4806bcd646323ec667d9a94).
Only evidence loading is adapted; all 36 prespecified boundary trials remain.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from statistics import mean

from verify import Evidence


FIXED = "M4-new"
HYBRID = "M3+M4"
CONFIGURED = "M3-new"


def round_ms(v):
    return str(v.quantize(Decimal("0.001")))


def audit(evidence):
    if not __debug__:
        raise RuntimeError("Do not use Python -O: archived validation uses assertions")
    events = evidence.load_csv("96-evaluation-events.csv")
    trials = {r["trial_id"]: r for r in events}
    assert len(events) == len(trials) == 96
    predicted = evidence.load_csv("96-frozen-predictions.csv")
    forecast = {(r["trial_id"], r["model"]): r for r in predicted}
    assert len(predicted) == len(forecast) == 288
    assert set(forecast) == {(i, m) for i in trials for m in (FIXED, HYBRID, CONFIGURED)}
    assert len({r["y_pod"] for r in events}) == 96
    assert all(r["bind_confirmed"] == "True" and r["http_status"] == "200" for r in events)
    boundary = [r for r in events if r["condition"] == "natural_boundary"]
    assert len(boundary) == 36 and all(r["capacity"] == "contended" for r in boundary)

    def summarize(rows, reference):
        errors = []
        paths = Counter()
        for r in rows:
            a, b = (forecast[r["trial_id"], m] for m in (reference, HYBRID))
            target = Decimal(r["b"])
            ea, eb = (abs(Decimal(f["predicted_bind_s"]) - target) * 1000 for f in (a, b))
            paths[f'{a["predicted_branch"]} -> {b["predicted_branch"]}'] += 1
            errors.append((ea, eb))
        return {
            "n": len(rows),
            "reference": reference,
            "reference_mae_ms": round_ms(mean(a for a, _ in errors)),
            "hybrid_mae_ms": round_ms(mean(b for _, b in errors)),
            "wins": sum(a > b for a, b in errors),
            "ties": sum(a == b for a, b in errors),
            "losses": sum(a < b for a, b in errors),
            "paths": dict(sorted(paths.items())),
        }

    by_rejection = {
        v: summarize([r for r in boundary if r["nofit"] == flag], FIXED)
        for v, flag in (("logged", "True"), ("not_logged", "False"))
    }
    assert by_rejection["logged"]["n"] + by_rejection["not_logged"]["n"] == 36
    by_duration_s = {
        condition: {
            duration: summarize(
                [r for r in events if r["condition"] == condition and Decimal(r["duration"]) == int(duration)],
                baseline,
            )
            for duration in ("2", "5")
        }
        for condition, baseline in (("natural_boundary", FIXED), ("natural_early", CONFIGURED))
    }
    return {
        "source": ["96-evaluation-events.csv", "96-frozen-predictions.csv"],
        "post_hoc": True,
        "primary_comparison_replaced": False,
        "by_logged_rejection": by_rejection,
        "by_assigned_duration_s": by_duration_s,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True, help="external ZIP or extracted evidence directory")
    args = parser.parse_args(argv)
    with Evidence(args.evidence) as evidence:
        evidence.verify_manifest()
        result = audit(evidence)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
