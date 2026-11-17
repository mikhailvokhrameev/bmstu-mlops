import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import io
import contextlib

ROOT=Path(__file__).resolve().parents[1]/'homework11/load'
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
runner=module('runner');summary=module('summarize')

class LoadTests(unittest.TestCase):
    def test_schedule_reproducible_exact_skew_and_offsets(self):
        rows=runner.schedule(120,2,True)
        self.assertEqual(rows,runner.schedule(120,2,True))
        self.assertEqual(sum(r['client']==0 for r in rows),192)
        self.assertEqual(len({r['client'] for r in rows}),4)
        self.assertEqual(rows[-1]['offset_seconds'],119.5)

    def test_journal_streams_before_file_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=io.StringIO()
            journal=runner.Journal(Path(tmp)/'log')
            with contextlib.redirect_stdout(output):
                journal.emit('attempt_start',id='inflight')
            self.assertEqual(json.loads(output.getvalue())['id'],'inflight')
            self.assertEqual(json.loads((Path(tmp)/'log').read_text())['event'],'attempt_start')
            journal.file.close()

    def test_replacement_rejects_other_old_replica(self):
        check=module('check_service')
        response={'model_id':'A','backend_mode':'simulated','pod':'old-survivor','text':'PERSISTED-CONTROL-123'}
        argv=['check','--url','http://service','--check','replacement','--old-pod','deleted','--expected-pod','new-pod']
        with patch('sys.argv',argv),patch.object(check,'request',return_value=(200,response)):
            with self.assertRaises(AssertionError):check.main()
        response['pod']='new-pod'
        with patch('sys.argv',argv),patch.object(check,'request',return_value=(200,response)),contextlib.redirect_stdout(io.StringIO()):
            check.main()

    def test_warmup_rejects_nonpositive_samples(self):
        check=module('check_warmup_cluster')
        for samples in ('0','-1'):
            argv=['check','--client','client','--pod','new','--service','svc','--samples',samples,'--output','/tmp/not-written']
            with patch('sys.argv',argv),contextlib.redirect_stderr(io.StringIO()),patch.object(check.subprocess,'run') as run:
                with self.assertRaises(SystemExit):check.main()
                run.assert_not_called()

    def test_warmup_preserves_command_failure(self):
        check=module('check_warmup_cluster')
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'evidence.json'
            argv=['check','--client','client','--pod','new','--service','svc','--output',str(path)]
            failed=subprocess.CompletedProcess(['kubectl'],1,stdout='partial response',stderr='exec failed')
            with patch('sys.argv',argv),patch.object(check.subprocess,'run',return_value=failed):
                with self.assertRaises(subprocess.CalledProcessError):check.main()
            record=json.loads(path.read_text())[0]
            self.assertEqual(record['stdout'],'partial response')
            self.assertEqual(record['stderr'],'exec failed')
            self.assertEqual(record['returncode'],1)

    def test_replacement_retries_and_preserves_all_responses(self):
        check=module('check_service')
        old={'model_id':'A','backend_mode':'simulated','pod':'old-survivor','text':'PERSISTED-CONTROL-123'}
        new=old|{'pod':'new-pod'}
        argv=['check','--url','http://service','--check','replacement','--old-pod','deleted','--expected-pod','new-pod','--attempts','2','--interval','0']
        output=io.StringIO()
        with patch('sys.argv',argv),patch.object(check,'request',side_effect=[(200,old),(200,new)]),contextlib.redirect_stdout(output):
            check.main()
        lines=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e['data']['pod'] for e in lines[:-1]],['old-survivor','new-pod'])
        self.assertTrue(lines[-1]['passed'])

    def test_balanced_schedule(self):
        rows=runner.schedule(120,2)
        self.assertEqual([sum(r['client']==c for r in rows) for c in range(4)],[60]*4)

    def exercise(self, mode):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if mode=='slow-error':
                    time.sleep(.12)
                    self.send_response(503);self.end_headers();self.wfile.flush();time.sleep(.7)
                    try:self.wfile.write(b'{}')
                    except OSError:pass
                    return
                if mode=='reject':
                    self.send_response(429);self.end_headers();self.wfile.write(b'{"detail":{"pod":"p"}}');return
                if mode=='content-length':
                    identity=dict(request_id=self.headers['X-Request-ID'],pod='p',model_id='A',backend_mode='simulated',runtime_sha256='hash')
                    body=('\n'.join(json.dumps(identity|event) for event in [dict(event='chunk',index=0,text='hello'),dict(event='complete',chunks=1)])+'\n').encode()
                    self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                if mode=='slow-chunks':time.sleep(.12)
                self.send_response(200);self.end_headers()
                identity=dict(request_id=self.headers['X-Request-ID'],pod='p',model_id='A',backend_mode='simulated',runtime_sha256='hash')
                self.wfile.write((json.dumps(identity|dict(event='chunk',index=0,text='hello'))+'\n').encode())
                if mode=='slow-chunks':
                    self.wfile.flush();time.sleep(.7)
                    try:self.wfile.write((json.dumps(identity|dict(event='complete',chunks=1))+'\n').encode())
                    except OSError:pass
                if mode=='malformed':self.wfile.write(b'{broken-json}\n')
                if mode=='success':self.wfile.write((json.dumps(identity|dict(event='complete',chunks=1))+'\n').encode())
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'log';journal=runner.Journal(path)
                row=runner.schedule(1,1)[0]
                journal.emit('planned',**row)
                runner.attempt(row,f'http://127.0.0.1:{server.server_port}',journal,0,timeout=.2 if mode.startswith('slow-') else 60)
                journal.file.close()
                events=[json.loads(s) for s in path.read_text().splitlines()]
                return events,summary.summarize(events)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_success_chunks_and_monotonic_timing(self):
        events,stats=self.exercise('success')
        self.assertEqual(stats['outcomes'],{'completed':1})
        self.assertEqual(stats['incomplete_streams'],0)
        self.assertEqual(len([e for e in events if e['event']=='chunk']),1)
        end=events[-1];self.assertGreaterEqual(end['total_seconds'],end['ttfc_seconds'])

    def test_content_length_completed_stream_remains_successful_at_eof(self):
        events,stats=self.exercise('content-length')
        self.assertEqual(stats['outcomes'],{'completed':1})
        self.assertTrue(events[-1]['stream_complete'])
        self.assertFalse(events[-1]['incomplete_stream'])
        self.assertEqual(events[-1]['chunks'],1)
        self.assertIsNone(events[-1]['error'])

    def test_http_rejection_accounted_separately(self):
        events,stats=self.exercise('reject')
        self.assertEqual(stats['outcomes'],{'rejected':1})
        self.assertEqual(events[-1]['status'],429)
        self.assertEqual(stats['successful_samples'],0)
        self.assertIsNone(stats['ttfc_p95'])

    def test_malformed_fragment_preserved_before_validation(self):
        events,stats=self.exercise('malformed')
        wires=[e for e in events if e['event']=='wire_line']
        self.assertEqual(wires[-1]['raw_utf8'],'{broken-json}\n')
        self.assertIsInstance(wires[-1]['received_monotonic'],float)
        self.assertEqual(stats['outcomes'],{'aborted':1})

    def test_deadline_cuts_connection_close_stream_between_chunks(self):
        events,stats=self.exercise('slow-chunks')
        end=events[-1]
        self.assertEqual(end['outcome'],'timeout')
        self.assertEqual(end['chunks'],1)
        self.assertTrue(end['incomplete_stream'])
        self.assertLess(end['total_seconds'],.28)
        self.assertEqual(stats['timeout_fraction'],1)

    def test_deadline_also_bounds_http_error_body(self):
        events,stats=self.exercise('slow-error')
        self.assertEqual(events[-1]['outcome'],'timeout')
        self.assertEqual(events[-1]['status'],503)
        self.assertLess(events[-1]['total_seconds'],.28)

    def test_truncated_stream_is_not_success(self):
        events,stats=self.exercise('truncated')
        self.assertEqual(stats['outcomes'],{'aborted':1})
        self.assertEqual(stats['incomplete_streams'],1)
        self.assertEqual(events[-1]['chunks'],1)
        self.assertEqual(stats['attempt_error_fraction'],1)

if __name__=='__main__':unittest.main()
