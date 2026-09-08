"""Administrative boundaries and recovery tests; all game delivery is mocked."""
from contextlib import nullcontext
import gzip
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import secrets
import struct
import sys
import socketserver
import shutil
import threading
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from admin.app import API, create_app
from admin.auth import b64, unb64
from admin.domain import validate
from admin.runtime import Runtime
from admin.settings import Settings
from admin.store import Conflict, Store, digest
from admin.agent import AgentClient, AgentServer
from admin.drill import verify_network_isolation


class DrillTests(unittest.TestCase):
    def test_isolation_check_uses_only_own_namespace_after_privilege_drop(self):
        def readlink(path):
            if str(path).replace('\\', '/') != '/proc/self/ns/net':
                raise PermissionError('Other process namespaces are private.')
            return Path('net:[200]')
        with patch.object(Path, 'readlink', readlink):
            verify_network_isolation('net:[100]')
            with self.assertRaises(RuntimeError):
                verify_network_isolation('net:[200]')
            with self.assertRaises(ValueError):
                verify_network_isolation('')


class VirtualPasskey:
    """A real P-256 authenticator for protocol tests without browser hardware."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cid = secrets.token_bytes(32)
        self.user = None

    def registration(self, options, origin):
        self.user = options['user']['id']
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)})
        client = json.dumps({'type': 'webauthn.create', 'challenge': options['challenge'], 'origin': origin, 'crossOrigin': False}).encode()
        auth = hashlib.sha256(options['rp']['id'].encode()).digest() + b'\x45' + struct.pack('>I', 0) + bytes(16) + struct.pack('>H', len(self.cid)) + self.cid + cose
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth})
        return {'id': b64(self.cid), 'rawId': b64(self.cid), 'type': 'public-key', 'response': {'clientDataJSON': b64(client), 'attestationObject': b64(attestation), 'transports': ['internal']}, 'clientExtensionResults': {}}

    def assertion(self, options, origin, counter=1):
        client = json.dumps({'type': 'webauthn.get', 'challenge': options['challenge'], 'origin': origin, 'crossOrigin': False}).encode()
        auth = hashlib.sha256(options['rpId'].encode()).digest() + b'\x05' + struct.pack('>I', counter)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64(self.cid), 'rawId': b64(self.cid), 'type': 'public-key', 'response': {'clientDataJSON': b64(client), 'authenticatorData': b64(auth), 'signature': b64(signature), 'userHandle': self.user}, 'clientExtensionResults': {}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name), origin='http://localhost:8092', demo=True)
        self.app = create_app(self.settings, background=False)
        self.client = TestClient(self.app, base_url=self.settings.origin)
        self.addCleanup(self.client.close)
        self.store = self.app.state.store
        self.headers = {'Origin': self.settings.origin, 'Content-Type': 'application/json'}

    def post(self, route, data=None, **kwargs):
        return self.client.post(API + route, json=data or {}, headers={**self.headers, **kwargs.pop('headers', {})}, **kwargs)

    def login_demo(self):
        response = self.post('/auth/demo')
        self.assertEqual(response.status_code, 200)
        self.headers['X-Oak-CSRF'] = response.json()['csrf']
        return response.json()['user']['id']

    def test_private_routes_do_not_leak_without_session(self):
        for path in ('/overview', '/jobs', '/players', '/backups', '/configuration', '/events', '/places', '/schedules', '/access', '/positions/stream', '/avatar/skin/'+'a'*64, '/avatar/texture/item/diamond_sword.png'):
            self.assertEqual(self.client.get(API + path).status_code, 401, path)

    def test_only_illustrative_map_can_be_framed_in_demo(self):
        response = self.client.get('/admin/assets/demo-map.html')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-frame-options'], 'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", response.headers['content-security-policy'])
        self.assertEqual(self.client.get('/admin/').headers['x-frame-options'], 'DENY')

    def test_origin_csrf_and_payload_boundaries(self):
        self.assertEqual(self.post('/auth/demo', headers={'Origin': 'https://attacker.invalid'}).status_code, 403)
        self.login_demo()
        self.assertEqual(self.post('/jobs', {'kind': 'save'}, headers={'X-Oak-CSRF': ''}).status_code, 403)
        self.assertEqual(self.post('/jobs', {'kind': 'save', 'padding': 'x' * 70000}).status_code, 413)
        self.assertEqual(self.post('/jobs', {'kind': 'save', 'params': {}}, headers={'Idempotency-Key': 'x' * 24}).status_code, 202)

    def test_roles_review_binding_and_idempotency(self):
        uid = self.login_demo()
        payload = {'kind': 'console', 'params': {'command': 'list'}}
        headers = {'Idempotency-Key': 'same-operation-key-123'}
        self.assertEqual(self.post('/jobs', payload, headers=headers).status_code, 400)
        review = self.post('/reviews', payload).json()
        changed = {**payload, 'params': {'command': 'time query daytime'}, 'review': review['id']}
        self.assertEqual(self.post('/jobs', changed, headers=headers).status_code, 409)
        payload['review'] = review['id']
        first = self.post('/jobs', payload, headers=headers)
        self.assertEqual(first.status_code, 202, first.text)
        second = self.post('/jobs', payload, headers=headers)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(self.post('/jobs', payload, headers={'Idempotency-Key': 'different-key-123456'}).status_code, 409)
        with self.store.transaction() as db:
            db.execute("UPDATE users SET role='observer' WHERE id=?", (uid,))
        self.assertEqual(self.post('/reviews', {'kind': 'console', 'params': {'command': 'list'}}).status_code, 403)
        self.assertEqual(self.client.get(API + '/jobs/' + first.json()['id']).status_code, 404)

    def test_environment_review_does_not_require_ten_minute_reauthentication(self):
        uid = self.login_demo()
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET verified=?', (time.time() - 3600,))
        payload = {'kind': 'environment_apply', 'params': {'action': 'override', 'revision': 0, 'weather': 'clear', 'minutes': 5}}
        review = self.post('/reviews', payload)
        self.assertEqual(review.status_code, 200, review.text)
        reviewed = {**payload, 'review': review.json()['id']}
        headers = {'Idempotency-Key': 'environment-aged-session-123'}
        self.assertEqual(self.post('/jobs', reviewed, headers={**headers, 'X-Oak-CSRF': ''}).status_code, 403)
        self.assertEqual(self.post('/jobs', reviewed, headers=headers).status_code, 202)
        self.assertEqual(self.post('/reviews', {'kind': 'console', 'params': {'command': 'list'}}).status_code, 403)
        with self.store.transaction() as db:
            db.execute("UPDATE users SET role='observer' WHERE id=?", (uid,))
        self.assertEqual(self.post('/reviews', payload).status_code, 403)

    def test_real_passkey_enrollment_and_login_replay_protection(self):
        invite = self.store.invite('Owner')
        authenticator = VirtualPasskey()
        options = self.post('/auth/enroll/options', {'token': invite}).json()
        credential = authenticator.registration(options['options'], self.settings.origin)
        enrolled = self.post('/auth/enroll/verify', {'challenge_id': options['challenge_id'], 'credential': credential})
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        self.assertIn('HttpOnly', enrolled.headers['set-cookie'])
        self.assertIn('SameSite=strict', enrolled.headers['set-cookie'])
        self.assertEqual(self.post('/auth/enroll/options', {'token': invite}).status_code, 403)
        self.client.cookies.clear()
        options = self.post('/auth/options').json()
        payload = {'challenge_id': options['challenge_id'], 'credential': authenticator.assertion(options['options'], self.settings.origin)}
        logged = self.post('/auth/verify', payload)
        self.assertEqual(logged.status_code, 200, logged.text)
        self.assertEqual(self.post('/auth/verify', payload).status_code, 403)
        options = self.post('/auth/options').json()
        wrong = authenticator.assertion(options['options'], 'https://attacker.invalid', counter=2)
        self.assertEqual(self.post('/auth/verify', {'challenge_id': options['challenge_id'], 'credential': wrong}).status_code, 403)

    def test_revocation_and_session_expiry(self):
        uid = self.login_demo()
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        self.assertEqual(self.client.get(API + '/overview').status_code, 401)
        self.login_demo()
        with self.store.transaction() as db:
            db.execute('UPDATE users SET disabled=1 WHERE id=?', (uid,))
        self.assertEqual(self.client.get(API + '/overview').status_code, 401)

    def test_places_schedules_and_freshness(self):
        self.login_demo()
        self.assertEqual(self.post('/places', {'name': '<script>alert(1)</script>', 'x': 12, 'y': 64, 'z': 30}).status_code, 201)
        self.assertEqual(self.post('/schedules', {'kind': 'console', 'params': {'command': 'list'}, 'interval_minutes': 60}).status_code, 400)
        schedule = self.post('/schedules', {'kind': 'backup', 'params': {'name': 'Automatic'}, 'interval_minutes': 60})
        self.assertEqual(schedule.status_code, 201, schedule.text)
        self.store.set('snapshot', {'sampled_at': time.time() - 120, 'fresh': True, 'online': True})
        self.assertFalse(self.client.get(API + '/overview').json()['snapshot']['fresh'])

    def test_no_demo_on_public_origin(self):
        with self.assertRaises(ValueError):
            Settings(Path(self.temp.name), demo=True)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.db')

    def test_claim_restart_and_cancellation_never_replay(self):
        job = self.store.create_job('owner', 'save', 'Save', {}, '1' * 20)
        self.assertEqual(self.store.claim()['id'], job['id'])
        with self.assertRaises(Conflict):
            self.store.cancel(job['id'])
        reopened = Store(self.store.path)
        reopened.recover()
        self.assertIsNone(reopened.claim())
        self.assertEqual(reopened.job(job['id'])['state'], 'interrupted')
        new = reopened.create_job('owner', 'save', 'Save', {}, '2' * 20)
        reopened.cancel(new['id'])
        self.assertIsNone(reopened.claim())

    def test_scheduler_coalesces_downtime_and_busy_jobs(self):
        with self.store.transaction() as db:
            db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?)', ('schedule', 'Backup', 'backup', '{"name":"Scheduled"}', 60, time.time() - 100000, 1, 'owner'))
        self.store.tick_schedules()
        self.store.tick_schedules()
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertGreater(self.store.one('SELECT next_run FROM schedules')['next_run'], time.time())

    def test_stale_samples_do_not_create_false_departures(self):
        self.store.sample({'fresh': True, 'players': [{'name': 'Player'}]})
        self.store.sample({'fresh': False, 'players': []})
        self.assertIsNone(self.store.one('SELECT * FROM player_sessions')['left_at'])
        self.store.sample({'fresh': True, 'players': []})
        self.assertIsNotNone(self.store.one('SELECT * FROM player_sessions')['left_at'])


@unittest.skipUnless(hasattr(socketserver, 'ThreadingUnixStreamServer'), 'Live agent transport requires Linux')
class AgentTests(unittest.TestCase):
    def test_durable_receipt_prevents_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), free_reserve=0)
            runtime.execute = Mock(return_value={'saved': True})
            path = str(Path(directory) / 'agent.sock')
            with AgentServer(path, runtime) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    client = AgentClient(path)
                    data = {'job': str(uuid.uuid4()), 'kind': 'save', 'params': {}}
                    self.assertEqual(client.call('execute', data), {'saved': True})
                    self.assertEqual(client.call('execute', data), {'saved': True})
                    runtime.execute.assert_called_once()
                    with self.assertRaises(RuntimeError):
                        client.call('execute', {**data, 'kind': 'backup', 'params': {'name': 'Different'}})
                    self.assertEqual(client.call('receipt', {'job': data['job']})['state'], 'completed')
                finally:
                    server.shutdown()
                    thread.join()


class ProxyTests(unittest.TestCase):
    def test_proxy_preserves_private_headers_and_never_changes_destination(self):
        spec = importlib.util.spec_from_file_location('oak_chat_proxy_test', Path(__file__).resolve().parents[1] / 'server/chat-server.py')
        chat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chat)
        handler = object.__new__(chat.Handler)
        handler.path = '/admin/api/jobs'
        handler.command = 'POST'
        handler.headers = {'Content-Length': '2', 'Content-Type': 'application/json', 'Origin': 'https://oak.test', 'Cookie': 'opaque-session', 'X-Oak-CSRF': 'csrf', 'Idempotency-Key': 'unique-operation-key'}
        handler.connection = Mock()
        handler.rfile, handler.wfile = io.BytesIO(b'{}'), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        upstream = Mock()
        upstream.getresponse.return_value.status = 202
        upstream.getresponse.return_value.getheaders.return_value = [('Content-Type', 'application/json'), ('Set-Cookie', 'opaque=new; HttpOnly'), ('Transfer-Encoding', 'chunked')]
        upstream.getresponse.return_value.read1.side_effect = [b'{"accepted":true}', b'']
        with patch.object(chat.http.client, 'HTTPConnection', return_value=upstream) as connect:
            handler.admin_proxy()
        connect.assert_called_once_with('127.0.0.1', 8092, timeout=150)
        self.assertEqual(upstream.request.call_args.args, ('POST', '/admin/api/jobs'))
        self.assertEqual(upstream.request.call_args.kwargs['headers']['Cookie'], 'opaque-session')
        self.assertEqual(handler.wfile.getvalue(), b'{"accepted":true}')
        handler.send_header.assert_any_call('Set-Cookie', 'opaque=new; HttpOnly')
        self.assertNotIn(('Transfer-Encoding', 'chunked'), [c.args for c in handler.send_header.call_args_list])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'server/world').mkdir(parents=True)
        (self.root / 'backups').mkdir()
        (self.root / 'server/world/level.dat').write_bytes(gzip.compress(b'\x0a\x00\x00\x00'))
        (self.root / 'server/server.properties').write_text('difficulty=normal\nmax-players=12\nrcon.password=private-test-value\nunknown-setting=preserve\n')
        self.runtime = Runtime(self.root, free_reserve=0)
        self.runtime.lock = lambda: nullcontext()
        self.runtime.legacy_backup_lock = lambda: nullcontext()
        self.runtime.active = Mock(return_value=False)
        self.runtime.rcon = Mock()
        self.progress = Mock()

    def archive(self, filename, entries):
        path = self.root / 'backups' / filename
        with tarfile.open(path, 'w:gz') as archive:
            for name, content, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                if kind == tarfile.REGTYPE:
                    item.size = len(content)
                    archive.addfile(item, io.BytesIO(content))
                else:
                    item.linkname = content.decode()
                    archive.addfile(item)
        return path

    def test_backup_restore_drill_preserves_source_and_marks_exact_evidence(self):
        original = (self.root / 'server/world/level.dat').read_bytes()
        job = str(uuid.uuid4())
        result = self.runtime.execute(job, 'backup', {'name': 'Checkpoint'}, self.progress)
        self.runtime.rcon.command.assert_not_called()
        verified = self.runtime.execute(str(uuid.uuid4()), 'verify_backup', {'backup': result['backup'], 'boot': False}, self.progress)
        self.assertEqual(verified['level'], 'extraction')
        self.assertFalse(verified['playable_boot_tested'])
        self.assertEqual((self.root / 'server/world/level.dat').read_bytes(), original)
        self.assertEqual(self.runtime.backups()[0]['fingerprint'], result['sha256'])

    def test_source_copy_failure_always_reenables_saving(self):
        self.runtime.active.return_value = True
        self.runtime.rcon.command.side_effect = lambda command: 'Saved the game' if command == 'save-all flush' else 'OK'
        with patch('admin.runtime.shutil.copy2', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.runtime.execute(str(uuid.uuid4()), 'backup', {'name': 'Failing'}, self.progress)
        self.assertEqual(self.runtime.rcon.command.call_args_list[-1].args, ('save-on',))
        self.assertFalse(list(self.runtime.backups_dir.glob('*.tar.gz')))

    def test_background_write_is_recopied_before_a_checkpoint_is_accepted(self):
        original_copy = shutil.copy2
        changed = False
        expected = gzip.compress(b'\x0a\x00\x00\x00updated')
        def copy_then_change(source, destination):
            nonlocal changed
            result = original_copy(source, destination)
            if str(source).endswith('level.dat') and not changed:
                changed = True
                Path(source).write_bytes(expected)
            return result
        with patch('admin.runtime.shutil.copy2', side_effect=copy_then_change):
            result = self.runtime.execute(str(uuid.uuid4()), 'backup', {'name': 'Settled'}, self.progress)
        with tarfile.open(self.runtime.backup_path(result['backup'])) as archive:
            self.assertEqual(archive.extractfile('world/level.dat').read(), expected)
        self.assertTrue(any(call.args[0] == 'Sincronizando gravações em andamento' for call in self.progress.call_args_list))

    def test_continuously_changing_source_is_never_accepted(self):
        original_copy = shutil.copy2
        def copy_then_change(source, destination):
            result = original_copy(source, destination)
            if str(source).endswith('level.dat'):
                with Path(source).open('ab') as file:
                    file.write(b'changing')
            return result
        stage = self.runtime.control / 'synthetic-stage'
        stage.mkdir()
        with patch('admin.runtime.shutil.copy2', side_effect=copy_then_change):
            with self.assertRaisesRegex(RuntimeError, 'did not settle'):
                self.runtime.stable_copy(stage, self.progress, attempts=2)

    def test_archive_traversal_links_and_false_world_are_rejected(self):
        for index, entry in enumerate((('../escape', b'bad', tarfile.REGTYPE), ('world/link', b'/etc/passwd', tarfile.SYMTYPE), ('mods/device', b'', tarfile.CHRTYPE))):
            path = self.archive(f'oak-20260101T00000{index}Z.tar.gz', [entry])
            with self.assertRaises(ValueError):
                self.runtime.inspect_archive(path)
        self.assertFalse((self.root / 'escape').exists())

    def test_config_allowlist_revision_and_secret_preservation(self):
        before = self.runtime.configuration()
        self.assertNotIn('rcon.password', before['values'])
        result = self.runtime.execute(str(uuid.uuid4()), 'settings_apply', {'revision': before['revision'], 'changes': {'difficulty': 'hard'}}, self.progress)
        self.assertTrue(result['restart_required'])
        content = (self.root / 'server/server.properties').read_text()
        self.assertIn('unknown-setting=preserve', content)
        self.assertIn('rcon.password=private-test-value', content)
        with self.assertRaises(ValueError):
            self.runtime.execute(str(uuid.uuid4()), 'settings_apply', {'revision': before['revision'], 'changes': {'difficulty': 'easy'}}, self.progress)
        with self.assertRaises(ValueError):
            validate('settings_apply', {'revision': before['revision'], 'changes': {'rcon.password': 'injection'}})

    def test_unsupported_console_lifecycle_and_untrusted_names(self):
        for command in ('stop', '/minecraft:save-off', 'save-on', 'execute as @a run minecraft:save-off', 'function unsafe:save'):
            with self.assertRaises(ValueError):
                validate('console', {'command': command})
        with self.assertRaises(ValueError):
            validate('player_action', {'player': '@a', 'action': 'ban'})
        with self.assertRaises(PermissionError):
            validate('player_action', {'player': 'Player', 'action': 'op'}, 'moderator')

    def test_partial_restore_journal_recovers_original_and_is_repeatable(self):
        job = str(uuid.uuid4())
        old = self.runtime.control / 'rollback' / job
        stage = self.runtime.control / 'restores' / job
        old.mkdir(parents=True)
        stage.mkdir(parents=True)
        original = (self.runtime.server / 'world/level.dat').read_bytes()
        (self.runtime.server / 'world').rename(old / 'world')
        (self.runtime.server / 'world').mkdir()
        (self.runtime.server / 'world/level.dat').write_bytes(b'partial incoming world')
        (self.runtime.server / 'mods').mkdir()
        marker = self.runtime.control / 'restore-pending.json'
        marker.write_text(json.dumps({'job': job, 'online': False, 'phase': 'prepared', 'timers': {'oak-map.timer': True}, 'entries': {'world': True, 'mods': False, 'server.properties': True}}))
        self.runtime.service = Mock()
        with self.assertRaises(RuntimeError):
            self.runtime.guard_start()
        with self.assertRaises(RuntimeError):
            self.runtime.execute(str(uuid.uuid4()), 'save', {}, self.progress)
        self.runtime.recover_restore()
        self.runtime.recover_restore()
        self.assertEqual((self.runtime.server / 'world/level.dat').read_bytes(), original)
        self.assertFalse((self.runtime.server / 'mods').exists())
        self.assertTrue((self.runtime.server / 'server.properties').exists())
        self.assertFalse(marker.exists())
        self.runtime.rcon.command.assert_not_called()

    def test_backup_mutation_invalidates_integrity_and_restore_review(self):
        result = self.runtime.execute(str(uuid.uuid4()), 'backup', {'name': 'Point'}, self.progress)
        path = self.runtime.backup_path(result['backup'])
        path.write_bytes(path.read_bytes() + b'changed')
        self.assertFalse(self.runtime.backups()[0]['integrity'])
        with self.assertRaises(ValueError):
            self.runtime.preview('restore_backup', {'backup': path.name, 'fingerprint': result['sha256']})

    def test_complete_restore_and_failed_boot_rollback(self):
        (self.runtime.server / 'libraries').mkdir()
        (self.runtime.server / 'libraries/runtime.jar').write_bytes(b'synthetic library')
        (self.runtime.server / 'fabric-server-launch.jar').write_bytes(b'synthetic launcher')
        archived = self.runtime.execute(str(uuid.uuid4()), 'backup', {'name': 'Complete'}, self.progress)
        target = self.runtime.server / 'world/level.dat'
        replacement = gzip.compress(b'\x0a\x00\x00\x00changed')
        target.write_bytes(replacement)
        self.runtime.service = Mock()
        fake_pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=123,pw_gid=123))
        params = {'backup': archived['backup'], 'fingerprint': archived['sha256']}
        with patch.dict(sys.modules, {'pwd': fake_pwd}), patch('admin.runtime.os.chown', create=True):
            result = self.runtime.execute(str(uuid.uuid4()), 'restore_backup', params, self.progress)
            self.assertEqual(result['restored'], archived['backup'])
            self.assertNotEqual(target.read_bytes(), replacement)
            target.write_bytes(replacement)
            self.runtime.active.return_value = True
            self.runtime.rcon.command.return_value = 'Saved the game'
            self.runtime.wait_game = Mock(side_effect=RuntimeError('Boot failed'))
            with self.assertRaisesRegex(RuntimeError, 'Boot failed'):
                self.runtime.execute(str(uuid.uuid4()), 'restore_backup', params, self.progress)
            self.assertEqual(target.read_bytes(), replacement)
            self.assertFalse((self.runtime.control / 'restore-pending.json').exists())

    def test_legacy_without_runtime_cannot_start_a_destructive_restore(self):
        result = self.runtime.execute(str(uuid.uuid4()), 'backup', {'name': 'World only'}, self.progress)
        self.runtime.service = Mock()
        with self.assertRaises(ValueError):
            self.runtime.execute(str(uuid.uuid4()), 'restore_backup', {'backup': result['backup'], 'fingerprint': result['sha256']}, self.progress)
        self.runtime.service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
