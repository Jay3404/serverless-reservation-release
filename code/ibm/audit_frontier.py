"""Independently audit every frozen TTL-frontier ledger; does not rank/select TTLs."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from audit import (BUDGET, CAPS, DAYS, DURATIONS, HERE, SLO, audit_cell, cell_name,
                   compare, read, require, sha)


TTLS = (0, 5, 15, 30, 45, 60)
ANCHOR = 30
MODES = ('stock', 'fixed', 'calibrated')
EXPECTED = {(day, cap, d, 'natural', mode, ttl) for day in DAYS for cap in CAPS
            for d in DURATIONS for mode in MODES for ttl in TTLS}
WAIT = 1.0


def parse_role(row):
    return tuple(row['role']) + (row['ttl'],)


def check_calibration(train_path, seal, bundle_path):
    trained = read(train_path/'EVALUATION_SEAL.json')
    planned = read(train_path/'CALIBRATION_PLAN.json')
    train = read(train_path/'train_summary.json')
    require(seal['prior_sha256']['train'] == sha(train_path/'EVALUATION_SEAL.json')
            and trained['train_summary_sha256'] == sha(train_path/'train_summary.json')
            and trained['calibration_plan_sha256'] == sha(train_path/'CALIBRATION_PLAN.json'),
            'wrong/changed upstream TRAIN calibration')
    expected_train = {('train', cap, d, 'natural', mode, ttl) for cap in CAPS for d in DURATIONS
                      for mode in MODES for ttl in TTLS}
    trained_keys = {parse_role(row) for row in train}
    require(len(train) == len(trained_keys) == len(expected_train) == 72 and trained_keys == expected_train
            and {tuple(role) + (ttl,) for role, ttl in planned['train_roles']} == expected_train
            and all(row['n'] == row['completed'] == 90087 for row in train)
            and planned['budget_48h'] == 2 * BUDGET and planned['budget_24h'] == BUDGET
            and planned['files'][bundle_path.name] == sha(bundle_path), 'wrong TRAIN comparison grid')
    for row in train:
        require(row['budget_gbs'] == 2 * BUDGET and row['eligible'] == (row['cost_gbs'] <= 2 * BUDGET),
                ('training eligibility', row['role'], row['ttl']))
    choices = []
    for cap in CAPS:
        for d in DURATIONS:
            affordable = [ttl for ttl in TTLS if sum(row['eligible'] for row in train if
                          row['role'][1:3] == [cap, d] and row['ttl'] == ttl) == len(MODES)]
            require(affordable, 'expected training-feasible TTL')
            choices.append(dict(cap=cap, d=d, ttl=max(affordable)))
    require(choices == trained['choices'] and all(x['ttl'] == 60 for x in choices), 'TRAIN choice recomputation differs')
    return dict(receipt_sha256=sha(train_path/'EVALUATION_SEAL.json'), checked_train_cells=len(train),
                largest_common_train_feasible_choices=choices,
                group_leakage_rule='No DEV score used to select; report all predeclared TTLs and the fixed 30-s anchor.')


def category(pair):
    """Descriptive, scoped to all three reported outcomes under the common ceiling."""
    if not pair['both_eligible']:
        return 'not_jointly_eligible'
    wait = pair['delta_wait_ms']
    cost = pair['delta_cost_gbs']
    slo = pair['delta_slo']
    zero_w = abs(wait) <= 1e-9
    zero_c = abs(cost) <= 2e-6
    if zero_w and zero_c and not slo:
        return 'tie'
    lower, higher = (wait < -1e-9, cost < -2e-6, slo < 0), (wait > 1e-9, cost > 2e-6, slo > 0)
    if any(lower) and not any(higher):
        return 'candidate_weakly_dominates'
    if any(higher) and not any(lower):
        return 'reference_weakly_dominates'
    return 'tradeoff'


def run(root, archive, archive_audit, bundle_path, kernel, train):
    seal, summary, bundle = read(root/'seal.json'), read(root/'summary.json'), read(bundle_path)
    archival = read(archive/'summary.json')
    previous = read(archive_audit)
    require(previous['verified'] and previous['cells'] == 58 and len(archival) == 58
            and not any(x['eligible'] for x in archival)
            and seal['prior_sha256']['archived_ttl60'] == previous['summary_sha256'] == sha(archive/'summary.json')
            and previous['run_seal_sha256'] == sha(archive/'seal.json'), 'archived 58 no longer match their independent audit')
    prev_cells = {tuple(x['role']): x for x in previous['cell_audits']}
    require(all(cell['cell_sha256'] == sha(archive/cell_name(role)) for role, cell in prev_cells.items()),
            'historical replay bytes changed after initial audit')
    require(seal['schema'] == 'frontier-v1' and seal['anchor'] == ANCHOR and seal['budget_gbs'] == BUDGET
            and all(seal['hashes'][p.name] == sha(p) for p in (bundle_path, kernel, HERE/'run.py', HERE/'frontier.py', HERE/'rebudget.py')),
            'frontier seal/hash/anchor mismatch')
    require(len(seal['roles']) == len(summary) == len(EXPECTED) == 144
            and {tuple(r) + (ttl,) for r, ttl in seal['roles']} == EXPECTED == {parse_role(r) for r in summary},
            'frontier role census differs')
    for ttl in TTLS:
        found = {p.name for p in (root/f'ttl{ttl}').glob('*.json.gz')}
        require(found == {cell_name(r[:-1]) for r in EXPECTED if r[-1] == ttl}, ('ledgers missing/extra', ttl))
    rows = {parse_role(row): row for row in summary}
    audits = []
    for key in sorted(EXPECTED):
        role, ttl = key[:-1], key[-1]
        path = root/f'ttl{ttl}'/cell_name(role)
        value = read(path)
        expect = {k: v for k, v in rows[key].items() if k != 'ttl'}
        # run_with_ttl adds the factor to the top-level summary only, not to the existing ledger schema.
        entry = audit_cell(value, expect, bundle, dict(slo_s=SLO, wait_s=WAIT))
        if ttl == 60:
            prev = prev_cells[role]
            require(all(prev[k] == entry[k] for k in ('trajectory_sha256', 'decision_sha256')),
                    ('repeated archival TTL60 trajectory differs', role))
        audits.append(dict(ttl=ttl, cell_sha256=sha(path), **entry))
    pairs = []
    for day in DAYS:
        for cap in CAPS:
            for d in DURATIONS:
                for ttl in TTLS:
                    grp = {(mode): rows[(day, cap, d, 'natural', mode, ttl)] for mode in MODES}
                    for ref, target in [('fixed', 'calibrated'), ('stock', 'fixed'), ('stock', 'calibrated')]:
                        peer = compare(grp[ref], grp[target])
                        pairs.append(dict(ttl=ttl, anchor=(ttl == ANCHOR), comparison=category(peer), **peer))
    eligible_by_ttl = {ttl: {mode: sum(rows[k]['eligible'] for k in rows if k[-2:] == (mode, ttl))
                             for mode in MODES} for ttl in TTLS}
    fixed_pairs = [p for p in pairs if p['reference'] == 'fixed']
    return dict(schema='FGCS_CLOSED_LOOP_INDEPENDENT_TTL_FRONTIER_AUDIT_V1', verified=True, cells=len(audits),
                ttl_anchor_prespecified=ANCHOR, audit_source_sha256=sha(Path(__file__)),
                helper_sha256=sha(HERE/'audit.py'), seal_sha256=sha(root/'seal.json'), summary_sha256=sha(root/'summary.json'),
                ttl60_archived_all_58_hashes_unchanged=True, ttl60_frontier_24_trajectory_and_decision_parity=True,
                calibration_crosscheck=check_calibration(train, seal, bundle_path),
                request_ledger_rows=sum(x['request_rows'] for x in audits), lifetime_rows=sum(x['instance_rows'] for x in audits),
                linked_real_victims=sum(x['actual_linked_victims'] for x in audits),
                eligible_total=sum(x['eligible'] for x in summary), eligible_by_ttl=eligible_by_ttl,
                fixed_vs_hybrid_descriptive_counts=dict(Counter(x['comparison'] for x in fixed_pairs)),
                fixed_vs_hybrid_anchor_descriptive_counts=dict(Counter(x['comparison'] for x in fixed_pairs if x['anchor'])),
                stronger_scope='Same budget ceiling is not equal actual cost; only previously seen days; no DEV-selected winner.',
                cells_audited=audits, pairs=pairs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('frontier', type=Path)
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--archive', required=True, type=Path)
    p.add_argument('--archive-audit', required=True, type=Path)
    p.add_argument('--kernel', type=Path, default=HERE.parent/'lifecycle_validation_20261107/pilot/kernel_v1.py')
    p.add_argument('--train', required=True, type=Path)
    p.add_argument('--out-dir', required=True, type=Path)
    a = p.parse_args()
    result = run(a.frontier, a.archive, a.archive_audit, a.bundle, a.kernel, a.train)
    a.out_dir.mkdir(parents=True, exist_ok=False)
    with (a.out_dir/'audit.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write('\n')
    with (a.out_dir/'paired.csv').open('x', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(result['pairs'][0]))
        w.writeheader()
        w.writerows(result['pairs'])
    print(json.dumps({k: result[k] for k in ('verified', 'cells', 'request_ledger_rows', 'lifetime_rows',
                       'linked_real_victims', 'eligible_total', 'eligible_by_ttl',
                       'fixed_vs_hybrid_descriptive_counts', 'fixed_vs_hybrid_anchor_descriptive_counts')}, indent=2))


if __name__ == '__main__':
    main()
