"""Recompute saved IBM replay comparisons from raw request and instance ledgers."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

import numpy as np
from prepare import content_id, validate


BUDGET_GBS = 392845.20121905557
EPS = 1e-7
POLICIES = ('ttl', 'planner', 'rule')
SETS = {'historical': 84, 'cache_bracket': 84, 'direct_prepare': 42}
RAW_FIELDS = {
    'blocked_intervals.json.gz', 'direct_wait.npz', 'pass1_instances.json.gz',
    'pass1_request_instance_links.npz', 'pass1_requests.npz', 'preparation_triggers.json.gz',
}


def json_at(p):
    return json.loads(p.read_bytes())


def gz_at(p):
    with gzip.open(p, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def csv_at(p):
    with p.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def eq(a, b, label):
    if isinstance(a, (bool, str, int)) or a is None:
        good = str(a) == b if isinstance(b, str) and a is not None else a == b
    else:
        good = math.isclose(a, float(b), abs_tol=3e-7, rel_tol=1e-12)
    if not good:
        raise ValueError(f'{label}: {a!r} != {b!r}')


def need(ok, label):
    if not ok:
        raise ValueError(label)


def write_table(p, rows):
    with p.open('x', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def scenario_key(r):
    return r['day'], int(r['H_gb']), r['endpoint']


def load_cell(root, row, memory, common, trace):
    d, h, endpoint = scenario_key(row)
    p = row['policy']
    need(d in trace and h in (8, 16) and p in POLICIES
         and endpoint in ('0', 'AL', 'AM', 'AU', 'BL', 'BM', 'BU')
         and row['role_id'] == f'{d}_{p}_{h}_{endpoint}', 'unexpected role key')
    cell = root / row['role_id']
    result = json_at(cell / 'result.json')
    role, ep = result['role'], result['episode']
    need(result['no_learning'] and result['no_test'] and role == dict(id=cell.name, day=d, policy=p, H=h, endpoint=endpoint), 'role')
    need(set(ep['raw_rows']) == RAW_FIELDS, 'unexpected raw file inventory')
    for filename, digest in ep['raw_rows'].items():
        eq(sha(cell / filename), digest, str(cell / filename))
    with np.load(cell / 'pass1_requests.npz', allow_pickle=False) as f:
        a = {name: f[name] for name in f.files}
    with np.load(cell / 'direct_wait.npz', allow_pickle=False) as f:
        direct = {name: f[name] for name in f.files}
    n = len(a['request_id'])
    eq(n, {'day1': 25319, 'day2': 24800}[d], 'daily request count')
    need(np.array_equal(a['request_id'], np.arange(n)), 'canonical request ids')
    need(trace[d]['request_count'] == n
         and trace[d]['start'] == ep['evaluation_start'] and trace[d]['end'] == ep['evaluation_end'],
         'frozen request window mismatch')
    for key in ('arrival', 'app_id', 'request_id'):
        need(np.array_equal(a[key], direct[key]), f'{cell}: direct linkage {key}')
        signature = hashlib.sha256(a[key].tobytes(order='C')).hexdigest()
        eq(signature, common.setdefault((d, key), signature), f'{cell}: daily shared request {key}')
    expected = trace[d]['requests']
    need(np.array_equal(a['arrival'], [r[2] for r in expected])
         and np.array_equal(a['app_id'], [int(r[1]) for r in expected]),
         'shared request arrays diverged from prepared bundle')
    need(np.isfinite(a['completed']).all() and np.isfinite(a['started']).all(), 'incomplete/non-finite')
    need((a['completed'] + EPS >= a['started']).all() and (a['started'] + EPS >= a['arrival']).all(), 'event order')
    need(np.allclose(a['started'] - a['arrival'], a['initialization_wait'] + a['other_wait'], atol=EPS, rtol=0), 'start wait partition')
    need(np.allclose(a['completed'] - a['arrival'], a['response'], atol=EPS, rtol=0), 'response')
    needed = ('init', 'busy', 'idle', 'retiring')
    instance_rows = gz_at(cell / 'pass1_instances.json.gz')
    intervals = gz_at(cell / 'blocked_intervals.json.gz')
    inst_ids = set()
    costs, tail, retiring = [], [], []
    events = defaultdict(float)
    for inst in instance_rows:
        m = memory[int(inst['app'])]
        born, ret = inst['born'], inst['confirmed_return']
        intent = inst['observed_lifecycle']['retirement']['at']
        need(ep['evaluation_start'] - EPS <= born <= intent <= ret <= ep['natural_tail_end'] + EPS, 'lifecycle event order')
        life = (ret - born) * m
        retired = (ret - intent) * m
        eq(life, math.fsum(inst['cost_gbs'][k] for k in needed), f'{cell}: lifecycle lifetime charge')
        eq(retired, inst['cost_gbs']['retiring'], f'{cell}: lifecycle pending return charge')
        costs.append(life)
        retiring.append(retired)
        tail.append(max(0.0, ret - max(ep['evaluation_end'], born)) * m)
        events[born] += m
        events[ret] -= m
        need(inst['instance'] not in inst_ids, 'duplicate instance ID')
        inst_ids.add(inst['instance'])
    eq(len(inst_ids), ep['counts']['creations'], 'instance census')
    resident = peak = 0.0
    for _, step in sorted(events.items()):
        resident += step
        need(-EPS <= resident <= h + EPS, f'{cell}: positive-time occupancy')
        peak = max(peak, resident)
    eq(resident, 0.0, f'{cell}: no reservation after natural tail')
    foreign, directly = np.zeros(n), np.zeros(n)
    for t in intervals:
        dt = t['end'] - t['start']
        need(dt > 0, 'positive-time blocked segment')
        directly[[int(x) for x in t['directly_blocked_request_ids']]] += dt
        foreign[[int(x) for x in t['foreign_only_blocked_request_ids']]] += dt
    need(np.allclose(directly, direct['directly_blocked_s'], atol=EPS, rtol=0)
         and np.allclose(foreign, direct['blocked_by_foreign_only_s'], atol=EPS, rtol=0), 'blocked interval integral')
    need((foreign <= directly + EPS).all() and (directly <= a['other_wait'] + EPS).all(), 'local blockage bounds')
    cost, tailcost, retirecost = math.fsum(costs), math.fsum(tail), math.fsum(retiring)
    init, other = math.fsum(a['initialization_wait']), math.fsum(a['other_wait'])
    eq(cost, ep['reservation_gb_seconds'], f'{cell}: cost')
    eq(tailcost, ep['reservation_gb_seconds'] - math.fsum(ep['cost_through_T_by_state_gbs'].values()), f'{cell}: tail cost')
    eq(ep['target_gbs'], BUDGET_GBS, 'fixed budget')
    eq(ep['tail_timeout_failures'], 0, 'tail timeout')
    result = dict(role_id=cell.name, day=d, H_gb=h, endpoint=endpoint, policy=p, arrivals=n, completed=n,
        completion=1.0, cold=int(a['cold'].sum()), other_s=other, other_ms_per_arrival=1000 * other/n, init_s=init,
        total_wait_s=other+init, cost_gbs=cost, target_gbs=BUDGET_GBS, over_gbs=max(cost-BUDGET_GBS, 0.0),
        within_budget=cost <= BUDGET_GBS, slo_s=ep['experimental_slo_seconds'],
        slo_violations=int((a['response'] > ep['experimental_slo_seconds']).sum()),
        direct_wait_s=math.fsum(directly), foreign_direct_wait_s=math.fsum(foreign),
        retiring_gbs=retirecost, tail_gbs=tailcost, peak_gb=peak, instances=len(instance_rows),
        raw_hashes=len(ep['raw_rows']), blocked_segments=len(intervals))
    for key in result.keys() & row.keys():
        eq(result[key], row[key], f'{cell}: official {key}')
    return result, a['other_wait'], foreign


def pairing(row, old, delta, foreign):
    other = row['other_s'] - old['other_s']
    ms = 1000 * other / row['arrivals']
    pct = 100 * other / old['other_s'] if old['other_s'] > EPS else None
    linked = int(((delta > EPS) & (foreign > EPS)).sum())
    practical = ms >= 1.0 and (pct is None or pct >= 10.0) and linked >= 10
    return dict(role_id=row['role_id'], zero_role_id=old['role_id'], day=row['day'], H_gb=row['H_gb'],
        endpoint=row['endpoint'], policy=row['policy'], arrivals=row['arrivals'], delta_other_ms_per_arrival=ms,
        delta_other_s=other, delta_other_pct=pct, delta_init_s=row['init_s']-old['init_s'],
        delta_total_s=row['total_wait_s']-old['total_wait_s'], delta_cold=row['cold']-old['cold'],
        delta_slo=row['slo_violations']-old['slo_violations'], delta_cost_gbs=row['cost_gbs']-old['cost_gbs'],
        over_gbs=row['over_gbs'], zero_budget_ok=old['within_budget'], budget_ok=row['within_budget'],
        practical_other_wait_increase=practical, linked_ids=linked, increased_wait_and_foreign_linked=linked)


def order(values):
    ranked = sorted(values, key=lambda r: (r['total_wait_s'], r['policy']))
    elig = [r for r in values if r['within_budget'] and r['completion'] == 1.0]
    best = min(elig, key=lambda r: (r['total_wait_s'], r['cold'], r['cost_gbs'], r['policy']))['policy'] if elig else 'NONE'
    return ' < '.join(r['policy'] for r in ranked), ';'.join(sorted(r['policy'] for r in elig)), best


def rank(cells, d, h, e):
    zero = {p: cells[d, h, '0', p] for p in POLICIES}
    actual = {p: cells[d, h, e, p] for p in POLICIES}
    rz, ez, wz = order(zero.values())
    re, ee, we = order(actual.values())
    flip = []
    for a, b in combinations(POLICIES, 2):
        pre, after = [arr[a]['total_wait_s']-arr[b]['total_wait_s'] for arr in (zero, actual)]
        if abs(pre) > EPS and abs(after) > EPS and (pre > 0) != (after > 0):
            flip.append(f'{a}/{b}')
    return dict(day=d, H_gb=h, endpoint=e, rank_total_wait=re, strict_rank_reversals=';'.join(flip),
        strict_reversals=';'.join(flip), eligible=ee, eligible_set_changed=ee!=ez, selected_under_budget=we,
        selection_changed=we!=wz, actual_policy_switch=we!=wz and we!='NONE' and wz!='NONE')


def execute(evidence, bundle_path, dest):
    need(not dest.exists(), 'fresh output namespace')
    bundle = gz_at(bundle_path)
    validate(bundle)
    bundle_sha = content_id(bundle)
    memory = {int(k): v['memory_gb'] for k, v in bundle['configs'].items()}
    eq(bundle['physics']['train_budget_24h_gbs'], BUDGET_GBS, 'TRAIN-derived budget')
    trace = {d: bundle['trace'][d] for d in ('day1', 'day2')}
    verified = json_at(evidence / 'manifest.json')['files']
    names = ('ibm-all-cells.csv', 'ibm-all-pairs.csv', 'ibm-all-rankings.csv')
    paths = [evidence / name for name in names]
    for path in paths:
        eq(sha(path), verified[path.name], 'archived RQ2 result SHA')
    official_cells, official_pairs_all, official_ranks_all = map(csv_at, paths)
    need((len(official_cells), len(official_pairs_all), len(official_ranks_all)) == (210, 174, 70),
         'unexpected archived table census')
    common, receipt, all_cells, all_pairs, all_ranks = {}, {}, [], [], []
    for batch, n in SETS.items():
        src = evidence / 'ledgers' / batch
        official = [r for r in official_cells if r['batch'] == batch]
        eq(len(official), n, f'{batch}: cell count')
        cells, requests = {}, {}
        for r in official:
            key = (*scenario_key(r), r['policy'])
            need(key not in cells, 'duplicate role key')
            row, waits, foreign = load_cell(src, r, memory, common, trace)
            cells[key] = row
            requests[key] = waits, foreign
        pairs = {}
        for k, row in cells.items():
            if row['endpoint'] == '0':
                continue
            d, h, e, p = k
            old_key = d, h, '0', p
            w, f = requests[k]
            old, _ = requests[old_key]
            pairs[row['role_id']] = pairing(row, cells[old_key], w-old, f)
        official_pairs = [r for r in official_pairs_all if r['batch'] == batch]
        eq(len(official_pairs), len(pairs), f'{batch}: paired count')
        for raw in official_pairs:
            r = pairs[raw['role_id']]
            for k in r.keys() & raw.keys():
                eq(r[k], raw[k] or None, f'{batch}/{raw["role_id"]}: paired {k}')
        ranks = {k[:3]: rank(cells, *k[:3]) for k in cells}
        official_ranks = [r for r in official_ranks_all if r['batch'] == batch]
        eq(len(official_ranks), len(ranks), f'{batch}: rank count')
        for raw in official_ranks:
            k = scenario_key(raw)
            for f in ranks[k].keys() & raw.keys():
                eq(ranks[k][f], raw[f], f'{batch}/{k}: rank {f}')
        all_cells.extend(dict(batch=batch, **r) for r in cells.values())
        all_pairs.extend(dict(batch=batch, **r) for r in pairs.values())
        all_ranks.extend(dict(batch=batch, **r) for r in ranks.values())
        receipt[batch] = dict(cells=len(cells), pairs=len(pairs), rank_scenarios=len(ranks),
            request_cell_rows=sum(r['arrivals'] for r in cells.values()), raw_hashes=sum(r['raw_hashes'] for r in cells.values()),
            blocked_segments=sum(r['blocked_segments'] for r in cells.values()),
            raw_instances=sum(r['instances'] for r in cells.values()),
            budget_eligible_cells=sum(r['within_budget'] for r in cells.values()),
            practical_pairs=sum(p['practical_other_wait_increase'] for p in pairs.values()),
            rank_reversal_scenarios=sum(bool(r['strict_rank_reversals']) for r in ranks.values()),
            eligible_set_change_scenarios=sum(r['eligible_set_changed'] for r in ranks.values()),
            actual_policy_switch_scenarios=sum(r['actual_policy_switch'] for r in ranks.values()))
    dest.mkdir(parents=True, exist_ok=False)
    for name, records in [('recomputed_cells.csv', all_cells), ('recomputed_pairs.csv', all_pairs), ('recomputed_rankings.csv', all_ranks)]:
        write_table(dest/name, records)
    with (dest/'RECEIPT.json').open('x', encoding='utf-8') as f:
        json.dump(dict(status='PASSED', scope='READ-ONLY RECOMPUTATION OF SAVED IBM DEV REPLAYS',
            no_new_simulation=True, no_new_platform_measurements=True, no_learning=True, no_test=True,
            code_sha256=sha(Path(__file__)), bundle_content_sha256=bundle_sha,
            shared_daily_input_arrays={d: {k: v for (day, k), v in common.items() if day == d} for d in ('day1','day2')},
            batches=receipt, outputs={name: sha(dest/name) for name in ('recomputed_cells.csv','recomputed_pairs.csv','recomputed_rankings.csv')}), f, indent=2)
        f.write('\n')
    print(f'PASS {len(all_cells)} raw cells, {len(all_pairs)} pairs, {len(all_ranks)} rank scenarios')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--evidence', type=Path, required=True, help='ibm/release-sensitivity evidence directory')
    cli.add_argument('--bundle', type=Path, required=True, help='assembled inputs.json.gz')
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    execute(args.evidence, args.bundle, args.out.resolve())
