#!/usr/bin/env python3
"""Read-only rollout evidence: raw Pod/Deployment/EndpointSlice/Events snapshots."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--namespace', default='hw11')
    p.add_argument('--release', default='hw11')
    p.add_argument('--output', required=True)
    p.add_argument('--duration', type=float, default=900)
    p.add_argument('--interval', type=float, default=1)
    a = p.parse_args()
    if a.duration <= 0 or a.interval <= 0:
        p.error('duration and interval must be positive')
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'config.json').write_text(json.dumps(vars(a), indent=2))
    selector = f'app.kubernetes.io/instance={a.release},app.kubernetes.io/name=inference'
    commands = [
        ['get', 'pods', '-l', selector, '-o', 'json'],
        ['get', 'deployment', a.release + '-inference', '-o', 'json'],
        ['get', 'replicasets', '-l', selector, '-o', 'json'],
        ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + a.release + '-inference-balanced', '-o', 'json'],
        ['get', 'events', '-o', 'json'],
    ]
    deadline = time.monotonic() + a.duration
    with (out / 'evidence.jsonl').open('a', buffering=1) as journal:
        try:
            while time.monotonic() < deadline:
                for command in commands:
                    argv = ['kubectl', '-n', a.namespace, *command]
                    row = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'argv': argv}
                    try:
                        result = subprocess.run(argv, text=True, capture_output=True, timeout=10)
                        row.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        row['error'] = str(exc)
                    row['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    journal.write(json.dumps(row) + '\n')
                time.sleep(min(a.interval, max(0, deadline-time.monotonic())))
        except KeyboardInterrupt:
            pass
    print(out)


if __name__ == '__main__':
    main()
