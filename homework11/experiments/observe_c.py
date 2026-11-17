#!/usr/bin/env python3
"""Read-only C evidence collector. Never applies resources or generates traffic."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

QUERIES = {
    'ttfc_p95': 'histogram_quantile(0.95, sum by (le) (rate(simulated_ttfc_seconds_bucket[1m])))',
    'ttfc_count': 'sum(increase(simulated_ttfc_seconds_count[1m]))',
    'refusal_ratio': 'sum(rate(simulated_requests_total{outcome=~"rejected|queue_timeout|unready|aborted"}[1m])) / sum(rate(simulated_requests_total[1m]))',
    'terminal_count': 'sum(increase(simulated_requests_total[1m]))',
    'alerts': 'ALERTS{alertname=~"InferenceHighTTFC|InferenceRefusalRate"}',
    'cpu': 'sum by (pod) (rate(container_cpu_usage_seconds_total{job="cadvisor",cpu="total",namespace="%s",pod=~"%s-inference-.*",id=~".*/pod[^/]+"}[1m]))',
    'ttfc_buckets': 'simulated_ttfc_seconds_bucket',
    'requests': 'simulated_requests_total',
    'running': 'simulated_running',
    'waiting': 'simulated_waiting',
    'memory': 'sum by (pod) (container_memory_working_set_bytes{job="cadvisor",namespace="%s",pod=~"%s-inference-.*",id=~".*/pod[^/]+"})',
    'ready_replicas': 'kube_deployment_status_replicas_ready',
}


def pod_times(pod):
    """Use API transition timestamps, retaining UID across replacement/name reuse."""
    return {'name': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
            'created': pod['metadata'].get('creationTimestamp'),
            'ready': next((c for c in pod.get('status', {}).get('conditions', [])
                           if c['type'] == 'Ready'), None),
            'deleting': pod['metadata'].get('deletionTimestamp')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--namespace', default='hw11')
    p.add_argument('--release', required=True)
    p.add_argument('--prometheus', default='http://127.0.0.1:9090')
    p.add_argument('--duration', type=float, default=1200)
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    if a.duration <= 0 or a.interval <= 0:
        p.error('duration and interval must be positive')
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    origin = time.time()
    deadline = time.monotonic() + a.duration
    queries = {k: (v % (a.namespace, a.release) if k in ('cpu', 'memory') else v) for k, v in QUERIES.items()}
    (out / 'config.json').write_text(json.dumps({'args': vars(a), 'start': origin, 'queries': queries}, indent=2))
    def record(kind, **data):
        with (out / 'evidence.jsonl').open('a') as f:
            f.write(json.dumps({'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                'wall_time': time.time(), 'kind': kind, **data}) + '\n')
    def kubectl(*args):
        cmd = ['kubectl', '-n', a.namespace, *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            record('kubectl', argv=cmd, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)
            return json.loads(r.stdout) if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            record('error', argv=cmd, error=str(e))
    def prom(path, params=None):
        url = a.prometheus.rstrip('/') + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                record('prometheus', url=url, status=response.status, body=response.read().decode())
        except Exception as e:
            record('error', url=url, error=str(e))
    selector = f'app.kubernetes.io/instance={a.release},app.kubernetes.io/name=inference'
    kubectl('get', '--raw', '/apis/metrics.k8s.io/v1beta1/nodes')
    kubectl('get', 'deployment', a.release + '-inference', '-o', 'json')
    kubectl('get', 'service', a.release + '-inference-balanced', a.release + '-inference-sticky', '-o', 'json')
    kubectl('get', 'configmap', a.release + '-inference-config', a.release + '-monitoring', '-o', 'json')
    prom('/api/v1/status/config')
    try:
        while time.monotonic() < deadline:
            kubectl('get', 'hpa', a.release + '-inference', '-o', 'json')
            pods = kubectl('get', 'pods', '-l', selector, '-o', 'json')
            if pods:
                record('pod_times', pods=[pod_times(pod) for pod in pods['items']])
            kubectl('get', 'deployment', a.release + '-inference', '-o', 'json')
            for policy in ('balanced', 'sticky'):
                kubectl('get', 'endpointslices', '-l', f'kubernetes.io/service-name={a.release}-inference-{policy}', '-o', 'json')
            kubectl('get', '--raw', f'/apis/metrics.k8s.io/v1beta1/namespaces/{a.namespace}/pods')
            prom('/api/v1/rules', {'type': 'alert'})
            for name, query in queries.items():
                prom('/api/v1/query', {'query': query})
            time.sleep(min(a.interval, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        record('interrupted')
    finally:
        end = time.time()
        for name, query in queries.items():
            prom('/api/v1/query_range', {'query': query, 'start': origin, 'end': end, 'step': '5s'})
        kubectl('get', 'events', '-o', 'json')
        record('end', start=origin, end=end)
    print(out)

if __name__ == '__main__':
    main()
