"""Recompute the sealed control experiment; v2 fixes the Z key and float readback."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics

from run import (ARMS, DISTURBANCES, SETTINGS, WINDOW, calibration, classify,
                 load_trial, projection, schedule, sha)


def verify(condition, message):
    if not condition:
        raise ValueError(message)


def read(where):
    return json.loads(where.read_text())


def average(values):
    usable=[v for v in values if v is not None]
    return statistics.mean(usable) if usable else None


def diff(a,b):
    return None if a is None or b is None else a-b


def exact(a,b):
    return a==b or a is not None and b is not None and math.isclose(a,b,abs_tol=1e-8,rel_tol=0)


def archive(folder, phase, pins, runner, model=None, sealed=None):
    plan=read(folder/'PLAN.json'); terminal=read(folder/'TERMINAL.json')
    expected=schedule(phase)
    verify(plan['source_hashes']==pins and plan['trials']==expected and
           terminal['plan_sha256']==sha(folder/'PLAN.json'), f'{phase} plan mismatch')
    if phase=='evaluation':
        verify(plan['model']==model and plan['seal_sha256']==sealed,'evaluation seal mismatch')
        stamped=(folder.parent/'ANALYSIS_V2.sha256').read_text().split()
        verify(len(stamped)==2 and stamped[0]==sha(Path(__file__)) and stamped[1].endswith('/score_v2.py'),
               'scoring code not sealed')
    verify(terminal['planned']==len(expected),'incorrect terminal denominator')
    raw=[]; out=[]; uids=set(); specs={}; env=read(folder/'environment.json') if (folder/'environment.json').exists() else None
    for assignment in expected:
        path=folder/f"trial-{assignment['trial_id']}.json"
        result=dict(**assignment, status='unattempted')
        if not path.exists():
            out.append(result); continue
        r=read(path); raw.append(r)
        verify(all(r.get(k)==v for k,v in assignment.items()),f'{path} assignment mismatch')
        result.update(status=r['status'],errors=r['errors'])
        pod=r.get('pod_creates',{})
        ids=[i['uid'] for i in pod.values()]
        verify(len(ids)==len(set(ids)) and not uids.intersection(ids),'Pod UID reused')
        uids.update(ids)
        events=folder/r['trial_id']
        manifest=events/'x-manifest.json'
        if manifest.exists():
            spec=read(manifest)['spec']; key=r['condition']
            verify(key not in specs or spec==specs[key],'known configuration changes within setting')
            specs[key]=spec
        if r.get('controller_decisions'):
            d=r['controller_decisions'][0]; signal=[json.loads(s) for s in (events/'signal.jsonl').read_text().splitlines()
                                                  if json.loads(s)['event']['event']=='sigterm_received']
            h=None
            if signal:
                verify(len(signal)==1,f'{path} duplicate handler rows')
                e=signal[0]; event=e['event']; when=event['wall_ns']/1e9
                if (e['x_uid']==r['x_uid'] and event['boot_id']==r['x_boot_id'] and event['pid']==1 and event['app_id']==0 and
                    r['x_retire_intent_s']<=when<=e['received']['epoch_ns']/1e9<=e['validated']['epoch_ns']/1e9<=d['t_s']):
                    h=when
            verify(d['signal_available']==(h is not None),'signal information cutoff mismatch')
            pred,left,expected_gb,action,use=projection(r['arm'],plan['model'],r['condition'],
                r['x_retire_intent_s'],d['t_s'],h,d['controller_free_input_gb'],d['controller_x_still_counted'],
                r['x_request_gb'],r['y_request_gb'])
            verify(exact(pred,d['x_predicted_return_s']) and exact(left,d['x_remaining_s']) and
                   exact(expected_gb,d['predicted_returnable_gb']) and action==d['proposed_action'] and
                   use==d['signal_used'],'first decision cannot be recomputed')
            result.update(first=action, first_applied=r.get('first_applied_action'), x_forecast_s=pred,
                signal_available=h is not None, signal_used=use, oracle=classify(r),
                first_deleted=bool(r.get('first_retire_applied')), later_deleted=bool(r.get('later_retire_applied')),
                x_remaining_lower_s=diff(r.get('x_release_lo_s'),d['t_s']),
                x_remaining_upper_s=diff(r.get('x_release_hi_s'),d['t_s']))
        if r['status']=='complete':
            logs=read(events/'scheduler-events.json'); audit=read(events/'binding-audit.json')
            witness={plan['cluster']+'/'+pod[k]['name'] for k in ('y','zn') if k in pod}
            for k in ('x','z'):
                bounds=runner['release'](logs,plan['cluster'],pod[k],witness)
                verify(bounds==(r[k+'_release_lo_s'],r[k+'_release_hi_s']),'raw X/Z release bracket mismatch')
            for k,p in (('y','y'),('zn','z')):
                if k in pod:
                    bind=runner['fit_view'](logs,audit,plan['cluster'],plan['cluster']+'-control-plane',pod[k])
                    verify(bind==r[k+'_binding'] and (p=='z' or bind['verified']) and bind['bind_s']==r[p+'_bind_s'],
                           'raw Bind evidence mismatch')
            cost=runner['cost'](r['cost_window_start_s'],r['cost_window_end_s'],r['reservation_intervals'])
            verify(exact(cost[0],r['cost_lower_gbs']) and exact(cost[1],r['cost_upper_gbs']) and
                   abs(r['cost_window_end_s']-r['cost_window_start_s']-WINDOW)<1e-6,'cost bound mismatch')
            result.update(y_bind_s=diff(r['y_bind_s'],r['y_arrival_s']),
                          y_http_s=diff(r['y_done_s'],r['y_arrival_s']), z_http_s=diff(r['z_done_s'],r['z_arrival_s']),
                          y_http_ok=r['y_http_ok'],z_http_ok=r['z_http_ok'],z_recreated=r['z_recreations'],
                          cost_lower_gbs=cost[0],cost_upper_gbs=cost[1])
        if all(r.get(k) is not None for k in ('x_release_lo_s','x_release_hi_s','z_release_lo_s','z_release_hi_s')):
            result['release_order']=('x_first' if r['x_release_hi_s']<r['z_release_lo_s'] else
                                     'z_first' if r['z_release_hi_s']<r['x_release_lo_s'] else 'ambiguous')
        out.append(result)
    verify(terminal['attempted']==len(raw) and terminal['complete']==sum(r['status']=='complete' for r in raw),
           'terminal counts disagree')
    return raw,out,uids,env


def summarize(rows):
    done=[r for r in rows if r['status']=='complete']
    witnessed=[r for r in rows if r.get('oracle') in ('wait','reclaim')]
    return dict(assigned=len(rows),complete=len(done), first_observed=sum('first' in r for r in rows),
                signal_available=sum(r.get('signal_available',False) for r in rows),
                signal_used=sum(r.get('signal_used',False) for r in rows),
                first_wait=sum(r.get('first')=='wait' for r in rows),
                first_reclaim=sum(r.get('first')=='reclaim' for r in rows),
                first_deleted=sum(r.get('first_deleted',False) for r in rows),
                later_deleted=sum(r.get('later_deleted',False) for r in rows),
                z_recreated=sum(r['z_recreated'] for r in done),
                oracle_scored=len(witnessed), oracle_wait=sum(r['oracle']=='wait' for r in witnessed),
                oracle_agree=sum(r.get('first')==r['oracle'] for r in witnessed),
                x_first=sum(r.get('release_order')=='x_first' for r in rows),
                z_first=sum(r.get('release_order')=='z_first' for r in rows),
                **{k:average([r[k] for r in done]) for k in ('y_bind_s','y_http_s','z_http_s','cost_lower_gbs','cost_upper_gbs')})


def pair_groups(rows):
    groups=defaultdict(dict)
    for r in rows:
        groups[r['condition'],r['disturbance'],r['block']][r['arm']]=r
    return groups


def pairs(rows):
    out=[]
    for (s,j,b),group in sorted(pair_groups(rows).items()):
        a=group['signal']
        for name in ('pooled','setting'):
            f=group[name]; use=a['status']==f['status']=='complete'
            out.append(dict(setting=s,disturbance=j,block=b,comparator=name,
                  signal_trial=a['trial_id'],fixed_trial=f['trial_id'],complete_pair=use,
                  signal_action=a.get('first'),fixed_action=f.get('first'),
                  signal_first_deleted=a.get('first_deleted'),fixed_first_deleted=f.get('first_deleted'),
                  cost_difference_lo_gbs=diff(a.get('cost_lower_gbs'),f.get('cost_upper_gbs')) if use else None,
                  cost_difference_hi_gbs=diff(a.get('cost_upper_gbs'),f.get('cost_lower_gbs')) if use else None,
                  **{k:diff(a.get(k),f.get(k)) if use else None for k in ('y_bind_s','y_http_s','z_http_s')}))
    return out


def csvout(path, rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('frozen','calibration'):
        p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--evaluation',type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--calibration-only',action='store_true')
    a=p.parse_args()
    runner,pins=load_trial(a.frozen.resolve())
    cal,_,cuids,cenv=archive(a.calibration,'calibration',pins,runner)
    sealpath=a.calibration/'CALIBRATION_SEAL.json'; model=read(sealpath)
    verify(calibration(cal,pins)==model and model['admitted'],'calibration seal cannot be recomputed')
    if a.calibration_only:
        print(json.dumps(dict(verified_calibration=len(cal),distinct_pod_uids=len(cuids),
                              calibration_sha256=sha(sealpath),calibration=model),indent=2,allow_nan=False))
        return
    if not a.evaluation or not a.out:
        p.error('--evaluation and --out required, unless --calibration-only is used')
    _,evaluation,euids,eenv=archive(a.evaluation,'evaluation',pins,runner,model,sha(sealpath))
    verify(not cuids.intersection(euids),'evaluation reuses calibration Pod UID')
    verify(cenv and eenv and cenv['node_id']!=eenv['node_id'],'cluster reuse')
    pair=pairs(evaluation)
    levels=[('all',evaluation)]
    levels.extend((j,[r for r in evaluation if r['disturbance']==j]) for j in DISTURBANCES)
    levels.extend((s+'/'+j,[r for r in evaluation if r['condition']==s and r['disturbance']==j])
                  for s in SETTINGS for j in DISTURBANCES)
    summary=[dict(group=name,arm=arm,**summarize([r for r in members if r['arm']==arm]))
             for name,members in levels for arm in ARMS]
    contrasts=[]
    for group,_ in levels:
        for f in ('pooled','setting'):
            pp=[x for x in pair if x['comparator']==f and group in ('all',x['disturbance'],x['setting']+'/'+x['disturbance'])]
            use=[x for x in pp if x['complete_pair']]
            contrasts.append(dict(group=group,comparator=f,assigned_pairs=len(pp),complete_pairs=len(use),
                   forecast_actions_differ=sum(x['signal_action'] is not None and x['fixed_action'] is not None and
                                              x['signal_action']!=x['fixed_action'] for x in pp),
                   actual_first_deletes_differ=sum(x['signal_first_deleted']!=x['fixed_first_deleted'] for x in use),
                   **{k:average([r[k] for r in use]) for k in
                      ('y_bind_s','y_http_s','z_http_s','cost_difference_lo_gbs','cost_difference_hi_gbs')}))
    a.out.mkdir(parents=True,exist_ok=False)
    csvout(a.out/'trials.csv',evaluation); csvout(a.out/'pairs.csv',pair); csvout(a.out/'summary.csv',summary)
    csvout(a.out/'contrasts.csv',contrasts)
    with (a.out/'RESULTS.json').open('x') as f:
        json.dump(dict(summary=summary,contrasts=contrasts,calibration=model,calibration_pod_uids=len(cuids),
                       evaluation_pod_uids=len(euids),calibration_node_id=cenv['node_id'],evaluation_node_id=eenv['node_id']),
                  f,indent=2,allow_nan=False); f.write('\n')


if __name__=='__main__':
    main()
