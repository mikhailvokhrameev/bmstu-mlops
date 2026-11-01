import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import release


class RealRelease(unittest.TestCase):
    def test_legacy_cannot_publish_real_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'accepted.json'
            target.write_bytes(b'previous\n')
            with self.assertRaises(ValueError):
                release.publish_verified({}, {}, target)
            self.assertEqual(target.read_bytes(), b'previous\n')

    def test_inventory_detects_any_changed_or_extra_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('manifest.json', 'adapter_config.json', 'adapter_model.safetensors', 'tokenizer_config.json'):
                (root / name).write_text('fixture')
            model = dict(name='fixture', revision='a' * 40)
            original = release.artifact_identity(root, model)
            (root / 'extra').write_text('changed')
            self.assertNotEqual(original, release.artifact_identity(root, model))
            with self.assertRaises(ValueError):
                release.artifact_identity(root, dict(name='fixture', revision='main'))

    def test_incomplete_attempt_is_persisted(self):
        from evaluation.release_evidence import create
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'release-report.json'
            (Path(tmp) / 'runtime.json').write_text('{}')
            with patch('evaluation.cli.candidate_model', side_effect=ValueError('missing artifact')):
                with self.assertRaises(ValueError):
                    create(Path(tmp) / 'runtime.json', output)
            import json
            report = json.loads(output.read_text())
            self.assertEqual(report['status'], 'incomplete')
            self.assertIs(report['passed'], False)


class VerifiedEvidenceTests(unittest.TestCase):
    def setUp(self):
        from evaluation.contract import fingerprint
        from evaluation.solution import score, quality_gate
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        adapter = self.root / 'adapter'
        adapter.mkdir()
        model = dict(name='fixture', revision='a' * 40)
        for name in ('adapter_config.json', 'adapter_model.safetensors', 'tokenizer_config.json'):
            (adapter / name).write_text('fixture')
        (adapter / 'manifest.json').write_text(json.dumps(dict(model=model)))
        identity = release.artifact_identity(adapter, model)
        rows = [dict(id=f'q{i}', group_id=f'g{i}', input=f'Q{i}', expected='B',
                     choices=['A', 'B'], slices=[f's{i % 3}'], source='fixture') for i in range(50)]
        behavioral = [dict(id=f'b{i}', group_id=f'b{i}', input=f'Behavior{i}', expected='B',
                           choices=['A', 'B'], slices=['behavior'], source='fixture', type=kind)
                      for i, kind in enumerate(['format', 'paraphrase', 'refusal'])]
        protocol = dict(config=dict(critical_slices=['s0', 's1']), golden=rows, behavioral=behavioral,
                        training_index=[dict(id='train', group_id='train', input='Unrelated train')], evaluation_code={})
        self.raw = [dict(id=r['id'], raw='{"answer":"B"}', error=None, latency_seconds=0.0)
                    for r in rows + behavioral]
        spec = dict(run_id='fixture-run', uri='models:/fixture/1', name='fixture', version='1')
        runtime = dict(dtype='float32', device='cpu')
        baseline_spec = dict(kind='base', name='fixture', revision='a' * 40)
        self.reports = {}
        for role, model_spec in [('baseline', baseline_spec), ('candidate', spec)]:
            evaluated = score(rows, self.raw[:50])
            evaluated.update(status='complete', model=model_spec, evaluation_run_id='eval',
                             behavioral=score(behavioral, self.raw[50:]),
                             protocol_sha256=fingerprint(protocol), golden_sha256=fingerprint(rows),
                             outputs_sha256=fingerprint(self.raw), baseline_spec_sha256=fingerprint(baseline_spec))
            if role == 'candidate':
                evaluated['release_binding'] = dict(artifact=identity, runtime=runtime)
            self.reports[role] = evaluated
            (self.root / (role + '-outputs.json')).write_text(json.dumps(self.raw))
            (self.root / (role + '-eval.json')).write_text(json.dumps(evaluated))
        evidence = {name: release.file_reference(self.root / name) for name in
                    ('baseline-eval.json', 'candidate-eval.json', 'baseline-outputs.json', 'candidate-outputs.json')}
        self.manifest = dict(schema_version=1, model_id=spec['run_id'], artifact_uri=spec['uri'],
                             model_sha256=release.sha256(release.canonical(identity)),
                             protocol_sha256=fingerprint(protocol), golden_sha256=fingerprint(rows),
                             runtime=runtime, runtime_sha256=release.sha256(release.canonical(runtime)), artifact=identity,
                             model_spec=spec, protocol_id='params', golden_id='golden', runtime_id='runtime',
                             evidence=evidence, protocol=protocol)
        self.report = {k: copy.deepcopy(v) for k, v in self.manifest.items() if k not in
                       ('schema_version', 'artifact_uri', 'runtime')}
        self.report.update(status='complete', passed=True, evaluation_run_id='eval',
                           metrics=dict(accuracy=1.0, format_rate=1.0), critical_slices=['s0', 's1'],
                           gate=quality_gate(self.reports['baseline'], self.reports['candidate'], ['s0', 's1']))
        self.target = self.root / 'accepted.json'
        self.target.write_bytes(b'previous pointer\n')

    def assert_refused(self):
        before = self.target.read_bytes()
        with self.assertRaises(ValueError):
            release.publish_verified(self.manifest, self.report, self.target)
        self.assertEqual(before, self.target.read_bytes())

    def test_verified_roundtrip(self):
        release.publish_verified(self.manifest, self.report, self.target)
        self.assertEqual(release.load_verified(self.target), (self.manifest, self.report))

    def test_critical_slices_cannot_be_removed_from_report(self):
        self.report['critical_slices'] = ['s0']
        self.assert_refused()

    def test_model_id_and_uri_must_match_model_spec(self):
        for field in ('model_id', 'artifact_uri'):
            with self.subTest(field=field):
                old = self.manifest[field]
                self.manifest[field] = 'different'
                if field == 'model_id':
                    self.report[field] = 'different'
                self.assert_refused()
                self.manifest[field] = old
                if field == 'model_id':
                    self.report[field] = old

    def test_raw_tampering_rejected_even_with_updated_hashes(self):
        from evaluation.contract import fingerprint
        raw = copy.deepcopy(self.raw)
        raw[0]['raw'] = '{"answer":"A"}'
        path = self.root / 'candidate-outputs.json'
        path.write_text(json.dumps(raw))
        self.reports['candidate']['outputs_sha256'] = fingerprint(raw)
        eval_path = self.root / 'candidate-eval.json'
        eval_path.write_text(json.dumps(self.reports['candidate']))
        for name in ('candidate-eval.json', 'candidate-outputs.json'):
            reference = release.file_reference(self.root / name)
            self.manifest['evidence'][name] = reference
            self.report['evidence'][name] = reference
        self.assert_refused()

    def test_behavioral_raw_tampering_rejected_with_updated_hashes(self):
        from evaluation.contract import fingerprint
        raw = copy.deepcopy(self.raw)
        raw[50]['raw'] = '{"answer":"A"}'
        (self.root / 'baseline-outputs.json').write_text(json.dumps(raw))
        self.reports['baseline']['outputs_sha256'] = fingerprint(raw)
        (self.root / 'baseline-eval.json').write_text(json.dumps(self.reports['baseline']))
        for name in ('baseline-eval.json', 'baseline-outputs.json'):
            reference = release.file_reference(self.root / name)
            self.manifest['evidence'][name] = reference
            self.report['evidence'][name] = reference
        self.assert_refused()

    def test_pinned_golden_data_changes_rejected(self):
        self.manifest['protocol']['golden'][0]['expected'] = 'A'
        self.report['protocol'] = copy.deepcopy(self.manifest['protocol'])
        self.assert_refused()

    def test_file_reference_rejects_symlink(self):
        link = self.root / 'alias.json'
        link.symlink_to(self.root / 'candidate-eval.json')
        with self.assertRaises(ValueError):
            release.file_reference(link)


if __name__ == '__main__':
    unittest.main()
