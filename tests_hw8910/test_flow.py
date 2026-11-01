"""Process-level checks for the Prefect release flow."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FlowTests(unittest.TestCase):
    def test_resume_and_invalidate_across_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'work'
            env = dict(os.environ, PREFECT_HOME=str(Path(temp) / 'prefect'),
                       PREFECT_SERVER_ANALYTICS_ENABLED='false')
            def run(*args):
                return subprocess.run([sys.executable, *map(str, args)], cwd=ROOT,
                                      env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(0, run('lab.py', '--workspace', root, 'init').returncode)
            failed = run('prefect_flow.py', '--workspace', root, '--fail-after-prepare')
            self.assertNotEqual(0, failed.returncode)
            self.assertIn('PREPARED', failed.stdout, failed.stderr)
            resumed = run('prefect_flow.py', '--workspace', root)
            self.assertEqual(0, resumed.returncode, resumed.stderr)
            self.assertIn('CACHE_HIT', resumed.stdout)
            (root / 'input.txt').write_text('changed data')
            changed = run('prefect_flow.py', '--workspace', root)
            self.assertEqual(0, changed.returncode, changed.stderr)
            self.assertIn('PREPARED', changed.stdout)
            for field, value in [('max_length', 64), ('template_sha256', 'c'*64),
                                 ('tokenizer_revision', 'r2'), ('code_revision', 'prepare-v2')]:
                config = json.loads((root / 'config.json').read_text())
                config[field] = value
                (root / 'config.json').write_text(json.dumps(config))
                reconfigured = run('prefect_flow.py', '--workspace', root)
                self.assertEqual(0, reconfigured.returncode, reconfigured.stderr)
                self.assertIn('PREPARED', reconfigured.stdout)
                print(f'FLOW INVALIDATED {field}', flush=True)
            before = (root / 'accepted.json').read_bytes()
            for report in ['report-incomplete.json', 'report-A.json', 'report-reject.json']:
                rejected = run('prefect_flow.py', '--workspace', root, '--report', report)
                self.assertNotEqual(0, rejected.returncode, rejected.stderr)
                self.assertEqual(before, (root / 'accepted.json').read_bytes())
                print(f'FLOW REJECTED {report}; pointer unchanged', flush=True)
            attempts = [json.loads(line) for line in (root / 'attempts.jsonl').read_text().splitlines()]
            self.assertEqual(10, len(attempts))
            self.assertEqual(len(attempts), len({attempt['attempt_id'] for attempt in attempts}))
            self.assertEqual(6, len(list((root / 'cache').glob('*.json'))))
            self.assertEqual(1, len((root / 'publications.jsonl').read_text().splitlines()))


if __name__ == '__main__':
    unittest.main()
