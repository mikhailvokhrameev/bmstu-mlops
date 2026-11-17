#!/usr/bin/env python3
"""Extract observed transitions; never infer unobserved alert recovery."""
import argparse
import json
p=argparse.ArgumentParser()
p.add_argument('evidence')
a=p.parse_args()
previous={}
for line in open(a.evidence):
    r=json.loads(line)
    t=r['observed_at']
    if r['kind']=='kubectl' and r.get('returncode')==0:
        try: obj=json.loads(r['stdout'])
        except ValueError: continue
        if obj.get('kind')=='HorizontalPodAutoscaler':
            status=obj.get('status',{})
            state=(status.get('currentReplicas'),status.get('desiredReplicas'))
            if previous.get('hpa')!=state:
                print(json.dumps({'time':t,'kind':'hpa','current_desired':state,'status':status}))
                previous['hpa']=state
    elif r['kind']=='pod_times':
        for pod in r['pods']:
            state=(pod.get('ready',{}).get('status') if pod.get('ready') else None,pod['deleting'])
            key='pod:'+pod['uid']
            if previous.get(key)!=state:
                print(json.dumps({'time':t,'kind':'pod',**pod}))
                previous[key]=state
    elif r['kind']=='prometheus' and '/api/v1/rules?' in r['url']:
        obj=json.loads(r['body'])
        for group in obj.get('data',{}).get('groups',[]):
            for rule in group['rules']:
                name=rule['name']
                if name not in ('InferenceHighTTFC','InferenceRefusalRate'): continue
                state=rule.get('state')
                if previous.get(name)!=state:
                    print(json.dumps({'time':t,'kind':'alert','name':name,'state':state,'rule':rule}))
                    previous[name]=state
