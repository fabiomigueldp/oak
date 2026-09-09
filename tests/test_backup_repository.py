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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.backup_repository import Repository, DEFAULT_POLICY, retention, validate_policy
from admin.domain import validate
from admin.runtime import Runtime
from admin.store import Store


class PolicyTests(unittest.TestCase):
    def test_retention_preserves_pins_latest_and_last_boot(self):
        points = [{'id': str(i), 'created': 1789000000 - i * 86400,
                   'pinned': i == 6, 'restoration': {'playable_boot_tested': i == 5}} for i in range(8)]
        self.assertEqual(retention(points, {**DEFAULT_POLICY, 'keep_recent': 2, 'keep_daily': 0, 'keep_weekly': 0}), ['2', '3', '4', '7'])

    def test_retention_calendar_buckets_keep_newest_per_day(self):
        points = [{'id': str(i), 'created': 1789000000 - i * 3600, 'restoration': {}} for i in range(60)]
        removed = retention(points, {**DEFAULT_POLICY, 'keep_recent': 2, 'keep_daily': 2, 'keep_weekly': 0})
        self.assertNotIn('0', removed)
        self.assertNotIn('1', removed)
        self.assertLess(len(points) - len(removed), 5)

    def test_invalid_policy_and_roles_fail_closed(self):
        for bad in ({**DEFAULT_POLICY, 'enabled': 1}, {**DEFAULT_POLICY, 'keep_recent': 0}, {**DEFAULT_POLICY, 'budget_gib': 1000}, {}):
            with self.assertRaises(ValueError): validate_policy(bad)
        with self.assertRaises(PermissionError): validate('backup_delete', {'backup': 'a'*64}, 'administrator')
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

    def test_protected_delete_and_explicit_reclamation(self):
        first = self.capture(); second = self.capture()
        self.repo.annotate({'backup': first, 'pinned': True})
        with self.assertRaises(ValueError): self.repo.delete(first)
        with self.assertRaises(ValueError): self.repo.delete(second)
        self.repo.annotate({'backup': first, 'pinned': False})
        self.repo.delete(first)
        self.assertEqual(len(self.repo.points()), 1)
        self.repo.check(Mock())

    def test_policy_revision_and_compatibility_guard(self):
        identifier = self.capture()
        policy = self.repo.policy()
        self.repo.update_policy({'revision': policy['revision'], 'policy': DEFAULT_POLICY})
        with self.assertRaises(ValueError): self.repo.update_policy({'revision': policy['revision'], 'policy': DEFAULT_POLICY})
        key = self.root/'crossplay/geyser/key.pem'
        key.parent.mkdir(parents=True)
        key.write_text('synthetic external key change')
        with self.assertRaises(ValueError): self.runtime.preview('restore_backup', {'backup': identifier, 'fingerprint': identifier})

    def test_restore_replaces_only_synthetic_world_and_keeps_safety(self):
        identifier = self.capture()
        world = self.root/'server/world/region.mca'
        original = world.read_bytes()
        world.write_bytes(b'new progress')
        self.runtime.service = Mock()
        with patch('os.chown'), patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())):
            result = self.runtime.restore(str(uuid.uuid4()), {'backup': identifier, 'fingerprint': identifier}, Mock())
        self.assertEqual(world.read_bytes(), original)
        self.assertNotEqual(result['safety_backup'], identifier)
        self.assertEqual(len(self.repo.points()), 2)
        self.assertFalse((self.runtime.control/'restore-pending.json').exists())

    def test_full_check_recovers_missing_catalog_without_losing_snapshot(self):
        identifier = self.capture()
        (self.repo.catalog/(identifier+'.json')).unlink()
        self.repo.check(Mock())
        self.assertTrue(self.repo.point(identifier)['pinned'])
        self.assertEqual(self.repo.point(identifier)['name'], 'Synthetic')


if __name__ == '__main__': unittest.main()
