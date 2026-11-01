"""Diagnostic HTTP availability is never release quality evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import release
from api_service import create_app, StubBackend


class DiagnosticAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.snapshot=self.root/'snapshot'; self.snapshot.mkdir()
        for name in ('config.json','tokenizer_config.json','tokenizer.json','model.safetensors'):
            (self.snapshot/name).write_text('{}')
        runtime=dict(base_model='fixture',base_revision='a'*40,device='cpu',dtype='float32',
                     generation=dict(system='system',max_new_tokens=32,enable_thinking=False,seed=42),do_sample=False)
        self.manifest=dict(artifact=release.base_artifact_identity(self.snapshot,dict(name='fixture',revision='a'*40)),
                           runtime=runtime,model_id='diagnostic-fixture',runtime_sha256=release.sha256(release.canonical(runtime)))
        self.path=self.root/'diagnostic.json'; self.save()

    def save(self):
        self.path.write_text(json.dumps(self.manifest))

    def test_verified_calls_load_verified_and_marks_responses(self):
        pointer=self.root/'accepted.json'
        with patch('api_service.release.load_verified',return_value=(self.manifest,{})) as verify, patch('api_service.TransformersBackend',StubBackend):
            with TestClient(create_app(pointer)) as client:
                self.assert_flags(client,False,True)
            verify.assert_called_once_with(pointer)

    def assert_flags(self,client,diagnostic,accepted):
        for response in (client.get('/ready'),client.post('/generate',json={'prompt':'hello'})):
            self.assertEqual(200,response.status_code)
            self.assertIs(response.json()['diagnostic_only'],diagnostic)
            self.assertIs(response.json()['accepted_release'],accepted)
        instance=client.app.state.backend; before=instance.calls
        for prompt in ('','   '):
            self.assertEqual(422,client.post('/generate',json={'prompt':prompt}).status_code)
        self.assertEqual(before,instance.calls)

    def test_diagnostic_requires_explicit_manifest(self):
        with self.assertRaises(ValueError):
            with TestClient(create_app(self.path,backend='diagnostic')): pass

    def test_diagnostic_and_stub_marks(self):
        with patch('api_service.release.load_verified',side_effect=AssertionError('accepted must not be read')), patch('api_service.TransformersBackend',StubBackend):
            with TestClient(create_app(self.root/'absent.json',backend='diagnostic',diagnostic_manifest=self.path)) as client:
                self.assert_flags(client,True,False)
        with TestClient(create_app(self.path,backend='stub')) as client:
            self.assert_flags(client,True,False)

    def assert_rejected(self):
        with patch('api_service.TransformersBackend') as backend:
            with self.assertRaises(ValueError):
                with TestClient(create_app(backend='diagnostic',diagnostic_manifest=self.path)): pass
            backend.assert_not_called()

    def test_changed_snapshot_rejected_before_backend(self):
        (self.snapshot/'model.safetensors').write_text('changed')
        self.assert_rejected()

    def test_changed_runtime_rejected_before_backend(self):
        self.manifest['runtime']['generation']['seed']=123; self.save()
        self.assert_rejected()

    def test_auto_device_rejected_even_with_matching_hash(self):
        self.manifest['runtime']['device']='auto'
        self.manifest['runtime_sha256']=release.sha256(release.canonical(self.manifest['runtime'])); self.save()
        self.assert_rejected()

    def test_base_identity_mismatch_rejected_even_with_matching_hash(self):
        self.manifest['runtime']['base_revision']='b'*40
        self.manifest['runtime_sha256']=release.sha256(release.canonical(self.manifest['runtime'])); self.save()
        self.assert_rejected()
