#!/usr/bin/env python3
"""Launch four distinct client Pods; collect immutable config and raw logs."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--namespace', default='hw11')
    p.add_argument('--name', required=True, help='Unique Kubernetes DNS label per run')
    p.add_argument('--schedule', required=True)
    p.add_argument('--url', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--image', default='python:3.12.8-slim-bookworm')
    p.add_argument('--lead-seconds', type=int, default=90)
    args = p.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    def kubectl(*argv, **kw):
        return subprocess.run(['kubectl','-n',args.namespace,*argv], check=True, text=True, **kw)
    raw = Path(args.schedule).read_text()
    rows = [json.loads(line) for line in raw.splitlines()]
    if set(row['client'] for row in rows) != {0,1,2,3}: raise ValueError('Schedule must include four clients')
    start = time.time()+args.lead_seconds
    config = dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=args.name),
                  data={'runner.py':Path(__file__).with_name('runner.py').read_text(), 'schedule.jsonl':raw})
    resources = [config]
    for client in range(4):
        resources.append(dict(apiVersion='v1', kind='Pod', metadata=dict(name=f'{args.name}-{client}', labels={'hw11-load':args.name}),
            spec=dict(restartPolicy='Never', terminationGracePeriodSeconds=5, containers=[dict(name='client', image=args.image,
            command=['python','/load/runner.py','run','--schedule','/load/schedule.jsonl','--client',str(client),
                     '--start-at',str(start),'--url',args.url,'--output','/results/requests.jsonl','--run',args.name],
            env=[dict(name='POD_IP', valueFrom={'fieldRef':{'fieldPath':'status.podIP'}})],
            resources={'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'500m','memory':'256Mi'}},
            volumeMounts=[{'name':'load','mountPath':'/load'},{'name':'results','mountPath':'/results'}])],
            volumes=[{'name':'load','configMap':{'name':args.name}}, {'name':'results','emptyDir':{'sizeLimit':'128Mi'}}])))
    manifest = dict(apiVersion='v1', kind='List', items=resources)
    (output/'schedule.jsonl').write_text(raw)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    kubectl('apply','-f','-',input=json.dumps(manifest))
    deadline = start+max(row['offset_seconds'] for row in rows)+240
    while time.time() < deadline:
        result = kubectl('get','pods','-l',f'hw11-load={args.name}','-o','json',capture_output=True)
        state = json.loads(result.stdout)
        (output/'pods.json').write_text(result.stdout)
        if len(state['items']) == 4 and all(item['status']['phase'] in ('Succeeded','Failed') for item in state['items']): break
        time.sleep(3)
    else: raise TimeoutError('Clients did not finish; Pods retained for diagnosis')
    ips = [item['status'].get('podIP') for item in state['items']]
    if len(set(ips)) != 4 or None in ips: raise RuntimeError(f'Need four different client IPs: {ips}')
    with (output/'requests.jsonl').open('w') as merged:
        for client in range(4):
            result = kubectl('logs',f'{args.name}-{client}',capture_output=True)
            (output/f'client-{client}.jsonl').write_text(result.stdout)
            merged.write(result.stdout)
    if any(item['status']['phase'] != 'Succeeded' for item in state['items']): raise RuntimeError('Client failed; inspect collected logs')
    print(f'Collected {output}; cleanup: kubectl -n {args.namespace} delete pod -l hw11-load={args.name}; kubectl -n {args.namespace} delete configmap {args.name}')

if __name__ == '__main__': main()
