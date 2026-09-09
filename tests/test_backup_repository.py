"""Policy and real restic recovery tests using synthetic worlds only."""
from contextlib import nullcontext
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import uuid
import zipfile
import struct

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.backup_repository import Repository, DEFAULT_POLICY, retention, validate_policy, game_version
from admin.domain import validate
from admin.runtime import Runtime
from admin.store import Store


class PolicyTests(unittest.TestCase):
    def test_cleanup_only_removes_completed_expired_recovery_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(Path(folder), free_reserve=0)
            repository = Repository(runtime)
            expired = runtime.control/'rollback'/str(uuid.uuid4())
            unknown = runtime.control/'rollback'/str(uuid.uuid4())
            expired.mkdir(parents=True); unknown.mkdir()
            (expired/'completed.json').write_text(json.dumps({'at': 1}))
            (runtime.control/'restore-pending.json').write_text('{}')
            repository.cleanup_recovery()
            self.assertTrue(expired.exists())
            (runtime.control/'restore-pending.json').unlink()
            repository.cleanup_recovery()
            self.assertFalse(expired.exists())
            self.assertTrue(unknown.exists())
    def test_metadata_clock_is_ignored_but_game_rules_are_detected(self):
        from admin.world_metadata import fingerprint
        def string(value):
            value = value.encode()
            return struct.pack('>H', len(value)) + value
        def nbt(clock, difficulty):
            return gzip.compress(b'\x0a\x00\x00\x0a' + string('Data') + b'\x04' + string('Time') + struct.pack('>q', clock)
                                 + b'\x01' + string('Difficulty') + bytes([difficulty]) + b'\x00\x00')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'level.dat'
            path.write_bytes(nbt(1, 2)); original = fingerprint(path)
            path.write_bytes(nbt(1000, 2)); self.assertEqual(fingerprint(path), original)
            path.write_bytes(nbt(1000, 3)); self.assertNotEqual(fingerprint(path), original)

    def test_resource_queue_allows_world_action_during_repository_check(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite3')
            check = store.create_job('owner', 'backup_check', 'Check', {}, 'check')
            store.claim()
            capture = store.create_job('owner', 'backup', 'Backup', {}, 'backup')
            action = store.create_job('owner', 'player_action', 'Player', {}, 'player')
            self.assertEqual(store.claim()['id'], action['id'])
            self.assertIsNone(store.claim())
            store.finish(check['id'], 'completed')
            self.assertIsNone(store.claim())
            store.finish(action['id'], 'completed')
            self.assertEqual(store.claim()['id'], capture['id'])
    def test_version_comes_from_archived_jar_not_cached_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'fabric-server-launcher.properties').write_text('serverJar=server.jar\n')
            with zipfile.ZipFile(root/'server.jar', 'w') as archive:
                archive.writestr('version.json', json.dumps({'id': '26.3-pre-3'}))
            self.assertEqual(game_version(root), '26.3-pre-3')

    def test_eviction_is_oldest_first_without_implicit_holds(self):
        points = [{'id': str(i), 'created': 1789000000 - i * 86400,
                   'pinned': i == 6, 'restoration': {'playable_boot_tested': i == 5}} for i in range(8)]
        self.assertEqual(retention(points, DEFAULT_POLICY), ['7', '6', '5', '4', '3', '2', '1'])

    def test_final_automatic_recovery_point_is_not_an_eviction_candidate(self):
        self.assertEqual(retention([{'id': 'a', 'created': 1}], DEFAULT_POLICY), [])
        self.assertEqual(retention([], DEFAULT_POLICY), [])

    def test_invalid_policy_and_roles_fail_closed(self):
        for bad in ({**DEFAULT_POLICY, 'enabled': 1}, {**DEFAULT_POLICY, 'keep_recent': 0}, {**DEFAULT_POLICY, 'budget_gib': 0}, {}):
            with self.assertRaises(ValueError): validate_policy(bad)
        validate('backup_delete', {'backup': 'a'*64}, 'administrator')
        with self.assertRaises(PermissionError): validate('backup_delete', {'backup': 'a'*64}, 'observer')
        with self.assertRaises(ValueError): validate('backup_edit', {'backup': '../x', 'pinned': True})
        with self.assertRaises(ValueError): validate('backup_edit', {'backup': 'a'*64, 'pinned': 'false'})

    def test_scheduler_coalesces_downtime_and_backs_off_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite3')
            status = {'ready': True, 'sampled_at': time.time(), 'policy': {**DEFAULT_POLICY, 'enabled': True}, 'backups': [], 'health': {}}
            store.set('backup_status', status)
            store.tick_backups(); store.tick_backups()
            self.assertEqual(len(store.jobs()), 1)
            job = store.claim()
            store.finish(job['id'], 'failed', error='Synthetic failure')
            store.tick_backups()
            self.assertEqual(len(store.jobs()), 1)
            status['sampled_at'] = time.time() - 120
            store.set('backup_status', status)
            store.tick_backups()
            self.assertEqual(len(store.jobs()), 1)

    def test_failed_boot_remains_eligible_for_a_bounded_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite3')
            now = time.time()
            store.set('backup_status', {'ready': True, 'sampled_at': now,
                'policy': {**DEFAULT_POLICY, 'enabled': True}, 'next_run': now + 10800,
                'health': {'data_checked': now, 'compacted': now},
                'backups': [{'id': 'a'*64, 'compatible': True, 'integrity': True, 'restorable': False,
                             'verification_failed': {'boot': True}, 'manifest': {'includes_runtime': True}, 'restoration': {}}]})
            store.tick_backups()
            self.assertEqual(store.jobs()[0]['kind'], 'verify_backup')
            job = store.claim()
            store.finish(job['id'], 'failed', error='Synthetic failure')
            store.tick_backups()
            self.assertEqual(len(store.jobs()), 1)

    def test_new_runtime_does_not_inherit_old_boot_assurance(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite3')
            now = time.time()
            current = {'id': 'a'*64, 'compatible': True, 'integrity': True,
                       'manifest': {'includes_runtime': True, 'version': 'new'}, 'restoration': {}}
            old = {**current, 'id': 'b'*64, 'manifest': {'version': 'old'},
                   'restoration': {'at': now, 'playable_boot_tested': True}}
            store.set('backup_status', {'ready': True, 'sampled_at': now,
                'policy': {**DEFAULT_POLICY, 'enabled': True}, 'next_run': now + 10800,
                'health': {'data_checked': now, 'compacted': now}, 'backups': [current, old]})
            store.tick_backups()
            self.assertEqual(store.jobs()[0]['params']['backup'], current['id'])


@unittest.skipUnless(shutil.which('restic'), 'Real restic executable required')
class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'server/world').mkdir(parents=True)
        (self.root/'server/libraries').mkdir()
        (self.root/'backups').mkdir()
        (self.root/'server/world/level.dat').write_bytes(gzip.compress(b'\x0a\x00\x00\x00'))
        (self.root/'server/world/region.mca').write_bytes(bytes(range(256))*8192)
        (self.root/'server/server.properties').write_text('rcon.password=synthetic\n')
        (self.root/'server/fabric-server-launch.jar').write_bytes(b'synthetic launcher, never executed')
        (self.root/'server/libraries/test.jar').write_bytes(b'synthetic library')
        self.runtime = Runtime(self.root, free_reserve=0)
        self.runtime.lock = lambda: nullcontext()
        self.runtime.legacy_backup_lock = lambda: nullcontext()
        self.runtime.active = Mock(return_value=False)
        self.runtime.rcon = Mock()
        self.repo = Repository(self.runtime)
        self.repo.initialize()

    def capture(self):
        return self.runtime.backup(str(uuid.uuid4()), 'Synthetic', Mock())['backup']

    def test_incremental_roundtrip_fingerprint_and_source_preservation(self):
        before = self.repo.measure()['bytes']
        first = self.capture()
        self.assertEqual(self.repo.point(first)['added_bytes'], self.repo.measure()['bytes'] - before)
        second = self.capture()
        self.assertEqual(len(self.repo.points()), 2)
        self.assertLess(self.repo.point(second)['added_bytes'], self.repo.point(first)['added_bytes'])
        result = self.runtime.verify_backup(str(uuid.uuid4()), second, Mock())
        self.assertEqual(result['level'], 'extraction')
        self.assertEqual(result['sha256'], second)
        self.assertEqual((self.root/'server/world/region.mca').read_bytes(), bytes(range(256))*8192)
        self.runtime.rcon.command.assert_not_called()
        with self.repo.archive(first) as path:
            self.assertTrue(self.runtime.inspect_archive(path)['includes_runtime'])
        self.assertEqual(self.repo.point(second)['restoration']['sha256'], second)

    def test_explicit_deletion_allows_favorites_and_latest_and_reclaims(self):
        first = self.capture(); second = self.capture()
        self.repo.annotate({'backup': first, 'pinned': True})
        self.repo.delete(first)
        self.assertEqual(len(self.repo.points()), 1)
        self.repo.delete(second)
        self.repo.compact(Mock())
        self.assertEqual(len(self.repo.points()), 0)
        self.assertFalse(self.repo.measure()['needs_reclaim'])
        self.repo.check(Mock())

    def test_policy_revision_and_compatibility_guard(self):
        identifier = self.capture()
        policy = self.repo.policy()
        self.repo.update_policy({'revision': policy['revision'], 'policy': DEFAULT_POLICY})
        with self.assertRaises(ValueError): self.repo.update_policy({'revision': policy['revision'], 'policy': DEFAULT_POLICY})
        key = self.root/'crossplay/geyser/key.pem'
        key.parent.mkdir(parents=True)
        key.write_text('synthetic external key change')
        preview = self.runtime.preview('restore_backup', {'backup': identifier, 'fingerprint': identifier})
        self.assertIn('Serviços externos mudaram', preview['impact'])

    def test_restore_replaces_only_synthetic_world_and_keeps_safety(self):
        identifier = self.capture()
        world = self.root/'server/world/region.mca'
        original = world.read_bytes()
        world.write_bytes(b'new progress')
        self.runtime.service = Mock()
        with patch('os.chown'), patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())):
            result = self.runtime.restore(str(uuid.uuid4()), {'backup': identifier, 'fingerprint': identifier}, Mock())
        self.assertEqual(world.read_bytes(), original)
        self.assertEqual(len(self.repo.points()), 1)
        self.assertEqual((self.runtime.control/'rollback'/result['rollback_retained']/'world/region.mca').read_bytes(), b'new progress')
        self.assertFalse((self.runtime.control/'restore-pending.json').exists())

    def test_full_check_recovers_missing_catalog_without_losing_snapshot(self):
        identifier = self.capture()
        (self.repo.catalog/(identifier+'.json')).unlink()
        self.repo.check(Mock())
        self.assertTrue(self.repo.point(identifier)['pinned'])
        self.assertEqual(self.repo.point(identifier)['name'], 'Synthetic')

    def test_automatic_idle_skip_and_runtime_change_detection(self):
        first = self.capture()
        result = self.repo.capture(str(uuid.uuid4()), 'Automatic', Mock(), automatic=True)
        self.assertTrue(result['skipped'])
        self.assertEqual(result['backup'], first)
        self.assertEqual(len(self.repo.points()), 1)
        self.assertGreater(self.repo.status()['health']['last_attempt'], self.repo.point(first)['created'])
        (self.runtime.server/'server.properties').write_text('rcon.password=synthetic\nmotd=Changed\n')
        result = self.repo.capture(str(uuid.uuid4()), 'Automatic', Mock(), automatic=True)
        self.assertNotIn('skipped', result)
        result = self.repo.capture(str(uuid.uuid4()), 'Automatic', Mock(), automatic=True, activity_at=time.time())
        self.assertNotIn('skipped', result)
        self.repo.delete(result['backup'])
        result = self.repo.capture(str(uuid.uuid4()), 'Automatic', Mock(), automatic=True)
        self.assertNotIn('skipped', result)

    def test_pressure_reclaims_oldest_but_keeps_valid_newest(self):
        world = self.runtime.server/'world/region.mca'
        world.write_bytes(os.urandom(2 * 1024 * 1024))
        first = self.capture()
        first_size = self.repo.measure()['bytes']
        world.write_bytes(os.urandom(2 * 1024 * 1024))
        newest = self.capture()
        self.repo.annotate({'backup': first, 'pinned': True})
        with patch.object(self.repo, 'policy', return_value={**DEFAULT_POLICY, 'budget_gib': first_size * 1.3 / 1024**3}):
            result = self.repo.compact(Mock())
        self.assertEqual(result['removed'], 1)
        self.assertEqual([p['id'] for p in self.repo.points()], [newest])
        self.assertLess(result['bytes'], first_size * 1.3)
        self.runtime.verify_backup(str(uuid.uuid4()), newest, Mock())

    def test_failed_replacement_keeps_previous_recovery_point(self):
        first = self.capture()
        command = self.repo.command
        def fail(*args, **kwargs):
            if args[0] == 'backup':
                raise RuntimeError('Synthetic storage failure')
            return command(*args, **kwargs)
        with patch.object(self.repo, 'command', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.repo.capture(str(uuid.uuid4()), 'Fail', Mock())
        self.assertEqual([p['id'] for p in self.repo.points()], [first])
        self.assertEqual(list((self.runtime.control/'staging').iterdir()), [])


if __name__ == '__main__': unittest.main()
