"""Real HTTP X/Z/Y retirement race on one independently owned Kind node."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import http.server
import ipaddress
import json
from pathlib import Path
import random
import re
import statistics
import subprocess
import threading
import time
import urllib.request

from kind_base import Plant

M = 128 * 1024**2
SPARE = 24 * 1024**2
X_CLEAN, Z_CLEAN, PRESTOP = 3., .55, 1.6
Y_OFFSET, Z_OFFSET, WINDOW = 3.03, 3.33, 8.5
CONDS = ('fast', 'slow')
ARMS = ('fixed','signal','force_wait','force_reclaim')
APP_BODY_SHA='8917f744995b1bb21a6fe803f2b466c42ddde213b111e6030eff9b9d694015f9'
HERE = Path(__file__).resolve().parent
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def need(ok, why):
    if not ok:
        raise ValueError(why)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, obj):
    with p.open('x') as f:
        json.dump(obj, f, indent=2, sort_keys=True, allow_nan=False); f.write('\n')


def stamp():
    l=time.monotonic_ns(); rt=time.time_ns(); h=time.monotonic_ns()
    return dict(epoch_ns=rt, monotonic_ns=(l+h)//2, uncertainty_ns=h-l)


def sec(t): return t['epoch_ns']/1e9


def at(t, delay):
    end=t['monotonic_ns']/1e9+delay
    while (remain:=end-time.monotonic())>0: time.sleep(remain)


def time_text(s): return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()


def quantity(s):
    m=re.fullmatch(r'(\d+)(Ki|Mi|Gi|Ti|k|M|G|T)?',str(s)); need(m,'unsupported stock quantity')
    return int(m[1])*{None:1,'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4,'k':1000,'M':1000**2,'G':1000**3,'T':1000**4}[m[2]]


def schedule(phase):
    rng=random.Random(2026111731 if phase=='evaluation' else 2026111730)
    rows=[]
    for b in range(6 if phase=='evaluation' else 4):
        cells=[(c,a) for c in CONDS for a in (ARMS if phase=='evaluation' else ('force_reclaim',))]
        rng.shuffle(cells)
        for j,(c,a) in enumerate(cells):
            rows.append(dict(trial_id=f'ra17-{phase[:3]}-{len(rows):02}',block=b+1,order=j+1,condition=c,arm=a))
    return rows


def fixture(plant, name, label, cleanup=0., prestop=0., memory=M, filler=False):
    wrapped=("import hashlib,os; p='/app/server.py'; b=open(p,'rb').read(); "
             f"assert hashlib.sha256(b).hexdigest()=='{APP_BODY_SHA}'; "
             "src=b.decode(); old='CLEANUP_SECONDS = 5.0'; assert src.count(old)==1; "
             "exec(compile(src.replace(old,'CLEANUP_SECONDS = '+str(float(os.environ['CLEANUP_S']))),p,'exec'),{'__name__':'__main__'})")
    ctr=dict(name='app',image=plant.app_image,imagePullPolicy='Never',
             command=['python','-B','-u','-c',wrapped],env=[dict(name=k,value=v) for k,v in
             dict(APP_ID=str(label), APP_CONCURRENCY='1', TERMINATION_MODE='B' if cleanup else 'A',CLEANUP_S=str(cleanup)).items()],
             securityContext=dict(runAsNonRoot=True,readOnlyRootFilesystem=True,allowPrivilegeEscalation=False,
                                  capabilities={'drop':['ALL']},seccompProfile={'type':'RuntimeDefault'}),
             resources=dict(requests=dict(memory=str(memory),cpu='50m'),limits=dict(memory=str(memory),cpu='1')))
    if filler:
        ctr['command']=['python','-B','-u','-c','import signal,sys,time;signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));time.sleep(1800)']
    else:
        ctr.update(ports=[{'containerPort':8080}],readinessProbe=dict(tcpSocket={'port':8080},periodSeconds=1,
                   timeoutSeconds=1,failureThreshold=20))
    if prestop:
        ctr['lifecycle']={'preStop':{'exec':{'command':['python','-B','-c',f'import time;time.sleep({prestop})']}}}
    return dict(apiVersion='v1',kind='Pod',metadata=dict(name=name,namespace=plant.namespace,labels={'fgcs-retirement-trial':plant.cluster}),
                spec=dict(restartPolicy='Never',schedulerName='default-scheduler',automountServiceAccountToken=False,
                          terminationGracePeriodSeconds=30,priorityClassName=plant.priority_class,containers=[ctr]))


def pod(plant,name):
    o=plant.kub('get','pod',name,'-n',plant.namespace,'--ignore-not-found','-o','json',timeout=8)
    return json.loads(o.stdout) if o.stdout.strip() else None


def create(plant, manifest):
    start=stamp(); o=plant.kub('create','-f','-','-o','json',stdin=json.dumps(manifest),timeout=10); ack=stamp()
    p=json.loads(o.stdout); m=p['metadata']; spec=p['spec']
    need(m['namespace']==plant.namespace and m['labels']['fgcs-retirement-trial']==plant.cluster and
         spec.get('preemptionPolicy')=='Never' and spec.get('priority')==0 and not spec.get('hostNetwork') and not spec.get('hostPID'),
         'non-preempting, owned Pod contract')
    return dict(name=m['name'],uid=m['uid'],create_submit_s=sec(start),create_ack_s=sec(ack))


def delete(plant, instance):
    name,uid=instance['name'],instance['uid']
    options=dict(apiVersion='v1',kind='DeleteOptions',preconditions={'uid':uid})
    start=stamp(); raw=plant.kub('delete','--raw',f'/api/v1/namespaces/{plant.namespace}/pods/{name}','-f','-',
                               stdin=json.dumps(options),timeout=10); ack=stamp()
    return dict(submit_s=sec(start),ack_s=sec(ack),response=json.loads(raw.stdout))


def wait_pod(plant,i,mode,timeout=16.):
    deadline=time.monotonic()+timeout; last=None
    while time.monotonic()<deadline:
        start=stamp(); p=pod(plant,i['name']); end=stamp()
        if p is not None:
            need(p['metadata']['uid']==i['uid'],'name reused or UID mismatch')
            if mode=='ready' and any(r['type']=='Ready' and r['status']=='True' for r in p.get('status',{}).get('conditions',())):
                return p,{'obs_s':sec(end),'last_not_ready_s':last}
        elif mode=='absent':
            return None,{'obs_s':sec(end),'last_present_s':last}
        last=sec(start); time.sleep(.07)
    raise TimeoutError(f'{i["name"]} {mode} timeout')


def upstream(plant,p,rid,app_id):
    ip=ipaddress.ip_address(p['status']['podIP']); need(ip.version==4 and ip.is_private and not ip.is_loopback,'bad Pod IP')
    endpoint=f'http://{ip}:8080/status' if rid is None else f'http://{ip}:8080/work?request_id={rid}&service_seconds=0.05'
    result=plant.node('curl','--noproxy','*','--silent','--show-error','--fail','--max-time','4',endpoint,timeout=5)
    body=json.loads(result.stdout)
    need(body['pid']==1 and body['app_id']==app_id and (rid is None or body['request_id']==rid),'wrong app response')
    return body


class Collector:
    def __init__(self, plant, instance, boot, folder):
        self.rows=[]; self.error=None; self.ready=threading.Event(); self.lock=threading.Lock(); self.stopping=False
        self.err=(folder/'signal.stderr').open('x')
        cmd=[str(plant.kubectl),'--kubeconfig',str(plant.private),'logs','-f','-n',plant.namespace,instance['name'],'-c','app']
        self.p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=self.err,text=True,bufsize=1)
        self.signal=None; self.uid=instance['uid']
        def collect():
            try:
                with (folder/'signal.jsonl').open('x') as raw:
                    for line in self.p.stdout:
                        received=stamp(); event=json.loads(line)
                        need(event['boot_id']==boot and event['pid']==1 and event['app_id']==0,'X handler identity')
                        e=dict(event=event,received=received,validated=stamp(),x_uid=self.uid)
                        with self.lock:
                            self.rows.append(e)
                            if event['event']=='sigterm_received':
                                need(self.signal is None,'duplicate X termination'); self.signal=e
                        raw.write(json.dumps(e)+'\n'); raw.flush()
                        if event['event']=='app_socket_listening': self.ready.set()
            except Exception as e: self.error=repr(e)
        self.t=threading.Thread(target=collect,daemon=True); self.t.start()

    def close(self):
        if self.p.poll() is None: self.p.terminate()
        self.t.join(5.); self.p.wait(timeout=5.); self.err.close()


def events(s):
    out=[]
    for l in s.splitlines():
        if '{' not in l: continue
        try: r=json.loads(l[l.index('{'):])
        except ValueError: continue
        if isinstance(r.get('ts'),(int,float)) and isinstance(r.get('msg'),str):
            need(1e12<r['ts']<1e13,'scheduler log is not epoch milliseconds')
            r['ts']/=1000.; out.append(r)
    return out


def ref(r):
    return r if isinstance(r,str) else f"{r['namespace']}/{r['name']}" if isinstance(r,dict) and 'namespace' in r else None


def fit_view(logs,aud,ns,node,i):
    key=ns+'/'+i['name']
    own=[l for l in logs if ref(l.get('pod'))==key]
    hits=[e for e in aud if e.get('verb')=='create' and e.get('stage')=='ResponseComplete'
          and e.get('objectRef',{}).get('subresource')=='binding' and e.get('objectRef',{}).get('name')==i['name']
          and e.get('objectRef',{}).get('namespace')==ns and e.get('requestObject',{}).get('metadata',{}).get('uid')==i['uid']
          and e.get('requestObject',{}).get('target',{}).get('name')==node and e.get('responseStatus',{}).get('code') in (200,201,202)]
    ok=[l for l in own if l['msg']=='Successfully bound pod to node']
    fail=[l for l in own if l['msg']=='Unable to schedule pod; no fit; waiting']
    mem=[l for l in fail if '1 Insufficient memory' in l.get('err','')]
    out=dict(memory_failures=mem,other_failures=len(fail)-len(mem),verified=False,bind_s=None)
    if len(ok)!=1 or len(hits)!=1: return out
    b=hits[0]; begin=time_text(b['requestReceivedTimestamp']); end=time_text(b['stageTimestamp']); marker=ok[0]['ts']
    if not begin<=end<=marker+.005 or abs(marker-end)>.25: return out
    attempts=[e['ts'] for e in own if e['msg']=='Attempting to schedule pod'
              and max((l['ts'] for l in fail),default=0)<e['ts']<=begin]
    out.update(bind_s=end,verified=True,bind_uid=i['uid'],scheduler_bind_s=marker,api_receive_s=begin,
               reserve_lower_s=attempts[0] if len(attempts)==1 else i['create_submit_s'],audit_id=b['auditID'])
    return out


def release(logs,ns,instance,known_targets):
    key=ns+'/'+instance['name']
    failure=any(l['msg']=='Scheduler cache RemovePod failed' and ref(l.get('pod'))==key for l in logs)
    begin=[l['ts'] for l in logs if l['msg']=='Delete event for scheduled pod' and ref(l.get('pod'))==key]
    if failure or len(begin)!=1: return None,None
    hints=[l['ts'] for l in logs if l['msg']=='another scheduled pod was deleted, and it may make the unscheduled pod schedulable'
           and ref(l.get('deletedPod'))==key and ref(l.get('pod')) in known_targets and l['ts']>=begin[0]]
    return begin[0],min(hints,default=None)


def cost(start,end, intervals):
    def dur(a,b): return max(0.,min(end,b)-max(start,a))*(M/1e9)
    return (sum(dur(first_hi,lo) for _,first_hi,lo,_ in intervals),
            sum(dur(first_lo,hi) for first_lo,_,_,hi in intervals))


class Gateway(http.server.ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self, fn):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                ingress=stamp()
                try: ret=fn(self.path,ingress); code=200
                except Exception as e: ret={'error':repr(e)}; code=500
                body=json.dumps(ret).encode(); self.send_response(code); self.send_header('Content-Length',str(len(body))); self.end_headers()
                try: self.wfile.write(body)
                except OSError: pass
        super().__init__(('127.0.0.1',0),Handler)
        self.t=threading.Thread(target=self.serve_forever,kwargs={'poll_interval':.025},daemon=True); self.t.start()

    def fetch(self,route,t,offset):
        at(t,offset); start=stamp()
        try:
            with HTTP.open(f'http://127.0.0.1:{self.server_port}{route}',timeout=22.) as r:
                status,body=r.status,json.loads(r.read())
            return dict(status=status,body=body,client_start=start,client_done=stamp())
        except Exception as e:
            return dict(status=getattr(e,'code',None),error=repr(e),client_start=start,client_done=stamp())

    def stop(self):
        self.shutdown(); self.server_close(); self.t.join(timeout=3.)


def node_budget(plant):
    a=json.loads(plant.kub('get','node',plant.node_name,'-o','json').stdout)['status']['allocatable']; alloc=quantity(a['memory'])
    items=json.loads(plant.kub('get','pods','-A','-o','json').stdout)['items']; base=0; rows=[]
    for p in items:
        if p['spec'].get('nodeName')!=plant.node_name or p['status'].get('phase') in ('Succeeded','Failed'): continue
        s=p['spec']; need(not s.get('overhead') and not s.get('resources'),'unverified system request semantics')
        need(not any(c.get('restartPolicy')=='Always' for c in s.get('initContainers',[])),'unverified system sidecar accounting')
        q=lambda c:quantity(c.get('resources',{}).get('requests',{}).get('memory','0'))
        asked=max(sum(q(c) for c in s['containers']),max((q(c) for c in s.get('initContainers',[])),default=0))
        base+=asked; rows.append((p['metadata']['namespace'],p['metadata']['uid'],asked))
    filler=alloc-base-2*M-SPARE
    need(256*1024**2<filler<8*1024**3,'dedicated node capacity outside fixed envelope (expect 3–4 GiB cgroup)')
    return dict(alloc_bytes=alloc,base_bytes=base,base_rows=sorted(rows),filler_bytes=filler)


def trial(plant,root,role,model,baseline):
    ident=role['trial_id']; folder=root/ident; folder.mkdir()
    row=dict(**role,status='incomplete',free_gb_at_decision=SPARE/1e9,y_request_gb=M/1e9,x_request_gb=M/1e9,z_request_gb=M/1e9,
             y_planned_offset_s=model['y_offset_s'],z_planned_offset_s=model['z_offset_s'],
             reservation_scope='X+Z_old+Y+Z_replacement conditional on assigned scheduler reservation',
             reservation_time_basis='host-aligned cache bounds and attempt-to-Bind; no RSS')
    live={}; clock_before=None; graph=None; observer=None; errors=[]
    z_lock=threading.Lock(); state={'z_retiring':False,'z_active':False}; dispatch=[]
    tag=ident.replace('ra17-','')
    def new(key, app, cleanup=0., delay=0., mem=M, filler=False):
        name=tag+'-'+key
        mf=fixture(plant,name,app,cleanup,delay,memory=mem,filler=filler)
        save(folder/(key+'-manifest.json'),mf); i=create(plant,mf); live[key]=i
        return i
    try:
        need(node_budget(plant)==baseline,'unexpected base reservation before trial')
        clock_before=plant.node_alignment()
        filler=new('f',0,mem=baseline['filler_bytes'],filler=True)
        x=new('x',0,X_CLEAN,PRESTOP if role['condition']=='slow' else 0.)
        z=new('z',2,Z_CLEAN)
        xp,_=wait_pod(plant,x,'ready'); zp,_=wait_pod(plant,z,'ready'); wait_pod(plant,filler,'ready')
        row.update(x_uid=x['uid'],z_uid=z['uid'],x_boot_id=upstream(plant,xp,None,0)['boot_id'],z_boot_id=upstream(plant,zp,None,2)['boot_id'])
        observer=Collector(plant,x,row['x_boot_id'],folder); need(observer.ready.wait(5.) and not observer.error,'X live signal stream not ready')
        # An existing Z must be idle and unprotected at the first Y decision.
        pool=ThreadPoolExecutor(max_workers=5)
        def retire_z(origin):
            try:
                result=delete(plant,z); row[origin+'_z_delete']=result
                if origin=='first': row.update(z_first_delete_s=result['submit_s'],z_first_delete_ack_s=result['ack_s'],first_retire_applied=True,
                                               first_z_delete_receipt_uid=z['uid'],first_applied_action='reclaim')
                else: row['later_retire_applied']=True
            except Exception as e: errors.append('Z DELETE '+repr(e))
        def forecast_action(value,now): return 'wait' if now<value<=now+1. else 'reclaim'
        def pick(y_ingress):
            with observer.lock:
                now=stamp(); signal=observer.signal
            t=sec(now)
            h=signal['event']['wall_ns']/1e9 if signal else None
            valid=bool(signal and sec(intent)<=h<=sec(signal['received'])<=sec(signal['validated'])<=t and signal['x_uid']==x['uid'])
            fixed=sec(intent)+model['fixed_s']; hybrid=h+X_CLEAN+model['residual_s'] if valid else fixed
            a,b=forecast_action(fixed,t),forecast_action(hybrid,t)
            chosen={'fixed':a,'signal':b,'force_wait':'wait','force_reclaim':'reclaim'}[role['arm']]
            row.update(decision_s=t,signal_used=valid,fixed_predicted_return_s=fixed,signal_predicted_return_s=hybrid,
                       fixed_shadow_action=a,signal_shadow_action=b,initial_action=chosen,first_proposed_action=chosen,
                       first_retire_applied=False,later_retire_applied=False,first_applied_action='wait' if chosen=='wait' else None,
                       x_signal_used_in_policy=valid and role['arm']=='signal')
            if chosen=='reclaim':
                with z_lock:
                    if not state['z_active'] and not state['z_retiring']:
                        state['z_retiring']=True; row['z_first_route_disabled_s']=sec(stamp())
                        dispatch.append(pool.submit(lambda:(at(y_ingress,.08),retire_z('first'))))
            dispatch.append(pool.submit(recheck,y_ingress))
        def recheck(ingress):
            if role.get('calibration'): return
            at(ingress,1.); y=live.get('y'); now=stamp()
            check=dict(t_s=sec(now),reason='common fixed forecast at ingress+1s')
            row['recheck']=check
            if not y or (p:=pod(plant,y['name'])) is None or p['spec'].get('nodeName'):
                check['result']='no_pending_Y'; return
            if pod(plant,x['name']) is None:
                check['result']='X API absent; no further retirement approved'; return
            with z_lock:
                if state['z_active'] or state['z_retiring'] or forecast_action(sec(intent)+model['fixed_s'],sec(now))=='wait':
                    check['result']='protected, already retiring, or still predicts within horizon'; return
                state['z_retiring']=True; check['result']='reclaim'
            retire_z('later')
        used=set(); ingress_lock=threading.Lock()
        def request(route, ingress):
            with ingress_lock:
                need(route in ('/y','/z') and route not in used,'one visit per route'); used.add(route)
            key=route[1]; row[key+'_arrival_s']=sec(ingress)
            try:
                if key=='y':
                    pick(ingress)
                    i=new('y',1); row.update(y_uid=i['uid'],y_submit_s=i['create_submit_s'],y_create_ack_s=i['create_ack_s'])
                    p,r=wait_pod(plant,i,'ready'); row['y_ready_s']=r['obs_s']; app=1
                else:
                    with z_lock:
                        retire=state['z_retiring']; state['z_active']=True
                    app=2
                    if retire:
                        i=new('zn',2,Z_CLEAN); row.update(z_serving_uid=i['uid'],z_recreations=1,z_submit_s=i['create_submit_s'])
                        p,r=wait_pod(plant,i,'ready'); row['z_ready_s']=r['obs_s']
                    else:
                        p=zp; row.update(z_serving_uid=z['uid'],z_recreations=0,z_ready_s=sec(ingress))
                body=upstream(plant,p,ident+'-'+key,app)
                row[key+'_upstream']=body; return body
            finally:
                if key=='z':
                    with z_lock: state['z_active']=False
        graph=Gateway(request)
        intent=stamp(); row.update(x_retire_intent_s=sec(intent),cost_window_start_s=sec(intent),cost_window_end_s=sec(intent)+WINDOW)
        future_x=pool.submit(delete,plant,x)
        fy=pool.submit(graph.fetch,'/y',intent,model['y_offset_s']); fz=pool.submit(graph.fetch,'/z',intent,model['z_offset_s'])
        row['x_delete']=future_x.result(timeout=12.)
        for key, future in (('y',fy),('z',fz)):
            q=future.result(timeout=24.); row[key+'_client']=q
            row[key+'_done_s']=sec(q['client_done']); row[key+'_http_ok']=q['status']==200 and row[key+'_done_s']<=sec(intent)+WINDOW
        for f in dispatch: f.result(timeout=12.)
        pool.shutdown(wait=True)
        at(intent,WINDOW)
        clock_after=plant.node_alignment()
        rawlog=plant.scheduler_logs(); rawaud=plant.audit_logs()
        # Preserve only experiment namespace records, without unrelated system Pod events.
        logs=[l for l in events(rawlog)
              if any((ref(l.get(k)) or '').startswith(plant.namespace+'/'+tag+'-') for k in ('pod','deletedPod'))]
        aud=[e for l in rawaud.splitlines() if l and (e:=json.loads(l)).get('objectRef',{}).get('namespace')==plant.namespace
             and e['objectRef'].get('name','').startswith(tag+'-')]
        save(folder/'scheduler-events.json',logs); save(folder/'binding-audit.json',aud)
        row['clock_before']=clock_before; row['clock_after']=clock_after
        witness={plant.namespace+'/'+i['name'] for k,i in live.items() if k in ('y','zn')}
        for k,i in live.items():
            if k in ('y','zn'):
                r=fit_view(logs,aud,plant.namespace,plant.node_name,i); row[k+'_binding']=r
                pfx='y' if k=='y' else 'z'; row[pfx+'_bind_s']=r['bind_s']
                if k=='y': row.update(y_bind_uid_verified=r['verified'],y_bind_witness_id=r.get('audit_id'))
            if k in ('x','z'):
                lo,hi=release(logs,plant.namespace,i,witness)
                row[k+'_release_lo_s']=lo; row[k+'_release_hi_s']=hi
        sig=observer.signal
        row.update(x_signal_uid=sig['x_uid'] if sig else None,x_signal_event_s=sig['event']['wall_ns']/1e9 if sig else None,
                   x_signal_received_s=sec(sig['received']) if sig else None,x_signal_validated_s=sec(sig['validated']) if sig else None,
                   signal_observer_complete=bool(sig and not observer.error),capacity_ledger_complete='y' in live and
                   (row.get('z_submit_s') is None or row.get('y_bind_s') is not None and row.get('z_bind_s') is not None
                    and row['z_bind_s']>=row['y_bind_s']),
                   y_memory_failure_count=len(row.get('y_binding',{}).get('memory_failures',[])))
        lo,hi=row['cost_window_start_s'],row['cost_window_end_s']; periods=[]
        for k,i in live.items():
            if k=='f': continue
            if k in ('x','z'):
                retires=k=='x' or state['z_retiring']; rlo,rhi=row[k+'_release_lo_s'],row[k+'_release_hi_s']
                guard_start=lo if k=='x' else row.get('first_z_delete',{}).get('submit_s',row.get('later_z_delete',{}).get('submit_s',lo))
                periods.append((lo,lo,(rlo or guard_start) if retires else hi,(rhi or hi) if retires else hi))
            else:
                b=row[k+'_binding']; done=b['bind_s']; s=i['create_submit_s']; confirmed=done or hi
                periods.append((b.get('reserve_lower_s',s),confirmed,hi,hi))
        c0,c1=cost(lo,hi,periods)
        row.update(cost_lower_gbs=c0,cost_upper_gbs=c1,reservation_window_complete=True,
                   filler_window_gbs=(hi-lo)*baseline['filler_bytes']/1e9,reservation_intervals=periods,
                   status='complete' if row.get('y_http_ok') and row.get('z_http_ok') and row.get('y_bind_uid_verified') and not errors else 'incomplete')
    except Exception as e:
        errors.append(repr(e))
    finally:
        if graph: graph.stop()
        if observer:
            try: observer.close()
            except Exception as e: errors.append('signal teardown '+repr(e))
        row['pod_creates']=live.copy()
        # Everything remaining is in the newly owned namespace and guarded by its exact UID.
        for k, i in list(live.items()):
            try:
                p=pod(plant,i['name']);
                if p and not p['metadata'].get('deletionTimestamp'):
                    need(p['metadata']['uid']==i['uid'],'cleanup UID mismatch'); delete(plant,i)
            except Exception as e: errors.append('cleanup '+repr(e))
        for i in live.values():
            try: wait_pod(plant,i,'absent',timeout=12.)
            except Exception as e: errors.append('cleanup '+repr(e))
        row['errors']=errors
        if errors: row['status']='incomplete'
        save(root/f'trial-{ident}.json',row)
    return row


def seal_calibration(rows,root,pins):
    use=[]
    for r in rows:
        if not (r.get('status')=='complete' and r.get('signal_observer_complete') and r.get('y_bind_uid_verified') and r.get('y_memory_failure_count') and
                all(r.get(k+'_release_lo_s') is not None and r.get(k+'_release_hi_s') is not None for k in ('x','z'))): continue
        xlo,xhi,zlo,zhi=[r[k+'_release_'+b+'_s'] for k,b in (('x','lo'),('x','hi'),('z','lo'),('z','hi'))]
        mid=(xlo+xhi)/2; t0=r['x_retire_intent_s']; h=r['x_signal_event_s']; yd=r['decision_s']
        order='x_first' if xhi<zlo else 'z_first' if zhi<xlo else 'unresolved'
        use.append(dict(trial_id=r['trial_id'],condition=r['condition'],order=order,
                        x_return_s=mid-t0, x_post_handler_residual_s=mid-h-X_CLEAN,x_return_after_decision_s=mid-yd,
                        x_interval_s=[xlo-t0,xhi-t0],z_interval_from_command_s=[zlo-r['z_first_delete_s'],zhi-r['z_first_delete_s']],
                        signal_available=r.get('signal_used'),verified_bind_s=r['y_bind_s']))
    good={c:sum(x['condition']==c and x['order']==('x_first' if c=='fast' else 'z_first') for x in use) for c in CONDS}
    admissible=min(good.values())>=3
    o=dict(admitted=admissible,calibration_plan_n=8,usable_n=len(use),interval_confirmed_order_counts=good,calibration=use,
           source_hashes=pins,x_cleanup_s=X_CLEAN,z_cleanup_s=Z_CLEAN,prestop_slow_s=PRESTOP,
           y_offset_s=Y_OFFSET,z_offset_s=Z_OFFSET,cost_window_s=WINDOW,bind_tolerance_s=.05,
           fixed_s=statistics.median(x['x_return_s'] for x in use) if use else None,
           residual_s=statistics.median(x['x_post_handler_residual_s'] for x in use) if use else None)
    save(root/'CALIBRATION_SEAL.json',o); print('CALIBRATION',json.dumps(o),flush=True)
    return o


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('calibration','evaluation')); p.add_argument('root',type=Path)
    p.add_argument('--calibration-seal',type=Path); p.add_argument('--kind',type=Path,required=True); p.add_argument('--kubectl',type=Path,required=True)
    a=p.parse_args(); pins={s:sha(HERE/s) for s in ('run_kind.py','kind_base.py','KIND_PROTOCOL.md')}
    if a.phase=='evaluation':
        need(a.calibration_seal is not None,'require separately completed calibration'); model=json.loads(a.calibration_seal.read_text())
        need(model['admitted'] and model['source_hashes']==pins,'calibration not admitted or source changed')
    else: model=dict(y_offset_s=Y_OFFSET,z_offset_s=Z_OFFSET,fixed_s=4.0,residual_s=.2)
    rs=schedule(a.phase); a.root.mkdir(parents=True,exist_ok=False)
    cluster='fgcs-retact17-'+('cal' if a.phase=='calibration' else 'eval')
    save(a.root/'PLAN.json',dict(trials=rs,phase=a.phase,source_hashes=pins,bind_tolerance_s=.05,model=model,
                                seal_sha256=sha(a.calibration_seal) if a.calibration_seal else None,cluster=cluster))
    out=[]; error=None
    try:
        with Plant(a.root,cluster,a.kind,a.kubectl) as plant:
            baseline=node_budget(plant); save(a.root/'CAPACITY.json',baseline)
            for r in rs:
                rr=trial(plant,a.root,{**r,'calibration':a.phase=='calibration'},model,baseline); out.append(rr)
                print(rr['trial_id'],rr['status'],rr.get('errors'),flush=True)
                if any(x.startswith('cleanup ') for x in rr['errors']): raise RuntimeError('stop: failed state reset')
        if a.phase=='calibration' and len(out)==len(rs): seal_calibration(out,a.root,pins)
    except Exception as e:
        error=repr(e); print('STOP',error,flush=True)
    finally:
        save(a.root/'TERMINAL.json',dict(planned=len(rs),attempted=len(out),complete=sum(x['status']=='complete' for x in out),error=error,
                                         phase=a.phase,plan_sha256=sha(a.root/'PLAN.json')))
    return int(error is not None or len(out)!=len(rs))


if __name__=='__main__': raise SystemExit(main())
