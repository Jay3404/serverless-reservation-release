"""Read-only RQ3 action replay on archived Kind trials; does not simulate latency."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / '.research/fgcs_baselines_20261118'
FROZEN = ROOT / '.research/fgcs_retirement_action_20261117'
DEFAULT_SOURCE = ROOT / 'outputs/fgcs_baselines_20261118'
DEFAULT_OUTPUT = ROOT / 'outputs/fgcs_configaware_20261119/replay'
CONDITIONS = {'fast': 0.0, 'slow': 1.6}
ARMS = ('kubernetes', 'instant', 'fixed', 'signal')
MODES = ('pooled', 'config', 'signal')
HORIZON = 1.0
X_CLEANUP = 3.0
SPARE = 24 * 2**20 / 1e9
REQUEST = 128 * 2**20 / 1e9
HINT = 'another scheduled pod was deleted, and it may make the unscheduled pod schedulable'
RECHECK_BLOCKS = {'no pending Y', 'X API absent; no further retirement approved', 'Z active or already retiring'}


def require(ok, problem):
    if not ok:
        raise ValueError(problem)


def agree(a, b):
    return a is not None and b is not None and abs(a - b) < 1e-6


def reference(ref, namespace, name):
    if isinstance(ref, dict):
        return ref.get('namespace') == namespace and ref.get('name') == name
    return ref == f'{namespace}/{name}'


class Inputs:
    def __init__(self, archive):
        self.archive = archive
        self.hashes = {}

    def read(self, path):
        p = Path(path)
        if not p.is_absolute():
            p = self.archive / p
        key = str(p.relative_to(self.archive)) if p.is_relative_to(self.archive) else str(p.relative_to(ROOT))
        data = p.read_bytes()
        h = hashlib.sha256(data).hexdigest()
        require(self.hashes.setdefault(key, h) == h, f'input changed during reading: {key}')
        return data

    def json(self, path):
        return json.loads(self.read(path))


def config_from_manifest(x_manifest):
    """Original X manifest exists before requesting its deletion and before Y arrives."""
    spec = x_manifest['spec']
    ctr, = spec['containers']
    env = {v['name']: v['value'] for v in ctr['env']}
    require(float(env['CLEANUP_S']) == X_CLEANUP and env['TERMINATION_MODE'] == 'B', 'unexpected X cleanup')
    require(spec['terminationGracePeriodSeconds'] == 30 and spec['schedulerName'] == 'default-scheduler', 'X settings changed')
    cmd = ctr.get('lifecycle', {}).get('preStop', {}).get('exec', {}).get('command')
    if cmd is None:
        return 0.0
    require(cmd == ['python', '-B', '-c', 'import time;time.sleep(1.6)'], 'unexpected X preStop configuration')
    return 1.6


def cache_bounds(logs, namespace, source, dst):
    is_source = lambda e: reference(e.get('pod'), namespace, source)
    require(not any(is_source(e) and e['msg'] == 'Scheduler cache RemovePod failed' for e in logs), f'{source} cache remove error')
    starts = [e['ts'] for e in logs if is_source(e) and e['msg'] == 'Delete event for scheduled pod']
    if not starts:
        return None, None
    require(len(starts) == 1, f'{source} ambiguous cache removal')
    lo = starts[0]
    hints = [e['ts'] for e in logs if e['msg'] == HINT and reference(e.get('deletedPod'), namespace, source)
             and any(reference(e.get('pod'), namespace, n) for n in dst) and e['ts'] >= lo]
    return lo, min(hints, default=None)


def load_stage(src, phase, plan, terminal):
    assignments = plan['trials']
    cal = phase == 'calibration'
    expected = {(b, c, a) for b in range(1, 5 if cal else 7) for c in CONDITIONS
                for a in (('force_reclaim',) if cal else ARMS)}
    require({(v['block'], v['condition'], v['arm']) for v in assignments} == expected and len(assignments) == len(expected), f'{phase} grid')
    require(len({v['trial_id'] for v in assignments}) == len(expected), f'{phase} trial identity')
    require(len(list((src.archive / phase).glob('trial-*.json'))) == len(expected), f'{phase} raw file count')
    require(terminal['plan_sha256'] == src.hashes[f'{phase}/PLAN.json'] and terminal['phase'] == plan['phase'] == phase
            and terminal['admitted'] and terminal['error'] is None and
            terminal['planned'] == terminal['attempted'] == terminal['complete'] == len(expected), f'{phase} terminal receipt')
    trials, uid_set = [], set()
    for role in assignments:
        tid = role['trial_id']
        r = src.json(f'{phase}/trial-{tid}.json')
        require(all(r[k] == v for k, v in role.items()) and r['calibration'] is cal and r['status'] == 'complete' and not r['errors'], f'{tid} role/status')
        require(r['reservation_window_complete'] and r['capacity_ledger_complete'] and r['signal_observer_complete'], f'{tid} recorded evidence incomplete')
        folder = f'{phase}/{tid}'
        mf = src.json(f'{folder}/x-manifest.json')
        ns = mf['metadata']['namespace']
        delay = config_from_manifest(mf)
        require(CONDITIONS[role['condition']] == delay and ns == plan['cluster'], f'{tid} configuration/cluster mismatch')
        creates = r['pod_creates']
        for pod in creates.values():
            require(pod['uid'] not in uid_set, f'{tid} previously used UID')
            uid_set.add(pod['uid'])
        x, y, z = (creates[k] for k in ('x', 'y', 'z'))
        require((x['uid'], y['uid'], z['uid']) == (r['x_uid'], r['y_uid'], r['z_uid']) and x['name'] == mf['metadata']['name'], f'{tid} Pod identity mismatch')
        logs = src.json(f'{folder}/scheduler-events.json')
        audit = src.json(f'{folder}/binding-audit.json')
        dst = {y['name']} | ({creates['zn']['name']} if 'zn' in creates else set())
        for k, pod in (('x', x), ('z', z)):
            bounds = cache_bounds(logs, ns, pod['name'], dst)
            require(bounds == (r[f'{k}_release_lo_s'], r[f'{k}_release_hi_s']), f'{tid} raw {k} cache bracket mismatch')
        sigfile = (json.loads(line) for line in src.read(f'{folder}/signal.jsonl').splitlines())
        events = [s for s in sigfile if s['event']['event'] == 'sigterm_received']
        require(len(events) == 1, f'{tid} signal witness count')
        sig = events[0]
        h, rec, valid = (sig['event']['wall_ns']/1e9, sig['received']['epoch_ns']/1e9, sig['validated']['epoch_ns']/1e9)
        require(sig['event']['cleanup_seconds'] == X_CLEANUP and sig['event']['boot_id'] == r['x_boot_id']
                and sig['x_uid'] == r['x_uid'] == r['x_signal_uid']
                and (h, rec, valid) == (r['x_signal_event_s'], r['x_signal_received_s'], r['x_signal_validated_s']), f'{tid} signal/receipt identity mismatch')
        matches = [e for e in audit if e.get('auditID') == r['y_bind_witness_id'] and e['stage'] == 'ResponseComplete'
                   and e['verb'] == 'create' and e['objectRef']['subresource'] == 'binding'
                   and reference(e['objectRef'], ns, y['name']) and e['requestObject']['metadata']['uid'] == y['uid']
                   and e['user']['username'] == 'system:kube-scheduler' and e['responseStatus']['code'] in (200, 201, 202)]
        require(len(matches) == 1 and r['y_bind_uid_verified'], f'{tid} missing UID-matched Bind')
        bind = datetime.fromisoformat(matches[0]['stageTimestamp'].replace('Z', '+00:00')).timestamp()
        success = [e for e in logs if e['msg'] == 'Successfully bound pod to node' and reference(e.get('pod'), ns, y['name'])]
        failure = [e for e in logs if e['msg'] == 'Unable to schedule pod; no fit; waiting' and 'Insufficient memory' in e.get('err', '')
                   and reference(e.get('pod'), ns, y['name'])]
        require(len(success) == 1 and agree(success[0]['ts'], r['y_binding']['scheduler_bind_s']) and
                r['y_memory_failure_count'] == len(failure) >= 1 and agree(bind,r['y_bind_s']), f'{tid} raw Bind or fit-failure mismatch')
        t0, t, lo, hi = (r[k] for k in ('x_retire_intent_s', 'decision_s', 'x_release_lo_s', 'x_release_hi_s'))
        receipt = r['x_delete']
        require(receipt['response']['metadata']['uid'] == x['uid'] and receipt['response']['metadata']['deletionTimestamp']
                and x['create_ack_s'] < t0 <= receipt['submit_s'] <= receipt['ack_s'] <= r['y_arrival_s'] <= t <= r['y_submit_s'] < bind
                and t0 <= h <= rec <= valid and h + X_CLEANUP <= lo <= hi
                and min(e['ts'] for e in failure) < min(r['x_release_lo_s'], r['z_release_lo_s'] or float('inf'))
                and r['y_submit_s'] == y['create_submit_s'] and z['create_ack_s'] < t < r['z_arrival_s'], f'{tid} event chronology')
        require(agree(r['free_gb_at_decision'], SPARE) and all(r[f'{k}_request_gb'] == REQUEST for k in ('x','y','z')), f'{tid} initial configuration of reserved capacity')
        r['_prestop_s'] = delay
        trials.append(r)
    return trials, uid_set


def calibrate(rows, seal):
    """The only fit function. Rows from the evaluation cannot enter this call."""
    require(len(rows) == seal['usable_n'] == seal['calibration_plan_n'] == 8 and seal['admitted'], 'historical calibration denominator')
    require(len(seal['calibration']) == 8, 'missing historical seal rows')
    witness = {p['trial_id']: p for p in seal['calibration']}
    checked = []
    for r in rows:
        require(r['calibration'] is True, 'noncalibration row in fit')
        tid = r['trial_id']
        t0, h, lo, hi, zl, zh = (r[k] for k in ('x_retire_intent_s','x_signal_event_s','x_release_lo_s','x_release_hi_s','z_release_lo_s','z_release_hi_s'))
        x_mid = (lo + hi)/2
        order = 'x_first' if hi < zl else 'z_first' if zh < lo else 'unresolved'
        require(order == ('x_first' if r['_prestop_s']==0 else 'z_first'), f'{tid} release-order gate')
        w = witness[tid]
        require(w['condition'] == r['condition'] and w['order'] == order and w['x_interval_s'] == [lo-t0,hi-t0]
                and w['x_return_s'] == x_mid-t0 and w['x_post_handler_residual_s'] == x_mid-h-X_CLEANUP, f'{tid} historical seal mismatch')
        checked.append({'trial_id':tid,'condition':r['condition'],'prestop_s':r['_prestop_s'],'expected_release_first':order,
                        'x_release_lo_after_intent_s':lo-t0,'x_release_hi_after_intent_s':hi-t0,
                        'x_release_mid_after_intent_s':x_mid-t0,'residual_after_handler_cleanup_s':x_mid-h-X_CLEANUP})
    fixed = median(r['x_release_mid_after_intent_s'] for r in checked)
    residual = median(r['residual_after_handler_cleanup_s'] for r in checked)
    require(fixed == seal['fixed_s'] and residual == seal['residual_s'], 'historical pooled calibration did not reproduce')
    by_config = {d:median(r['x_release_mid_after_intent_s'] for r in checked if r['prestop_s']==d) for d in CONDITIONS.values()}
    return checked, fixed, residual, by_config


def policy_proposal(remaining, free, x_present, instant=False):
    qualifies = remaining == 0 if instant else 0 < remaining <= HORIZON
    return 'wait' if free + (REQUEST if x_present and qualifies else 0) >= REQUEST else 'reclaim'


def replay_state(r, d, pooled, residual, by_config):
    t, t0 = d['t_s'], r['x_retire_intent_s']
    h, rec, valid = [r[f'x_signal_{k}_s'] for k in ('event','received','validated')]
    available = t0 <= h <= rec <= valid <= t
    require(d['signal_available'] is available and d['signal_used'] is (available and r['arm']=='signal')
            and agree(d['signal_received_s'],rec) and agree(d['signal_validated_s'],valid), f'{r["trial_id"]} cutoff replay mismatch')
    at = {'pooled':t0+pooled, 'config':t0+by_config[r['_prestop_s']],
          'signal':h+X_CLEANUP+residual if available else t0+pooled}
    require(agree(at['pooled'],d['fixed_predicted_return_s']) and agree(at['signal'],d['signal_predicted_return_s']), f'{r["trial_id"]} original forecasts did not reproduce')
    remaining = {mode: predicted-t for mode,predicted in at.items()}
    free, present = d['controller_free_input_gb'], d['controller_x_still_counted']
    p = {m:policy_proposal(rem, free, present) for m,rem in remaining.items()}
    arm = r['arm']
    logged = ({'signal':p['signal'],'fixed':p['pooled'],'instant':policy_proposal(0,free,present,instant=True),'kubernetes':'wait'})[arm]
    retired_in_view = present and (0 if arm == 'instant' else remaining['signal' if arm == 'signal' else 'pooled'])
    expected_returnable = None if arm == 'kubernetes' else (REQUEST if
                present and (retired_in_view == 0 if arm == 'instant' else 0 < retired_in_view <= HORIZON) else 0.)
    require(d['proposed_action'] == logged and d['predicted_returnable_gb'] == expected_returnable, f'{r["trial_id"]} logged decision mismatch')
    return available, remaining, p


def error_to_interval(x, lo, hi):
    return max(lo-x, x-hi, 0.)


def replay(rows, pooled, residual, by_config):
    moments, checks = [], []
    for r in rows:
        tid = r['trial_id']
        condition, arm, block = (r[k] for k in ('condition','arm','block'))
        d, *later = r['controller_decisions']
        t, t0 = r['decision_s'], r['x_retire_intent_s']
        lo = r['x_release_lo_s']-t
        hi = r['x_release_hi_s']-t
        mid = (lo+hi)/2
        require(d['phase']=='first' and d['t_s']==t and d['controller_x_still_counted'] is True
                and d['x_reserved_actual'] is True and d['later_memory_fit_failure_witness'] is True
                and d['controller_free_input_gb'] == d['actual_free_gb'] == SPARE
                and d['y_pending'] is None and d['skip_reason'] in {None,'extra retirement disabled throughout observation'}, f'{tid} initial common state/exclusion')
        require(r['first_proposed_action'] == r['first_applied_action'] == d['proposed_action']
                and r['first_retire_applied'] is (r['first_applied_action']=='reclaim'), f'{tid} first guard/application')
        if r['first_retire_applied']:
            mutation = r['first_z_delete']
            require(t <= r['z_first_route_disabled_s'] <= r['z_first_delete_s'] == mutation['submit_s'] <= mutation['ack_s']
                    and mutation['response']['metadata']['uid'] == r['z_uid'] and mutation['response']['metadata']['deletionTimestamp'], f'{tid} first mutation receipt')
        require(not r['later_retire_applied'] and r.get('later_z_delete') is None and r.get('z_later_route_disabled_s') is None, f'{tid} later mutation altered audit')
        available, remain, p = replay_state(r,d,pooled,residual,by_config)
        x_class = ('within' if 0.<lo<=hi<=HORIZON else 'beyond' if lo>HORIZON else
                   'overdue' if hi<=0. else 'boundary_unresolved')
        row = dict(trial_id=tid, block=block, condition=condition, original_arm=arm, preStop_s=r['_prestop_s'],
                   X_UID=r['x_uid'], Y_UID=r['y_uid'], Z_UID=r['z_uid'],
                   first_decision_after_intent_s=t-t0, original_first_proposal=d['proposed_action'],
                   original_first_applied=r['first_applied_action'], initial_reclaim_guard='idle_Z_and_no_user_request_yet',
                   original_first_z_delete_applied=r['first_retire_applied'],
                   free_gb=SPARE, required_gb=REQUEST, X_retiring_reservation_gb=REQUEST,
                   signal_available=available, original_policy_used_signal=d['signal_used'],
                   signal_received_after_intent_s=d['signal_received_s']-t0,
                   signal_validated_after_intent_s=d['signal_validated_s']-t0,
                   X_release_lo_after_intent_s=r['x_release_lo_s']-t0, X_release_hi_after_intent_s=r['x_release_hi_s']-t0,
                   X_release_lo_remaining_ms=lo*1000,X_release_hi_remaining_ms=hi*1000,
                   X_release_band_vs_1s=x_class, absolute_threshold_margin_lower_ms=error_to_interval(HORIZON,lo,hi)*1000)
        for mode in MODES:
            row.update({f'{mode}_remaining_ms': remain[mode]*1000, f'{mode}_proposal': p[mode],
                        f'{mode}_first_action_guarded_replay':p[mode],
                        f'{mode}_absolute_threshold_margin_ms':abs(remain[mode]-HORIZON)*1000,
                        f'{mode}_forecast_error_to_midpoint_ms':abs(remain[mode]-mid)*1000,
                        f'{mode}_forecast_signed_error_to_midpoint_ms':(remain[mode]-mid)*1000,
                        f'{mode}_forecast_distance_to_interval_ms':error_to_interval(remain[mode],lo,hi)*1000})
        moments.append(row)
        q = dict(trial_id=tid, block=block, condition=condition, original_arm=arm, original_recheck_observed=bool(later))
        if not later:
            require(arm=='kubernetes' and r.get('recheck') is None, f'{tid} missing or unexpected recheck')
            q.update(eligible=None, guard='No controller recheck on original Kubernetes arm; alternative history unobserved',
                     **{f'{m}_path_status':'original_recheck_not_observed' for m in MODES})
        else:
            re, = later
            dt = re['t_s']-r['y_arrival_s']
            require(re==r['recheck'] and re['phase']=='recheck' and 1. <= dt < 1.2, f'{tid} recheck identity/cutoff')
            guard = re['skip_reason']
            eligible = guard is None
            require(eligible or guard in RECHECK_BLOCKS, f'{tid} unexpected recheck guard')
            require((guard=='no pending Y') is (re['y_pending'] is False) and
                    (not eligible or all([re['y_pending'],re['controller_x_still_counted']])), f'{tid} recheck guard mismatch')
            sig_available, remr, pr = replay_state(r,re,pooled,residual,by_config)
            require(guard is not None or re['proposed_action']=='wait', f'{tid} unexplained absence of later retirement')
            q.update(recheck_after_ingress_s=dt, eligible=eligible, guard=guard or 'guards passed',
                     original_y_pending=re['y_pending'],original_controller_x_counted=re['controller_x_still_counted'],
                     original_z_already_retired=r['first_retire_applied'],
                     original_recheck_logged_proposal=re['proposed_action'],
                     original_recheck_proposal_used=eligible,
                     original_recheck_actual_action='wait' if eligible else 'guard blocked',
                     signal_available=sig_available,original_recheck_applied_z_delete=False)
            for m in MODES:
                first_match = p[m]==r['first_applied_action']
                if not first_match:
                    s = 'prior_first_action_would_change; no alternate trajectory'
                elif not eligible:
                    s = 'same recorded retirement path; guard blocks recheck'
                elif pr[m]=='wait':
                    s = 'same recorded retirement path; eligible wait'
                else:
                    s = 'later action would change; no alternate trajectory'
                q.update({f'{m}_remaining_ms':remr[m]*1000,f'{m}_proposal_on_recorded_state':pr[m],
                          f'{m}_proposal_used_in_replay':eligible and first_match,f'{m}_path_status':s})
        checks.append(q)
    return moments, checks


def extent(vals):
    return {'min':min(vals),'max':max(vals),'median':median(vals),'mean':mean(vals)}


def sensitivity_to_calibration_range(cal, decisions):
    """Replace median with the observed calibration range, never with eval summaries."""
    minimum = min(r['x_release_lo_after_intent_s'] for r in cal)
    maximum = max(r['x_release_hi_after_intent_s'] for r in cal)
    min_mid = min(r['x_release_mid_after_intent_s'] for r in cal)
    max_mid = max(r['x_release_mid_after_intent_s'] for r in cal)
    return {
        'calibration_only_extreme_midpoint_delays_s':[min_mid,max_mid],
        'calibration_only_all_plausible_bound_delay_range_s':[minimum,maximum],
        'minimum_threshold_margin_throughout_bound_range_ms':min(
            error_to_interval(HORIZON, minimum-r['first_decision_after_intent_s'],
                              maximum-r['first_decision_after_intent_s'])*1000 for r in decisions),
        'first_states_invariant_at_both_bound_extremes_n':sum(
            0. < minimum-r['first_decision_after_intent_s'] <= maximum-r['first_decision_after_intent_s'] <= HORIZON
            if r['config_proposal']=='wait' else
            maximum-r['first_decision_after_intent_s'] <= 0. or minimum-r['first_decision_after_intent_s'] > HORIZON
            for r in decisions)}


def summarize(cal, model, residual, cfg, first, second):
    by_condition = {}
    for c, setting in CONDITIONS.items():
        a = [r for r in first if r['condition']==c]
        t = [r for r in cal if r['condition']==c]
        by_condition[c] = {
            'prestop_s':setting,'calibration_n':len(t),'calibration_ids':[r['trial_id'] for r in t],
            'config_fixed_release_delay_s':cfg[setting],
            'calibration_observed_bounds_after_intent_s':[min(r['x_release_lo_after_intent_s'] for r in t),max(r['x_release_hi_after_intent_s'] for r in t)],
            'evaluation_initial_states_n':len(a),
            'evaluation_signal_available_n':sum(r['signal_available'] for r in a),
            'observed_release_bounds_after_intent_s':[min(r['X_release_lo_after_intent_s'] for r in a),max(r['X_release_hi_after_intent_s'] for r in a)],
            'observed_release_remaining_bounds_ms':[min(r['X_release_lo_remaining_ms'] for r in a),max(r['X_release_hi_remaining_ms'] for r in a)],
            'observed_release_1s_class':dict(Counter(r['X_release_band_vs_1s'] for r in a)),
            'observed_lower_margin_from_1s_ms':min(r['absolute_threshold_margin_lower_ms'] for r in a),
            'original_proposed_action_counts':dict(Counter(r['original_first_proposal'] for r in a)),
            'calibration_range_sensitivity':sensitivity_to_calibration_range(t,a),
            'original_signal_trajectories':{m:dict(Counter(r[f'{m}_proposal'] for r in a if r['original_arm']=='signal')) for m in MODES},
            'predictors':{m:{'initial_proposals':dict(Counter(r[f'{m}_proposal'] for r in a)),
                          'remaining_ms':extent([r[f'{m}_remaining_ms'] for r in a]),
                          'min_predicted_abs_margin_from_1s_ms':min(r[f'{m}_absolute_threshold_margin_ms'] for r in a),
                          'MAE_ms_X_observed_cache_midpoint':mean(r[f'{m}_forecast_error_to_midpoint_ms'] for r in a),
                          'bias_ms_X_observed_cache_midpoint':mean(r[f'{m}_forecast_signed_error_to_midpoint_ms'] for r in a),
                          'mean_distance_to_observed_X_cache_bracket_ms':mean(r[f'{m}_forecast_distance_to_interval_ms'] for r in a)} for m in MODES}}
    signal = [r for r in first if r['original_arm']=='signal']
    re_signal = [r for r in second if r['original_arm']=='signal']
    eligible = [r for r in second if r['eligible']]
    elig_sig = [r for r in re_signal if r['eligible']]
    return {'scope':'Post-hoc decision replay on 48 observed initial states; new analysis of old Kind data, not a newly deployed policy or a latency estimate.',
            'model_fit':'Configuration known from X preStop manifest before decision; per-setting median of X release interval midpoints; calibration trials only. Signal uses historical pooled residual and pooled fallback.',
            'horizon_s':HORIZON,'x_cleanup_s':X_CLEANUP,
            'pooled_fixed_release_delay_s':model,'pooled_signal_residual_s':residual,
            'by_condition':by_condition,
            'initial_states_n':len(first),
            'first_config_vs_signal_proposal_diff_n':sum(r['config_proposal']!=r['signal_proposal'] for r in first),
            'first_config_vs_pooled_proposal_diff_n':sum(r['config_proposal']!=r['pooled_proposal'] for r in first),
            'first_config_vs_original_proposal_diff_n':sum(r['config_proposal']!=r['original_first_proposal'] for r in first),
            'signal_assigned_path_support':{
                'original_signal_n':len(signal),
                'first_config_vs_signal_actual_diff_n':sum(r['config_first_action_guarded_replay']!=r['original_first_applied'] for r in signal),
                'same_recorded_retirement_path_n':sum(r['config_path_status'].startswith('same recorded') for r in re_signal),
                'recheck_guard_counts':dict(Counter(r['guard'] for r in re_signal)),
                'recheck_action_eligible_n':len(elig_sig),
                'recheck_config_vs_signal_diff_n':sum(r['config_proposal_on_recorded_state']!=r['signal_proposal_on_recorded_state'] for r in elig_sig),
                'recheck_unused_logged_config_vs_signal_proposal_diff_n':sum(not r['eligible'] and r['config_proposal_on_recorded_state']!=r['signal_proposal_on_recorded_state'] for r in re_signal)},
            'all_observed_rechecks_n':sum(r['original_recheck_observed'] for r in second),
            'eligible_observed_rechecks_n':len(eligible),
            'eligible_recheck_original_arm_counts':dict(Counter(r['original_arm'] for r in eligible)),
            'eligible_rechecks_same_first_history_for_config_n':sum(r['config_proposal_used_in_replay'] for r in eligible),
            'eligible_rechecks_same_first_history_for_signal_n':sum(r['signal_proposal_used_in_replay'] for r in eligible),
            'limits':['No Y/Z latency, cost or service endpoint is assigned to an unrun path.',
                      'A matching path on an observed signal row means no replayed retirement change, not that a new physical run has identical timing.',
                      'Retrospective X cache release bounds diagnose forecast accuracy; they are never policy inputs and are not physical memory reclamation.',
                      'An observed recheck after a different first action cannot establish the alternative policy path.',
                      'The two manifest settings are deliberately separated relative to one second; this archive cannot test near-threshold configurations or signal outages.']}


def put_csv(path, rows):
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


def put_json(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def run(archive, output):
    src = Inputs(archive)
    cal_phase = 'calibration'
    cal_plan = src.json(f'{cal_phase}/PLAN.json')
    cal_end = src.json(f'{cal_phase}/TERMINAL.json')
    seal = src.json(f'{cal_phase}/CALIBRATION_SEAL.json')
    require(cal_plan['source_hashes']==seal['source_hashes'] and seal['x_cleanup_s']==X_CLEANUP
            and seal['prestop_slow_s']==CONDITIONS['slow'], 'calibration source or fixture contract')
    for name in ('KIND_PROTOCOL.md','kind_base.py','run_kind.py','run_kind_baselines.py'):
        p = (FROZEN if name in ('kind_base.py','run_kind.py') else BACKUP) / name
        require(hashlib.sha256(src.read(p)).hexdigest() == seal['source_hashes'][name], f'historical source changed: {name}')
    cal_raw, cal_uids = load_stage(src,cal_phase,cal_plan,cal_end)
    cal, pooled, residual, by_config = calibrate(cal_raw,seal)
    # The fit is complete before reading any evaluation rows.
    eval_phase = 'evaluation'
    eval_plan = src.json(f'{eval_phase}/PLAN.json')
    eval_end = src.json(f'{eval_phase}/TERMINAL.json')
    manifest = src.json('scored/scoring_manifest.json')
    seal_hash = src.hashes['calibration/CALIBRATION_SEAL.json']
    require(manifest['calibration']['seal_sha256']==seal_hash==eval_plan['seal_sha256'] and eval_plan['model']==seal
            and manifest['calibration']['calibration_plan_sha256']==src.hashes['calibration/PLAN.json']
            and manifest['plan_sha256']==src.hashes['evaluation/PLAN.json'] and manifest['runner_terminal']==eval_end
            and all(eval_plan['source_hashes'][k]==v for k,v in seal['source_hashes'].items()), 'historical pinned receipts disagree')
    for name, metadata_key in (('score_kind.py','scorer_sha256'), ('SCORING.md','scoring_protocol_sha256')):
        p = BACKUP/name
        require(hashlib.sha256(src.read(p)).hexdigest()==manifest[metadata_key]==eval_plan['source_hashes'][name], f'historical scoring source changed: {name}')
    eval_raw, eval_uids = load_stage(src,eval_phase,eval_plan,eval_end)
    require(not cal_uids.intersection(eval_uids)
            and set(r['trial_id'] for r in cal_raw).isdisjoint(r['trial_id'] for r in eval_raw)
            and max(r['clock_after']['controller_end']['realtime_ns'] for r in cal_raw)
                < min(r['clock_before']['controller_start']['realtime_ns'] for r in eval_raw)
            and len(cal_uids)==manifest['calibration']['calibration_uids']
            and len(eval_uids)==manifest['calibration']['evaluation_uids'], 'calibration/evaluation identity or chronology')
    for name, h in manifest['raw_input_sha256'].items():
        require(src.hashes[f'evaluation/{name}']==h,f'input no longer matches archived score receipt: {name}')
    first, later = replay(eval_raw,pooled,residual,by_config)
    summary = summarize(cal,pooled,residual,by_config,first,later)
    summary['input_receipts'] = {'historical_calibration_seal_sha256':seal_hash,
                                 'evaluation_plan_sha256':src.hashes['evaluation/PLAN.json'],
                                 'evaluation_raw_scoring_receipt_sha256':src.hashes['scored/scoring_manifest.json'],
                                 'replay_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 'evaluation_scoring_receipt_raw_hashes_reverified_n':len(manifest['raw_input_sha256']),
                                 'calibration_earliest_evaluation_after_last_calibration_s':
                                 (min(r['clock_before']['controller_start']['realtime_ns'] for r in eval_raw)
                                  -max(r['clock_after']['controller_end']['realtime_ns'] for r in cal_raw))/1e9}
    output.mkdir(parents=True,exist_ok=False)
    put_csv(output/'calibration_by_config.csv',cal)
    put_csv(output/'first_decisions.csv',first)
    put_csv(output/'rechecks.csv',later)
    put_json(output/'summary.json',summary)
    put_json(output/'input_sha256.json',src.hashes)
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__=='__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    run(args.source.resolve(),args.output.resolve())
