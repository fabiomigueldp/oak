"""Keep public quality updates isolated from map coverage and renderer settings."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('map_defaults', Path(__file__).resolve().parents[1] / 'scripts/map-defaults.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MapDefaultsTests(unittest.TestCase):
    def test_preserves_unrelated_config_and_map_list(self):
        config = '\n'.join(key + ': 1600 # keep comment' for key in module.DEFAULTS)
        config += '\nwebroot: "web"\nclient-decompression: true\n'
        settings = json.dumps({'version': '5.23', 'maps': ['overworld', 'another'], 'scripts': ['custom.js']})
        updated, data = module.prepare(config, settings)
        self.assertIn('hires-slider-default: 250 # keep comment', updated)
        self.assertIn('webroot: "web"\nclient-decompression: true', updated)
        self.assertEqual(json.loads(data)['maps'], ['overworld', 'another'])
        self.assertEqual(json.loads(data)['scripts'], ['custom.js'])

    def test_rejects_missing_fields_and_unreviewed_versions(self):
        with self.assertRaises(ValueError):
            module.prepare('', '{"version":"5.23"}')
        with self.assertRaises(ValueError):
            module.prepare('', '{"version":"future"}')

    def test_failed_second_write_restores_first_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'web').mkdir()
            config = root / 'config/webapp.conf'
            settings = root / 'web/settings.json'
            original = '\n'.join(key + ': 1600' for key in module.DEFAULTS)
            config.write_text(original)
            settings.write_text('{"version":"5.23","maps":["overworld"]}')
            write = module.replace_file
            def failing_write(path, content):
                if path == settings:
                    raise OSError('simulated write failure')
                return write(path, content)
            with patch.object(module, 'replace_file', failing_write):
                with self.assertRaises(OSError):
                    module.apply(root, root / 'backups')
            self.assertEqual(config.read_text(), original)
            self.assertEqual(json.loads(settings.read_text())['maps'], ['overworld'])


if __name__ == '__main__':
    unittest.main()
