"""A release hash must identify an actual device, not hardware-dependent auto."""
import copy
import json
import unittest
from unittest.mock import MagicMock,patch
import types
import release
from tests_hw8910 import test_base_release as fixtures


class RuntimeDevice(unittest.TestCase):
    def setUp(self):fixtures.BaseRelease.setUp(self)

    def test_bound_auto_cannot_publish_even_with_matching_hashes(self):
        self.manifest['runtime']['device']='auto'
        self.manifest['runtime_sha256']=release.sha256(release.canonical(self.manifest['runtime']))
        self.report['runtime_sha256']=self.manifest['runtime_sha256']
        for role in ('baseline','candidate'):
            evaluated=self.reports[role]
            evaluated['release_binding']['runtime']=copy.deepcopy(self.manifest['runtime'])
            path=self.root/(role+'-eval.json');path.write_text(json.dumps(evaluated))
            ref=release.file_reference(path)
            self.manifest['evidence'][role+'-eval.json']=ref
            self.report['evidence'][role+'-eval.json']=ref
        with self.assertRaisesRegex(ValueError,'device'):
            release.publish_verified(self.manifest,self.report,self.target)

    def test_model_parameters_must_match_evaluated_device(self):
        model=MagicMock();model.parameters.return_value=[types.SimpleNamespace(device='mps:0')]
        model.buffers.return_value=[]
        with self.assertRaisesRegex(ValueError,'device'):
            release.verify_model_device(model,'cpu')

    def test_cuda_requires_an_explicit_index(self):
        for name in ('auto','cuda','cpu:0','bogus'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                release.validate_runtime_device({'device':name})
        for name in ('cpu','mps','cuda:0','cuda:2'):
            self.assertEqual(name,release.validate_runtime_device({'device':name}))

    def test_adapter_evaluation_pins_auto_before_generation(self):
        from evaluation.release_evidence import evaluation_runtime
        fake=types.SimpleNamespace(resolve_device=lambda _: 'mps')
        cfg=dict(system='system',max_new_tokens=32,seed=42,enable_thinking=False)
        with patch.dict('sys.modules',{'src.runtime':fake}):
            runtime=evaluation_runtime({'model':dict(name='fixture',revision='a'*40,device='auto',dtype='float32')},cfg)
        self.assertEqual('mps',runtime['device'])

    def test_startup_refuses_a_model_moved_to_the_wrong_device(self):
        from api_service import TransformersBackend
        model=MagicMock();model.to.return_value=model;model.eval.return_value=model
        model.parameters.return_value=[types.SimpleNamespace(device='mps:0')]
        model.buffers.return_value=[]
        auto=MagicMock();auto.from_pretrained.return_value=model
        fake_transformers=types.SimpleNamespace(AutoModelForCausalLM=auto,AutoTokenizer=MagicMock())
        fake_runtime=types.SimpleNamespace(resolve_device=lambda _: 'cpu',resolve_dtype=lambda _: 'float32',set_seed=lambda _:None)
        with patch.dict('sys.modules',{'torch':MagicMock(),'transformers':fake_transformers,'src.runtime':fake_runtime}):
            with self.assertRaisesRegex(ValueError,'device'):
                TransformersBackend(self.manifest)

    def test_adapter_auto_binding_is_also_refused(self):
        from tests_hw8910 import test_real_release as adapter_fixtures
        adapter_fixtures.VerifiedEvidenceTests.setUp(self)
        self.manifest['runtime']['device']='auto'
        self.manifest['runtime_sha256']=release.sha256(release.canonical(self.manifest['runtime']))
        self.report['runtime_sha256']=self.manifest['runtime_sha256']
        self.reports['candidate']['release_binding']['runtime']=copy.deepcopy(self.manifest['runtime'])
        path=self.root/'candidate-eval.json';path.write_text(json.dumps(self.reports['candidate']))
        ref=release.file_reference(path)
        self.manifest['evidence']['candidate-eval.json']=ref
        self.report['evidence']['candidate-eval.json']=ref
        with self.assertRaisesRegex(ValueError,'device'):
            release.publish_verified(self.manifest,self.report,self.target)
