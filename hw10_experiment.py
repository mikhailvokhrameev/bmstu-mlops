import json,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
import lab
from benchmark_api import run
out=Path('metrics/hw10')
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp)/'workspace';lab.initialize(root)
 events=[]
 for version in ('A','B','A'):
  (root/'accepted.json').write_bytes((root/f'manifest-{version}.json').read_bytes())
  p=subprocess.Popen([sys.executable,'api_service.py','--backend','stub','--pointer',str(root/'accepted.json'),'--port','18092'],stdout=subprocess.DEVNULL,stderr=None)
  try:
   for _ in range(100):
    try:
     with urllib.request.urlopen('http://127.0.0.1:18092/ready') as r: ready=json.load(r)
     break
    except Exception:time.sleep(.05)
   else:raise RuntimeError('Server not ready')
   def generate():
    req=urllib.request.Request('http://127.0.0.1:18092/generate',data=b'{"prompt":"hello"}',headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req) as r:return json.load(r)
   before=generate()
   (root/'accepted.json').write_bytes((root/'manifest-B.json').read_bytes())
   after=generate();assert before['model_id']==after['model_id']==f'fixture-{version}'
   events.append(dict(expected=version,ready=ready,before=before,after_pointer_change=after))
   if len(events)==1:run('http://127.0.0.1:18092',out)
  finally:p.terminate();p.wait(timeout=10)
 (out/'stub-version-scenario.json').write_text(json.dumps(events,indent=2))
