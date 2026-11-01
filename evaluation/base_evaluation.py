"""Evaluate the configured pinned base locally; preserve bound immutable evidence."""
import gc
from pathlib import Path
import time
import uuid

import release
from evaluation import cli
from evaluation.contract import fingerprint, read_json, read_rows, write_json
from evaluation.solution import quality_gate, score


def generate_base(identity, runtime, rows):
    # Evaluation may load a candidate before approval. Serving still always uses
    # load_verified; this shared generator prevents eval/API runtime divergence.
    from api_service import TransformersBackend
    backend=TransformersBackend(dict(artifact=identity,runtime=runtime,model_id='base-evaluation',
        runtime_sha256=release.sha256(release.canonical(runtime))))
    outputs=[]
    for row in rows:
        started=time.perf_counter();raw='';error=None
        try:
            raw=backend.generate(row['input'],runtime['generation']['max_new_tokens'])['text']
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
        outputs.append(dict(id=row['id'],raw=raw,error=error,latency_seconds=time.perf_counter()-started))
    del backend
    gc.collect()
    return outputs


def evaluate_base(snapshot, runtime_path, output):
    """Two real generation passes; no reuse of saved answers and no automatic acceptance.

    This command evaluates only the pinned configured base. It cannot label a
    different base/adapter as the reference or overwrite older release evidence.
    """
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    status=dict(status='incomplete',passed=False)
    try:
        config,rows,behavioral,baseline,proof,protocol_hash=cli.inputs()
        if baseline.get('kind') != 'base':
            raise ValueError('evaluate-base requires a configured base reference; adapter reference needs normal evaluate')
        runtime=read_json(runtime_path)
        release.validate_base_runtime(runtime,baseline,config)
        identity=release.base_artifact_identity(snapshot,baseline)
        protocol=cli.protocol_snapshot(config,rows,behavioral,read_rows(config['training_index']))
        if fingerprint(protocol) != protocol_hash:
            raise ValueError('Protocol changed while preparing evaluation')
        evaluation_id='base-'+uuid.uuid4().hex
        candidate=dict(baseline,run_id=evaluation_id,uri=identity['root'])
        binding=dict(artifact=identity,runtime=runtime)
        write_json(output/'evaluated-runtime.json',runtime)
        write_json(output/'protocol.json',protocol)
        write_json(output/'model-spec.json',candidate)
        reports={}
        for role,spec in (('baseline',baseline),('candidate',candidate)):
            outputs=generate_base(identity,runtime,rows+behavioral)
            write_json(output/(role+'-outputs.json'),outputs)
            if release.base_artifact_identity(snapshot,baseline) != identity:
                raise ValueError('Base snapshot changed during evaluation')
            by_id={item['id']:item for item in outputs}
            if len(by_id) != len(outputs) or set(by_id) != {r['id'] for r in rows+behavioral}:
                raise ValueError('Generated output IDs do not match protocol')
            evaluated=score(rows,[by_id[r['id']] for r in rows])
            evaluated.update(status='complete',model=spec,evaluation_run_id=evaluation_id,
                protocol_sha256=protocol_hash,golden_sha256=fingerprint(rows),
                baseline_spec_sha256=fingerprint(baseline),
                behavioral=score(behavioral,[by_id[r['id']] for r in behavioral]),leakage=proof,
                outputs_sha256=fingerprint(outputs),release_binding=binding)
            reports[role]=evaluated
            write_json(output/(role+'-eval.json'),evaluated)
        try:
            decision=quality_gate(reports['baseline'],reports['candidate'],config['critical_slices'])
            if decision.get('passed') is not True:
                raise ValueError('Quality gate did not confirm passed=True')
        except Exception as exc:
            decision=dict(passed=False,reason=f'{type(exc).__name__}: {exc}')
            write_json(output/'quality-gate.json',decision)
            status.update(status='rejected',reason=decision['reason'])
            raise
        write_json(output/'quality-gate.json',decision)
        status.update(status='complete',passed=True,evaluation_run_id=evaluation_id)
        return output
    except Exception as exc:
        status.setdefault('reason',f'{type(exc).__name__}: {exc}')
        raise
    finally:
        write_json(output/'evaluation-status.json',status)
