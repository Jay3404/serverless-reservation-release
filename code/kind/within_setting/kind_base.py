"""Isolated single-node Kind bootstrap for the return-action experiment.

Only the fresh, labeled cluster and its dedicated network may be mutated. Uses
the existing verified images, and no image building/pulling or default kubeconfig.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


NODE_IMAGE_ID = 'sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed'
NODE_IMAGE = 'kindest/node:v1.36.4@' + NODE_IMAGE_ID
APP_IMAGE = 'kind.local/lifecycle-termination:20261107-v1'
APP_IMAGE_ID = 'sha256:faba761370e90510db7122a082dc0fdca8a2bb62a9235a79d3d080c05cff6112'
APP_SOURCE_SHA256 = '8917f744995b1bb21a6fe803f2b466c42ddde213b111e6030eff9b9d694015f9'
OWNER_LABEL = 'fgcs-retirement-action-owner'
NODE_MEMORY = 8 * 2**30
NODE_CPU = 4
AUDIT_IN_NODE = '/var/log/fgcs-retirement-action/bindings.jsonl'
AUDIT_POLICY_IN_NODE = '/etc/kubernetes/fgcs-retirement-action-audit.json'
PRIORITY_CLASS = 'fgcs-retirement-nonpreempting'
FAIL = 'Unable to schedule pod; no fit; waiting'
BEGIN = 'Delete event for scheduled pod'
QUEUE = 'another scheduled pod was deleted, and it may make the unscheduled pod schedulable'
ATTEMPT = 'Attempting to schedule pod'
BIND_OK = 'Successfully bound pod to node'
CLUSTER_RE = re.compile(r'(?:fgcs-|lc-)[a-z0-9](?:[a-z0-9-]{0,39}[a-z0-9])?')


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def clock():
    return {'realtime_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}


def save_new(p, obj):
    with p.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, indent=2, allow_nan=False, sort_keys=True)
        stream.write('\n')


def bytes_value(quantity):
    number, unit = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)', str(quantity)).groups()
    powers = {'': 1, **{c.lower() if c=='K' else c: 1000**p for p,c in enumerate('KMGTPE',1)},
              **{c+'i': 1024**p for p,c in enumerate('KMGTPE',1)}}
    value = Decimal(number) * powers[unit]
    need(value == int(value), f'fractional byte request is not admitted: {quantity}')
    return int(value)


def pod_ref(obj):
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict) and {'namespace','name'} <= obj.keys():
        return f"{obj['namespace']}/{obj['name']}"
    return None


def log_rows(raw):
    rows = []
    for line in raw.splitlines():
        i = line.find('{')
        if i < 0:
            continue
        try:
            x = json.loads(line[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(x.get('ts'), (int, float)) and isinstance(x.get('msg'), str):
            need(1e12 < x['ts'] < 1e13, 'expected pinned Klog epoch milliseconds')
            x['ts'] /= 1000.0
            rows.append(x)
    return rows


class Plant:
    app_image = APP_IMAGE
    priority_class = PRIORITY_CLASS
    save = staticmethod(save_new)

    def __init__(self, root, cluster, kind, kubectl):
        need(CLUSTER_RE.fullmatch(cluster) is not None, 'cluster must have new lc-/fgcs- scope')
        self.root = Path(root).resolve()
        self.cluster = self.namespace = cluster
        self.node_name = cluster + '-control-plane'
        self.kind = str(Path(kind).resolve()) if os.sep in str(kind) else shutil.which(str(kind))
        self.kubectl = str(Path(kubectl).resolve()) if os.sep in str(kubectl) else shutil.which(str(kubectl))
        self.docker = shutil.which('docker')
        need(all(p and os.path.isfile(p) and os.access(p, os.X_OK) for p in (self.kind,self.kubectl,self.docker)),
             'docker, kind, kubectl executables must already be installed')
        self.private = self.root / 'private-kubeconfig'
        need(not self.private.exists(), 'unowned kubeconfig collision')
        self.env = os.environ.copy()
        self.env.pop('KUBECONFIG', None)
        self.env.update(KIND_EXPERIMENTAL_PROVIDER='docker', KIND_EXPERIMENTAL_DOCKER_NETWORK=cluster)
        self.node_id = self.network_id = None
        self.before = self.node_identity = None
        self.creation_allowed = False

    def _command(self, argv, check=True, timeout=30, stdin=None):
        return subprocess.run([str(v) for v in argv], env=self.env, input=stdin,
                              text=True, capture_output=True, check=check, timeout=timeout)

    def _docker(self,*args,**kwargs):
        return self._command((self.docker,*args),**kwargs)

    def _snapshot(self):
        containers = [json.loads(s) for s in self._docker('ps','-a','--no-trunc','--format','{{json .}}').stdout.splitlines()]
        networks = [json.loads(s) for s in self._docker('network','ls','--no-trunc','--format','{{json .}}').stdout.splitlines()]
        return {'containers': sorted((x['ID'],x['Names'],x.get('State')) for x in containers),
                'networks': sorted((x['ID'],x['Name'],x.get('Driver')) for x in networks)}

    def _inspect(self,name,kind):
        cmd = ('network','inspect',name) if kind=='network' else ('container','inspect',name)
        p = self._docker(*cmd,check=False)
        if p.returncode == 0:
            result = json.loads(p.stdout)
            need(len(result)==1,'nonunique Docker inspect')
            return result[0]
        need('No such' in (p.stderr+p.stdout) or 'not found' in (p.stderr+p.stdout),
             f'could not safely determine {kind} presence: {p.stderr.strip()}')
        return None

    def _owns_node(self, row):
        labs = row.get('Config',{}).get('Labels') or {}
        return (row.get('Name') == '/'+self.node_name and labs.get(OWNER_LABEL)==self.cluster
                and labs.get('io.x-k8s.kind.cluster')==self.cluster and
                labs.get('io.x-k8s.kind.role')=='control-plane' and
                (self.node_id is None or self.node_id==row.get('Id')))

    def _owns_network(self,row):
        return (row.get('Name')==self.cluster and (row.get('Labels') or {}).get(OWNER_LABEL)==self.cluster
                and self.network_id is not None and row.get('Id')==self.network_id)

    def kub(self,*args,check=True,timeout=30,stdin=None):
        need(self.node_id is not None and self.private.is_file() and not self.private.is_symlink(),
             'cluster identity and private kubeconfig required')
        deny=('--kubeconfig','--context','--server','--cluster','--token','--user')
        for v in args:
            if v=='--': break
            need(not any(str(v)==name or str(v).startswith(name+'=') for name in deny), 'kubeconfig override refused')
        return self._command((self.kubectl,'--kubeconfig',self.private,*args),check=check,timeout=timeout,stdin=stdin)

    def node(self,*args,check=True,timeout=30,stdin=None):
        need(self.node_id is not None, 'exact owned Docker node ID required')
        return self._docker('exec',*(('-i',) if stdin is not None else ()),self.node_id,*args,
                            check=check,timeout=timeout,stdin=stdin)

    def scheduler_logs(self):
        return self.kub('logs','-n','kube-system','kube-scheduler-'+self.node_name).stdout

    def audit_logs(self):
        return self.node('cat',AUDIT_IN_NODE).stdout

    def node_alignment(self):
        start = clock()
        value = int(self.node('date','+%s%N').stdout.strip())
        end = clock()
        need(start['realtime_ns'] <= value <= end['realtime_ns'], 'node realtime outside controller bracket')
        drift = abs((end['realtime_ns']-start['realtime_ns']) - (end['monotonic_ns']-start['monotonic_ns']))
        need(drift < 2_000_000, 'realtime/monotonic discontinuity')
        return {'controller_start':start,'node_realtime_ns':value,'controller_end':end,'clock_delta_drift_ns':drift}

    def _config(self, host_bytes):
        # The prior node reported host memory, not the enclosing cgroup cap.
        # Set scheduler-visible memory near 4 GiB and validate actual allocatable.
        need(host_bytes>=12*2**30,'host is too small for the separate experiment node')
        system_reserved = host_bytes-4*2**30
        policy = {'apiVersion':'audit.k8s.io/v1','kind':'Policy','omitStages':['RequestReceived'],
                  'rules':[{'level':'Request','verbs':['create'],'namespaces':[self.namespace],
                            'resources':[{'group':'','resources':['pods/binding']}]},{'level':'None'}]}
        path=self.root/'binding-audit-policy.json'
        save_new(path,policy)
        kubeadm = {'kind':'ClusterConfiguration','apiVersion':'kubeadm.k8s.io/v1beta4',
                   'scheduler':{'extraArgs':[{'name':'v','value':'5'},{'name':'logging-format','value':'json'}]},
                   'apiServer':{'extraArgs':[{'name':'audit-policy-file','value':AUDIT_POLICY_IN_NODE},
                                             {'name':'audit-log-path','value':AUDIT_IN_NODE},
                                             {'name':'audit-log-mode','value':'blocking'}],
                                'extraVolumes':[{'name':'fgcs-policy','hostPath':AUDIT_POLICY_IN_NODE,
                                                 'mountPath':AUDIT_POLICY_IN_NODE,'readOnly':True,'pathType':'File'},
                                                {'name':'fgcs-logs','hostPath':str(Path(AUDIT_IN_NODE).parent),
                                                 'mountPath':str(Path(AUDIT_IN_NODE).parent),
                                                 'readOnly':False,'pathType':'DirectoryOrCreate'}]}}
        cfg = {'apiVersion':'kind.x-k8s.io/v1alpha4','kind':'Cluster','name':self.cluster,
               'networking':{'apiServerAddress':'127.0.0.1','apiServerPort':0},
               'nodes':[{'role':'control-plane','kubeadmConfigPatches':[
                   f'kind: KubeletConfiguration\nsystemReserved:\n  memory: "{system_reserved}"\n',json.dumps(kubeadm)],
                   'extraMounts':[{'hostPath':str(path),'containerPath':AUDIT_POLICY_IN_NODE,'readOnly':True}]}]}
        path=self.root/'kind-config.json'
        save_new(path,cfg)
        return path,system_reserved

    def _node_budget(self, reserve):
        nodes=json.loads(self.kub('get','nodes','-o','json').stdout)['items']
        need(len(nodes)==1 and nodes[0]['metadata']['name']==self.node_name,'one owned scheduler target required')
        nd=nodes[0]
        cond={x['type']:x['status'] for x in nd['status']['conditions']}
        need(cond.get('Ready')=='True' and cond.get('MemoryPressure')=='False','node Ready and no memory pressure required')
        pods=json.loads(self.kub('get','pods','-A','-o','json').stdout)['items']
        baseline={}
        for p in pods:
            s=p['spec']
            if s.get('nodeName')!=self.node_name or p['status'].get('phase') in ('Failed','Succeeded'):
                continue
            need(p['metadata']['namespace'] in ('kube-system','local-path-storage'), 'unexpected baseline workload')
            need(not s.get('initContainers') and not s.get('resources'),'nonlinear baseline requests are unsupported')
            key=p['metadata']['namespace']+'/'+p['metadata']['name']
            baseline[key]=bytes_value(s.get('overhead',{}).get('memory','0')) + sum(
                bytes_value(c.get('resources',{}).get('requests',{}).get('memory','0')) for c in s['containers'])
        alloc=bytes_value(nd['status']['allocatable']['memory'])
        need(3*2**30 <= alloc <= 5*2**30,'owned allocatable not near 4 GiB; do not run trials')
        sched=json.loads(self.kub('get','pod','kube-scheduler-'+self.node_name,'-n','kube-system','-o','json').stdout)
        version=self.kub('version','-o','json').stdout
        need(json.loads(version)['serverVersion']['gitVersion']=='v1.36.4','unexpected Kubernetes server version')
        return {'allocatable_bytes':alloc,'existing_request_bytes':sum(baseline.values()),'system_requests_bytes':baseline,
                'system_reserved_bytes':reserve,'docker_memory_cap':NODE_MEMORY,'docker_cpu_cap':NODE_CPU,
                'node_uid':nd['metadata']['uid'],'node_id':self.node_id,'kubernetes_version':json.loads(version),
                'kubelet_version':nd['status']['nodeInfo']['kubeletVersion'], 'node_image':NODE_IMAGE,
                'app_image':APP_IMAGE,'app_image_id':APP_IMAGE_ID,
                'scheduler_image':sched['spec']['containers'][0]['image'],
                'scheduler_image_id':sched['status']['containerStatuses'][0]['imageID'],
                'kind_version':self._command((self.kind,'version')).stdout.strip()}

    def __enter__(self):
        self.root.mkdir(parents=True,exist_ok=True)
        self.before=self._snapshot()
        save_new(self.root/'docker-before.json',self.before)
        try:
            need(self._inspect(self.node_name,'container') is None and self._inspect(self.cluster,'network') is None,
                 'fresh cluster and network names required')
            need(not any(name.startswith(self.cluster+'-') for _,name,_ in self.before['containers']),
                 'cluster-prefix Docker name collision')
            info=json.loads(self._docker('info','--format','{{json .}}').stdout)
            need(info['OSType']=='linux' and int(info['NCPU'])>=NODE_CPU,'bounded Linux Docker node required')
            need(shutil.disk_usage(self.root).free>=8*2**30,'less than 8 GiB of workspace disk space')
            for name, wanted in ((NODE_IMAGE,NODE_IMAGE_ID),(APP_IMAGE,APP_IMAGE_ID)):
                rows=json.loads(self._docker('image','inspect',name).stdout)
                need(len(rows)==1 and rows[0]['Id']==wanted, f'exact cached image unavailable: {name}')
            cfg,reserve=self._config(int(info['MemTotal']))
            bindir=self.root/'owned-bin'
            bindir.mkdir()
            wrapper=bindir/'docker'
            args=['--cpus',str(NODE_CPU),'--memory',str(NODE_MEMORY),'--memory-swap',str(NODE_MEMORY),
                  '--label',OWNER_LABEL+'='+self.cluster]
            with wrapper.open('x',encoding='utf-8') as f:
                f.write(f'#!{sys.executable}\nimport os,sys\na=sys.argv[1:]\n'
                        f'if a and a[0]=="run":\n'
                        f'    if "--name" not in a or a[a.index("--name")+1]!={self.node_name!r}:\n'
                        f'        sys.exit("unexpected Kind run target")\n'
                        f'    a[1:1]={args!r}\n'
                        f'os.execv({self.docker!r},[{self.docker!r}]+a)\n')
            wrapper.chmod(0o700)
            self.env['PATH']=str(bindir)+os.pathsep+self.env.get('PATH','')
            self.network_id=self._docker('network','create','--driver','bridge','--label',
                                         OWNER_LABEL+'='+self.cluster,self.cluster).stdout.strip()
            self.creation_allowed=True
            self._command((self.kind,'create','cluster','--name',self.cluster,'--config',cfg,'--image',NODE_IMAGE,
                           '--kubeconfig',self.private,'--wait','150s','--retain'),timeout=350)
            d=self._inspect(self.node_name,'container')
            need(d is not None and self._owns_node(d), 'owned node label/role not attested')
            h=d['HostConfig']
            need(h['Memory']==NODE_MEMORY and h['MemorySwap']==NODE_MEMORY and h['NanoCpus']==NODE_CPU*10**9,
                 'node not using requested CPU/memory bound')
            need(set(d['NetworkSettings']['Networks'])=={self.cluster}, 'node attached to unexpected Docker network')
            self.node_id=d['Id']
            self.private.chmod(0o600)
            self._command((self.kind,'load','docker-image',APP_IMAGE,'--name',self.cluster),timeout=180)
            self.kub('create','namespace',self.namespace)
            priority={'apiVersion':'scheduling.k8s.io/v1','kind':'PriorityClass','metadata':{'name':PRIORITY_CLASS,
                      'labels':{OWNER_LABEL:self.cluster}},'value':0,'globalDefault':False,'preemptionPolicy':'Never'}
            actual=json.loads(self.kub('create','-f','-','-o','json',stdin=json.dumps(priority)).stdout)
            need(actual.get('globalDefault',False) is False and actual.get('value')==0 and
                 actual.get('preemptionPolicy')=='Never','nonpreemption PriorityClass not attested')
            self.node_identity=self._node_budget(reserve)
            save_new(self.root/'environment.json',self.node_identity)
            self.check_external()
            return self
        except BaseException:
            self._finish()
            raise

    def check_external(self):
        actual=self._snapshot()
        external={'containers':[r for r in actual['containers'] if r[0]!=self.node_id],
                  'networks':[r for r in actual['networks'] if r[0]!=self.network_id]}
        need(external==self.before,'external Docker inventory changed; do not touch unrelated workloads')

    def _finish(self):
        report={}
        errors=[]
        try:
            if self.creation_allowed:
                d=self._inspect(self.node_name,'container')
                if d is not None:
                    need(self._owns_node(d),'cleanup refuses unowned Docker node')
                    # Target the verified exact ID. Do not let Kind discover or delete other clusters.
                    p=self._docker('container','rm','-f',d['Id'],check=False,timeout=90)
                    need(p.returncode==0,f'owned node cleanup failed: {p.stderr.strip()}')
                report['owned_node_absent']=self._inspect(self.node_name,'container') is None
        except Exception as exc:
            errors.append(repr(exc))
        try:
            if self.network_id is not None:
                n=self._inspect(self.cluster,'network')
                if n is not None:
                    need(self._owns_network(n) and not n.get('Containers'),'cleanup refuses unowned or connected network')
                    self._docker('network','rm',self.network_id,timeout=50)
                report['owned_network_absent']=self._inspect(self.cluster,'network') is None
        except Exception as exc:
            errors.append(repr(exc))
        if self.before is not None:
            try:
                report['after']=self._snapshot()
                report['external_identical']=report['after']==self.before
                need(report['external_identical'],'external Docker inventory differed from entry')
            except Exception as exc:
                errors.append(repr(exc))
        report['errors']=errors
        save_new(self.root/'docker-cleanup.json',report)
        if errors:
            raise RuntimeError(f'owned cleanup/inventory not verified: {errors}')

    def __exit__(self, exc_type, exc, tb):
        self._finish()
        return False
