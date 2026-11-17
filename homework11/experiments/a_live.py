#!/usr/bin/env python3
"""Explicit live A executor; mutates hw11 and generates traffic. Run only when authorized."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
HELM='/private/tmp/darwin-arm64/helm'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',required=True)
p.add_argument('--phase',choices=['rollout','bad-b','kill','drain'], required=True)
a=p.parse_args()
out=Path(a.output).resolve(); out.mkdir(parents=True, exist_ok=False)
(out/'orchestrator.py').write_text(Path(__file__).read_text())
count=0

def cmd(argv, check=True):
 global count
 count+=1
 stem=f'{count:05d}'
 started=time.time()
 r=subprocess.run(argv,capture_output=True,text=True,cwd=ROOT)
 (out/(stem+'.stdout')).write_text(r.stdout)
 (out/(stem+'.stderr')).write_text(r.stderr)
 with (out/'commands.jsonl').open('a') as f:
  f.write(json.dumps(dict(argv=argv,started=started,ended=time.time(),returncode=r.returncode,stdout=stem+'.stdout',stderr=stem+'.stderr'))+'\n')
 if check and r.returncode: raise RuntimeError(f'Command failed: {argv}: {r.stderr}')
 return r

def kube(*args,check=True): return cmd(['kubectl','--context','hw11','-n','hw11',*args],check)
def helm(*args): return cmd([HELM,'--kube-context','hw11',*args])
def mark(kind,**data):
 with (out/'markers.jsonl').open('a') as f: f.write(json.dumps(dict(kind=kind,wall=time.time(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),**data))+'\n')

def upgrade(version='A',warmup=20):
 return helm('upgrade','hw11','homework11/chart','-n','hw11','--reuse-values','--set','hpa.enabled=false','--set','replicaCount=2','--set',f'artifact.version={version}','--set','simulator.premature_ready=false','--set',f'simulator.warmup_seconds={warmup}','--set','simulator.chunks=32','--set-json','simulator.chunk_cpu_seconds=0.5','--set','terminationGracePeriodSeconds=120','--set','preStopSeconds=5')

caffeine=subprocess.Popen(['caffeinate','-dimsu'])
observer=None; load=None
try:
 if cmd(['kubectl','config','current-context']).stdout.strip()!='hw11': raise RuntimeError('Wrong current context')
 helm('get','values','hw11','-n','hw11','-a')
 helm('get','manifest','hw11','-n','hw11')
 kube('get','pods','-o','json')
 upgrade()
 kube('rollout','status','deployment/hw11-inference','--timeout=240s')
 kube('get','hpa','-o','json')
 history=json.loads(helm('history','hw11','-n','hw11','-o','json').stdout)
 revision=str(history[-1]['revision'])
 observer=subprocess.Popen([sys.executable,'homework11/experiments/a_observe.py','--output',str(out/'observer'),'--duration','480'],cwd=ROOT,stdout=(out/'observer.stdout').open('w'),stderr=(out/'observer.stderr').open('w'))
 sys.path.insert(0,str(ROOT/'homework11/load'))
 from runner import schedule
 rows=schedule(duration=120,rate=0.05,seed=111)
 for row in rows: row['payload']['max_new_tokens']=32
 sched=out/'schedule.jsonl'; sched.write_text(''.join(json.dumps(r)+'\n' for r in rows))
 name='a-'+a.phase+'-'+datetime.datetime.now().strftime('%H%M%S')
 argv=[sys.executable,'homework11/load/cluster.py','--namespace','hw11','--name',name,'--schedule',str(sched),'--url','http://hw11-inference-balanced:8092','--output',str(out/'load'),'--lead-seconds','30']
 mark('load_launch',argv=argv,name=name)
 load=subprocess.Popen(argv,cwd=ROOT,stdout=(out/'load.stdout').open('w'),stderr=(out/'load.stderr').open('w'))
 first=None; deadline=time.monotonic()+100
 while time.monotonic()<deadline and first is None:
  for client in range(4):
   r=kube('logs',f'{name}-{client}',check=False)
   if r.returncode: continue
   for line in r.stdout.splitlines():
    try: event=json.loads(line)
    except ValueError: continue
    if event.get('event')=='chunk': first=event; break
   if first: break
  if not first: time.sleep(0.5)
 if first is None: raise RuntimeError('No first chunk; no mutation triggered')
 mark('first_chunk_trigger',event=first)
 pod=first['data']['pod']
 if a.phase=='rollout':
  mark('upgrade_B_start',request_id=first['request_id'],pod=pod)
  upgrade('B')
  kube('rollout','status','deployment/hw11-inference','--timeout=240s')
  mark('upgrade_B_ready')
 elif a.phase=='bad-b':
  mark('bad_B_start')
  upgrade('B',3600)
  time.sleep(35)
  kube('get','pods','-o','json')
  kube('get','endpointslices','-l','kubernetes.io/service-name=hw11-inference-balanced','-o','json')
  mark('rollback_start',revision=revision)
  helm('rollback','hw11',revision,'-n','hw11','--wait','--timeout','240s')
  kube('rollout','status','deployment/hw11-inference','--timeout=240s')
  mark('rollback_ready')
 elif a.phase=='kill':
  mark('kill_start',pod=pod,request_id=first['request_id'])
  pod_before=json.loads(kube('get','pod',pod,'-o','json').stdout)
  container=next(c for c in pod_before['status']['containerStatuses'] if c['name']=='api')['containerID']
  if not container.startswith('docker://'): raise RuntimeError('Expected docker runtime; refuse ambiguous kill')
  container_id=container.removeprefix('docker://')
  mark('kill_exact_container',pod_uid=pod_before['metadata']['uid'],container_id=container_id)
  kill_result=cmd(['docker','exec','hw11','docker','kill','--signal=KILL',container_id],check=False)
  mark('kill_exec_result',returncode=kill_result.returncode)
  mark('kill_command_end')
  time.sleep(2)
  kube('get','pod',pod,'-o','json')
  kube('logs',pod,'-c','api','--previous',check=False)
 elif a.phase=='drain':
  mark('delete_start',pod=pod,request_id=first['request_id'])
  kube('delete','pod',pod,'--wait=false')
  mark('delete_command_end')
 load.wait(timeout=360)
 mark('load_end',returncode=load.returncode)
 if load.returncode: raise RuntimeError('Load failed; see raw evidence')
 cmd([sys.executable,'homework11/load/summarize.py',str(out/'load/requests.jsonl')])
 kube('rollout','status','deployment/hw11-inference','--timeout=240s')
 kube('get','pods','-o','json')
 helm('get','values','hw11','-n','hw11','-a')
 mark('finished')
finally:
 if observer:
  observer.send_signal(signal.SIGINT)
  try: observer.wait(timeout=30)
  except subprocess.TimeoutExpired: observer.terminate()
 caffeine.terminate()
 if load and load.poll() is None:
  mark('orchestrator_stopped_load_still_running',pid=load.pid)
