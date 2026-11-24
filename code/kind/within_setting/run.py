"""Prospective RQ3 experiment using a hash-pinned owned Kind plant."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import shutil
import statistics
import sys

HERE = Path(__file__).resolve().parent
PINS = {'run_kind.py': '11b3750c3336ba8c73c5a105df1f94452f6d43203d1d25da57676dceb76bd301',
        'kind_base.py': '426897e42a06cf90b80431e7aa4b7064883a8b61307c2c4e5110944056862241',
        'run_kind_baselines.py': '36774383a1e24030069352e9276219f07533f84e6c79315ae4b3f468f1c2e4cc'}
SETTINGS = {'d29': 2.9, 'd31': 3.1}
DISTURBANCES = {'short': 0.0, 'long': 1.55}
ARMS = ('pooled', 'setting', 'signal')
Y, Z, WINDOW, HORIZON = 3.55, 3.80, 8.5, 1.0
FIXTURE_FILE = '/fgcs-runtime/timing'
PRESTOP = "import time; d=float(open('/fgcs-runtime/timing').read()); assert d in (0.0,1.55); time.sleep(d)"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace(src, old, new):
    if src.count(old) != 1:
        raise ValueError(f'overlay anchor count: {old!r}')
    return src.replace(old, new)


def schedule(phase):
    calibration = phase == 'calibration'
    rng = random.Random(20261123031 if calibration else 20261123032)
    rows = []
    for block in range(1, 4 if calibration else 5):
        cells = [(s, j, a) for s in SETTINGS for j in DISTURBANCES
                 for a in (('force_reclaim',) if calibration else ARMS)]
        rng.shuffle(cells)
        for order, (s, j, a) in enumerate(cells, 1):
            rows.append(dict(trial_id=f'ws23-{phase[:3]}-{len(rows):02}', block=block, order=order,
                             condition=s, disturbance=j, arm=a, calibration=calibration))
    return rows


def projection(arm, model, setting, intent, decision, handler, free, x_reserved, memory, demand):
    """No disturbance, scheduler truth, or future event appears in this interface."""
    if arm not in (*ARMS, 'force_reclaim'):
        raise ValueError('invalid arm')
    terms = model['settings'][setting]
    fixed = intent + (model['pooled_s'] if arm == 'pooled' else terms['fixed_s'])
    use = arm == 'signal' and handler is not None
    forecast = handler + SETTINGS[setting] + terms['residual_s'] if use else fixed
    remaining = forecast - decision
    expected = memory if x_reserved and 0 < remaining <= HORIZON else 0.
    action = 'wait' if free + expected >= demand else 'reclaim'
    return forecast, remaining, expected, 'reclaim' if arm == 'force_reclaim' else action, use


def disturb(plant, manifest):
    """The Pod spec is invariant within a known cleanup setting."""
    spec = manifest['spec']
    spec['securityContext'] = {'fsGroup': 65532}
    spec['volumes'] = [{'name': 'runtime', 'emptyDir': {}}]
    ctr = spec['containers'][0]
    ctr['volumeMounts'] = [{'name': 'runtime', 'mountPath': '/fgcs-runtime'}]
    ctr['lifecycle'] = {'preStop': {'exec': {'command': ['python','-B','-c',PRESTOP]}}}


def seed_fixture(plant, name, seconds):
    # Only the fixture gets this value. Do not pass it to the decision function.
    return plant.kub('exec', '-n', plant.namespace, name, '-c', 'app', '--', 'python', '-B', '-c',
                     f"from pathlib import Path; p=Path('{FIXTURE_FILE}'); p.write_text({str(seconds)!r}); assert float(p.read_text())=={seconds!r}", timeout=12)


def load_trial(frozen):
    actual = {name: sha(frozen/name) for name in PINS}
    if actual != PINS:
        raise ValueError(f'frozen source mismatch: {actual}')
    sys.path.insert(0, str(frozen))
    spec = importlib.util.spec_from_file_location('frozen_baselines', frozen/'run_kind_baselines.py')
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    source = (frozen/'run_kind.py').read_text()
    lo, hi = source.index('        def forecast_action('), source.index('        used=set(); ingress_lock=')
    decisions = prior.DECISIONS
    decisions = replace(decisions, "fixed=sec(intent)+model['fixed_s']\n            hybrid=h+X_CLEAN+model['residual_s'] if valid else fixed", "fixed=sec(intent)+model['settings'][role['condition']]['fixed_s']\n            hybrid=h+SETTINGS[role['condition']]+model['settings'][role['condition']]['residual_s'] if valid else fixed")
    decisions = replace(decisions, "left, expected, action=estimate(arm,t,fixed,hybrid,free,x_reserved,M/1e9,M/1e9)", "forecast,left,expected,action,used=projection(arm,model,role['condition'],sec(intent),t,h if valid else None,free,x_reserved,M/1e9,M/1e9)\n                d.update(x_predicted_return_s=forecast,signal_used=used)")
    decisions = replace(decisions, "if role['arm']!='kubernetes':", "if not role['calibration']:")
    source = source[:lo]+decisions+source[hi:]
    source = replace(source, "        save(folder/(key+'-manifest.json'),mf);", "        if key=='x': disturb(plant,mf)\n        save(folder/(key+'-manifest.json'),mf);")
    source = replace(source, "x=new('x',0,X_CLEAN,PRESTOP if role['condition']=='slow' else 0.)", "x=new('x',0,SETTINGS[role['condition']])")
    source = replace(source, "        observer=Collector(plant,x,row['x_boot_id'],folder);", "        seed_fixture(plant,x['name'],DISTURBANCES[role['disturbance']])\n        observer=Collector(plant,x,row['x_boot_id'],folder);")
    # Extra custody fields without sending observed release data to the controller.
    source = replace(source, "        lo,hi=row['cost_window_start_s'],row['cost_window_end_s']; periods=[]",
                     prior.READBACK+"        lo,hi=row['cost_window_start_s'],row['cost_window_end_s']; periods=[]")
    env = dict(__file__=str(frozen/'run_kind.py'), __name__='within_setting_trial',
               SETTINGS=SETTINGS, DISTURBANCES=DISTURBANCES, projection=projection,
               seed_fixture=seed_fixture, disturb=disturb)
    exec(compile(source, 'hash-pinned within-setting overlay', 'exec'), env)
    return env, {**actual, 'trial_overlay': hashlib.sha256(source.encode()).hexdigest(),
                 'run.py': sha(HERE/'run.py'), 'PROTOCOL.md': sha(HERE/'PROTOCOL.md')}


def classify(row):
    lo, hi, dec = (row.get(k) for k in ('x_release_lo_s', 'x_release_hi_s', 'decision_s'))
    if lo is None or hi is None or dec is None:
        return 'missing'
    if dec < lo <= hi <= dec+HORIZON:
        return 'wait'
    if dec+HORIZON < lo <= hi:
        return 'reclaim'
    return 'ambiguous'


def calibration(rows, pins):
    samples = []
    for r in rows:
        keys = ('x_release_lo_s', 'x_release_hi_s', 'z_release_lo_s', 'z_release_hi_s', 'x_signal_event_s',
                'x_retire_intent_s', 'decision_s')
        if not (r['status']=='complete' and r.get('signal_used') and all(r.get(k) is not None for k in keys)):
            continue
        a,b,c,d,h,t0,t = (r[k] for k in keys)
        if not (t0 <= h <= t < a <= b and c <= d):
            continue
        s,j = r['condition'], r['disturbance']
        mid=(a+b)/2
        samples.append(dict(trial_id=r['trial_id'], condition=s, disturbance=j, x_return_s=mid-t0,
                            x_interval_s=[a-t0,b-t0], x_remaining_interval_s=[a-t,b-t],
                            z_interval_s=[c-t0,d-t0], residual_s=mid-h-SETTINGS[s],
                            order='x_first' if b<c else 'z_first' if d<a else 'ambiguous',
                            oracle=classify(r)))
    cells={}
    settings={}
    for s in SETTINGS:
        use=[x for x in samples if x['condition']==s]
        if use:
            settings[s]={k: statistics.median(x[v] for x in use)
                         for k,v in (('fixed_s','x_return_s'),('residual_s','residual_s'))}
        for j in DISTURBANCES:
            target=('wait','x_first') if j=='short' else ('reclaim','z_first')
            cells[f'{s}/{j}']=sum(x['disturbance']==j and (x['oracle'],x['order'])==target for x in use)
    return dict(admitted=len(samples)==12 and all(n>=2 for n in cells.values()), source_hashes=pins,
                pooled_s=statistics.median(x['x_return_s'] for x in samples) if samples else None,
                settings=settings, planned=12, samples=samples, validated_cells=cells,
                y_offset_s=Y,z_offset_s=Z,window_s=WINDOW,decision_horizon_s=HORIZON)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('check','calibration','evaluation'))
    p.add_argument('--frozen',type=Path,required=True)
    for k in ('kind','kubectl','root','seal'):
        p.add_argument('--'+k,type=Path)
    a=p.parse_args()
    b,pins=load_trial(a.frozen.resolve())
    if a.phase=='check':
        assert all(len(schedule(p))==n for p,n in (('calibration',12),('evaluation',48)))
        print(json.dumps(pins,sort_keys=True)); return 0
    is_eval=a.phase=='evaluation'
    if not (a.kind and a.kubectl and a.root and (a.seal if is_eval else not a.seal)):
        p.error('kind, kubectl and a fresh root required; only evaluation takes a calibration seal')
    if is_eval:
        model=json.loads(a.seal.read_text())
        if not (model['admitted'] and model['source_hashes']==pins and model['y_offset_s']==Y and model['z_offset_s']==Z):
            raise ValueError('qualifying hash-matched calibration required')
    else:
        model=dict(pooled_s=4.5, settings={s:dict(fixed_s=4.5, residual_s=0.07) for s in SETTINGS},
                   y_offset_s=Y,z_offset_s=Z)
    rows=schedule(a.phase)
    cluster='fgcs-ws23-'+('eval' if is_eval else 'cal')
    a.root.parent.mkdir(parents=True,exist_ok=True)
    with (a.root.parent/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if shutil.disk_usage(a.root.parent).free < 8*2**30:
            raise RuntimeError('less than 8 GiB available before launch')
        a.root.mkdir(exist_ok=False)
        b['save'](a.root/'PLAN.json',dict(phase=a.phase,trials=rows,source_hashes=pins,model=model,
                                       cluster=cluster,seal_sha256=sha(a.seal) if a.seal else None))
        out=[]; error=None; admitted=False
        try:
            with b['Plant'](a.root,cluster,a.kind,a.kubectl) as plant:
                baseline=b['node_budget'](plant); b['save'](a.root/'CAPACITY.json',baseline)
                for row in rows:
                    plant.check_external()
                    available=shutil.disk_usage(a.root).free
                    if available<8*2**30:
                        raise RuntimeError('less than 8 GiB available between trials')
                    result=b['trial'](plant,a.root,row,model,baseline)
                    out.append(result)
                    print(result['trial_id'],result['status'],result['errors'],flush=True)
                    if any(x.startswith('cleanup ') for x in result['errors']):
                        raise RuntimeError('owned trial cleanup not verified; halt')
            if is_eval:
                admitted=all(r['status']=='complete' for r in out)
            else:
                seal=calibration(out,pins)
                b['save'](a.root/'CALIBRATION_SEAL.json',seal)
                admitted=seal['admitted']
        except Exception as exc:
            error=repr(exc); print('STOP',error,flush=True)
        finally:
            b['save'](a.root/'TERMINAL.json',dict(planned=len(rows),attempted=len(out),
                       complete=sum(r['status']=='complete' for r in out),error=error,admitted=admitted,
                       plan_sha256=sha(a.root/'PLAN.json')))
    return 2 if error or len(out)!=len(rows) else (0 if admitted else 3)


if __name__=='__main__':
    raise SystemExit(main())
