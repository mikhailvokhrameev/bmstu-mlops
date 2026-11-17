"""CPU simulator contract tests; run with unittest (no model downloads)."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from api_service import create_app


class SimulatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        artifact = Path(self.temp.name) / 'model.json'
        artifact.write_text('{"version":"A"}')
        self.config = dict(artifact_path=str(artifact), artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                           warmup_seconds=0, hit_cpu_seconds=0, miss_cpu_seconds=0,
                           chunk_cpu_seconds=0, chunks=3, cache_entries=1)

    async def test_stream_identity_history_and_bounded_cache(self):
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                for marker in ('ALPHA', 'BETA'):
                    response = await client.post('/generate/stream', json={'prompt':'continue', 'history':[{'role':'user','content':marker}]}, headers={'X-Request-ID':marker})
                    self.assertEqual(response.status_code, 200)
                    events = [json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual([e['event'] for e in events], ['chunk'] * 3 + ['complete'])
                    self.assertEqual(events[-1]['request_id'], marker)
                    self.assertEqual(events[-1]['model_id'], 'A')
                    self.assertEqual(events[-1]['backend_mode'], 'simulated')
                    self.assertIn(marker, ''.join(e.get('text','') for e in events))
                    self.assertNotIn('BETA' if marker == 'ALPHA' else 'ALPHA', response.text)
                response = await client.post('/generate', json={'prompt':'continue', 'history':[{'role':'user','content':'BETA'}]})
                self.assertTrue(response.json()['cache_hit'])
                response = await client.post('/generate', json={'prompt':'continue', 'history':[{'role':'user','content':'ALPHA'}]})
                self.assertFalse(response.json()['cache_hit'])
                self.assertIn('simulated_requests_total', (await client.get('/metrics')).text)

    async def test_corrupt_artifact_prevents_startup(self):
        self.config['artifact_sha256'] = '0' * 64
        app = create_app(backend='simulated', simulated_config=self.config)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            async with app.router.lifespan_context(app):
                pass

    async def test_warmup_rejects_work_until_ready(self):
        self.config['warmup_seconds'] = .05
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                self.assertEqual((await client.get('/health')).status_code, 200)
                self.assertEqual((await client.get('/ready')).status_code, 503)
                self.assertEqual((await client.post('/generate', json={'prompt':'hello'})).status_code, 503)
                await asyncio.sleep(.08)
                self.assertEqual((await client.get('/ready')).status_code, 200)

    async def test_queue_capacity_and_timeout(self):
        self.config.update(max_queue=1, queue_timeout_seconds=.05, miss_cpu_seconds=.3)
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                first = asyncio.create_task(client.post('/generate', json={'prompt':'one'}))
                await asyncio.sleep(.01)
                second = asyncio.create_task(client.post('/generate', json={'prompt':'two'}))
                await asyncio.sleep(.001)
                third = await client.post('/generate', json={'prompt':'three'})
                self.assertEqual(third.status_code, 429)
                self.assertEqual(third.json()['detail']['backend_mode'], 'simulated')
                self.assertEqual(third.json()['detail']['model_id'], 'A')
                timed_out = await second
                self.assertEqual(timed_out.status_code, 503)
                self.assertIn('request_id', timed_out.json()['detail'])
                self.assertIn('pod', timed_out.json()['detail'])
                self.assertEqual((await first).status_code, 200)

    async def test_cancelled_stream_releases_capacity_and_counts_abort(self):
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            state = app.state.simulator
            identity = dict(request_id='cancel', pod=state.pod, model_id=state.model_id, backend_mode='simulated')
            from simulated_backend import SimulatedRequest
            import time
            await state.acquire(identity)
            events = state.events(SimulatedRequest(prompt='one'), identity, time.perf_counter(), time.perf_counter())
            self.assertEqual((await anext(events))['event'], 'chunk')
            await events.aclose()
            self.assertEqual(state.running, 0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                metrics = (await client.get('/metrics')).text
                self.assertIn('outcome="aborted"', metrics)
                self.assertIn('simulated_ttfc_seconds_count{model_id="A",pod="' + state.pod + '"} 0.0', metrics)
                self.assertIn('simulated_total_seconds_count{model_id="A",pod="' + state.pod + '"} 0.0', metrics)
                self.assertEqual((await client.post('/generate', json={'prompt':'next'})).status_code, 200)

    async def test_premature_probe_reproduces_warmup_defect(self):
        self.config.update(warmup_seconds=10, premature_ready=True)
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                self.assertEqual((await client.get('/ready')).status_code, 200)
                self.assertEqual((await client.post('/generate', json={'prompt':'one'})).status_code, 503)

    async def test_asgi_disconnect_keeps_slot_until_cpu_worker_finishes(self):
        import time
        self.config.update(miss_cpu_seconds=.3)
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            messages = asyncio.Queue()
            await messages.put({'type':'http.request', 'body':b'{"prompt":"slow"}', 'more_body':False})
            began = asyncio.Event()
            async def receive():
                return await messages.get()
            async def send(message):
                if message['type'] == 'http.response.start':
                    began.set()
            scope = {'type':'http', 'asgi':{'version':'3.0','spec_version':'2.0'},
                     'http_version':'1.1','method':'POST','scheme':'http',
                     'path':'/generate/stream','raw_path':b'/generate/stream','query_string':b'',
                     'headers':[(b'content-type',b'application/json')],
                     'client':('127.0.0.1',1234),'server':('test',80)}
            task = asyncio.create_task(app(scope,receive,send))
            await began.wait()
            await asyncio.sleep(.02)
            await messages.put({'type':'http.disconnect'})
            await asyncio.sleep(.03)
            self.assertEqual(app.state.simulator.running, 1)
            await asyncio.wait_for(task, 3)
            self.assertEqual(app.state.simulator.running, 0)

    async def test_invalid_requests_keep_identity(self):
        app = create_app(backend='simulated', simulated_config=self.config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                for payload in ({'prompt':''}, {'prompt':'   '}, {'prompt':'ok','history':[{'role':'bad','content':'x'}]}):
                    response = await client.post('/generate', json=payload, headers={'X-Request-ID':'invalid-1'})
                    self.assertEqual(response.status_code,422)
                    detail = response.json()['detail']
                    self.assertEqual(detail['request_id'],'invalid-1')
                    self.assertEqual(detail['backend_mode'],'simulated')
                    self.assertEqual(detail['model_id'],'A')
                    self.assertIn('pod',detail)
