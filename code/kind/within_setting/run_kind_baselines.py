"""Finite, isolated follow-up to the frozen action study. No changes to its source."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
PINS = {'run_kind.py': '11b3750c3336ba8c73c5a105df1f94452f6d43203d1d25da57676dceb76bd301',
        'kind_base.py': '426897e42a06cf90b80431e7aa4b7064883a8b61307c2c4e5110944056862241'}
ARMS = ('kubernetes', 'instant', 'fixed', 'signal')
CONDS = ('fast', 'slow')
Y, Z, WINDOW, HORIZON = 3.55, 3.80, 8.5, 1.0


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def estimate(arm, now, fixed_at, signal_at, actual_free, x_reserved, x_memory, need):
    """Information only: no reservation ledger update, even for the instant arm."""
    remaining = {'instant': 0., 'fixed': fixed_at-now, 'signal': signal_at-now}[arm]
    within = remaining == 0 if arm == 'instant' else 0 < remaining <= HORIZON
    returned = x_memory if x_reserved and within else 0.
    return remaining, returned, 'wait' if actual_free + returned >= need else 'reclaim'


DECISIONS = '''
        def information(phase, y_pending, x_reserved, free, snapshot):
            with observer.lock:
                now=stamp(); signal=observer.signal
            t=sec(now)
            h=signal['event']['wall_ns']/1e9 if signal else None
            valid=bool(signal and sec(intent)<=h<=sec(signal['received'])<=sec(signal['validated'])<=t
                       and signal['x_uid']==x['uid'])
            fixed=sec(intent)+model['fixed_s']
            hybrid=h+X_CLEAN+model['residual_s'] if valid else fixed
            arm=role['arm']
            d=dict(phase=phase,t_s=t,actual_free_gb=None,
                   controller_free_input_gb=free,x_reserved_actual=None,
                   controller_x_still_counted=x_reserved,capacity_snapshot=snapshot,
                   y_pending=y_pending,signal_used=valid and arm=='signal',
                   signal_available=valid,signal_received_s=sec(signal['received']) if signal else None,
                   signal_validated_s=sec(signal['validated']) if signal else None,
                   fixed_predicted_return_s=fixed,signal_predicted_return_s=hybrid,skip_reason=None)
            if arm=='kubernetes':
                d.update(x_remaining_s=None,predicted_returnable_gb=None,proposed_action='wait',
                         skip_reason='extra retirement disabled throughout observation')
            else:
                left, expected, action=estimate(arm,t,fixed,hybrid,free,x_reserved,M/1e9,M/1e9)
                d.update(x_remaining_s=left,predicted_returnable_gb=expected,proposed_action=action)
            row.setdefault('controller_decisions',[]).append(d)
            return d

        def pick(ingress):
            d=information('first',None,True,SPARE/1e9,'prepacked X and Z before Y CREATE')
            chosen=d['proposed_action']
            row.update(decision_s=d['t_s'],signal_used=d['signal_available'],initial_action=chosen,
                       first_proposed_action=chosen,first_retire_applied=False,later_retire_applied=False,
                       first_applied_action='wait' if chosen=='wait' else None,
                       x_signal_used_in_policy=d['signal_used'],
                       fixed_predicted_return_s=d['fixed_predicted_return_s'],
                       signal_predicted_return_s=d['signal_predicted_return_s'])
            if chosen=='reclaim':
                with z_lock:
                    if not state['z_active'] and not state['z_retiring']:
                        state['z_retiring']=True; row['z_first_route_disabled_s']=sec(stamp())
                        dispatch.append(pool.submit(lambda:(at(ingress,.08),retire_z('first'))))
                    else:
                        d['skip_reason']='Z active or already retiring'
            if role['arm']!='kubernetes':
                dispatch.append(pool.submit(recheck,ingress))

        def recheck(ingress):
            at(ingress,1.); y=live.get('y')
            p=pod(plant,y['name']) if y else None
            q=pod(plant,x['name'])
            pending=bool(p and not p['spec'].get('nodeName'))
            d=information('recheck',pending,q is not None,SPARE/1e9 if q is not None else (SPARE+M)/1e9,
                          'Y and X owned Pod API status; X presence is conservative, not exact cache state')
            row['recheck']=d
            if not pending:
                d['skip_reason']='no pending Y'; return
            if q is None:
                d['skip_reason']='X API absent; no further retirement approved'; return
            with z_lock:
                if state['z_active'] or state['z_retiring']:
                    d['skip_reason']='Z active or already retiring'; return
                if d['proposed_action']=='wait':
                    return
                state['z_retiring']=True; row['z_later_route_disabled_s']=sec(stamp())
            retire_z('later')
'''

READBACK = '''
        # Cache evidence is diagnostic only; never supplied to the controller.
        for d in row.get('controller_decisions', []):
            t=d['t_s']; x_low=row.get('x_release_lo_s'); x_high=row.get('x_release_hi_s')
            z_low=row.get('z_release_lo_s'); z_high=row.get('z_release_hi_s')
            # A same-Y memory failure prevents claiming that either 128-MiB
            # reservation was absent earlier. The only additions are Y/Z-new.
            failures=row.get('y_binding',{}).get('memory_failures',[])
            z_new=row.get('z_submit_s',float('inf'))
            before_fail=any(t<=e['ts']<z_new for e in failures)
            d['x_reserved_actual']=(True if before_fail or x_low is not None and t<x_low
                                    else False if x_high is not None and t>=x_high else None)
            z_held=(True if before_fail or not state['z_retiring'] or z_low is not None and t<z_low
                    else False if z_high is not None and t>=z_high else None)
            if (d['x_reserved_actual'] is not None and z_held is not None
                    and not any(row.get(k+'_submit_s',float('inf'))<=t for k in ('y','z'))):
                d['actual_free_gb']=(SPARE + M*(int(not d['x_reserved_actual'])+int(not z_held)))/1e9
            elif before_fail and d['y_pending'] is True and row.get('z_submit_s',float('inf'))>t:
                d['actual_free_gb']=SPARE/1e9
            d['later_memory_fit_failure_witness']=before_fail
'''


def load_frozen(source):
    if {k: sha(source/k) for k in PINS} != PINS:
        raise ValueError('prior runner hash mismatch')
    sys.path.insert(0, str(source))
    spec = importlib.util.spec_from_file_location('run_kind_prior', source/'run_kind.py')
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.Y_OFFSET, base.Z_OFFSET = Y, Z
    raw = (source/'run_kind.py').read_text()
    lo, hi = raw.index('        def forecast_action('), raw.index('        used=set(); ingress_lock=')
    revised = raw[:lo]+DECISIONS+raw[hi:]
    anchor = "        lo,hi=row['cost_window_start_s'],row['cost_window_end_s']; periods=[]"
    if revised.count(anchor) != 1:
        raise ValueError('readback anchor mismatch')
    revised = revised.replace(anchor, READBACK+anchor)
    env = {'__file__': str(source/'run_kind.py'), '__name__': 'baseline_trial', 'estimate': estimate}
    exec(compile(revised, 'hash-pinned baseline overlay', 'exec'), env)
    return base, env['trial'], hashlib.sha256(revised.encode()).hexdigest()


def schedule(phase):
    rng = random.Random(2026111830 if phase=='calibration' else 2026111831)
    rows=[]
    for block in range(1, 5 if phase=='calibration' else 7):
        cells=[(c,a) for c in CONDS for a in (('force_reclaim',) if phase=='calibration' else ARMS)]
        rng.shuffle(cells)
        for order,(condition,arm) in enumerate(cells,1):
            rows.append(dict(trial_id=f'fb18-{phase[:3]}-{len(rows):02}',block=block,order=order,
                             condition=condition,arm=arm))
    return rows


def instant_precheck(folder, base):
    """Require a real pending-Y capacity witness before launching evaluation."""
    witnesses=[]
    for assignment in schedule('calibration'):
        path=folder/f"trial-{assignment['trial_id']}.json"
        if not path.is_file():
            continue
        r=json.loads(path.read_text())
        for f in r.get('y_binding',{}).get('memory_failures',[]):
            t=f['ts']; limit=min(r.get('x_release_lo_s') or 0,r.get('z_release_lo_s') or 0)
            if r.get('y_submit_s',float('inf'))<=t<limit and r.get('y_bind_uid_verified'):
                left, forecast, action=estimate('instant',t,0.,0.,base.SPARE/1e9,True,base.M/1e9,base.M/1e9)
                witnesses.append(dict(trial_id=r['trial_id'],condition=r['condition'],y_uid=r.get('y_uid'),
                  at_s=t,x_release_lower_s=r['x_release_lo_s'],z_release_lower_s=r['z_release_lo_s'],
                  actual_free_gb=base.SPARE/1e9,actual_x_reserved=True,y_pending_at_memory_failure=True,
                  estimate_remaining_s=left,forecast_available_gb=base.SPARE/1e9+forecast,proposed_action=action))
                break
    base.need({w['condition'] for w in witnesses}==set(CONDS) and
              all(w['estimate_remaining_s']==0 and w['proposed_action']=='wait' for w in witnesses),
              'calibration lacks representative actual Pending/capacity witnesses')
    return witnesses


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('phase',choices=('calibration','evaluation'))
    ap.add_argument('root',type=Path)
    for k in ('source','kind','kubectl'):
        ap.add_argument('--'+k,type=Path,required=True)
    ap.add_argument('--calibration-seal',type=Path)
    args=ap.parse_args()
    b, modified_trial, overlay_hash=load_frozen(args.source.resolve())
    pins={**PINS,'run_kind_baselines.py':sha(Path(__file__)), 'trial_overlay':overlay_hash,
          'KIND_PROTOCOL.md':sha(HERE/'KIND_PROTOCOL.md')}
    eval=args.phase=='evaluation'
    model=json.loads(args.calibration_seal.read_text()) if eval else dict(y_offset_s=Y,z_offset_s=Z,fixed_s=4.,residual_s=.2)
    precheck=None
    if eval:
        b.need(model['admitted'] and model['source_hashes']==pins and
               model['y_offset_s']==Y and model['z_offset_s']==Z,'separate qualifying calibration required')
        pins={**pins,'score_kind.py':sha(HERE/'score_kind.py'),'SCORING.md':sha(HERE/'SCORING.md')}
        precheck=instant_precheck(args.calibration_seal.parent,b)
    # Representative synthetic admission gate, not a physical observation.
    left, available, action=estimate('instant',10.,1.,1.,b.SPARE/1e9,True,b.M/1e9,b.M/1e9)
    b.need(left==0 and available==b.M/1e9 and action=='wait','instant decision contract')
    rows=schedule(args.phase)
    args.root.mkdir(parents=True,exist_ok=False)
    cluster='fgcs-baseline18-'+('eval' if eval else 'cal')
    b.save(args.root/'PLAN.json',dict(phase=args.phase,trials=rows,model=model,source_hashes=pins,
             bind_tolerance_s=.05,window_s=WINDOW,decision_horizon_s=HORIZON,cluster=cluster,
             seal_sha256=sha(args.calibration_seal) if eval else None))
    if precheck is not None:
        b.save(args.root/'INSTANT_PRECHECK.json',precheck)
    out=[]; error=None; admitted=False
    try:
        with b.Plant(args.root,cluster,args.kind,args.kubectl) as plant:
            baseline=b.node_budget(plant); b.save(args.root/'CAPACITY.json',baseline)
            for assignment in rows:
                plant.check_external()
                trial=modified_trial if eval else b.trial
                result=trial(plant,args.root,{**assignment,'calibration':not eval},model,baseline)
                out.append(result)
                print(result['trial_id'],result['status'],result.get('errors'),flush=True)
                if any(x.startswith('cleanup ') for x in result['errors']):
                    raise RuntimeError('stop: failed state reset')
        admitted=True if eval else b.seal_calibration(out,args.root,pins)['admitted']
    except Exception as e:
        error=repr(e); print('STOP',error,flush=True)
    finally:
        b.save(args.root/'TERMINAL.json',dict(planned=len(rows),attempted=len(out),
               complete=sum(x['status']=='complete' for x in out),error=error,phase=args.phase,
               admitted=admitted,plan_sha256=b.sha(args.root/'PLAN.json')))
    return 2 if error or len(out)!=len(rows) else (0 if admitted else 3)


if __name__=='__main__':
    raise SystemExit(main())
