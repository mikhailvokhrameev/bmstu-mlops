#!/usr/bin/env python3
"""Run INSIDE a client Pod, targeting a ClusterIP Service (except warmup: direct new Pod IP)."""
import argparse
import json
import time
import urllib.error
import urllib.request


def request(url, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url+path, data=body, headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--url',required=True)
    p.add_argument('--check',choices=['warmup','version','replacement','isolation'],required=True)
    p.add_argument('--version',default='A')
    p.add_argument('--old-pod')
    p.add_argument('--expected-pod', help='Exact newly created replacement Pod name')
    p.add_argument('--attempts', type=int, default=1, help='Bounded Service routing observations for replacement')
    p.add_argument('--interval', type=float, default=0.2)
    args=p.parse_args()
    if args.attempts <= 0 or args.interval < 0: p.error('attempts must be positive and interval nonnegative')
    evidence={'check':args.check,'url':args.url,'passed':False}
    if args.check=='warmup':
        ready=request(args.url,'/ready');work=request(args.url,'/generate',{'prompt':'warmup-check'})
        evidence.update(ready=ready,work=work)
        assert ready[0]==503 and work[0]==503, evidence
    elif args.check=='isolation':
        outputs=[]
        for marker in ('DIALOGUE-ALPHA-1729','DIALOGUE-BETA-2718'):
            code,data=request(args.url,'/generate',{'prompt':'continue','history':[{'role':'user','content':marker}]})
            assert code==200 and marker in data['text'], (code,data)
            outputs.append(data)
        assert 'DIALOGUE-BETA-2718' not in outputs[0]['text']
        assert 'DIALOGUE-ALPHA-1729' not in outputs[1]['text']
        evidence['responses']=outputs
    else:
        if args.check=='replacement':
            assert args.old_pod and args.expected_pod, '--old-pod and --expected-pod are required for replacement'
            assert args.expected_pod != args.old_pod, 'Replacement must differ from deleted Pod'
        observations=[]
        for number in range(args.attempts if args.check=='replacement' else 1):
            try:
                code,data=request(args.url,'/generate',{'prompt':'restored','history':[{'role':'user','content':'PERSISTED-CONTROL-123'}]})
                observation={'event':'response','attempt':number+1,'wall':time.time(),'status':code,'data':data}
            except Exception as exc:
                observation={'event':'transport_error','attempt':number+1,'wall':time.time(),'error':repr(exc)}
                code,data=None,{}
            observations.append(observation)
            print(json.dumps(observation),flush=True)
            valid=code==200 and data.get('model_id')==args.version and data.get('backend_mode')=='simulated'
            if args.check=='replacement':
                valid=valid and data.get('pod')==args.expected_pod and 'PERSISTED-CONTROL-123' in data.get('text','')
            if valid: break
            if number+1 < args.attempts: time.sleep(args.interval)
        evidence['observations']=observations
        assert valid, evidence
        evidence['response']=data
    evidence['passed']=True
    print(json.dumps(evidence))

if __name__=='__main__': main()
