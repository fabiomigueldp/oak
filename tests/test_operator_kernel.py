"""Operator durability, process ownership and authenticated socket boundaries.

All Minecraft delivery is mocked. Host jobs use disposable temporary directories.
"""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oak_operator.daemon import authorize_peer, dispatch, error_response, Server
from oak_operator.kernel import Kernel, TERMINAL, process_identity


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name) / 'state'
        self.kernel = Kernel(self.state, max_workers=3, poll_seconds=.02)

    def tearDown(self):
        self.kernel.close()
        self.directory.cleanup()

    def job(self, source="print('ok')", **kwargs):
        return self.kernel.call('jobs.submit', {'kind': 'python', 'source': source, **kwargs}, actor='test:owner')

    def wait(self, job, desired=TERMINAL, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.kernel.call('jobs.get', {'id': job['id']})
            if value['status'] in desired:
                return value
            time.sleep(.02)
        self.fail('Job did not reach expected state: ' + repr(value))

    def test_python_output_environment_and_default_workspace(self):
        self.kernel.start()
        job = self.job("import os\nprint(os.environ['OAK_TEST'])\nprint(os.getcwd())", environment={'OAK_TEST': 'verified'})
        value = self.wait(job)
        self.assertEqual(value['state'], 'succeeded')
        self.assertIn('verified', value['output'])
        self.assertIn(str(self.state / 'workspace'), value['output'])
        self.assertEqual(value['exit_code'], 0)
        self.assertEqual(value['actor'], 'test:owner')

    def test_failure_retains_exit_code_and_output(self):
        self.kernel.start()
        value = self.wait(self.job("print('before failure')\nraise SystemExit(7)"))
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['exit_code'], 7)
        self.assertIn('before failure', value['output'])

    def test_output_cursor_is_bounded_and_full_output_remains_on_disk(self):
        self.kernel.start()
        value = self.wait(self.job("print('x' * 150000)"))
        self.assertEqual(len(value['output'].encode()), 65536)
        self.assertTrue(value['has_more'])
        next_page = self.kernel.call('jobs.get', {'id': value['id'], 'offset': value['next_offset']})
        self.assertEqual(next_page['offset'], 65536)
        expected_size = 150000 + len(os.linesep.encode())
        self.assertEqual(Path(value['output_path']).stat().st_size, expected_size)
        end = self.kernel.call('jobs.get', {'id': value['id'], 'offset': 9999999})
        self.assertEqual(end['output'], '')
        self.assertEqual(end['next_offset'], expected_size)

    def test_idempotency_is_durable_and_rejects_changed_source_or_actor(self):
        first = self.job(idempotency='same-request')
        self.assertEqual(first['id'], self.job(idempotency='same-request')['id'])
        with self.assertRaises(ValueError):
            self.job("print('changed')", idempotency='same-request')
        with self.assertRaises(ValueError):
            self.kernel.call('jobs.submit', {'kind': 'python', 'source': "print('ok')", 'idempotency': 'same-request'}, actor='other')
        self.kernel.close()
        self.kernel = Kernel(self.state)
        self.assertEqual(first['id'], self.job(idempotency='same-request')['id'])

    def test_cancel_queued_prevents_execution(self):
        marker = Path(self.directory.name) / 'must-not-exist'
        job = self.job(f"from pathlib import Path\nPath({str(marker)!r}).touch()")
        self.kernel.call('jobs.cancel', {'id': job['id']})
        self.kernel.start()
        self.assertEqual(self.wait(job)['status'], 'cancelled')
        self.assertFalse(marker.exists())

    def test_cancel_running_process(self):
        self.kernel.start()
        job = self.job("import time\nprint('started', flush=True)\ntime.sleep(90)", timeout_seconds=None)
        self.wait(job, {'running'})
        self.kernel.call('jobs.cancel', {'id': job['id']})
        self.assertEqual(self.wait(job)['status'], 'cancelled')

    def test_timeout_is_explicit_and_process_stops(self):
        self.kernel.start()
        job = self.job('import time\ntime.sleep(90)', timeout_seconds=.15)
        self.assertEqual(self.wait(job)['status'], 'timed_out')

    def test_shutdown_interrupts_without_replay(self):
        self.kernel.start()
        job = self.job('import time\ntime.sleep(90)', timeout_seconds=None)
        self.wait(job, {'running'})
        self.kernel.close()
        self.kernel = Kernel(self.state, poll_seconds=.02).start()
        self.assertEqual(self.wait(job)['status'], 'interrupted')
        self.assertEqual(len(self.kernel.call('jobs.list')['jobs']), 1)

    def test_resource_leases_serialize_conflicting_work_only(self):
        self.kernel.start()
        first = self.job('import time\ntime.sleep(.7)', resources=['region:spawn'])
        second = self.job("print('second')", resources=['region:spawn'])
        unrelated = self.job("print('unrelated')", resources=['region:arena'])
        a, b, c = self.wait(first), self.wait(second), self.wait(unrelated)
        self.assertGreaterEqual(b['started'], a['ended'])
        self.assertLess(c['started'], a['ended'])

    def test_only_one_scheduler_owns_state(self):
        self.kernel.start()
        other = Kernel(self.state)
        try:
            with self.assertRaisesRegex(RuntimeError, 'Another operator kernel'):
                other.start()
        finally:
            other.close()

    def test_recovery_never_kills_a_reused_pid(self):
        job = self.job()
        with self.kernel.store.transaction() as db:
            db.execute("UPDATE jobs SET status='running',pid=?,identity=? WHERE id=?", (123456, json.dumps({'start': 'old', 'boot': 'old'}), job['id']))
        with patch('oak_operator.kernel.process_identity', return_value={'start': 'new', 'boot': 'old'}), patch('oak_operator.kernel.terminate_group') as kill:
            self.kernel.start()
            kill.assert_not_called()
        self.assertEqual(self.wait(job)['status'], 'interrupted')

    def test_mock_minecraft_raw_function_and_schedule_use_world_lock(self):
        calls = []
        runtime = SimpleNamespace(rcon=SimpleNamespace(command=lambda line: calls.append(line) or 'accepted'), lock=Mock(return_value=nullcontext()))
        self.kernel.runtime = runtime
        self.kernel.start()
        job = self.kernel.call('jobs.submit', {'kind': 'command', 'source': 'function oak:test\nschedule function oak:later 20t'})
        self.assertEqual(self.wait(job)['status'], 'succeeded')
        self.assertEqual(calls, ['function oak:test', 'schedule function oak:later 20t'])
        runtime.lock.assert_called_once()

    def test_demo_never_executes_any_source_or_contacts_minecraft(self):
        self.kernel.close()
        runtime = Mock()
        self.kernel = Kernel(self.state, runtime=runtime, demo=True, poll_seconds=.02).start()
        marker = Path(self.directory.name) / 'demo-escape'
        source = f"from pathlib import Path\nPath({str(marker)!r}).touch()"
        with patch('oak_operator.kernel.subprocess.Popen', side_effect=AssertionError('Demo launched a process')):
            for kind in ('shell', 'python', 'javascript', 'command'):
                value = self.wait(self.kernel.call('jobs.submit', {'kind': kind, 'source': source}))
                self.assertEqual(value['status'], 'succeeded')
                self.assertTrue(value['demo'])
        runtime.rcon.command.assert_not_called()
        self.assertFalse(marker.exists())
        with self.assertRaises(ValueError):
            self.kernel.call('files.read', {'path': str(Path(__file__).resolve())})

    def test_once_routine_persists_and_fires_once_after_restart(self):
        routine = self.kernel.call('routines.upsert', {'name': 'Once', 'trigger': {'type': 'once', 'at': time.time() - 1}, 'job': {'kind': 'python', 'source': "print('once')"}})
        self.kernel.close()
        self.kernel = Kernel(self.state, poll_seconds=.02).start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.kernel.call('jobs.list')['jobs']:
            time.sleep(.02)
        jobs = self.kernel.call('jobs.list')['jobs']
        self.assertEqual(len(jobs), 1)
        self.wait(jobs[0])
        self.kernel._tick()
        self.assertEqual(len(self.kernel.call('jobs.list')['jobs']), 1)
        saved = self.kernel.call('routines.list')['routines'][0]
        self.assertEqual(saved['id'], routine['id'])
        self.assertFalse(saved['enabled'])

    def test_missed_interval_coalesces_and_revision_conflicts(self):
        routine = self.kernel.call('routines.upsert', {'name': 'Interval', 'trigger': {'type': 'interval', 'seconds': 30}, 'job': {'kind': 'python', 'source': "print('interval')"}})
        with self.kernel.store.transaction() as db:
            db.execute('UPDATE routines SET next_at=? WHERE id=?', (time.time() - 300, routine['id']))
        self.kernel._tick()
        self.kernel._tick()
        self.assertEqual(len(self.kernel.call('jobs.list')['jobs']), 1)
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            self.kernel.call('routines.upsert', {**routine, 'revision': 0})
        saved = self.kernel.call('routines.upsert', {**routine, 'name': 'Renamed'})
        self.assertEqual(saved['revision'], 2)

    def test_event_routine_retains_cursor_and_event_payload_is_data(self):
        self.kernel.call('events.publish', {'name': 'player.enter', 'data': {'old': True}})
        self.kernel.call('routines.upsert', {'name': 'Event', 'trigger': {'type': 'event', 'name': 'player.enter'}, 'job': {'kind': 'python', 'source': "import os,json\nprint(json.loads(os.environ['OAK_EVENT'])['data']['player'])"}})
        event = self.kernel.call('events.publish', {'name': 'player.enter', 'data': {'player': "x'; touch should-not-run"}})
        self.kernel._tick()
        self.kernel._tick()
        jobs = self.kernel.call('jobs.list')['jobs']
        self.assertEqual(len(jobs), 1)
        detail = self.kernel.call('jobs.get', {'id': jobs[0]['id']})
        payload = json.loads(detail['spec']['environment']['OAK_EVENT'])
        self.assertEqual(payload['id'], event['id'])
        self.kernel.start()
        self.assertIn("x'; touch should-not-run", self.wait(jobs[0])['output'])
        self.kernel._tick()
        self.assertEqual(len(self.kernel.call('jobs.list')['jobs']), 1)

    def test_notebooks_keep_versions_and_detect_conflicts(self):
        notebook = self.kernel.call('notebooks.save', {'title': 'Plan', 'content': '# Plan\n\n```python\nprint(1)\n```'})
        revised = self.kernel.call('notebooks.save', {**notebook, 'content': '# Result\nJob complete.'})
        self.assertEqual(revised['revision'], 2)
        old = self.kernel.call('notebooks.get', {'id': notebook['id'], 'revision': 1})
        self.assertEqual(old['content'], notebook['content'])
        self.assertEqual(Path(old['path']).read_text(), notebook['content'])
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            self.kernel.call('notebooks.save', notebook)
        self.kernel.call('notebooks.delete', {'id': notebook['id'], 'revision': 2})
        self.assertEqual(self.kernel.call('notebooks.get', {'id': notebook['id'], 'revision': 1})['content'], notebook['content'])

    def test_files_are_atomic_and_hash_checked(self):
        path = Path(self.directory.name) / 'files' / 'sample.txt'
        created = self.kernel.call('files.write', {'path': str(path), 'content': 'first', 'expected_sha256': ''})
        self.assertEqual(created['sha256'], hashlib.sha256(b'first').hexdigest())
        with self.assertRaisesRegex(ValueError, 'hash conflict'):
            self.kernel.call('files.write', {'path': str(path), 'content': 'wrong', 'expected_sha256': 'old'})
        self.assertEqual(path.read_text(), 'first')
        revised = self.kernel.call('files.write', {'path': str(path), 'content': 'second', 'expected_sha256': created['sha256']})
        result = self.kernel.call('files.read', {'path': str(path), 'offset': 1, 'limit': 2})
        self.assertEqual(result['content'], 'ec')
        self.assertEqual(result['sha256'], revised['sha256'])
        self.assertEqual(len(self.kernel.call('files.list', {'path': str(path.parent)})['files']), 1)

    def test_invalid_numeric_and_environment_values_do_not_enter_queue(self):
        for value in (float('nan'), float('inf'), True, -1):
            with self.assertRaises(ValueError):
                self.job(timeout_seconds=value)
        with self.assertRaises(ValueError):
            self.job(environment={'BAD=NAME': 'x'})
        with self.assertRaises(ValueError):
            self.job(cwd='relative')
        self.assertEqual(self.kernel.call('jobs.list')['jobs'], [])

    def test_default_timeout_is_unlimited_and_lists_do_not_repeat_large_sources(self):
        first = self.job('x=' + repr('a' * 70000))
        self.assertIsNone(first['spec']['timeout_seconds'])
        listing = self.kernel.call('jobs.list')['jobs'][0]
        self.assertNotIn('source', listing['spec'])
        self.assertGreater(listing['source_bytes'], 70000)
        self.assertEqual(self.kernel.call('jobs.get', {'id': first['id']})['spec']['source'], first['spec']['source'])

    @unittest.skipUnless(os.name == 'posix', 'Linux nonblocking special-file reads')
    def test_device_file_reads_are_bounded_without_hashing_infinite_streams(self):
        result = self.kernel.call('files.read', {'path': '/dev/zero', 'limit': 17})
        self.assertEqual(len(result['content']), 17)
        self.assertIsNone(result['sha256'])
        self.assertFalse(result['regular_file'])

    def test_disabled_event_routine_does_not_replay_events_while_disabled(self):
        routine = self.kernel.call('routines.upsert', {'name': 'Paused', 'enabled': False, 'trigger': {'type': 'event', 'name': 'manual.event'}, 'job': {'kind': 'python', 'source': 'pass'}})
        self.kernel.call('events.publish', {'name': 'manual.event'})
        self.kernel.call('routines.upsert', {**routine, 'enabled': True})
        self.kernel._tick()
        self.assertEqual(self.kernel.call('jobs.list')['jobs'], [])

    @unittest.skipUnless(os.name == 'posix', 'Linux process group ownership')
    def test_background_descendant_is_cleaned_after_parent_finishes(self):
        self.kernel.start()
        source = "import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'])\nprint(p.pid,flush=True)"
        value = self.wait(self.job(source))
        self.assertEqual(value['status'], 'succeeded')
        child = int(value['output'].strip())
        try:
            status = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
            self.assertEqual(status, 'Z')
        except FileNotFoundError:
            pass


class DaemonAuthenticationTests(unittest.TestCase):
    def test_daemon_errors_preserve_actionable_codes(self):
        for error, expected in ((ValueError('Invalid kind'), 'invalid_input'),
                                (ValueError('Notebook revision conflict'), 'conflict'),
                                (KeyError('Job not found'), 'not_found'),
                                (PermissionError('Denied'), 'permission_denied'),
                                (RuntimeError('Not available'), 'unavailable')):
            self.assertEqual(error_response(error)['error']['code'], expected)

    def test_peer_uid_is_authority_even_if_caller_claims_owner(self):
        with patch('oak_operator.daemon.peer_identity', return_value=(99, 1005, 1005)):
            with self.assertRaises(PermissionError):
                authorize_peer(Mock(), 1001)
        with patch('oak_operator.daemon.peer_identity', return_value=(99, 0, 0)):
            self.assertEqual(authorize_peer(Mock(), 1001)['uid'], 0)
        with patch('oak_operator.daemon.peer_identity', return_value=(99, 1001, 1001)):
            self.assertEqual(authorize_peer(Mock(), 1001)['uid'], 1001)

    def test_actor_label_cannot_replace_authenticated_identity(self):
        kernel = Mock()
        dispatch(kernel, {'method': 'discover', 'data': {}, 'actor': 'owner'}, {'uid': 1001})
        kernel.call.assert_called_once_with('discover', {}, actor='uid:1001/owner')
        for request in ({}, {'method': 'discover', 'data': []}, {'method': 'discover', 'actor': '\0owner'}):
            with self.assertRaises(ValueError):
                dispatch(kernel, request, {'uid': 1001})

    @unittest.skipUnless(os.name == 'posix', 'Linux authenticated Unix socket')
    def test_unix_socket_ndjson_authentication_and_request_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Kernel(Path(directory) / 'state', demo=True)
            path = Path(directory) / 'operator.sock'
            with Server(path, kernel, os.getuid()) as server:
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
                thread.start()
                try:
                    def exchange(payload):
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                            connection.settimeout(3)
                            connection.connect(str(path))
                            connection.sendall(payload)
                            with connection.makefile('rb') as reader:
                                return json.loads(reader.readline())
                    result = exchange(b'{"method":"discover","actor":"socket-test"}\n')
                    self.assertTrue(result['ok'])
                    self.assertEqual(result['result']['actor'], f'uid:{os.getuid()}/socket-test')
                    self.assertEqual(exchange(b'{"method":"discover","data":NaN}\n')['error']['code'], 'invalid_input')
                    self.assertEqual(exchange(b'not-json\n')['error']['code'], 'invalid_input')
                finally:
                    server.shutdown()
                    thread.join()
                    kernel.close()


if __name__ == '__main__':
    unittest.main()
