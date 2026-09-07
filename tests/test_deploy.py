"""Exercise deployment rollback and runtime preservation in a temporary directory."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).resolve().parents[1] / 'scripts/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        (self.repo / 'public').mkdir(parents=True)
        self.live = root / 'live'
        self.live.mkdir()
        (self.repo / 'public/index.html').write_text('new release')
        (self.live / 'index.html').write_text('previous release')
        (self.live / 'status.json').write_text('runtime state')
        self.state = root / 'deployments'
        for name, value in [('REPO', self.repo), ('STATE', self.state),
                            ('TARGETS', {'public/index.html': self.live / 'index.html'})]:
            p = patch.object(deploy, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name in ('run', 'activate'):
            p = patch.object(deploy, name)
            p.start()
            self.addCleanup(p.stop)
        for name, value in [('geteuid', lambda: 0), ('chown', lambda *args: None)]:
            p = patch.object(deploy.os, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_success_preserves_runtime_and_records_commit(self):
        with patch.object(deploy, 'health'):
            deploy.main('a' * 40)
        self.assertEqual((self.live / 'index.html').read_text(), 'new release')
        self.assertEqual((self.live / 'status.json').read_text(), 'runtime state')
        self.assertEqual(json.loads((self.state / 'current.json').read_text())['commit'], 'a' * 40)

    def test_failed_health_restores_previous_files(self):
        with patch.object(deploy, 'health', side_effect=[RuntimeError('unhealthy candidate'), None]):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy candidate'):
                deploy.main('b' * 40)
        self.assertEqual((self.live / 'index.html').read_text(), 'previous release')
        self.assertEqual((self.live / 'status.json').read_text(), 'runtime state')
        self.assertFalse((self.state / 'current.json').exists())

    def test_new_assets_are_installed_and_recorded_as_previously_absent(self):
        (self.repo / 'public/map-profile.js').write_text('profile')
        deploy.TARGETS['public/map-profile.js'] = self.live / 'map-profile.js'
        with patch.object(deploy, 'health'):
            deploy.main('c' * 40)
        self.assertEqual((self.live / 'map-profile.js').read_text(), 'profile')
        manifest = json.loads(next(self.state.glob('*/manifest.json')).read_text())
        self.assertFalse(manifest['files']['public/map-profile.js']['existed'])

    def test_failed_release_removes_only_newly_introduced_assets(self):
        (self.repo / 'public/map-profile.js').write_text('profile')
        deploy.TARGETS['public/map-profile.js'] = self.live / 'map-profile.js'
        with patch.object(deploy, 'health', side_effect=[RuntimeError('profile missing'), None]):
            with self.assertRaisesRegex(RuntimeError, 'profile missing'):
                deploy.main('d' * 40)
        self.assertFalse((self.live / 'map-profile.js').exists())
        self.assertEqual((self.live / 'index.html').read_text(), 'previous release')
        self.assertEqual((self.live / 'status.json').read_text(), 'runtime state')

if __name__ == '__main__':
    unittest.main()
