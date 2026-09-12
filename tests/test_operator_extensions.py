"""Package isolation and service lifecycle checks; all host operations are mocked."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak_operator.packages import Packages
from oak_operator.services import Services


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        server = self.root / 'server'
        server.mkdir()
        (server / 'server.properties').write_text('level-name=world\n')
        self.runtime = SimpleNamespace(server=server, lock=nullcontext, rcon=Mock())
        self.runtime.rcon.command.return_value = 'Reloading!'
        self.packages = Packages(self.root / 'state', self.runtime)
        self.payload = {'name': 'test', 'version': '1', 'kind': 'datapack', 'files': {'pack.mcmeta': '{"pack":{"pack_format":1,"description":"test"}}', 'data/test/function/a.mcfunction': 'say test'}}

    def call(self, method, data=None):
        return self.packages.call('packages.' + method, data or {'name': 'test'}, 'test')

    def test_install_does_not_activate_or_contact_game(self):
        result = self.call('install', self.payload)
        self.assertFalse(result['active'])
        self.runtime.rcon.command.assert_not_called()
        self.assertFalse((self.runtime.server / 'world').exists())
        with self.assertRaises(ValueError):
            self.call('install', {**self.payload, 'files': {**self.payload['files'], 'new': 'different'}})

    def test_traversal_is_rejected_before_any_files_written(self):
        for name in ('../escape', '/absolute', 'a\\escape', 'a/../escape', 'a//b'):
            with self.assertRaises(ValueError):
                self.call('install', {**self.payload, 'files': {name: 'text'}})
        self.assertFalse((self.root / 'state').exists())

    def test_changed_release_files_cannot_be_activated_or_reinstalled(self):
        installed = self.call('install', self.payload)
        (Path(installed['path']) / 'pack.mcmeta').write_text('{"pack":{"pack_format":99}}')
        with self.assertRaisesRegex(ValueError, 'changed after installation'):
            self.call('activate')
        with self.assertRaisesRegex(ValueError, 'changed after installation'):
            self.call('install', self.payload)
        self.runtime.rcon.command.assert_not_called()

    def test_activate_archive_and_detect_external_change(self):
        self.call('install', self.payload)
        result = self.call('activate')
        with zipfile.ZipFile(result['target']) as archive:
            self.assertEqual(archive.read('data/test/function/a.mcfunction'), b'say test')
        self.runtime.rcon.command.assert_called_once_with('reload')
        Path(result['target']).write_bytes(b'external change')
        with self.assertRaises(ValueError):
            self.call('deactivate')
        self.assertEqual(Path(result['target']).read_bytes(), b'external change')

    def test_lost_reload_keeps_actual_disk_state(self):
        self.call('install', self.payload)
        self.runtime.rcon.command.side_effect = ConnectionError('lost acknowledgement')
        with self.assertRaises(ConnectionError):
            self.call('activate')
        result = self.call('get')
        self.assertTrue(result['active'])
        self.assertFalse(result['reload_confirmed'])
        self.assertTrue(Path(result['target']).exists())

    def test_demo_never_activates_on_host(self):
        self.packages = Packages(self.root / 'demo', self.runtime, demo=True)
        self.call('install', self.payload)
        self.assertTrue(self.call('activate')['demo'])
        self.runtime.rcon.command.assert_not_called()


class ServiceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix' and shutil.which('systemd-analyze'), 'systemd verification requires Linux')
    def test_generated_unit_passes_real_systemd_parser(self):
        with tempfile.TemporaryDirectory(prefix='oak service ') as directory:
            root = Path(directory)
            services = Services(root, unit_root=root / 'units', run=Mock(return_value=''))
            result = services.call('services.upsert', {
                'name': 'parser-test', 'kind': 'python', 'source': 'print("isolated parser test")', 'cwd': str(root),
            }, 'test')
            checked = subprocess.run(['systemd-analyze', 'verify', str(root / 'units' / result['unit'])], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_python_service_uses_daemon_interpreter_and_sdk_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services = Services(root, unit_root=root / 'units', run=Mock(return_value=''))
            result = services.call('services.upsert', {
                'name': 'sdk-test', 'kind': 'python', 'source': 'from oak_operator.sdk import Oak', 'cwd': str(root),
            }, 'test')
            unit = (root / 'units' / result['unit']).read_text()
            self.assertIn('ExecStart="' + sys.executable + '" "', unit)
            self.assertIn('Environment="PYTHONPATH=' + str(Path(__file__).resolve().parents[1]) + '"\n', unit)

    def test_managed_service_lifecycle_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Mock(return_value='')
            services = Services(root, unit_root=root / 'units', run=runner)
            result = services.call('services.upsert', {'name': 'example', 'kind': 'python', 'source': 'print(1)', 'cwd': str(root)}, 'test')
            unit = root / 'units' / result['unit']
            self.assertTrue(unit.exists())
            runner.assert_called_once_with('systemctl', 'daemon-reload')
            services.call('services.control', {'name': 'example', 'action': 'start'}, 'test')
            runner.assert_called_with('systemctl', 'start', 'oak-operator-example.service')
            services.call('services.delete', {'name': 'example'}, 'test')
            self.assertFalse(unit.exists())

    def test_demo_and_invalid_unit_do_not_call_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Mock()
            services = Services(Path(directory), demo=True, run=runner)
            services.call('services.upsert', {'name': 'example', 'source': 'echo 1'}, 'test')
            services.call('services.control', {'name': 'example', 'action': 'start'}, 'test')
            with self.assertRaises(ValueError):
                services.call('services.upsert', {'name': '../oak', 'source': 'bad'}, 'test')
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
