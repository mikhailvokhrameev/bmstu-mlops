#!/usr/bin/env python3
"""Explicit Kubernetes A version/replacement/dialogue checks after drain."""
import json
from pathlib import Path
import subprocess
import sys
import time

out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
name='a-final-checks-'+time.strftime('%H%M%S')
url='http://hw11-inference-balanced:8092'
old='hw11-inference-c4f57b9c4-mrqx9'; new='hw11-inference-c4f57b9c4-48b4m'
checks=[['--check','version','--version','A'],['--check','replacement','--version','A','--old-pod',old,'--expected-pod',new,'--attempts','12'],['--check','isolation']]
script='import subprocess,sys\nchecks='+repr(checks)+'\nfor args in checks:\n subprocess.run([sys.executable,"/checks/check_service.py","--url",'+repr(url)+',*args],check=True)\n'
manifest={'apiVersion':'v1','kind':'List','items':[
 {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name},'data':{'check_service.py':Path('homework11/load/check_service.py').read_text(),'run.py':script}},
 {'apiVersion':'v1','kind':'Pod','metadata':{'name':name},'spec':{'restartPolicy':'Never','containers':[{'name':'client','image':'python:3.12.8-slim-bookworm','command':['python','-u','/checks/run.py'],'volumeMounts':[{'name':'checks','mountPath':'/checks'}]}],'volumes':[{'name':'checks','configMap':{'name':name}}]}}]}
path=out/'manifest.json';path.write_text(json.dumps(manifest,indent=2))
i=0

def cmd(*args,check=True):
 global i
 i+=1
 argv=['kubectl','--context','hw11','-n','hw11',*args]
 result=subprocess.run([sys.executable,'homework11/experiments/a_command.py','--output',str(out/f'{i:04}'),'--',*argv])
 if check and result.returncode:raise RuntimeError(argv)
 return json.loads((out/f'{i:04}/stdout.txt').read_text()) if args[-1]=='json' else None

caffeine=subprocess.Popen(['caffeinate','-dimsu'])
try:
 cmd('get','pod',new,'-o','json')
 cmd('get','endpointslices','-l','kubernetes.io/service-name=hw11-inference-balanced','-o','json')
 cmd('apply','-f',str(path))
 deadline=time.monotonic()+600
 while time.monotonic()<deadline:
  state=cmd('get','pod',name,'-o','json')
  if state.get('status',{}).get('phase') in ('Succeeded','Failed'):break
  time.sleep(5)
 else:raise TimeoutError('Check client retained')
 cmd('logs',name)
 (out/'result.json').write_text(json.dumps({'pod':name,'phase':state['status']['phase'],'expected_pod':new,'old_pod':old},indent=2))
 if state['status']['phase']!='Succeeded':raise RuntimeError('Check failed; inspect client logs')
finally:caffeine.terminate()
