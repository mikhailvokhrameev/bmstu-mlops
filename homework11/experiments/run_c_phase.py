#!/usr/bin/env python3
"""Explicitly authorized load phase wrapper; stores commands, timestamps and logs."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('--name', required=True)
p.add_argument('--rate', type=float, required=True)
p.add_argument('--duration', type=float, default=240)
p.add_argument('--policy', choices=['balanced','sticky'], default='balanced')
p.add_argument('--skew', action='store_true')
a=p.parse_args()
root=Path('results')
(root/'c-commands.jsonl').touch(exist_ok=True)
def run(cmd):
    with (root/'c-commands.jsonl').open('a') as f:
        f.write(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':'start','argv':cmd})+'\n')
    with (root/(a.name+'.log')).open('a') as f:
        result=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
    with (root/'c-commands.jsonl').open('a') as f:
        f.write(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':'end','argv':cmd,'returncode':result.returncode})+'\n')
    if result.returncode: sys.exit(result.returncode)
schedule=str(root/(a.name+'-schedule.jsonl'))
run([sys.executable,'homework11/load/runner.py','schedule','--duration',str(a.duration),'--rate',str(a.rate),'--seed','111','--output',schedule]+(['--skew'] if a.skew else []))
rows=[json.loads(line) for line in Path(schedule).read_text().splitlines()]
for row in rows:
    row['payload']['history'][0]['content']=f'CONTROL-{int(row["id"][1:])%16:02} ' + 'prefix '*4
Path(schedule).write_text(''.join(json.dumps(row)+'\n' for row in rows))
run([sys.executable,'homework11/experiments/launch_c.py','--namespace','hw11','--name',a.name,'--schedule',schedule,'--url',f'http://hw11-inference-{a.policy}:8092','--output',str(root/a.name),'--lead-seconds','30'])
run([sys.executable,'homework11/load/summarize.py',str(root/a.name/'requests.jsonl')])
