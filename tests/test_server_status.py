"""Validate status framing with synthetic responses; never connect to Minecraft."""
import io
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import server_status as status

spec = importlib.util.spec_from_file_location('presentation', Path(__file__).resolve().parents[1] / 'scripts/server-presentation.py')
presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presentation)


class StatusTests(unittest.TestCase):
    def response(self, data):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.makefile.return_value = io.BytesIO(data)
        return patch.object(status.socket, 'create_connection', return_value=sock)

    def frame(self, data):
        content = json.dumps(data).encode()
        packet = b'\x00' + status.varint(len(content)) + content
        return status.varint(len(packet)) + packet

    def test_preserves_actual_status(self):
        expected = {'version': {'name': '26.3-pre-3', 'protocol': 1073742159},
                    'players': {'online': 4, 'max': 12}, 'description': {'text': 'Oak'}, 'favicon': 'data:image/png;base64,test'}
        with self.response(self.frame(expected)):
            self.assertEqual(status.query(), expected)
        with patch.object(status, 'query', return_value=expected.copy()):
            result = status.compatibility_status()
        self.assertEqual(result['players'], expected['players'])
        self.assertEqual(result['description'], expected['description'])
        self.assertEqual(result['favicon'], expected['favicon'])
        self.assertEqual(result['version']['protocol'], 776)

    def test_rejects_malformed_frames(self):
        for data in (b'\x00', b'\x80' * 5, status.varint(status.MAX_PACKET + 1), b'\x05\x00', b'\x02\x01\x00'):
            with self.subTest(data=data), self.response(data):
                with self.assertRaises((ValueError, EOFError)):
                    status.query()

    def test_rejects_invalid_counts(self):
        for count in (-1, True, '4', None):
            with self.subTest(count=count), self.response(self.frame({'version': {}, 'players': {'online': count, 'max': 12}})):
                with self.assertRaises(ValueError):
                    status.query()

    def test_does_not_fabricate_status_on_failure(self):
        with patch.object(status, 'query', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                status.compatibility_status()

    def test_settings_replacement_preserves_unrelated_values(self):
        original = 'motd=previous\nrcon.password=synthetic-test-value\nlevel-name=world\n'
        updated = presentation.replace_once(original, r'^motd=.*$', 'motd=' + presentation.MOTD)
        self.assertIn('rcon.password=synthetic-test-value\nlevel-name=world\n', updated)
        self.assertIn(r'\n\u00a77', updated)

    def test_settings_replacement_rejects_missing_or_duplicate_fields(self):
        for text in ('', 'motd=a\nmotd=b\n'):
            with self.assertRaises(ValueError):
                presentation.replace_once(text, r'^motd=.*$', 'motd=Oak')


if __name__ == '__main__':
    unittest.main()
