"""Validate recovery exports without reading any production backup."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('pull_backup', Path(__file__).resolve().parents[1] / 'scripts/pull-backup.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
export_spec = importlib.util.spec_from_file_location('export_backup', Path(__file__).resolve().parents[1] / 'scripts/export-backup.py')
exporter = importlib.util.module_from_spec(export_spec)
export_spec.loader.exec_module(exporter)
installer_spec = importlib.util.spec_from_file_location('install_control', Path(__file__).resolve().parents[1] / 'scripts/install-control.py')
installer = importlib.util.module_from_spec(installer_spec)
if os.name == 'nt':
    with patch.dict(sys.modules, {'pwd': SimpleNamespace()}):
        installer_spec.loader.exec_module(installer)
else:
    installer_spec.loader.exec_module(installer)


class ExportTests(unittest.TestCase):
    def archive(self, root, corrupt=False, extra=None):
        path = root / 'export.tar'
        files = {'control/repository.key': b'synthetic-test-key', 'control/repository/config': b'{}', 'operator/operator.sqlite3': b'fake database'}
        if extra:
            files[extra] = b'escape'
        manifest = {'files': {name: {'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)} for name, value in files.items()}}
        if corrupt:
            files['control/repository/config'] = b'changed'
        files['manifest.json'] = json.dumps(manifest).encode()
        with tarfile.open(path, 'w') as archive:
            for name, value in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
        return path

    def test_complete_archive_passes_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(len(module.verify(self.archive(root))['files']), 3)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_corruption_and_traversal_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                module.verify(self.archive(root, corrupt=True))
            with self.assertRaises(RuntimeError):
                module.verify(self.archive(root, extra='../escape'))

    def exercise_pull(self, root, ack_result=0, corrupt=False, events=None):
        source = self.archive(root, corrupt=corrupt).read_bytes()
        events = events if events is not None else []
        def execute(command, **kwargs):
            self.assertEqual(command[0], 'ssh')
            if '--ack-sha256' in command:
                events.append('ack')
                if isinstance(ack_result, Exception):
                    raise ack_result
                return subprocess.CompletedProcess(command, ack_result, b'', b'')
            events.append('download')
            kwargs['stdout'].write(source)
            return subprocess.CompletedProcess(command, 0)
        fsync, replace = os.fsync, module.durable_replace
        def flushed(descriptor):
            events.append('fsync')
            return fsync(descriptor)
        def replaced(source, target):
            events.append('archive-renamed' if str(target).endswith('.tar') else 'status-renamed')
            return replace(source, target)
        with patch.object(module, 'secure_destination', side_effect=lambda path: path.mkdir(parents=True, exist_ok=True)), \
                patch.object(module.shutil, 'disk_usage', return_value=SimpleNamespace(free=6 * 1024 ** 3)), \
                patch.object(module.subprocess, 'run', side_effect=execute), \
                patch.object(module.os, 'fsync', side_effect=flushed), \
                patch.object(module, 'durable_replace', side_effect=replaced):
            return module.pull(root / 'destination', keep=1)

    def test_copy_is_durable_before_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            root, events = Path(directory), []
            result = self.exercise_pull(root, events=events)
            self.assertTrue(result['server_acknowledged'])
            self.assertLess(events.index('fsync'), events.index('archive-renamed'))
            self.assertLess(events.index('archive-renamed'), events.index('ack'))
            self.assertEqual(json.loads((root / 'destination/latest.json').read_text())['sha256'], result['sha256'])
            self.assertEqual(list((root / 'destination').glob('*.partial')), [])

    def test_failed_ack_retains_new_archive_and_old_recovery_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'destination').mkdir()
            old = root / 'destination/oak-recovery-20000101T000000Z.tar'
            old.write_bytes(b'old recovery point')
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement failed'):
                self.exercise_pull(root, ack_result=255)
            status = json.loads((root / 'destination/latest.json').read_text())
            self.assertFalse(status['server_acknowledged'])
            self.assertTrue(Path(status['path']).exists())
            self.assertEqual(old.read_bytes(), b'old recovery point')
            self.assertEqual(len(list((root / 'destination').glob('*.tar'))), 2)

    def test_ack_timeout_is_reported_after_preserving_verified_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement failed'):
                self.exercise_pull(root, ack_result=subprocess.TimeoutExpired('ssh', 30))
            status = json.loads((root / 'destination/latest.json').read_text())
            self.assertFalse(status['server_acknowledged'])
            module.verify(Path(status['path']))

    def test_bad_download_is_never_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            root, events = Path(directory), []
            with self.assertRaises(RuntimeError):
                self.exercise_pull(root, corrupt=True, events=events)
            self.assertNotIn('ack', events)
            self.assertEqual(list((root / 'destination').glob('*.tar')), [])
            self.assertEqual(list((root / 'destination').glob('*.partial')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows security descriptor integration')
    def test_windows_backup_acl_removes_old_explicit_everyone_grants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'private'
            destination.mkdir()
            nested = destination / 'recovery.key'
            nested.write_text('synthetic key')
            hidden = {'creationflags': subprocess.CREATE_NO_WINDOW}
            subprocess.run(['icacls', str(destination), '/grant:r', '*S-1-1-0:(OI)(CI)R'], check=True, capture_output=True, **hidden)
            subprocess.run(['icacls', str(nested), '/grant:r', '*S-1-1-0:R'], check=True, capture_output=True, **hidden)
            module.secure_destination(destination)
            saved = root / 'acl.txt'
            subprocess.run(['icacls', str(destination), '/save', str(saved), '/T', '/Q'], check=True, capture_output=True, **hidden)
            descriptors = saved.read_text(encoding='utf-16-le')
            self.assertNotIn(';;;WD)', descriptors)
            self.assertNotIn(';;;S-1-1-0)', descriptors)
            self.assertIn(';;;SY)', descriptors)
            self.assertEqual(nested.read_text(), 'synthetic key')

    @unittest.skipUnless(os.name == 'posix', 'Linux repository flock and SQLite export')
    def test_export_records_per_entry_consistency_without_changing_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control, admin, operator = root / 'control', root / 'admin', root / 'operator'
            (control / 'repository/data').mkdir(parents=True)
            (control / 'repository/config').write_bytes(b'config')
            (control / 'repository/data/pack').write_bytes(b'encrypted pack')
            (control / 'repository.key').write_bytes(b'synthetic key')
            admin.mkdir()
            operator.mkdir()
            for path in (admin / 'control.sqlite3', operator / 'operator.sqlite3'):
                with sqlite3.connect(path) as database:
                    database.execute('CREATE TABLE records(value TEXT)')
                    database.execute("INSERT INTO records VALUES('unchanged')")
            (operator / 'services').mkdir()
            (operator / 'services/example.json').write_text('{"source":"pass"}')
            before = {path: path.read_bytes() for path in (admin / 'control.sqlite3', operator / 'operator.sqlite3')}
            target = root / 'export.tar'
            with patch.object(exporter, 'CONTROL', control), patch.object(exporter, 'ADMIN_STATE', admin), patch.object(exporter, 'OPERATOR_STATE', operator), target.open('wb') as stream:
                exporter.export(stream)
            manifest = module.verify(target)
            self.assertEqual(manifest['format'], 2)
            self.assertFalse(manifest['consistency']['platform_snapshot'])
            self.assertTrue(manifest['recovery']['quarantine_required'])
            self.assertEqual(set(manifest['entry_captures']), set(manifest['files']))
            self.assertEqual(manifest['entry_captures']['operator/operator.sqlite3']['method'], 'sqlite-backup')
            for path, value in before.items():
                self.assertEqual(path.read_bytes(), value)


class ControlRollbackTests(unittest.TestCase):
    def test_missing_new_worker_cannot_abort_old_release_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / 'oak-control.service'
            unit.write_bytes(b'candidate unit')
            current = Mock()
            previous = root / 'previous'
            with patch.object(installer, 'UNIT_ROOT', root), \
                    patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 5)), \
                    patch.object(installer, 'run') as run:
                errors = installer.rollback(current, previous, {unit: b'previous unit'}, False, {'active': False, 'enabled': False})
            self.assertEqual(errors, [])
            self.assertEqual(unit.read_bytes(), b'previous unit')
            current.symlink_to.assert_called_once_with(previous, target_is_directory=True)
            self.assertIn(('systemctl', 'start', 'oak-control-agent.service', 'oak-control.service'), [call.args for call in run.call_args_list])

    def test_new_worker_is_disabled_before_its_unit_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / 'oak-control-worker.service'
            worker.write_bytes(b'candidate')
            observed = []
            def run(*args):
                if args[:2] == ('systemctl', 'disable'):
                    observed.append(worker.exists())
            with patch.object(installer, 'UNIT_ROOT', root), \
                    patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(installer, 'run', side_effect=run):
                errors = installer.rollback(Mock(), root / 'previous', {worker: None}, False, {'active': False, 'enabled': False})
            self.assertEqual(errors, [])
            self.assertEqual(observed, [True])
            self.assertFalse(worker.exists())

    def test_previously_disabled_worker_remains_disabled_and_stopped(self):
        with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), patch.object(installer, 'run') as run:
            errors = installer.rollback(Mock(), Path('/previous'), {}, True, {'active': False, 'enabled': False})
        self.assertEqual(errors, [])
        calls = [call.args for call in run.call_args_list]
        self.assertIn(('systemctl', 'disable', 'oak-control-worker.service'), calls)
        self.assertNotIn(('systemctl', 'start', 'oak-control-worker.service'), calls)

    def test_rollback_continues_after_a_failed_recovery_step(self):
        with patch.object(installer.subprocess, 'run', side_effect=OSError('systemctl unavailable')), patch.object(installer, 'run') as run:
            current = Mock()
            errors = installer.rollback(current, Path('/previous'), {}, True, {'active': True, 'enabled': True})
        self.assertEqual(len(errors), 1)
        current.symlink_to.assert_called_once()
        self.assertIn(('systemctl', 'start', 'oak-control-worker.service'), [call.args for call in run.call_args_list])


if __name__ == '__main__':
    unittest.main()
