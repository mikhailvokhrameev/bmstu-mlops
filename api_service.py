"""FastAPI inference. Real mode accepts only release.load_verified envelopes."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from typing import Protocol
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
import release


class Backend(Protocol):
    model_id: str
    runtime_sha256: str
    limit: int
    def generate(self, prompt: str, limit: int) -> dict: ...


class StubBackend:
    """Explicit fixture backend; never used by default."""
    def __init__(self, manifest):
        self.model_id=manifest['model_id']
        self.runtime_sha256=manifest['runtime_sha256']
        self.limit=32
        self.calls=0

    def generate(self, prompt, limit):
        self.calls+=1
        time.sleep(.01)
        return dict(text=f'STUB ONLY: {self.model_id}',output_tokens=1)


class TransformersBackend:
    def __init__(self, manifest):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from src.runtime import resolve_device, resolve_dtype, set_seed
        runtime=manifest['runtime']
        release.validate_runtime_device(runtime)
        expected={'base_model','base_revision','device','dtype','generation','do_sample'}
        if set(runtime) != expected or runtime['do_sample'] is not False:
            raise ValueError('Unsupported evaluated runtime')
        if set(runtime['generation']) != {'system','max_new_tokens','enable_thinking','seed'}:
            raise ValueError('Unsupported generation runtime')
        identity=manifest['artifact']
        if (runtime['base_model'],runtime['base_revision']) != (identity['base_model']['name'],identity['base_model']['revision']):
            raise ValueError('Runtime/base identity mismatch')
        self.runtime=runtime
        self.limit=runtime['generation']['max_new_tokens']
        self.model_id=manifest['model_id']; self.runtime_sha256=manifest['runtime_sha256']
        set_seed(runtime['generation']['seed'])
        self.device=resolve_device(runtime['device'])
        if release.canonical_device_name(self.device) != runtime['device']:
            raise ValueError('Resolved device differs from evaluated runtime device')
        root=identity['root']
        self.tokenizer=AutoTokenizer.from_pretrained(root,local_files_only=True)
        if identity.get('kind', 'adapter') == 'base':
            # Load exactly the files that load_verified inventoried, not a separately
            # resolved HF cache snapshot with the same display name.
            model=AutoModelForCausalLM.from_pretrained(root,dtype=resolve_dtype(runtime['dtype']),local_files_only=True)
        else:
            from peft import PeftModel
            base=AutoModelForCausalLM.from_pretrained(runtime['base_model'],revision=runtime['base_revision'],
                dtype=resolve_dtype(runtime['dtype']),local_files_only=True)
            model=PeftModel.from_pretrained(base,root,local_files_only=True)
        self.model=model.to(self.device).eval()
        self.loaded_device=release.verify_model_device(self.model,runtime['device'])
        self.torch=torch

    def generate(self,prompt,limit):
        cfg=self.runtime['generation']
        text=self.tokenizer.apply_chat_template([{'role':'system','content':cfg['system']},
            {'role':'user','content':prompt}],tokenize=False,add_generation_prompt=True,
            enable_thinking=cfg['enable_thinking'])
        batch=self.tokenizer(text,return_tensors='pt').to(self.device)
        if batch['input_ids'].shape[1]+limit > getattr(self.model.config,'max_position_embeddings',2048):
            raise ValueError('Prompt exceeds model context')
        with self.torch.inference_mode():
            output=self.model.generate(**batch,max_new_tokens=limit,do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        tokens=output[0,batch['input_ids'].shape[1]:]
        return dict(text=self.tokenizer.decode(tokens,skip_special_tokens=True),output_tokens=len(tokens))


class GenerateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    prompt: str=Field(min_length=1,max_length=4096)
    max_new_tokens: StrictInt | None=Field(default=None,ge=1,le=512)


def create_app(pointer=Path('accepted.json'),backend='transformers',max_parallel=1):
    if max_parallel < 1:
        raise ValueError('max_parallel must be positive')
    @asynccontextmanager
    async def lifespan(app):
        app.state.ready=False
        if backend == 'stub':
            instance=StubBackend(json.loads(Path(pointer).read_text()))
        elif backend == 'transformers':
            manifest,_=release.load_verified(pointer)
            instance=await asyncio.to_thread(TransformersBackend,manifest)
        else:
            raise ValueError('Unknown backend')
        app.state.backend=instance
        app.state.semaphore=asyncio.Semaphore(max_parallel)
        await asyncio.to_thread(instance.generate,'Hello',instance.limit)
        app.state.ready=True
        try:
            yield
        finally:
            app.state.ready=False
    app=FastAPI(lifespan=lifespan)
    app.state.ready=False

    @app.get('/health')
    def health():
        return {'alive':True,'backend':backend}

    @app.get('/ready')
    def ready():
        if not app.state.ready:
            raise HTTPException(503,'Loading or warming up')
        return {'ready':True,'model_id':app.state.backend.model_id,'runtime_sha256':app.state.backend.runtime_sha256}

    @app.post('/generate')
    async def generate(request:GenerateRequest):
        started=time.perf_counter()
        if not request.prompt.strip():
            raise HTTPException(422,'Empty prompt')
        if not app.state.ready:
            raise HTTPException(503,'Loading or warming up')
        instance=app.state.backend
        limit=instance.limit if request.max_new_tokens is None else request.max_new_tokens
        # Real quality approval covers exactly the evaluated limit, not arbitrary settings.
        if (backend == 'transformers' and limit != instance.limit) or limit > instance.limit:
            raise HTTPException(422,'Unsupported generation limit')
        async with app.state.semaphore:
            acquired=time.perf_counter()
            try:
                # Shield worker from client cancellation so semaphore covers actual compute.
                task=asyncio.create_task(asyncio.to_thread(instance.generate,request.prompt,limit))
                try:
                    result=await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from exc
            finished=time.perf_counter()
        return dict(result,request_id=str(uuid.uuid4()),model_id=instance.model_id,
            runtime_sha256=instance.runtime_sha256,queue_seconds=acquired-started,
            generation_seconds=finished-acquired,total_seconds=finished-started,max_new_tokens=limit)
    return app


def main():
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument('--pointer',type=Path,default=Path('accepted.json'))
    parser.add_argument('--backend',choices=['stub','transformers'],default='transformers')
    parser.add_argument('--port',type=int,default=8092)
    parser.add_argument('--max-parallel',type=int,default=1)
    args=parser.parse_args()
    uvicorn.run(create_app(args.pointer,args.backend,args.max_parallel),host='127.0.0.1',port=args.port)

if __name__=='__main__':
    main()
