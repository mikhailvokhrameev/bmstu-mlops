#!/usr/bin/env python3
"""Capture pod-level cgroups for this cgroup-v2 runtime, not container children."""
import datetime
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
import observe_c

config=json.loads(Path('results/c-observer/config.json').read_text())
with Path('results/c-cadvisor-backfill.jsonl').open('a') as f:
    for kind in ('cpu','memory'):
        query=observe_c.QUERIES[kind]%('hw11','hw11')
        url='http://127.0.0.1:19090/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'start':config['start'],'end':time.time(),'step':'5s'})
        body=urllib.request.urlopen(url,timeout=30).read().decode()
        f.write(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'name':kind,'url':url,'body':body})+'\n')
        print(kind,len(json.loads(body)['data']['result']))
