#!/usr/bin/env python3
"""Run one explicitly supplied experiment command, preserving raw evidence.

No command runs with --help. Use only after the coordinating agent releases C.
This wrapper does not retry failures or interpret them as successful evidence.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, help='New evidence directory for this command')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not command:
        p.error('explicit command required after --')
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    meta = {'argv': command, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'start_wall': time.time()}
    (out / 'command.json').write_text(json.dumps(meta, indent=2))
    with (out / 'stdout.txt').open('w') as stdout, (out / 'stderr.txt').open('w') as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr)
            meta['returncode'] = result.returncode
        except OSError as exc:
            meta.update(returncode=127, error=str(exc))
    meta.update(ended_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), end_wall=time.time())
    (out / 'command.json').write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta))
    raise SystemExit(meta['returncode'])


if __name__ == '__main__':
    main()
