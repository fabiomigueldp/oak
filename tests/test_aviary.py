"""Aviport permissions, validation and stale-edit recovery without real game delivery."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.domain import validate
from admin.demo import DemoAgent


class AviaryTests(unittest.TestCase):
    def request(self, **changes):
        return {'action': 'edit', 'revision': 0, 'port': 'harbor', 'name': 'Harbor', 'shared': True, 'departureYaw': None, 'arrivalYaw': -90, **changes}

    def test_edits_cannot_change_coordinates_or_execute_commands(self):
        for change in ({'x': 100}, {'command': 'stop'}, {'action': 'delete'}, {'shared': 1}, {'name': 'A\nB'}, {'revision': True}, {'port': '../settings'}, {'arrivalYaw': float('nan')}, {'departureYaw': 181}, {'arrivalYaw': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate('aviary_edit', self.request(**change))
        self.assertEqual(validate('aviary_edit', self.request())['arrivalYaw'], -90)

    def test_edits_require_administrative_role(self):
        for role in ('moderator', 'observer'):
            with self.assertRaises(PermissionError):
                validate('aviary_edit', self.request(), role)

    @patch('admin.demo.time.sleep')
    def test_receipt_and_revision_prevent_duplicate_or_stale_edits(self, _sleep):
        demo = DemoAgent()
        job = {'job': str(uuid.uuid4()), 'kind': 'aviary_edit', 'params': self.request(name='New harbor')}
        result = demo.call('execute', job)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(demo.call('execute', job), result)
        with self.assertRaises(ValueError):
            demo.call('execute', {**job, 'job': str(uuid.uuid4())})
        state = demo.call('aviary', {'port': 'harbor'})
        self.assertEqual(state['ports'][0]['name'], 'New harbor')
        self.assertTrue(state['ports'][0]['check']['clear'])
        self.assertEqual(state['ports'][0]['x'], 16)


if __name__ == '__main__':
    unittest.main()
