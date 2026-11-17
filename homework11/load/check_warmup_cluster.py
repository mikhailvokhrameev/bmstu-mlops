#!/usr/bin/env python3
"""Observe readiness, EndpointSlice and direct/Service requests during warmup."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--namespace',default='hw11')
    p.add_argument('--client',required=True,help='Running client Pod with Python')
    p.add_argument('--pod',required=True,help='New server Pod currently warming up')
    p.add_argument('--service',required=True)
    p.add_argument('--samples',type=int,default=5)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    if a.samples <= 0: p.error('--samples must be positive')
    evidence=[]
    def kubectl(*args,input=None):
        result=subprocess.run(['kubectl','-n',a.namespace,*args],input=input,text=True,capture_output=True)
        evidence.append({'event':'command','wall':time.time(),'argv':list(args),'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        result.check_returncode()
        return json.loads(result.stdout)
    client_code=Path(__file__).with_name('check_service.py').read_text()
    try:
        for _ in range(a.samples):
            pod=kubectl('get','pod',a.pod,'-o','json')
            ip=pod['status']['podIP']
            slices=kubectl('get','endpointslice','-l',f'kubernetes.io/service-name={a.service}','-o','json')
            sample={'wall':time.time(),'pod':pod,'endpoint_slices':slices}
            evidence.append(sample)
            assert not any(c['type']=='Ready' and c['status']=='True' for c in pod['status'].get('conditions',[])), 'Pod is already Ready'
            assert not any(ip in e.get('addresses',[]) and e.get('conditions',{}).get('ready',True) for item in slices['items'] for e in item.get('endpoints',[])), 'Unready Pod is eligible Service endpoint'
            sample['direct']=kubectl('exec','-i',a.client,'--','python','-','--check','warmup','--url',f'http://{ip}:8092',input=client_code)
            # Direct service requests carry a unique marker; every observed Pod is checked.
            source="import json, urllib.request\nu='http://"+a.service+":8092/generate'\nr=urllib.request.urlopen(urllib.request.Request(u,data=b'{\"prompt\":\"service-warmup-check\",\"max_new_tokens\":1}',headers={'Content-Type':'application/json'}),timeout=60)\nprint(r.read().decode())\n"
            response=kubectl('exec','-i',a.client,'--','python','-',input=source)
            sample['service_response']=response
            assert response['pod']!=a.pod, 'Service routed to warming Pod'
    finally:
        Path(a.output).write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'passed':True,'samples':a.samples,'output':a.output}))

if __name__=='__main__':main()
