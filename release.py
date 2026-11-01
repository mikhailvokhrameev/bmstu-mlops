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
