"""Synthetic baseline contract regressions, never real acceptance evidence."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import release
from tests_hw8910 import test_real_release as fixtures


class BaseRelease(unittest.TestCase):
    def setUp(self):
        fixtures.VerifiedEvidenceTests.setUp(self)
        root=self.root/'base';root.mkdir()
        for name in ('config.json','tokenizer_config.json','tokenizer.json','model.safetensors'):
            (root/name).write_text('synthetic fixture')
        identity=dict(kind='base',root=str(root.resolve()),base_model=dict(name='fixture',revision='a'*40),
            files={p.name:release.sha256(p.read_bytes()) for p in sorted(root.iterdir())})
        spec=dict(kind='base',name='fixture',revision='a'*40,run_id='base-fixture',uri=str(root.resolve()))
        self.manifest.update(artifact=identity,model_spec=spec,model_id=spec['run_id'],artifact_uri=spec['uri'],
            model_sha256=release.sha256(release.canonical(identity)))
        from evaluation.contract import fingerprint
        runtime=dict(base_model='fixture',base_revision='a'*40,device='cpu',dtype='float32',
            generation=dict(system='system',max_new_tokens=32,seed=42,enable_thinking=False),do_sample=False)
        self.manifest['runtime']=runtime
        self.manifest['runtime_sha256']=release.sha256(release.canonical(runtime))
        self.report['runtime_sha256']=self.manifest['runtime_sha256']
        self.manifest['protocol']['config'].update(runtime['generation'])
        self.manifest['protocol_sha256']=fingerprint(self.manifest['protocol'])
        self.report['protocol_sha256']=self.manifest['protocol_sha256']
        self.report['protocol']=copy.deepcopy(self.manifest['protocol'])
        for role in ('baseline','candidate'):
            self.reports[role]['protocol_sha256']=self.manifest['protocol_sha256']
            (self.root/(role+'-eval.json')).write_text(json.dumps(self.reports[role]))
            self.manifest['evidence'][role+'-eval.json']=release.file_reference(self.root/(role+'-eval.json'))
        candidate=self.reports['candidate']
        candidate.update(model=spec,release_binding=dict(artifact=identity,runtime=self.manifest['runtime']))
        self.reports['baseline']['release_binding']=candidate['release_binding']
        (self.root/'baseline-eval.json').write_text(json.dumps(self.reports['baseline']))
        self.manifest['evidence']['baseline-eval.json']=release.file_reference(self.root/'baseline-eval.json')
        (self.root/'candidate-eval.json').write_text(json.dumps(candidate))
        self.manifest['evidence']['candidate-eval.json']=release.file_reference(self.root/'candidate-eval.json')
        self.report.update({k:copy.deepcopy(self.manifest[k]) for k in ('artifact','model_spec','model_id','model_sha256','evidence')})

    def test_base_roundtrip_and_detect_changed_weights_on_load(self):
        release.publish_verified(self.manifest,self.report,self.target)
        self.assertEqual(release.load_verified(self.target),(self.manifest,self.report))
        (Path(self.manifest['artifact']['root'])/'model.safetensors').write_text('tampered')
        with self.assertRaises(ValueError):release.load_verified(self.target)

    def test_legacy_baseline_without_bound_evaluation_cannot_publish(self):
        candidate=self.reports['candidate'];candidate.pop('release_binding')
        (self.root/'candidate-eval.json').write_text(json.dumps(candidate))
        ref=release.file_reference(self.root/'candidate-eval.json')
        self.manifest['evidence']['candidate-eval.json']=ref
        self.report['evidence']['candidate-eval.json']=ref
        fixtures.VerifiedEvidenceTests.assert_refused(self)

    def test_base_revision_and_kind_must_match_inventory(self):
        for key,value in [('revision','b'*40),('kind','adapter')]:
            original=self.manifest['model_spec'][key]
            self.manifest['model_spec'][key]=value
            self.report['model_spec']=copy.deepcopy(self.manifest['model_spec'])
            fixtures.VerifiedEvidenceTests.assert_refused(self)
            self.manifest['model_spec'][key]=original

    def test_rejected_candidate_preserves_accepted_base_pointer(self):
        release.publish_verified(self.manifest,self.report,self.target)
        before=self.target.read_bytes()
        rejected=copy.deepcopy(self.report);rejected['passed']=False
        with self.assertRaises(ValueError):release.publish_verified(self.manifest,rejected,self.target)
        self.assertEqual(before,self.target.read_bytes())
        self.assertEqual('base-fixture',release.load_verified(self.target)[0]['model_id'])

    def test_base_cannot_change_runtime_identity_or_evaluated_generation(self):
        for key,value in [('base_revision','b'*40),('generation',dict(system='different',max_new_tokens=32,seed=42,enable_thinking=False))]:
            original=copy.deepcopy(self.manifest['runtime'])
            self.manifest['runtime'][key]=value
            self.manifest['runtime_sha256']=release.sha256(release.canonical(self.manifest['runtime']))
            self.report['runtime_sha256']=self.manifest['runtime_sha256']
            fixtures.VerifiedEvidenceTests.assert_refused(self)
            self.manifest['runtime']=original

    def test_base_failed_behavior_cannot_be_published_as_pass(self):
        from evaluation.contract import fingerprint
        from evaluation.solution import score
        raw=copy.deepcopy(self.raw); raw[50]['raw']='{"answer":"A"}'
        evaluated=self.reports['candidate']
        evaluated['outputs_sha256']=fingerprint(raw)
        evaluated['behavioral']=score(self.manifest['protocol']['behavioral'],raw[50:])
        (self.root/'candidate-outputs.json').write_text(json.dumps(raw))
        (self.root/'candidate-eval.json').write_text(json.dumps(evaluated))
        for name in ('candidate-outputs.json','candidate-eval.json'):
            reference=release.file_reference(self.root/name)
            self.manifest['evidence'][name]=reference
            self.report['evidence'][name]=reference
        fixtures.VerifiedEvidenceTests.assert_refused(self)

    def test_base_inventory_rejects_unpinned_and_missing_tokenizer(self):
        root=Path(self.manifest['artifact']['root'])
        with self.assertRaises(ValueError):release.base_artifact_identity(root,dict(name='fixture',revision='main'))
        (root/'tokenizer.json').unlink()
        with self.assertRaises(ValueError):release.base_artifact_identity(root,dict(name='fixture',revision='a'*40))

    def test_base_inventory_rejects_symlink(self):
        root=Path(self.manifest['artifact']['root'])
        (root/'alias').symlink_to(root/'config.json')
        with self.assertRaises(ValueError):release.base_artifact_identity(root,dict(name='fixture',revision='a'*40))


class BaseBackend(unittest.TestCase):
    def test_base_loads_verified_local_snapshot_without_peft(self):
        from api_service import TransformersBackend
        runtime=dict(base_model='fixture',base_revision='a'*40,device='cpu',dtype='float32',
                     generation=dict(system='system',max_new_tokens=32,seed=42,enable_thinking=False),do_sample=False)
        manifest=dict(model_id='base-fixture',runtime_sha256='b'*64,runtime=runtime,
            artifact=dict(kind='base',root='/fixture/base',base_model=dict(name='fixture',revision='a'*40)))
        model=MagicMock();model.to.return_value=model;model.eval.return_value=model
        model.parameters.return_value=[types.SimpleNamespace(device='cpu')]
        model.buffers.return_value=[]
        auto=MagicMock();auto.from_pretrained.return_value=model
        tokenizer=MagicMock()
        fake_transformers=types.SimpleNamespace(AutoModelForCausalLM=auto,AutoTokenizer=tokenizer)
        fake_runtime=types.SimpleNamespace(resolve_device=lambda _: 'cpu',resolve_dtype=lambda _: 'float32',set_seed=lambda _:None)
        with patch.dict('sys.modules',{'torch':MagicMock(),'transformers':fake_transformers,'peft':None,'src.runtime':fake_runtime}):
            loaded=TransformersBackend(manifest)
        self.assertIs(loaded.model,model)
        auto.from_pretrained.assert_called_once_with('/fixture/base',dtype='float32',local_files_only=True)
        tokenizer.from_pretrained.assert_called_once_with('/fixture/base',local_files_only=True)
