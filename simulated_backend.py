"""Explicit CPU workload, local LRU prefix cache and streaming instrumentation."""
import asyncio
import anyio
from collections import OrderedDict
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import Response, StreamingResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: str = Field(pattern='^(system|user|assistant)$')
    content: str = Field(min_length=1, max_length=4096)


class SimulatedRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=4096)
    history: list[Message] = Field(default_factory=list, max_length=64)
    max_new_tokens: StrictInt | None = Field(default=None, ge=1, le=512)


def burn_cpu(seconds):
    """Spend process CPU time rather than sleeping (observable by CPU HPA)."""
    end = time.thread_time() + seconds
    value = b'cpu-simulated'
    while time.thread_time() < end:
        # Long hash input releases the GIL while consuming real CPU.
        value = hashlib.sha256(value * 128).digest()


class Simulator:
    def __init__(self, config, max_parallel):
        defaults = dict(warmup_seconds=20, max_queue=32, queue_timeout_seconds=10,
                        cache_entries=64, prefix_chars=512, hit_cpu_seconds=.02,
                        miss_cpu_seconds=.8, chunk_cpu_seconds=.02, chunks=8,
                        premature_ready=False)
        unknown = set(config) - set(defaults) - {'artifact_path', 'artifact_sha256'}
        if unknown:
            raise ValueError(f'Unknown simulator settings: {sorted(unknown)}')
        self.config = defaults | config
        for name in ('warmup_seconds', 'queue_timeout_seconds', 'hit_cpu_seconds', 'miss_cpu_seconds', 'chunk_cpu_seconds'):
            value = self.config[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        for name in ('max_queue', 'cache_entries', 'prefix_chars', 'chunks'):
            value = self.config[name]
            if type(value) is not int or value < (0 if name in ('max_queue', 'cache_entries') else 1):
                raise ValueError(f'Invalid {name}')
        if type(self.config['premature_ready']) is not bool:
            raise ValueError('premature_ready must be boolean')
        data = Path(self.config['artifact_path']).read_bytes()
        self.runtime_sha256 = hashlib.sha256(data).hexdigest()
        if self.runtime_sha256 != self.config['artifact_sha256']:
            raise ValueError('Simulator artifact SHA-256 mismatch')
        self.model_id = json.loads(data)['version']
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError('Artifact version must be nonempty')
        self.pod = os.environ.get('POD_NAME', os.environ.get('HOSTNAME', 'local'))
        self.ready = False
        self.stopping = False
        self.running = self.waiting = 0
        self.max_parallel = max_parallel
        self.condition = asyncio.Condition()
        self.cache = OrderedDict()
        self.registry = CollectorRegistry()
        labels = ['pod', 'model_id']
        self.count = Counter('simulated_requests_total', 'Request outcomes', labels + ['outcome'], registry=self.registry)
        self.running_metric = Gauge('simulated_running', 'Active requests', labels, registry=self.registry).labels(self.pod, self.model_id)
        self.waiting_metric = Gauge('simulated_waiting', 'Queued requests', labels, registry=self.registry).labels(self.pod, self.model_id)
        self.latencies = {name: Histogram(f'simulated_{name}_seconds', name, labels,
            buckets=(.005,.01,.025,.05,.1,.25,.5,1,2,3,5,10,20,40,80), registry=self.registry).labels(self.pod,self.model_id)
            for name in ('ttfc', 'total', 'queue', 'inter_chunk')}

    async def warmup(self):
        await asyncio.sleep(self.config['warmup_seconds'])
        self.ready = True

    def log(self, identity, outcome, **fields):
        self.count.labels(self.pod, self.model_id, outcome).inc()
        logging.getLogger('uvicorn.error').info(json.dumps(identity | fields | {'event':outcome}, ensure_ascii=False))

    async def acquire(self, identity):
        if not self.ready or self.stopping:
            self.log(identity, 'unready')
            raise HTTPException(503, 'Loading, warming up or draining')
        async with self.condition:
            if self.running >= self.max_parallel:
                if self.waiting >= self.config['max_queue']:
                    self.log(identity, 'rejected')
                    raise HTTPException(429, 'Queue full', headers={'Retry-After':'1'})
                self.waiting += 1
                self.waiting_metric.set(self.waiting)
                try:
                    await asyncio.wait_for(self.condition.wait_for(lambda: self.running < self.max_parallel), self.config['queue_timeout_seconds'])
                except asyncio.CancelledError:
                    self.log(identity, 'aborted')
                    raise
                except TimeoutError:
                    self.log(identity, 'queue_timeout')
                    raise HTTPException(503, 'Queue timeout')
                finally:
                    self.waiting -= 1
                    self.waiting_metric.set(self.waiting)
            self.running += 1
            self.running_metric.set(self.running)

    async def release(self):
        async with self.condition:
            self.running -= 1
            self.running_metric.set(self.running)
            self.condition.notify_all()

    async def compute(self, seconds):
        task = asyncio.create_task(asyncio.to_thread(burn_cpu, seconds))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Starlette cancels an AnyIO scope repeatedly at every checkpoint.
            # Keep admission occupied until the underlying CPU thread exits.
            with anyio.CancelScope(shield=True):
                await task
            raise

    async def events(self, payload, identity, started, acquired):
        completed = False
        try:
            context = '\n'.join(f'{m.role}: {m.content}' for m in payload.history) + '\nuser: ' + payload.prompt
            key = hashlib.sha256(context[:self.config['prefix_chars']].encode()).hexdigest()
            hit = key in self.cache
            if hit:
                self.cache.move_to_end(key)
            await self.compute(self.config['hit_cpu_seconds' if hit else 'miss_cpu_seconds'])
            if self.config['cache_entries']:
                self.cache[key] = True
                self.cache.move_to_end(key)
                while len(self.cache) > self.config['cache_entries']:
                    self.cache.popitem(last=False)
            text = f'SIMULATED {self.model_id}: {context}'
            chunks = min(payload.max_new_tokens or self.config['chunks'], self.config['chunks'])
            previous = None
            ttfc = None
            intervals = []
            for index in range(chunks):
                await self.compute(self.config['chunk_cpu_seconds'])
                now = time.perf_counter()
                if previous is None:
                    ttfc = now-started
                else:
                    intervals.append(now-previous)
                previous = now
                yield identity | dict(event='chunk', index=index, text=text[len(text)*index//chunks:len(text)*(index+1)//chunks], cache_hit=hit)
            finished = time.perf_counter()
            self.latencies['ttfc'].observe(ttfc)
            for interval in intervals:
                self.latencies['inter_chunk'].observe(interval)
            self.latencies['total'].observe(finished-started)
            self.latencies['queue'].observe(acquired-started)
            completed = True
            self.log(identity, 'completed', chunks=chunks, cache_hit=hit, total_seconds=finished-started)
            yield identity | dict(event='complete', chunks=chunks, cache_hit=hit, queue_seconds=acquired-started,
                                  generation_seconds=finished-acquired, total_seconds=finished-started)
        finally:
            if not completed:
                self.log(identity, 'aborted')
            with anyio.CancelScope(shield=True):
                await self.release()


def create_simulated_app(config, max_parallel):
    if max_parallel < 1:
        raise ValueError('max_parallel must be positive')
    if isinstance(config, (str, Path)):
        config = json.loads(Path(config).read_text())
    if config is None:
        path = os.environ.get('SIMULATED_CONFIG')
        if not path:
            raise ValueError('simulated backend requires --simulated-config or SIMULATED_CONFIG')
        config = json.loads(Path(path).read_text())

    @asynccontextmanager
    async def lifespan(app):
        state = Simulator(config, max_parallel)
        app.state.simulator = state
        warmup = asyncio.create_task(state.warmup())
        if state.config['warmup_seconds'] == 0:
            await warmup
        try:
            yield
        finally:
            state.stopping = True
            state.ready = False
            warmup.cancel()
            await asyncio.gather(warmup, return_exceptions=True)
            async with state.condition:
                await state.condition.wait_for(lambda: state.running == 0)

    app = FastAPI(lifespan=lifespan)

    def identity_for(request):
        state = app.state.simulator
        return dict(request_id=request.headers.get('x-request-id', str(uuid.uuid4()))[:128], pod=state.pod,
                    model_id=state.model_id, runtime_sha256=state.runtime_sha256, backend_mode='simulated')

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        identity = identity_for(request)
        app.state.simulator.log(identity, 'invalid')
        return JSONResponse(status_code=422, content={'detail':identity | {'error':'Invalid request'}})

    @app.get('/health')
    async def health():
        return {'alive':True, 'backend_mode':'simulated'}

    @app.get('/ready')
    @app.get('/startup')
    async def ready():
        state = app.state.simulator
        if state.stopping or not (state.ready or state.config['premature_ready']):
            raise HTTPException(503, 'Loading or warming up')
        return {'ready':True, 'model_id':state.model_id, 'runtime_sha256':state.runtime_sha256, 'pod':state.pod, 'backend_mode':'simulated'}

    @app.get('/metrics')
    async def metrics():
        return Response(generate_latest(app.state.simulator.registry), media_type=CONTENT_TYPE_LATEST)

    async def begin(payload, request):
        state = app.state.simulator
        identity = identity_for(request)
        if not payload.prompt.strip():
            state.log(identity, 'invalid')
            raise HTTPException(422, identity | {'error':'Empty prompt'})
        started = time.perf_counter()
        try:
            await state.acquire(identity)
        except HTTPException as exc:
            exc.detail = identity | {'error':exc.detail}
            raise
        return state.events(payload, identity, started, time.perf_counter()), identity

    @app.post('/generate/stream')
    async def stream(payload: SimulatedRequest, request: Request):
        events, identity = await begin(payload, request)
        async def encode():
            async for event in events:
                yield json.dumps(event, ensure_ascii=False) + '\n'
        return StreamingResponse(encode(), media_type='application/x-ndjson', headers={'X-Request-ID':identity['request_id'], 'X-Pod-Name':identity['pod']})

    @app.post('/generate')
    async def generate(payload: SimulatedRequest, request: Request):
        events, _ = await begin(payload, request)
        text = ''
        async for event in events:
            text += event.get('text', '')
        return event | {'text':text}

    return app
