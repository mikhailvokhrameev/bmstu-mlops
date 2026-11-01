"""Inspect real base files; optional load is diagnostic, never publication or approval."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
import release


def check(diagnostic_load=False):
    baseline=json.loads(Path('evaluation/baseline.json').read_text())
    snapshot=Path.home()/'.cache/huggingface/hub'/('models--'+baseline['name'].replace('/','--'))/'snapshots'/baseline['revision']
    result=dict(kind='base-availability-diagnostic',accepted_release=False,baseline=baseline,snapshot_exists=snapshot.is_dir())
    try:
        manifest,_=release.load_verified('accepted.json')
        result['accepted_model_id']=manifest['model_id']
    except Exception as exc:
        result['accepted_api_smoke_blocked']=f'{type(exc).__name__}: {exc}'
    from evaluation.solution import quality_gate
    evaluated=json.loads(Path('metrics/baseline-eval.json').read_text())
    config=json.loads(Path('evaluation/params.json').read_text())
    result['existing_baseline_eval']=dict(evaluation_run_id=evaluated.get('evaluation_run_id'),
        overall=evaluated['overall'],behavioral=evaluated['behavioral']['overall'],release_binding_present='release_binding' in evaluated)
    try:
        gate=quality_gate(evaluated,evaluated,config['critical_slices'])
        result['diagnostic_baseline_gate']=dict(gate,note='Diagnostic only, never acceptance or a new evaluation.')
    except Exception as exc:
        result['diagnostic_baseline_gate']=dict(passed=False,reason=f'{type(exc).__name__}: {exc}',
            note='No new evaluation or acceptance; diagnostic of saved baseline against unchanged gate.')
    if snapshot.is_dir():
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'base'
            shutil.copytree(snapshot,root,symlinks=False)
            identity=release.base_artifact_identity(root,baseline)
            result['snapshot_files_sha256']=identity['files']
            result['inventory_status']='verified local bytes; not acceptance evidence'
            if diagnostic_load:
                import torch
                from transformers import AutoModelForCausalLM,AutoTokenizer
                from src.runtime import set_seed
                config=json.loads(Path('evaluation/params.json').read_text())
                torch.set_num_threads(2);set_seed(config['seed'])
                started=time.perf_counter()
                tokenizer=AutoTokenizer.from_pretrained(root,local_files_only=True)
                model=AutoModelForCausalLM.from_pretrained(root,dtype=torch.float32,local_files_only=True).to('cpu').eval()
                loaded=time.perf_counter()
                messages=[dict(role='system',content=config['system']),dict(role='user',content='Что такое MLflow?')]
                prompt=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=config['enable_thinking'])
                batch=tokenizer(prompt,return_tensors='pt')
                with torch.inference_mode():
                    output=model.generate(**batch,max_new_tokens=config['max_new_tokens'],do_sample=False,
                        pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
                tokens=output[0,batch['input_ids'].shape[1]:]
                result['diagnostic_load']=dict(status='ok',device='cpu',dtype='float32',load_seconds=loaded-started,
                    generation_seconds=time.perf_counter()-loaded,output_tokens=len(tokens),text=tokenizer.decode(tokens,skip_special_tokens=True),
                    note='Direct local model capability check. No API, release, quality approval or accepted-model benchmark.')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--diagnostic-load',action='store_true')
    p.add_argument('--output',type=Path,default=Path('metrics/hw10/base-availability.json'))
    args=p.parse_args()
    try: result=check(args.diagnostic_load)
    except Exception as exc: result=dict(kind='base-availability-diagnostic',accepted_release=False,error=f'{type(exc).__name__}: {exc}')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
