#!/usr/bin/env python3
"""Summarize actual A evidence without converting missing observations into success."""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__); p.add_argument('directory'); a=p.parse_args()
d=Path(a.directory)
events=[json.loads(x) for x in (d/'load/requests.jsonl').read_text().splitlines() if x.strip()]
markers=[json.loads(x) for x in (d/'markers.jsonl').read_text().splitlines()]
ends=[x for x in events if x.get('event')=='attempt_end']
starts=[x for x in events if x.get('event')=='attempt_start']
result={'counts':dict(Counter(x['event'] for x in events)), 'outcomes':dict(Counter(x['outcome'] for x in ends)),
        'http_5xx':sum(x.get('status') is not None and x['status']>=500 for x in ends),
        'incomplete':sum(x.get('incomplete_stream',False) for x in ends),
        'attempts_without_end':sorted(set(x['request_id'] for x in starts)-set(x['request_id'] for x in ends)),
        'versions':dict(Counter(x.get('identity',{}).get('model_id') for x in ends)), 'markers':markers}
# Convert monotonic receive times using the attempt's wall/monotonic pair.
for event in events:
 if 'received_monotonic' in event:
  event['received_wall']=event['sent_wall']+event['received_monotonic']-event['sent_monotonic']
trigger=next((x for x in markers if x['kind']=='first_chunk_trigger'),None)
if trigger:
 rid=trigger['event']['request_id']; pod=trigger['event']['data']['pod']
 result['trigger_attempt']=[x for x in events if x.get('request_id')==rid]
 observations=[]
 for line in (d/'observer/evidence.jsonl').read_text().splitlines():
  row=json.loads(line)
  if row.get('returncode')!=0: continue
  try: body=json.loads(row.get('stdout',''))
  except ValueError: continue
  for item in body.get('items',[]):
   if item.get('kind')=='Pod' and item.get('metadata',{}).get('name')==pod:
    observations.append({'observed_at':row['observed_at'],'metadata':item['metadata'],'status':item.get('status',{})})
 result['trigger_pod_observations']=observations
result['zero_failure_check']=bool(ends) and len(starts)==len(ends) and all(x['outcome']=='completed' for x in ends) and not result['http_5xx'] and not result['incomplete']
(d/'analysis.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('markers','trigger_attempt','trigger_pod_observations')},indent=2))
