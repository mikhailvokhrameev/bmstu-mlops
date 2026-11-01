"""Independent incident regressions; synthetic fixtures never prove model quality."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lab
import release
import service


class Incidents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'workspace'
        self.cli('init')

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'lab.py'), '--workspace', str(self.root), *args], capture_output=True, text=True)

    def read(self, name):
        return json.loads((self.root / name).read_text())

    @contextlib.contextmanager
    def server(self, warmup=0):
        state = service.ServiceState(self.root, warmup)
        server = ThreadingHTTPServer(('127.0.0.1', 0), service.handler_for(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield 'http://127.0.0.1:' + str(server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def http(self, url, path, payload=None):
        req = Request(url + path, data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def test_01_resume_content_and_config_cache(self):
        self.assertNotEqual(self.cli('run', '--fail-after-prepare').returncode, 0)
        resumed = self.cli('run')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('CACHE_HIT', resumed.stdout)
        for field, value in [('input', 'changed bytes'), ('template_sha256', 'c'*64), ('max_length', 64), ('tokenizer_revision', 'r2'), ('code_revision', 'prepare-v2')]:
            with self.subTest(field=field):
                if field == 'input':
                    (self.root/'input.txt').write_text(value)
                else:
                    config = self.read('config.json'); config[field] = value
                    (self.root/'config.json').write_text(json.dumps(config))
                rerun = self.cli('run')
                self.assertEqual(rerun.returncode, 0, rerun.stderr)
                self.assertIn('PREPARED', rerun.stdout)

    def test_02_incomplete_and_foreign_report(self):
        before = hashlib.sha256((self.root/'accepted.json').read_bytes()).hexdigest()
        for report in ['report-incomplete.json', 'report-A.json']:
            with self.subTest(report=report):
                (self.root/'accepted.json').write_bytes((self.root/'manifest-A.json').read_bytes())
                result = self.cli('run', '--report', report)
                after = hashlib.sha256((self.root/'accepted.json').read_bytes()).hexdigest()
                print(f'POINTER {report} before={before} after={after}', flush=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, after)

    def test_03_rejection_and_interrupted_write(self):
        target = self.root/'accepted.json'; before = target.read_bytes()
        self.assertNotEqual(self.cli('run', '--report', 'report-reject.json').returncode, 0)
        self.assertEqual(before, target.read_bytes())
        # Atomic publishers must fsync their temporary file before replacing target.
        # This fault applies to fdopen/NamedTemporaryFile implementations alike.
        with patch('os.fsync', side_effect=OSError('injected temporary-file fsync failure')):
            with self.assertRaises(OSError):
                release.publish(self.read('manifest-B.json'), self.read('report-B.json'), target)
        self.assertEqual(before, target.read_bytes())
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_03b_replace_failure_preserves_pointer(self):
        target = self.root/'accepted.json'; before = target.read_bytes()
        with patch('os.replace', side_effect=OSError('injected replace failure')):
            with self.assertRaises(OSError):
                release.publish(self.read('manifest-B.json'), self.read('report-B.json'), target)
        self.assertEqual(before, target.read_bytes())
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_04_duplicate_publication_across_processes(self):
        for _ in range(3):
            result = self.cli('run')
            self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in (self.root/'publications.jsonl').read_text().splitlines()]
        self.assertEqual('fixture-B', self.read('accepted.json')['model_id'])
        self.assertEqual(1, len(events))

    def test_04b_runtime_and_evaluation_are_release_identity(self):
        manifest = self.read('manifest-B.json')
        report = self.read('report-B.json')
        for revision in range(3):
            if revision == 1:
                manifest['runtime']['generation']['max_new_tokens'] = 16
                manifest['runtime_sha256'] = lab.digest(lab.canonical(manifest['runtime']))
                report['runtime_sha256'] = manifest['runtime_sha256']
                report['evaluation_run_id'] = 'fixture-runtime-16'
            elif revision == 2:
                report['evaluation_run_id'] = 'fixture-new-evaluation'
                report['metrics']['accuracy'] = .9
            lab.write(self.root/'manifest-B.json', manifest)
            lab.write(self.root/'report-B.json', report)
            for _ in range(2):
                result = self.cli('run')
                self.assertEqual(0, result.returncode, result.stderr)
        events = [json.loads(line) for line in (self.root/'publications.jsonl').read_text().splitlines()]
        self.assertEqual('fixture-B', self.read('accepted.json')['model_id'])
        self.assertEqual(manifest['runtime_sha256'], self.read('accepted.json')['runtime_sha256'])
        self.assertEqual(3, len(events), 'Three distinct releases, each retried, must produce exactly three events')

    def test_04c_recovery_between_pointer_and_journal(self):
        # Fault at the publish/run boundary: pointer publication has completed,
        # but run has not had a chance to append its publication event.
        original_publish = release.publish
        def interrupted(*args, **kwargs):
            original_publish(*args, **kwargs)
            raise OSError('injected interruption after publish before journal append')
        with patch.object(release, 'publish', interrupted):
            with self.assertRaises(OSError):
                lab.run(self.root, 'report-B.json')
        self.assertEqual('fixture-B', self.read('accepted.json')['model_id'])
        for _ in range(2):
            result = self.cli('run')
            self.assertEqual(0, result.returncode, result.stderr)
        events = [json.loads(line) for line in (self.root/'publications.jsonl').read_text().splitlines()]
        self.assertEqual(1, len(events), 'Recovery must reconcile accepted pointer with exactly one durable release event')
        self.assertEqual('fixture-B', events[0]['model_id'])

    def test_05_warmup_http(self):
        with self.server(warmup=10) as url:
            self.assertEqual(200, self.http(url, '/health')[0])
            self.assertFalse(self.http(url, '/debug')[1]['warmup_finished'])
            ready = self.http(url, '/ready')
            generated = self.http(url, '/generate', {'prompt':'hello', 'max_new_tokens':1})
            with self.subTest(endpoint='ready'):
                self.assertEqual(503, ready[0])
            with self.subTest(endpoint='generate'):
                self.assertEqual(503, generated[0])
            self.assertEqual(0, self.http(url, '/debug')[1]['calls'])

    def test_06_loaded_version_and_restart_http(self):
        mismatches = []
        with self.server() as url:
            first = self.http(url, '/generate', {'prompt':'hello'})[1]
            self.assertEqual('fixture-A', first['model_id'])
            self.assertEqual(0, self.cli('run').returncode)
            result = self.http(url, '/generate', {'prompt':'hello'})[1]
            if result['model_id'] != 'fixture-A' or 'fixture-A' not in result['answer']:
                mismatches.append(result)
        with self.server() as url:
            result = self.http(url, '/generate', {'prompt':'hello'})[1]
            self.assertEqual('fixture-B', result['model_id'])
            self.assertIn('fixture-B', result['answer'])
        release.publish(self.read('manifest-A.json'), self.read('report-A.json'), self.root/'accepted.json')
        with self.server() as url:
            result = self.http(url, '/generate', {'prompt':'hello'})[1]
            self.assertEqual('fixture-A', result['model_id'])
            self.assertIn('fixture-A', result['answer'])
        self.assertEqual([], mismatches)

    def test_07_invalid_requests_do_not_compute_http(self):
        with self.server() as url:
            for payload in [{'prompt':''}, {'prompt':'hello','max_new_tokens':0}, {'prompt':'hello','max_new_tokens':100000}, {'prompt':'hello','max_new_tokens':'2'}]:
                with self.subTest(payload=payload):
                    before = self.http(url, '/debug')[1]['calls']
                    status, _ = self.http(url, '/generate', payload)
                    after = self.http(url, '/debug')[1]['calls']
                    self.assertTrue(400 <= status < 500, (status, before, after))
                    self.assertEqual(before, after)
            before = self.http(url, '/debug')[1]['calls']
            self.assertEqual(200, self.http(url, '/generate', {'prompt':'hello','max_new_tokens':32})[0])
            self.assertEqual(before+1, self.http(url, '/debug')[1]['calls'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
