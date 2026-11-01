"""Preserve every HTTP attempt; same eight prompts at concurrency 1 and 2."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
import urllib.request
import uuid

PROMPTS=['Choose a color.','Say hello.','What is 2+2?','Name an animal.',
         'Name a city.','Write one word.','What is water?','Say goodbye.']

def run(url,output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    rows=[]; summary=[]
    for concurrency in (1,2):
        def attempt(item):
            index,prompt=item
            started=time.perf_counter()
            row=dict(attempt_id=str(uuid.uuid4()),index=index,concurrency=concurrency,prompt=prompt,
                     status=None,error=None)
            try:
                request=urllib.request.Request(url+'/generate',data=json.dumps({'prompt':prompt}).encode(),
                    headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=120) as response:
                    row['status']=response.status; row.update(json.load(response))
            except Exception as exc:
                row['error']=f'{type(exc).__name__}: {exc}'
                row['status']=getattr(exc,'code',None)
            row['client_seconds']=time.perf_counter()-started
            return row
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            batch=list(pool.map(attempt,enumerate(PROMPTS)))
        elapsed=time.perf_counter()-started
        rows.extend(batch)
        successes=[r for r in batch if r['status']==200 and r['error'] is None]
        latencies=[r['client_seconds'] for r in successes]
        summary.append(dict(concurrency=concurrency,n=len(batch),successes=len(successes),errors=len(batch)-len(successes),
            batch_seconds=elapsed,successful_requests_per_second=len(successes)/elapsed,
            mean_success_seconds=statistics.mean(latencies) if latencies else None,
            max_success_seconds=max(latencies) if latencies else None,
            error_latencies_seconds=[r['client_seconds'] for r in batch if r not in successes]))
        (output/'attempts.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        (output/'summary.json').write_text(json.dumps(summary,indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--url',default='http://127.0.0.1:8092')
    p.add_argument('--output',type=Path,default=Path('metrics/hw10'))
    args=p.parse_args(); print(json.dumps(run(args.url,args.output),indent=2))
