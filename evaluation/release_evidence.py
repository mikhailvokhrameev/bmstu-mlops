"""Bind the existing evaluator to immutable release evidence; never invent a pass."""
import argparse
from pathlib import Path

import release
from evaluation import cli
from evaluation.contract import read_json, write_json


def evaluation_runtime(manifest, config):
    import os
    from src.runtime import resolve_device
    device=resolve_device(os.environ.get('HW6_DEVICE', manifest['model'].get('device', 'auto')))
    if str(device) == 'cuda':
        import torch
        device=f'cuda:{torch.cuda.current_device()}'
    device=release.canonical_device_name(device)
    return dict(base_model=manifest['model']['name'], base_revision=manifest['model']['revision'],
                device=device,
                dtype=os.environ.get('HW6_DTYPE', manifest['model'].get('dtype', 'float32')),
                generation={k: config[k] for k in ('system', 'max_new_tokens', 'enable_thinking', 'seed')},
                do_sample=False)


def create(runtime_path, output=Path('metrics/release-report.json'), *, base_evaluation=None):
    """Persist rejected and incomplete attempts too. Raises after persisting refusal."""
    if base_evaluation is not None:
        return create_base(base_evaluation, runtime_path, output)
    report = dict(status='incomplete', passed=False)
    try:
        runtime = read_json(runtime_path)
        spec = cli.candidate_model()
        source, adapter = cli.manifest_for(spec)
        config, rows, behavioral, _, _, protocol = cli.inputs()
        from evaluation.contract import read_rows
        protocol_snapshot = cli.protocol_snapshot(config, rows, behavioral, read_rows(config['training_index']))
        identity = release.artifact_identity(adapter, source['model'])
        evidence = {name: release.file_reference(Path('metrics') / name) for name in
                    ('baseline-eval.json', 'candidate-eval.json', 'baseline-outputs.json', 'candidate-outputs.json')}
        from evaluation.contract import fingerprint
        manifest = dict(schema_version=1, model_id=spec['run_id'], artifact_uri=spec['uri'],
                        model_sha256=release.sha256(release.canonical(identity)),
                        protocol_sha256=protocol, golden_sha256=fingerprint(rows),
                        runtime=runtime, runtime_sha256=release.sha256(release.canonical(runtime)),
                        artifact=identity, evidence=evidence, model_spec=spec, protocol=protocol_snapshot,
                        protocol_id='evaluation/params.json', golden_id=config['golden'],
                        runtime_id=str(runtime_path))
        report.update({k: manifest[k] for k in ('model_id', 'model_sha256', 'protocol_sha256',
                      'golden_sha256', 'runtime_sha256', 'protocol_id', 'golden_id', 'runtime_id')})
        report.update(evidence=evidence, artifact=identity, model_spec=spec, protocol=protocol_snapshot)
        write_json(Path(output).with_name('release-manifest.json'), manifest)
        baseline, candidate = cli.current_reports()
        if candidate.get('release_binding') != dict(artifact=identity, runtime=runtime):
            raise ValueError('Evaluation lacks matching artifact/runtime binding; repeat make evaluate')
        report.update(status='rejected', evaluation_run_id=candidate['evaluation_run_id'],
                      metrics={k: candidate['overall'][k] for k in ('accuracy', 'format_rate')})
        from evaluation.solution import quality_gate
        result = quality_gate(baseline, candidate, config['critical_slices'])
        if not isinstance(result, dict) or result.get('passed') is not True:
            raise ValueError('Quality gate did not return passed=True')
        report.update(status='complete', passed=True, gate=result, critical_slices=config['critical_slices'])
        release.validate_release(manifest, report)
        release.verify_artifacts(manifest, report)
    except Exception as exc:
        report.update(passed=False, reason=f'{type(exc).__name__}: {exc}')
        write_json(output, report)
        raise
    write_json(output, report)
    return manifest, report


def create_base(evaluation_dir, runtime_path, output):
    """Assemble only previously generated, bound base evidence; rescore and gate again."""
    report=dict(status='incomplete',passed=False)
    try:
        directory=Path(evaluation_dir)
        protocol=read_json(directory/'protocol.json')
        spec=read_json(directory/'model-spec.json')
        candidate=read_json(directory/'candidate-eval.json')
        baseline=read_json(directory/'baseline-eval.json')
        runtime=read_json(runtime_path)
        binding=candidate.get('release_binding')
        if not isinstance(binding,dict) or binding.get('runtime') != runtime:
            raise ValueError('Base evaluation lacks matching runtime binding; run evaluate-base')
        identity=binding.get('artifact',{})
        if identity.get('kind') != 'base' or baseline.get('release_binding') != binding:
            raise ValueError('Both base evaluation roles require the same bound snapshot/runtime')
        if read_json(directory/'evaluated-runtime.json') != runtime:
            raise ValueError('Requested runtime differs from evaluated runtime')
        release.validate_base_runtime(runtime,spec,protocol['config'])
        actual=release.base_artifact_identity(identity['root'],identity['base_model'])
        if actual != identity:
            raise ValueError('Base snapshot changed since evaluation')
        evidence={name:release.file_reference(directory/name) for name in
            ('baseline-eval.json','candidate-eval.json','baseline-outputs.json','candidate-outputs.json')}
        from evaluation.contract import fingerprint
        manifest=dict(schema_version=1,model_id=spec['run_id'],artifact_uri=spec['uri'],
            model_sha256=release.sha256(release.canonical(identity)),
            protocol_sha256=fingerprint(protocol),golden_sha256=fingerprint(protocol['golden']),
            runtime=runtime,runtime_sha256=release.sha256(release.canonical(runtime)),
            artifact=identity,evidence=evidence,model_spec=spec,protocol=protocol,
            protocol_id=str((directory/'protocol.json').resolve()),golden_id=protocol['config']['golden'],
            runtime_id=str(Path(runtime_path).resolve()))
        report.update({key:value for key,value in manifest.items() if key not in ('schema_version','artifact_uri','runtime')})
        report.update(status='rejected',evaluation_run_id=candidate['evaluation_run_id'],
            metrics={key:candidate['overall'][key] for key in ('accuracy','format_rate')})
        write_json(Path(output).with_name('release-manifest.json'),manifest)
        from evaluation.solution import quality_gate
        decision=quality_gate(baseline,candidate,protocol['config']['critical_slices'])
        if decision.get('passed') is not True:
            raise ValueError('Quality gate did not confirm passed=True')
        report.update(status='complete',passed=True,gate=decision,critical_slices=protocol['config']['critical_slices'])
        release.validate_release(manifest,report)
        release.verify_artifacts(manifest,report)
    except Exception as exc:
        report.update(status='rejected' if report['status'] in ('complete','rejected') else 'incomplete',
            passed=False,reason=f'{type(exc).__name__}: {exc}')
        write_json(output,report)
        raise
    write_json(output,report)
    return manifest,report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('metrics/release-report.json'))
    parser.add_argument('--publish', type=Path)
    parser.add_argument('--base-evaluation', type=Path, help='Immutable directory produced by evaluate-base')
    args = parser.parse_args()
    manifest, report = create(args.runtime, args.output, base_evaluation=args.base_evaluation)
    if args.publish:
        release.publish_verified(manifest, report, args.publish)


if __name__ == '__main__':
    main()
