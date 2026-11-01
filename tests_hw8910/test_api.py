"""FastAPI contract tests; fixtures are never real model quality evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import lab
from api_service import create_app

class API(unittest.TestCase):
    def test_restart_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'workspace'; lab.initialize(root)
            seen=[]
            for version in ('A','B','A'):
                (root/'accepted.json').write_bytes((root/f'manifest-{version}.json').read_bytes())
                with TestClient(create_app(root/'accepted.json', backend='stub')) as client:
                    self.assertEqual(200,client.get('/ready').status_code)
                    result=client.post('/generate',json={'prompt':'hello'}).json()
                    seen.append(result['model_id'])
                    (root/'accepted.json').write_bytes((root/'manifest-B.json').read_bytes())
                    self.assertEqual(result['model_id'],client.post('/generate',json={'prompt':'hello'}).json()['model_id'])
            self.assertEqual(['fixture-A','fixture-B','fixture-A'],seen)

    def test_validation_before_compute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'workspace'; lab.initialize(root)
            with TestClient(create_app(root/'accepted.json',backend='stub')) as client:
                state=client.app.state.backend; before=state.calls
                for payload in ({'prompt':''},{'prompt':'a'*4097},{'prompt':'a','max_new_tokens':True},{'prompt':'a','max_new_tokens':33}):
                    self.assertEqual(422,client.post('/generate',json=payload).status_code)
                self.assertEqual(before,state.calls)

    def test_real_rejects_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'workspace'; lab.initialize(root)
            with self.assertRaises((KeyError,ValueError)):
                with TestClient(create_app(root/'accepted.json')): pass

    def test_unready_and_compute_bound(self):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from api_service import StubBackend
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'workspace'; lab.initialize(root)
            app=create_app(root/'accepted.json',backend='stub')
            with TestClient(app) as client:
                app.state.ready=False
                self.assertEqual(503,client.get('/ready').status_code)
                self.assertEqual(200,client.get('/health').status_code)
                self.assertEqual(503,client.post('/generate',json={'prompt':'hi'}).status_code)
                app.state.ready=True
                active=0; peak=0; lock=threading.Lock()
                original=StubBackend.generate
                def measured(instance,prompt,limit):
                    nonlocal active,peak
                    with lock:
                        active+=1; peak=max(active,peak)
                    try:
                        time.sleep(.03)
                        return original(instance,prompt,limit)
                    finally:
                        with lock: active-=1
                with patch.object(StubBackend,'generate',measured):
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        results=list(pool.map(lambda _:client.post('/generate',json={'prompt':'hi'}),range(4)))
                self.assertTrue(all(r.status_code==200 for r in results))
                self.assertEqual(1,peak)
