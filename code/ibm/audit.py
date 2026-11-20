"""Recount the 58 closed-loop outputs without importing the replay or kernel.

Usage: python -B audit.py RUN_DIR --bundle SEALED_INPUT.json.gz --out-dir AUDIT_DIR
Existing outputs are not modified. No request-level independence is assumed.
"""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
STATES = ('init', 'busy', 'idle', 'retiring')
CAPS = (8, 16)
DURATIONS = (2, 5)
DAYS = ('day1', 'day2')
MODES = ('stock', 'fixed', 'signal', 'calibrated', 'oracle')
PERTURB = ('missing', 'late500', 'bias250')
EPS = 1e-9
CLOCK_TOL = 2e-8
COST_TOL = 2e-6
SLO = 8.21376629075
BUDGET = 392845.20121905557
EXPECTED = {(day, cap, duration, 'natural', mode)
            for day in DAYS for cap in CAPS for duration in DURATIONS for mode in MODES} | {
                (day, 8, 5, condition, mode) for day in DAYS for condition in PERTURB
                for mode in ('fixed', 'signal', 'calibrated')}


def require(ok, context):
    if not ok:
        raise ValueError(context)


def close(x, y, context, abs_tol=CLOCK_TOL, rel_tol=1e-10):
    require(math.isclose(x, y, rel_tol=rel_tol, abs_tol=abs_tol), (context, x, y))


def read(path):
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else
          path.open(encoding='utf-8')) as stream:
        return json.load(stream)


def sha(path):
    with path.open('rb') as stream:
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
        return h.hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def cell_name(role):
    return '_'.join(map(str, role)) + '.json.gz'


def distribution(x):
    ordered = sorted(x)

    def quantile(q):
        i = q * (len(ordered) - 1)
        return 1000 * (ordered[math.floor(i)] + (i % 1) *
                       (ordered[math.ceil(i)] - ordered[math.floor(i)]))

    return dict(mean=1000 * math.fsum(ordered) / len(ordered), p95=quantile(.95), p99=quantile(.99))


def sampled_release(times, seed, day, app_id, ordinal):
    identifier = f'{seed}|{day}|{app_id}|{ordinal}|release'.encode()
    key = int.from_bytes(hashlib.sha256(identifier).digest()[:8], 'big')
    return times[key % len(times)]


def event_sweep(events, lo, hi, limit):
    """Independent cap/cost integration from lifetime births and true releases."""
    live = infix = total = peak = 0.
    last = lo
    for t, change in sorted(events.items()):
        require(t >= last, ('event outside trace window or descending', t, last))
        total += (t - last) * live
        infix += max(0., min(t, hi) - last) * live
        live += change
        require(-1e-8 <= live <= limit + 1e-8, ('lifetime reservation geometry', t, live, limit))
        peak = max(peak, live)
        last = t
    close(live, 0., 'lifetime sweep after full tail')
    return total, total - infix, peak


def audit_cell(value, expected, bundle, seal):
    s = value['summary']
    role = tuple(s['role'])
    day, cap, d, condition, mode = role
    require(s == expected, ('summary and ledger summary differ', role))
    src = bundle['trace'][day]
    t_end = src['end']
    raw = {row[0]: row[1:] for row in src['requests']}
    req = value['requests']
    acts = value['decisions']
    events = value['lifecycle']
    instances = value['instances']
    cfg = bundle['configs']
    release_samples = [(r['r_lo'] + r['r_hi']) / 2 for r in bundle['calibration']
                       if r['duration'] == d and r['capacity'] == 'contended']
    require(len(release_samples) == 6, ('calibration sample count', role))
    require(len(raw) == len(req) == len({r['request_id'] for r in req})
            == s['n'] == s['completed'] == (25319 if day == 'day1' else 24800), ('census', role))
    require(set(raw) == {r['request_id'] for r in req}, ('denominator IDs', role))
    require(s['slo_s'] == seal['slo_s'] == SLO, ('SLO changed', role))
    totals = Counter()
    warm_starts, cold_starts = defaultdict(list), defaultdict(list)
    for r in req:
        rid = r['request_id']
        app, arrival, duration = raw[rid]
        require(r['app_id'] == app and r['arrival'] == arrival and src['start'] <= arrival < t_end,
                ('request input changed', role, rid))
        require(arrival <= r['started'] <= r['completed'] and r['initialization_wait'] >= 0
                and r['other_wait'] >= 0, ('request chronology', role, rid))
        close(r['completed'] - r['started'], duration, (role, rid, 'native service'))
        close(r['response'], r['completed'] - arrival, (role, rid, 'response'))
        close(r['started'] - arrival, r['other_wait'] + r['initialization_wait'],
              (role, rid, 'other wait + initialization'))
        require(type(r['cold']) is bool and (r['cold'] or r['initialization_wait'] <= EPS),
                ('warm request charged initialization', role, rid))
        claim = r['started'] - r['initialization_wait']
        (cold_starts if r['cold'] else warm_starts)[app].append((claim, r['started'], r['completed'], rid))
        totals.update(cold=int(r['cold']), slo_misses=int(r['response'] > SLO),
                      service_alone_gt_slo=int(duration > SLO), blocked=int(r['other_wait'] > EPS))
    require(all(s[k] == totals[k] for k in ('cold', 'slo_misses', 'service_alone_gt_slo', 'blocked')),
            ('request summary differs', role, dict(totals)))
    for label, field in [('waits', 'other_wait'), ('initialization', 'initialization_wait'), ('response', 'response')]:
        for k, v in distribution([r[field] for r in req]).items():
            close(v, s[label][k], (role, label, k))

    # A retired instance must keep its reservation through the sampled real
    # return (no time credit from a prediction), regardless of its retirement cause.
    by_id = {p['instance']: p for p in instances}
    require(len(by_id) == len(instances) == s['creations'], ('creation census', role))
    intents, returns = {}, {}
    ordinals = Counter()
    last = src['start']
    for name, ident, app, t, reason in events:
        require(t >= last and ident in by_id and by_id[ident]['app'] == app, ('lifecycle order/identity', role, ident))
        last = t
        if name == 'retirement_intent':
            require(ident not in intents, ('duplicate retirement', role, ident))
            sampled = sampled_release(release_samples, bundle['physics']['seed'], day, app, ordinals[app])
            ordinals[app] += 1
            intents[ident] = (t, reason, sampled)
        elif name == 'confirmed_return':
            require(ident in intents and ident not in returns and reason == 'observed_replay_event',
                    ('unmatched/duplicate return', role, ident))
            rt, _, delay = intents[ident]
            close(t, rt + delay, (role, ident, 'observed return vs seeded physical law'), rel_tol=0)
            returns[ident] = t
        else:
            raise ValueError(('unknown lifecycle event', role, name))
    require(set(intents) == set(returns) == set(by_id), ('not full lifecycle census', role))
    require(sum(e[1] == 'capacity' for e in intents.values()) == s['reclaim'], ('actual retire count', role))
    state_parts = defaultdict(list)
    occupancy = defaultdict(float)
    available_by_app = defaultdict(list)
    for ident, x in by_id.items():
        gb = cfg[x['app']]['memory_gb']
        born, returned = x['born'], x['confirmed_return']
        retired, _, _ = intents[ident]
        require(src['start'] <= born <= retired <= returned <= t_end + bundle['physics']['tail_limit_s']
                and not x['speculative'] and set(x['cost_gbs']) == set(STATES)
                and all(v >= 0 for v in x['cost_gbs'].values()), ('instance ledger chronology', role, ident))
        close(returns[ident], returned, (role, ident, 'confirmed-return join'), rel_tol=0)
        close(x['cost_gbs']['retiring'], gb * (returned - retired), (role, ident, 'retiring cost'), abs_tol=COST_TOL)
        close(math.fsum(x['cost_gbs'].values()), gb * (returned - born), (role, ident, 'lifetime cost'), abs_tol=COST_TOL)
        ready = born + x['cost_gbs']['init'] / gb
        available_by_app[x['app']].append((ready, born, retired))
        occupancy[born] += gb
        occupancy[returned] -= gb
        for state, part in x['cost_gbs'].items():
            state_parts[state].append(part)

    # No per-request instance key is exported; existentially verify that the
    # correct app had a physical initializing/ready instance for each start.
    for app, slots in available_by_app.items():
        slots.sort()
        ready_times = [slot[0] for slot in slots]
        earliest = -math.inf
        j = 0
        for _, start, done, rid in sorted(warm_starts[app], key=lambda q: q[1]):
            while j < len(slots) and slots[j][0] <= start + CLOCK_TOL:
                earliest = max(earliest, slots[j][2])
                j += 1
            require(earliest >= done - CLOCK_TOL, ('no physical warm instance for request', role, rid))
        for claim, start, done, rid in cold_starts[app]:
            beg = bisect_left(ready_times, start - CLOCK_TOL)
            end = bisect_right(ready_times, start + CLOCK_TOL)
            require(any(born <= claim + CLOCK_TOL and retired >= done - CLOCK_TOL for _, born, retired in slots[beg:end]),
                    ('no physical initializing lifetime for request', role, rid))
    require(set(available_by_app) >= (set(cold_starts) | set(warm_starts)), ('missing app lifetime', role))
    for state in STATES:
        close(math.fsum(state_parts[state]), s['state_cost_gbs'][state], (role, state), abs_tol=COST_TOL)
    total_cost, tail_cost, aux_peak = event_sweep(occupancy, src['start'], t_end, cap)
    close(total_cost, s['cost_gbs'], (role, 'lifetime integral'), abs_tol=COST_TOL)
    close(tail_cost, s['tail_gbs'], (role, 'independent post-window integral'), abs_tol=COST_TOL)
    close(s['retiring_gbs'], s['state_cost_gbs']['retiring'], (role, 'retiring total'), abs_tol=COST_TOL)
    require(aux_peak <= s['stock_max'] + 1e-8 and s['stock_max'] <= cap + 1e-8, ('stock maximum', role))
    require(s['budget_gbs'] == bundle['physics']['train_budget_24h_gbs'] == BUDGET
            and s['eligible'] == (s['cost_gbs'] <= BUDGET), ('common budget', role))
    close(max(0., total_cost - BUDGET), s['excess_gbs'], (role, 'budget excess'), abs_tol=COST_TOL)

    actual_by_id = set()
    last = src['start']
    counts = Counter()
    for row in acts:
        t, active, shadow = row['t'], row['victims'], row['fixed_shadow_victims']
        due, sdue = row['wait_until'], row['fixed_shadow_wait_until']
        require(last <= t <= t_end and row['head'] in raw and raw[row['head']][0] == row['app']
                and raw[row['head']][1] <= t, ('matched-state decision cannot see future request head', role, row))
        require(0 <= row['visible'] <= row['pending'] and -EPS <= row['free_gb'] < row['required_gb']
                and row['required_gb'] == cfg[row['app']]['memory_gb'], ('blocked-state proof', role, row))
        require(all(x is None or t < x <= t + seal['wait_s'] for x in (due, sdue))
                and (due is None or not active), ('watch horizon or simultaneous wait/retire', role, row))
        if mode == 'fixed' or (mode == 'calibrated' and condition == 'missing'):
            require(active == shadow and due == sdue, ('fixed/fallback shadow action disagreement', role, row))
        for victim in active:
            require(victim in intents and victim not in actual_by_id and intents[victim][:2] == (t, 'capacity'),
                    ('victim was not really retired at decision', role, row, victim))
            actual_by_id.add(victim)
        counts.update(decisions=1, signal_visible=int(row['visible'] > 0),
                      waits_due_to_forecast=int(due is not None),
                      decisions_different_from_fixed_same_state=int(active != shadow))
        last = t
    require(all(counts[k] == s[k] for k in ('decisions', 'signal_visible', 'waits_due_to_forecast',
                                        'decisions_different_from_fixed_same_state')),
            ('decision summary differs', role, dict(counts)))
    if mode != 'stock':
        require(all(t >= t_end or reason != 'capacity' or ident in actual_by_id
                    for ident, (t, reason, _) in intents.items()), ('unlinked pre-window-end victim', role))
    require(condition != 'missing' or not counts['signal_visible'], ('signal leaked to missing treatment', role))
    comparison_fingerprint = canonical_sha({k: value[k] for k in ('requests', 'instances', 'lifecycle')})
    decision_fingerprint = canonical_sha(acts)
    no_visible_fingerprint = canonical_sha([{k: v for k, v in row.items() if k != 'visible'} for row in acts])
    return dict(role=role, request_rows=len(req), instance_rows=len(instances), lifecycle_rows=len(events),
                actual_linked_victims=len(actual_by_id), cost_gbs=total_cost, tail_gbs=tail_cost, stock_peak_gb=aux_peak,
                trajectory_sha256=comparison_fingerprint, decision_sha256=decision_fingerprint,
                decision_excluding_visibility_sha256=no_visible_fingerprint)


def compare(reference, candidate):
    ref, arm = tuple(reference['role']), tuple(candidate['role'])
    require(ref[:4] == arm[:4] and reference['budget_gbs'] == candidate['budget_gbs']
            and reference['n'] == candidate['n'], ('incomparable pair', ref, arm))
    # All differences are candidate minus reference; negative is less.
    return dict(day=ref[0], cap_gb=ref[1], cleanup_s=ref[2], scenario=ref[3], reference=ref[4], candidate=arm[4],
                n=reference['n'], both_eligible=reference['eligible'] and candidate['eligible'],
                reference_eligible=reference['eligible'], candidate_eligible=candidate['eligible'],
                ref_wait_ms=reference['waits']['mean'], arm_wait_ms=candidate['waits']['mean'],
                delta_wait_ms=candidate['waits']['mean'] - reference['waits']['mean'],
                delta_wait_p95_ms=candidate['waits']['p95'] - reference['waits']['p95'],
                delta_wait_p99_ms=candidate['waits']['p99'] - reference['waits']['p99'],
                delta_init_ms=candidate['initialization']['mean'] - reference['initialization']['mean'],
                delta_response_ms=candidate['response']['mean'] - reference['response']['mean'],
                delta_response_p95_ms=candidate['response']['p95'] - reference['response']['p95'],
                delta_response_p99_ms=candidate['response']['p99'] - reference['response']['p99'],
                ref_cost_gbs=reference['cost_gbs'], arm_cost_gbs=candidate['cost_gbs'],
                delta_cost_gbs=candidate['cost_gbs'] - reference['cost_gbs'],
                delta_cost_pct=100 * (candidate['cost_gbs'] / reference['cost_gbs'] - 1),
                ref_excess_gbs=reference['excess_gbs'], arm_excess_gbs=candidate['excess_gbs'],
                ref_slo=reference['slo_misses'], arm_slo=candidate['slo_misses'],
                delta_slo=candidate['slo_misses'] - reference['slo_misses'],
                delta_slo_pp=100 * (candidate['slo_misses'] - reference['slo_misses']) / reference['n'],
                ref_cold=reference['cold'], arm_cold=candidate['cold'],
                delta_cold=candidate['cold'] - reference['cold'],
                delta_blocked=candidate['blocked'] - reference['blocked'],
                delta_actual_reclaim=candidate['reclaim'] - reference['reclaim'],
                candidate_decisions=candidate['decisions'],
                candidate_same_state_action_changes=candidate['decisions_different_from_fixed_same_state'])


def recalculate(root, bundle_path, runner, kernel):
    seal, summary, bundle = read(root/'seal.json'), read(root/'summary.json'), read(bundle_path)
    require(seal['schema'] == 'fgcs-closed-loop-v1' and seal['source_sha256'] == sha(runner)
            and seal['kernel_sha256'] == sha(kernel) and seal['bundle_sha256'] == sha(bundle_path), 'seal hash mismatch')
    require(len(seal['roles']) == len(summary) == len(EXPECTED) == 58 and
            {tuple(r) for r in seal['roles']} == EXPECTED == {tuple(r['role']) for r in summary}, 'unexpected run census')
    require({cell_name(r) for r in EXPECTED} == {p.name for p in root.glob('*.json.gz')}, 'gzip output census mismatch')
    sources = {tuple(row['role']): row for row in summary}
    audits = {}
    for role in sorted(EXPECTED):
        path = root/cell_name(role)
        audits[role] = dict(cell_sha256=sha(path), **audit_cell(read(path), sources[role], bundle, seal))
    exact_checks = []
    for day in DAYS:
        fx, hybrid = (day, 8, 5, 'missing', 'fixed'), (day, 8, 5, 'missing', 'calibrated')
        require(all(audits[fx][k] == audits[hybrid][k] for k in ('trajectory_sha256', 'decision_sha256')),
                ('missing fallback not exact', day))
        exact_checks.append(dict(day=day, missing_hybrid_equals_fixed_actions_and_full_state=True))
        canon = (day, 8, 5, 'natural', 'fixed')
        for treatment in PERTURB:
            variant = (day, 8, 5, treatment, 'fixed')
            require(all(audits[canon][k] == audits[variant][k] for k in ('trajectory_sha256', 'decision_excluding_visibility_sha256')),
                    ('forecast sensitivity should not affect fixed-control actions/outcomes', variant))
    pairs = []
    for role in sorted(EXPECTED):
        prefix, mode = role[:4], role[4]
        if mode in ('signal', 'calibrated', 'oracle'):
            pairs.append(compare(sources[prefix + ('fixed',)], sources[role]))
        if mode in ('fixed', 'calibrated', 'oracle') and role[3] == 'natural':
            pairs.append(compare(sources[prefix + ('stock',)], sources[role]))
        if mode == 'calibrated':
            pairs.append(compare(sources[prefix + ('signal',)], sources[role]))
    report = dict(schema='FGCS_CLOSED_LOOP_INDEPENDENT_AUDIT_V1', verified=True, cells=len(audits),
                  source_sha256=sha(Path(__file__)), input_bundle_sha256=sha(bundle_path),
                  run_seal_sha256=sha(root/'seal.json'), summary_sha256=sha(root/'summary.json'),
                  replayed_request_rows=sum(x['request_rows'] for x in audits.values()),
                  replayed_lifetime_rows=sum(x['instance_rows'] for x in audits.values()),
                  linked_real_victims=sum(x['actual_linked_victims'] for x in audits.values()),
                  byte_canonicalized_fallback_checks=exact_checks,
                  notes=['Rows are repeated replays of two previously seen days, not independent platform observations.',
                         'Cost integrates scheduled reservation over birth–confirmed return, including natural tail.',
                         'Per-request rows do not contain instance IDs; served-instance consistency is existential, not an exact assignment reconstruction.',
                         'Fixed vs hybrid estimates information effect on a common rule. Stock changes the rule and is a contextual benchmark.',
                         'Pairs report same budget ceiling with potentially different actual spend; no artificial cost padding.'],
                  cell_audits=list(audits.values()), pairs=pairs)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--runner', type=Path, default=HERE/'run.py')
    parser.add_argument('--kernel', type=Path, default=HERE/'kernel_v1.py')
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    report = recalculate(args.run_dir, args.bundle, args.runner, args.kernel)
    args.out_dir.mkdir(parents=True, exist_ok=False)
    with (args.out_dir/'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    with (args.out_dir/'paired.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report['pairs'][0]))
        writer.writeheader()
        writer.writerows(report['pairs'])
    print(json.dumps({k: report[k] for k in ('verified', 'cells', 'replayed_request_rows',
                   'replayed_lifetime_rows', 'linked_real_victims', 'byte_canonicalized_fallback_checks')}, indent=2))


if __name__ == '__main__':
    main()
