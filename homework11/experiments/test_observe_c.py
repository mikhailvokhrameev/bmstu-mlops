import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import observe_c


class EvidenceTests(unittest.TestCase):
    def test_collector_fetches_unlabelled_objects_by_name_and_both_endpoints(self):
        # Execute a complete collector cycle without subprocess/network access.
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'new-evidence')
            response = MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.read.return_value = b'{"status":"success","data":{}}'
            result = MagicMock(returncode=0, stdout='{"items":[]}', stderr='')
            with patch('sys.argv', ['observe_c.py', '--release', 'demo', '--duration', '1', '--output', output]), \
                 patch.object(observe_c.subprocess, 'run', return_value=result) as run, \
                 patch.object(observe_c.urllib.request, 'urlopen', return_value=response), \
                 patch.object(observe_c.time, 'monotonic', side_effect=[0, 0, 2, 2]), \
                 patch.object(observe_c.time, 'sleep'):
                observe_c.main()
            argv = [call.args[0] for call in run.call_args_list]
            prefix = ['kubectl', '-n', 'hw11', 'get']
            self.assertIn(prefix + ['deployment', 'demo-inference', '-o', 'json'], argv)
            self.assertIn(prefix + ['service', 'demo-inference-balanced', 'demo-inference-sticky', '-o', 'json'], argv)
            self.assertIn(prefix + ['configmap', 'demo-inference-config', 'demo-monitoring', '-o', 'json'], argv)
            for policy in ('balanced', 'sticky'):
                self.assertIn(prefix + ['endpointslices', '-l', 'kubernetes.io/service-name=demo-inference-' + policy, '-o', 'json'], argv)
            records = [json.loads(line) for line in (Path(output) / 'evidence.jsonl').read_text().splitlines()]
            self.assertEqual(records[-1]['kind'], 'end')
            self.assertFalse(any(record['kind'] == 'error' for record in records))

    def test_ready_timestamp_is_api_timestamp_not_observation_time(self):
        pod = {'metadata': {'name': 'p', 'uid': 'uid-1', 'creationTimestamp': '2026-01-01T00:00:00Z'},
               'status': {'conditions': [{'type': 'Initialized', 'status': 'True'},
                                         {'type': 'Ready', 'status': 'True', 'lastTransitionTime': '2026-01-01T00:00:20Z'}]}}
        result = observe_c.pod_times(pod)
        self.assertEqual(result['uid'], 'uid-1')
        self.assertEqual(result['ready']['lastTransitionTime'], '2026-01-01T00:00:20Z')

    def test_pending_pod_does_not_invent_ready_time(self):
        result = observe_c.pod_times({'metadata': {'name': 'p', 'uid': 'uid-2'}})
        self.assertIsNone(result['ready'])
        self.assertIsNone(result['created'])

    def test_queries_keep_alert_states_and_histogram_buckets(self):
        self.assertIn('ALERTS{', observe_c.QUERIES['alerts'])
        self.assertNotIn('alertstate=', observe_c.QUERIES['alerts'])
        self.assertIn('sum by (le)', observe_c.QUERIES['ttfc_p95'])
        self.assertIn('queue_timeout', observe_c.QUERIES['refusal_ratio'])


if __name__ == '__main__':
    unittest.main()
