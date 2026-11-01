"""Content identity, strict release metadata validation and atomic publication."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def cache_key(data_path: Path, config: dict) -> str:
    return hashlib.sha256(canonical({'data_sha256': hashlib.sha256(Path(data_path).read_bytes()).hexdigest(), 'config': config})).hexdigest()


def validate_release(manifest: dict, report: dict) -> None:
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    require(isinstance(manifest, dict) and isinstance(report, dict), 'Expected mappings')
    require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1, 'Invalid schema')
    for key in ('model_id', 'artifact_uri'):
        require(nonempty(manifest.get(key)), 'Missing ' + key)
    for key in ('model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256'):
        value = manifest.get(key)
        require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, 'Invalid ' + key)
        require(report.get(key) == value, 'Report mismatch: ' + key)
    runtime = manifest.get('runtime')
    require(isinstance(runtime, dict) and bool(runtime), 'Missing runtime')
    try:
        runtime_hash = hashlib.sha256(canonical(runtime)).hexdigest()
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid runtime JSON') from exc
    require(runtime_hash == manifest['runtime_sha256'], 'Stale runtime hash')
    require(report.get('model_id') == manifest['model_id'], 'Report model mismatch')
    require(report.get('status') == 'complete' and report.get('passed') is True, 'Incomplete or rejected evaluation')
    require(nonempty(report.get('evaluation_run_id')), 'Missing evaluation run')
    metrics = report.get('metrics')
    require(isinstance(metrics, dict), 'Missing metrics')
    for key in ('accuracy', 'format_rate'):
        value = metrics.get(key)
        require(type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value), 'Invalid metric: ' + key)


def atomic_write(target: Path, payload: bytes) -> None:
    """Single-writer atomic replacement; remove temporary files on every failure."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    atomic_write(target, canonical(manifest))


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def file_reference(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Evidence symlinks are forbidden: ' + str(path))
    path = path.resolve()
    if not path.is_file():
        raise ValueError('Expected regular evidence file: ' + str(path))
    return dict(path=str(path), sha256=sha256(path.read_bytes()))


def artifact_identity(root, model):
    """Inventory every adapter/tokenizer file, including its training manifest."""
    root = Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{40}', model.get('revision', '')) or not model.get('name'):
        raise ValueError('Base model requires exact commit revision and name')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Artifact symlinks are forbidden')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    if not {'adapter_config.json', 'manifest.json', 'tokenizer_config.json'} <= files.keys():
        raise ValueError('Incomplete adapter/tokenizer inventory')
    if not any(name in files for name in ('adapter_model.safetensors', 'adapter_model.bin')):
        raise ValueError('Missing adapter weights')
    return dict(root=str(root), base_model=dict(name=model['name'], revision=model['revision']), files=files)


def base_artifact_identity(root, model):
    """Hash a materialized pinned base snapshot, including tokenizer and all shards.

    HF cache symlinks must first be materialized outside Git. No implicit network
    resolution or adapter/training manifest is involved in a base release.
    """
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Base snapshot root symlinks are forbidden')
    root = root.resolve()
    if not root.is_dir() or not model.get('name') or not re.fullmatch('[0-9a-f]{40}', model.get('revision', '')):
        raise ValueError('Base snapshot requires directory, name and exact commit revision')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Base snapshot symlinks are forbidden')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    if not {'config.json', 'tokenizer_config.json'} <= files.keys():
        raise ValueError('Missing base config/tokenizer config')
    if not ('tokenizer.json' in files or {'vocab.json', 'merges.txt'} <= files.keys() or 'tokenizer.model' in files):
        raise ValueError('Missing tokenizer vocabulary')
    if not any(name in files for name in ('model.safetensors', 'pytorch_model.bin')):
        indexes = [name for name in ('model.safetensors.index.json', 'pytorch_model.bin.index.json') if name in files]
        if len(indexes) != 1:
            raise ValueError('Missing base weights or unambiguous shard index')
        weights = json.loads((root / indexes[0]).read_text()).get('weight_map')
        if not isinstance(weights, dict) or not weights or not set(weights.values()) <= files.keys():
            raise ValueError('Missing indexed base weight shards')
    return dict(kind='base', root=str(root), base_model=dict(name=model['name'], revision=model['revision']), files=files)


def canonical_device_name(device):
    """Canonical concrete devices; CUDA always carries its actual index."""
    name = str(device)
    if name in ('cpu', 'cpu:0'):
        return 'cpu'
    if name in ('mps', 'mps:0'):
        return 'mps'
    if re.fullmatch(r'cuda:(0|[1-9][0-9]*)', name):
        return name
    raise ValueError('Runtime device must be concrete: cpu, mps or cuda:N')


def validate_runtime_device(runtime):
    name = runtime.get('device')
    concrete = canonical_device_name(name)
    if name != concrete:
        raise ValueError('Runtime device must use canonical concrete spelling')
    return concrete


def verify_model_device(model, expected):
    """Fail before warmup if any loaded parameter/buffer is on another device."""
    expected = validate_runtime_device(dict(device=expected))
    count = 0
    for tensors in (model.parameters(), model.buffers()):
        for tensor in tensors:
            count += 1
            if canonical_device_name(tensor.device) != expected:
                raise ValueError('Loaded model device differs from evaluated runtime device')
    if not count:
        raise ValueError('Cannot verify loaded model device without tensors')
    return expected


def validate_base_runtime(runtime, model, config):
    validate_runtime_device(runtime)
    if set(runtime) != {'base_model', 'base_revision', 'device', 'dtype', 'generation', 'do_sample'}:
        raise ValueError('Unsupported base runtime')
    if runtime['base_model'] != model['name'] or runtime['base_revision'] != model['revision'] or runtime['do_sample'] is not False:
        raise ValueError('Base runtime does not match pinned snapshot')
    generation = {key: config.get(key) for key in ('system', 'max_new_tokens', 'seed', 'enable_thinking')}
    if runtime['generation'] != generation:
        raise ValueError('Base runtime differs from evaluated generation protocol')
    if (not isinstance(generation['system'], str) or not generation['system'].strip()
            or type(generation['max_new_tokens']) is not int or not 1 <= generation['max_new_tokens'] <= 512
            or type(generation['seed']) is not int or type(generation['enable_thinking']) is not bool):
        raise ValueError('Invalid base generation settings')


def verify_artifacts(manifest, report):
    """Strict real-release contract. Legacy lab metadata cannot enter this path."""
    validate_runtime_device(manifest['runtime'])
    identity = manifest.get('artifact')
    if not isinstance(identity, dict):
        raise ValueError('Missing real artifact inventory')
    kind = identity.get('kind', 'adapter')
    if kind == 'base':
        actual = base_artifact_identity(identity['root'], identity['base_model'])
        spec = manifest.get('model_spec', {})
        if spec.get('kind') != 'base' or any(spec.get(k) != identity['base_model'][k] for k in ('name', 'revision')):
            raise ValueError('Base release specification does not match pinned snapshot')
        if spec.get('uri') != identity['root']:
            raise ValueError('Base release URI must identify the inventoried local snapshot')
        validate_base_runtime(manifest['runtime'], spec, manifest['protocol']['config'])
    elif kind == 'adapter':
        actual = artifact_identity(identity['root'], identity['base_model'])
        training = json.loads((Path(identity['root']) / 'manifest.json').read_text())
        if any(training.get('model', {}).get(k) != identity['base_model'][k] for k in ('name', 'revision')):
            raise ValueError('Training manifest base identity mismatch')
    else:
        raise ValueError('Unsupported real artifact kind')
    if actual != identity or sha256(canonical(actual)) != manifest['model_sha256']:
        raise ValueError('Artifact changed since evaluation')
    for key in ('artifact', 'model_spec', 'protocol_id', 'golden_id', 'runtime_id', 'evidence', 'protocol'):
        if key not in manifest or report.get(key) != manifest[key]:
            raise ValueError('Missing or mismatched release binding: ' + key)
    spec = manifest['model_spec']
    if manifest['model_id'] != spec.get('run_id') or manifest['artifact_uri'] != spec.get('uri'):
        raise ValueError('Release model id/URI mismatch')
    evidence = manifest['evidence']
    expected = {'baseline-eval.json', 'candidate-eval.json', 'baseline-outputs.json', 'candidate-outputs.json'}
    if not isinstance(evidence, dict) or set(evidence) != expected:
        raise ValueError('Incomplete evaluation evidence')
    for reference in evidence.values():
        if file_reference(reference['path']) != reference:
            raise ValueError('Evaluation evidence changed')
    source = json.loads(Path(evidence['candidate-eval.json']['path']).read_text())
    if source.get('model') != manifest['model_spec'] or source.get('evaluation_run_id') != report['evaluation_run_id']:
        raise ValueError('Evaluation identity mismatch')
    for key in ('protocol_sha256', 'golden_sha256'):
        if source.get(key) != manifest[key]:
            raise ValueError('Evaluation mismatch: ' + key)
    if source.get('release_binding') != dict(artifact=identity, runtime=manifest['runtime']):
        raise ValueError('Artifact/runtime was not bound at evaluation time')
    from evaluation.solution import quality_gate
    baseline = json.loads(Path(evidence['baseline-eval.json']['path']).read_text())
    if kind == 'base' and baseline.get('release_binding') != source.get('release_binding'):
        raise ValueError('Base reference evaluation lacks matching snapshot/runtime binding')
    if kind == 'base' and (baseline.get('model', {}).get('kind') != 'base' or any(
            baseline.get('model', {}).get(key) != identity['base_model'][key] for key in ('name', 'revision'))):
        raise ValueError('Base reference specification differs from bound snapshot')
    from evaluation.contract import fingerprint, validate_golden
    from evaluation.solution import score
    protocol = manifest['protocol']
    if fingerprint(protocol) != manifest['protocol_sha256']:
        raise ValueError('Protocol snapshot hash mismatch')
    rows, behavioral = protocol['golden'], protocol['behavioral']
    validate_golden(rows, protocol['training_index'], behavioral)
    if fingerprint(rows) != manifest['golden_sha256']:
        raise ValueError('Golden snapshot hash mismatch')
    critical = protocol['config']['critical_slices']
    if report.get('critical_slices') != critical:
        raise ValueError('Critical slices differ from pinned protocol')
    for role, evaluated in (('baseline', baseline), ('candidate', source)):
        outputs = json.loads(Path(evidence[role + '-outputs.json']['path']).read_text())
        if fingerprint(outputs) != evaluated.get('outputs_sha256'):
            raise ValueError('Raw outputs mismatch: ' + role)
        if evaluated.get('protocol_sha256') != manifest['protocol_sha256'] or evaluated.get('golden_sha256') != manifest['golden_sha256']:
            raise ValueError('Report data/protocol mismatch: ' + role)
        by_id = {o['id']: o for o in outputs}
        if len(by_id) != len(outputs) or set(by_id) != {r['id'] for r in rows + behavioral}:
            raise ValueError('Raw output ids mismatch: ' + role)
        rescored = score(rows, [by_id[r['id']] for r in rows])
        if any(evaluated.get(k) != rescored[k] for k in ('overall', 'slices', 'per_item')):
            raise ValueError('Report differs from rescored raw outputs: ' + role)
        if evaluated.get('behavioral') != score(behavioral, [by_id[r['id']] for r in behavioral]):
            raise ValueError('Behavioral report differs from raw outputs: ' + role)
    if source.get('baseline_spec_sha256') != fingerprint(baseline['model']) or baseline.get('baseline_spec_sha256') != fingerprint(baseline['model']):
        raise ValueError('Baseline specification mismatch')
    result = quality_gate(baseline, source, critical)
    if result.get('passed') is not True or report.get('gate') != result:
        raise ValueError('Gate evidence mismatch')
    if report['metrics'] != {k: source['overall'][k] for k in ('accuracy', 'format_rate')}:
        raise ValueError('Evaluation metrics mismatch')


def publish_verified(manifest, report, target):
    validate_release(manifest, report)
    verify_artifacts(manifest, report)
    atomic_write(target, canonical(dict(manifest=manifest, report=report)))


def load_verified(target):
    record = json.loads(Path(target).read_text())
    manifest, report = record['manifest'], record['report']
    validate_release(manifest, report)
    verify_artifacts(manifest, report)
    return manifest, report
