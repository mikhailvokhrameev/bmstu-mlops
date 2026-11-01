import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import benchmark_api

class DiagnosticBenchmark(unittest.TestCase):
    def test_every_attempt_and_equal_prompts(self):
        calls=[]
        def response(request,timeout):
            calls.append(json.loads(request.data)['prompt'])
            if len(calls)==1:
                raise TimeoutError('fixture timeout')
            result=io.StringIO(json.dumps(dict(diagnostic_only=True,accepted_release=False,
                model_id='diagnostic-fixture',runtime_sha256='runtime',request_id='fixture')))
            result.status=200
            return result
        with tempfile.TemporaryDirectory() as tmp, patch('benchmark_api.urllib.request.urlopen',side_effect=response):
            summary=benchmark_api.run('http://fixture',tmp)
            rows=[json.loads(line) for line in (Path(tmp)/'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows),16)
        self.assertEqual(sum(s['successes'] for s in summary),15)
        self.assertEqual(sum(s['errors'] for s in summary),1)
        self.assertEqual(sum(r['error'] is not None for r in rows),1)
        for concurrency in (1,2):
            self.assertEqual([r['prompt'] for r in sorted(rows,key=lambda r:r['index']) if r['concurrency']==concurrency],benchmark_api.PROMPTS)
        for row in rows:
            if row['error'] is None:
                self.assertTrue(row['diagnostic_only'])
                self.assertFalse(row['accepted_release'])
                self.assertEqual(row['model_id'],'diagnostic-fixture')
                self.assertEqual(row['runtime_sha256'],'runtime')
