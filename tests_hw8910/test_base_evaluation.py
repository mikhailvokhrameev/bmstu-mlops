"""End-to-end base evaluation/publication fixtures; generation only is synthetic."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tests_hw8910 import test_base_release as fixtures
import release
from evaluation import release_evidence
from evaluation import base_evaluation
from evaluation.contract import fingerprint


class BaseEvaluation(unittest.TestCase):
    def setUp(self):
        fixtures.BaseRelease.setUp(self)
        self.snapshot=Path(self.manifest['artifact']['root'])
        self.runtime=self.root/'runtime.json'
        self.runtime.write_text(json.dumps(self.manifest['runtime']))
        self.protocol=copy.deepcopy(self.manifest['protocol'])
        self.protocol['config'].update(golden='fixture-golden',training_index='fixture-training')
        self.output=self.root/'new-evaluation'

    def evaluate(self, generator=None):
        protocol=self.protocol;config=protocol['config']
        with patch('evaluation.base_evaluation.cli.inputs',return_value=(config,protocol['golden'],protocol['behavioral'],self.reports['baseline']['model'],{},fingerprint(protocol))), \
             patch('evaluation.base_evaluation.cli.protocol_snapshot',return_value=protocol), \
             patch('evaluation.base_evaluation.read_rows',return_value=protocol['training_index']), \
             patch('evaluation.base_evaluation.generate_base',side_effect=generator or (lambda *args:copy.deepcopy(self.raw))):
            return base_evaluation.evaluate_base(self.snapshot,self.runtime,self.output)

    def test_bound_base_evaluation_to_verified_pointer(self):
        self.evaluate()
        baseline=json.loads((self.output/'baseline-eval.json').read_text())
        candidate=json.loads((self.output/'candidate-eval.json').read_text())
        self.assertEqual(baseline['release_binding'],candidate['release_binding'])
        self.assertEqual(candidate['release_binding']['artifact']['kind'],'base')
        manifest,report=release_evidence.create(self.output/'evaluated-runtime.json',self.output/'release-report.json',base_evaluation=self.output)
        release.publish_verified(manifest,report,self.target)
        self.assertEqual(release.load_verified(self.target),(manifest,report))

    def test_rejected_real_gate_persists_outputs_and_cannot_publish(self):
        raw=copy.deepcopy(self.raw);raw[-1]['raw']='{"answer":"A"}'
        with self.assertRaises(ValueError):self.evaluate(lambda *args:raw)
        self.assertFalse(json.loads((self.output/'quality-gate.json').read_text())['passed'])
        self.assertTrue((self.output/'candidate-outputs.json').is_file())
        before=self.target.read_bytes()
        with self.assertRaises(ValueError):
            release_evidence.create(self.runtime,self.output/'release-report.json',base_evaluation=self.output)
        self.assertEqual(before,self.target.read_bytes())
        self.assertFalse(json.loads((self.output/'release-report.json').read_text())['passed'])

    def test_changed_snapshot_during_generation_closes_evaluation(self):
        def mutate(*args):
            (self.snapshot/'new-file').write_text('changed during evaluation')
            return self.raw
        with self.assertRaises(ValueError):self.evaluate(mutate)
        self.assertEqual('incomplete',json.loads((self.output/'evaluation-status.json').read_text())['status'])

    def test_new_runtime_does_not_inherit_previous_base_evaluation(self):
        self.evaluate()
        runtime=json.loads(self.runtime.read_text());runtime['dtype']='bfloat16'
        self.runtime.write_text(json.dumps(runtime))
        with self.assertRaises(ValueError):
            release_evidence.create(self.runtime,self.output/'release-report.json',base_evaluation=self.output)

    def test_tampered_outputs_cannot_pass_assembly(self):
        self.evaluate()
        raw=copy.deepcopy(self.raw);raw[0]['raw']='{"answer":"A"}'
        (self.output/'candidate-outputs.json').write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            release_evidence.create(self.runtime,self.output/'release-report.json',base_evaluation=self.output)

    def test_existing_evidence_directory_is_not_overwritten(self):
        self.evaluate();before=(self.output/'candidate-eval.json').read_bytes()
        with self.assertRaises(FileExistsError):self.evaluate()
        self.assertEqual(before,(self.output/'candidate-eval.json').read_bytes())

    def test_cli_evaluate_base_dispatches_without_adapter_lookup(self):
        from evaluation import cli
        with patch('sys.argv',['evaluation.cli','evaluate-base','--snapshot',str(self.snapshot),
                    '--runtime',str(self.runtime),'--output-dir',str(self.output)]), \
             patch('evaluation.base_evaluation.evaluate_base') as evaluate, \
             patch('evaluation.cli.candidate_model',side_effect=AssertionError('adapter lookup forbidden')):
            cli.main()
        evaluate.assert_called_once_with(self.snapshot,self.runtime,self.output)

    def test_release_evidence_cli_publishes_bound_base(self):
        self.evaluate()
        with patch('sys.argv',['evaluation.release_evidence','--runtime',str(self.runtime),
                '--base-evaluation',str(self.output),'--output',str(self.output/'release-report.json'),
                '--publish',str(self.target)]), \
             patch('evaluation.cli.candidate_model',side_effect=AssertionError('adapter lookup forbidden')):
            release_evidence.main()
        manifest,_=release.load_verified(self.target)
        self.assertEqual('base',manifest['artifact']['kind'])
