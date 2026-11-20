"""Finite closed-loop IBM replay using the unchanged native event engine."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import json
import math
from pathlib import Path
import signal
import statistics
from time import monotonic

from kernel_v1 import Kernel, NativeApp, Request

SCHEMA = "fgcs-closed-loop-v1"
CAPS = (8, 16)
DURATIONS = (2, 5)
DAYS = ("day1", "day2")
MODES = ("stock", "fixed", "signal", "calibrated", "oracle")
MAX_WAIT = 1.0
WATCHDOG = 1.0
SLO = 8.21376629075  # fixed original TRAIN-derived replay threshold; not an SLA
EPS = 1e-9


def forecast(mode, t0, handler_at, delivered_at, true_at, now, d, fixed, residual):
    if mode == "oracle":
        if true_at is None:
            raise ValueError("oracle requires a private already-retiring return timestamp")
        return true_at
    if true_at is not None:
        raise ValueError("non-oracle must not receive hidden return timestamp")
    if mode in ("signal", "calibrated") and (
            handler_at is not None and delivered_at is not None
            and t0 <= handler_at <= delivered_at <= now):
        return handler_at + d + residual
    if mode in ("signal", "configured"):
        return t0 + d
    if mode in ("fixed", "calibrated"):
        return t0 + fixed
    raise ValueError(mode)


def select_victims(free, required, retiring, candidates, now, max_wait):
    """Use predictions only to defer a retirement; never to authorize admission."""
    if free + EPS >= required:
        return (), None
    projected = free
    for eta, memory in sorted(retiring):
        if not now < eta <= now + max_wait:
            continue  # overdue forecasts cannot be treated as freed memory
        projected += memory
        if projected + EPS >= required:
            return (), eta
    selected = []
    # No projected near-term fit: issue immediate retirement to the same LRU
    # idle-victim menu, capped by the shortfall if those victims do return.
    for ident, memory in candidates:
        if projected + EPS >= required:
            break
        selected.append(ident)
        projected += memory
    return tuple(selected), None


def index(n, *keys):
    out = hashlib.sha256("|".join(map(str, keys)).encode()).digest()
    return int.from_bytes(out[:8], "big") % n


def phys(bundle, d):
    raw = [r for r in bundle["calibration"] if float(r["duration"]) == d
           and r["capacity"] == "contended"]
    samples = [
        (float(r["h"]), float(r["signal_available"]),
         (float(r["r_lo"]) + float(r["r_hi"])) / 2, r["trial_id"])
        for r in raw
    ]
    assert len(samples) == 6 and len({s[-1] for s in samples}) == 6
    assert all(0 <= h <= delivery < release and release >= h + d
               for h, delivery, release, _ in samples)
    return samples, statistics.median(s[2] for s in samples), statistics.median(
        s[2] - s[0] - d for s in samples)


class Loop(Kernel):
    def __init__(self, configs, requests, *, day, d, mode, treatment, physics, law, **kwargs):
        self.day, self.d, self.mode, self.treatment = day, d, mode, treatment
        self.seed, self.prep = physics["seed"], physics["prep_s"]
        self.samples, self.fixed, self.residual = law
        self.ordinal = Counter()
        self.public_signal, self.private_release, self.pending = {}, {}, {}
        self.decision_rows = []
        self.lifecycle_rows = []
        self.overdue = 0
        self.max_stock = 0.0
        self.last = None
        super().__init__(configs, requests, prep_sampler=self._prepare,
                         return_sampler=self._sample_return,
                         record_events=False, **kwargs)
        self.default_ttl = 60.0  # match the TRAIN-selected TTL60 operating point

    def _prepare(self, app, origin, now):
        assert origin == "reactive" and self._queues[app.app_id]
        trigger = self._queues[app.app_id][0]
        return self.prep[index(len(self.prep), self.seed, self.day, app.app_id, trigger, "prep")]

    def _sample_return(self, app, origin, now):
        n = self.ordinal[app.app_id]
        self.ordinal[app.app_id] += 1
        self.last = self.samples[index(len(self.samples), self.seed, self.day, app.app_id, n, "release")]
        return self.last[2]

    def _emit(self, kind, **fields):
        super()._emit(kind, **fields)
        if kind == "retirement_intent":
            ident, app = fields["instance"], fields["app"]
            h, delivered, release, _ = self.last
            self.pending[ident] = (self.now, app, self.configs[app].memory_gb)
            self.public_signal[ident] = (self.now + h, self.now + delivered)
            self.private_release[ident] = self.now + release
            self.lifecycle_rows.append((kind, ident, app, self.now, fields["reason"]))
        elif kind == "confirmed_return":
            ident = fields["instance"]
            assert abs(self.private_release.pop(ident) - self.now) <= EPS
            self.public_signal.pop(ident)
            self.pending.pop(ident)
            self.lifecycle_rows.append((kind, ident, fields["app"], self.now, "observed_replay_event"))

    def _integrate(self, at):
        self.max_stock = max(self.max_stock, self.reserved_gb)
        super()._integrate(at)

    def _view(self, mode):
        seen, view, expired = 0, [], 0
        for ident, (t0, _, gb) in self.pending.items():
            h, delivered = self.public_signal[ident]
            if self.treatment == "late500":
                delivered += .5
            if self.treatment == "missing" or delivered > self.now:
                h = delivered = None  # even event timestamp withheld pre-receipt
            seen += int(h is not None)
            due = forecast(mode, t0, h, delivered,
                           self.private_release[ident] if mode == "oracle" else None,
                           self.now, self.d, self.fixed, self.residual)
            if self.treatment == "bias250" and mode == "calibrated":
                due += .25  # common policy, deliberate prediction-only perturbation
            expired += int(due <= self.now)
            view.append((due, gb))
        return view, seen, expired

    def _make_room(self, app_id):
        # Scores never override physical admission. No changed policy in tail.
        if self.mode == "stock" or self._closing:
            return super()._make_room(app_id)
        required = self.configs[app_id].memory_gb
        assert self._queues[app_id] and required <= self.capacity_gb
        free = self.capacity_gb - self.reserved_gb
        options = sorted((p for p in self._instances.values() if p.state == "idle"),
                         key=lambda p: (p.idle_since, p.ident))
        candidates = [(p.ident, self.configs[p.app_id].memory_gb) for p in options]
        view, seen, expired = self._view(self.mode)
        fixed, _, _ = self._view("fixed")
        active, until = select_victims(free, required, view, candidates, self.now, MAX_WAIT)
        shadow, shadow_until = select_victims(free, required, fixed, candidates, self.now, MAX_WAIT)
        self.overdue += expired
        self.decision_rows.append(dict(t=self.now, app=app_id, head=self._queues[app_id][0],
                                       free_gb=free, required_gb=required,
                                       pending=len(view), visible=seen, victims=active,
                                       fixed_shadow_victims=shadow, wait_until=until,
                                       fixed_shadow_wait_until=shadow_until))
        for ident in active:
            self._retire(self._instances[ident], "capacity")


def ms(values):
    if not values:
        return dict(mean=None, p95=None, p99=None)
    ordered = sorted(values)
    def q(p):
        x = p * (len(ordered) - 1)
        i = int(x)
        return 1000 * (ordered[i] + (x - i) * (ordered[min(i + 1, len(ordered)-1)] - ordered[i]))
    return dict(mean=1000 * statistics.mean(values), p95=q(.95), p99=q(.99))


def roles():
    for day in DAYS:
        for cap in CAPS:
            for d in DURATIONS:
                for mode in MODES:
                    yield day, cap, d, "natural", mode
        for treatment in ("missing", "late500", "bias250"):
            for mode in ("fixed", "signal", "calibrated"):
                yield day, 8, 5, treatment, mode


def run_cell(bundle, role, out=None, limit_s=90):
    def timeout(*_):
        raise TimeoutError("finite cell wall timeout")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(limit_s)
    wall = monotonic()
    day, cap, d, treatment, mode = role
    trace, cfg, p = bundle["trace"][day], bundle["configs"], bundle["physics"]
    requests = [Request(str(i), str(a), float(t), float(r))
                for i, a, t, r in trace["requests"]]
    k = Loop([NativeApp(str(a), **x) for a, x in cfg.items()], requests,
             day=day, d=d, mode=mode, treatment=treatment, physics=p, law=phys(bundle, d),
             start=trace["start"], capacity_gb=cap)
    # Common clock: on exogenous dispatch plus periodic retries when any
    # request remains queued. No observer-arrival-triggered decision leaks.
    for i in range(1, int(round((trace["end"] - trace["start"]) / WATCHDOG))):
        k.run_until(trace["start"] + i * WATCHDOG)
        if any(k._waiting_other.values()):
            k._dispatch_all()
    result = k.finish(trace["end"], max_tail=p["tail_limit_s"])
    n = len(requests)
    rows = result["completed_request_rows"]
    assert result["finished"] and result["observed_requests"] == result["completed_requests"] == n
    assert not k.pending and not k.public_signal and not k.private_release
    assert result["remaining_reserved_gb"] <= EPS and k.max_stock <= cap + EPS
    assert all(math.isclose(x["response"], x["initialization_wait"] + x["other_wait"]
                            + x["completed"] - x["started"], abs_tol=1e-8) for x in rows)
    cost = result["reservation_gb_seconds"]
    budget = p["train_budget_24h_gbs"]
    counts = result["counts"]
    dec = k.decision_rows
    summary = dict(role=role, n=n, completed=n, budget_gbs=budget, cost_gbs=cost,
                   excess_gbs=max(0.0, cost - budget), eligible=cost <= budget,
                   retiring_gbs=result["cost_by_state_gbs"]["retiring"],
                   state_cost_gbs=result["cost_by_state_gbs"],
                   tail_gbs=cost-sum(result["cost_through_T_by_state_gbs"].values()),
                   cold=result["cold_requests"], creations=counts.get("creations", 0),
                   reclaim=counts.get("retire_capacity", 0), stock_max=k.max_stock,
                   slo_s=SLO, slo_misses=sum(x["response"] > SLO for x in rows),
                   service_alone_gt_slo=sum(r.duration > SLO for r in requests),
                   waits=ms([x["other_wait"] for x in rows]),
                   initialization=ms([x["initialization_wait"] for x in rows]),
                   response=ms([x["response"] for x in rows]),
                   blocked=sum(x["other_wait"] > EPS for x in rows),
                   decisions=len(dec), signal_visible=sum(x["visible"] > 0 for x in dec),
                   waits_due_to_forecast=sum(x["wait_until"] is not None for x in dec),
                   decisions_different_from_fixed_same_state=sum(
                       x["victims"] != x["fixed_shadow_victims"] for x in dec),
                   duration_s=monotonic()-wall)
    if out is not None:
        path = out / ("_".join(map(str, role)) + ".json.gz")
        with path.open("xb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as f:
            f.write(json.dumps(dict(summary=summary, decisions=dec, requests=rows,
                                    instances=result["instance_rows"], lifecycle=k.lifecycle_rows),
                               separators=(",", ":"), allow_nan=False).encode())
    signal.alarm(0)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    assert 1 <= args.workers <= 8
    with gzip.open(args.bundle, "rt", encoding="utf-8") as stream:
        bundle = json.load(stream)
    jobs = list(roles())
    if args.smoke:
        jobs = [("day1", 8, 5, "natural", "calibrated")]
    assert len(jobs) == len(set(jobs))
    args.out.mkdir(exist_ok=False)
    run_seal = dict(schema=SCHEMA, roles=jobs, physical=phys(bundle, 2)[1:]+phys(bundle, 5)[1:],
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    kernel_sha256=hashlib.sha256(Path(__file__).with_name("kernel_v1.py").read_bytes()).hexdigest(),
                    bundle_sha256=hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
                    workers=args.workers, slo_s=SLO, wait_s=MAX_WAIT, watchdog_s=WATCHDOG, timeout_cell_s=90)
    with (args.out / "seal.json").open("x") as f:
        json.dump(run_seal, f, indent=2)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_cell, bundle, role, args.out) for role in jobs]
        summaries = []
        for role, f in zip(jobs, futures):
            summary = f.result(timeout=120)
            assert tuple(summary["role"]) == role
            summaries.append(summary)
            print(*role, summary["eligible"], round(summary["duration_s"], 2), flush=True)
    with (args.out / "summary.json").open("x") as f:
        json.dump(summaries, f, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
