"""Environment boundaries and review semantics, without a running game."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.domain import validate
from admin.environment import DEFAULTS, environment_changes
from admin.demo import DemoAgent


class EnvironmentTests(unittest.TestCase):
    def request(self, **policy):
        return {'action': 'configure', 'revision': 0, 'policy': {**copy.deepcopy(DEFAULTS), **policy}}

    def test_configuration_is_typed_and_bounded(self):
        self.assertEqual(validate('environment_apply', self.request(cycle='custom'))['policy']['cycle'], 'custom')
        for policy in ({'day': float('nan')}, {'day': float('inf')}, {'day': True}, {'night': 0},
                       {'day': '10'}, {'clear_min': 90, 'clear_max': 30}, {'storm_chance': 1.5},
                       {'rules': {'pvp': 1}}, {'rules': {'random_tick_speed': 13}},
                       {'rules': {'arbitrary_command': 'stop'}}, {'cycle': 'unknown'}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate('environment_apply', self.request(**policy))

    def test_moderators_and_observers_cannot_modify_environment(self):
        for role in ('moderator', 'observer'):
            with self.assertRaises(PermissionError):
                validate('environment_apply', self.request(), role)

    def test_overrides_require_finite_integer_duration_and_revision(self):
        good = {'action': 'override', 'revision': 0, 'weather': 'rain', 'minutes': 15}
        self.assertEqual(validate('environment_apply', good), good)
        for change in ({'minutes': 0}, {'minutes': 121}, {'minutes': True}, {'minutes': '5'},
                       {'revision': -1}, {'revision': False}, {'weather': 'snow'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate('environment_apply', {**good, **change})

    def test_review_exposes_duration_and_rule_values(self):
        demo = DemoAgent()
        changes = environment_changes(demo.environment_state,
            {'action': 'override', 'weather': 'rain', 'minutes': 15})
        self.assertEqual(changes[1]['after'], '15 minutos corridos')
        changes = environment_changes(demo.environment_state, self.request(rules={'pvp': False}))
        self.assertEqual(changes[0]['before'], 'Ativado')
        self.assertEqual(changes[0]['after'], 'Desativado')

    @patch('admin.demo.time.sleep')
    def test_demo_revision_receipt_and_expiry(self, _sleep):
        demo = DemoAgent()
        request = {'job': str(uuid.uuid4()), 'kind': 'environment_apply', 'params': self.request(cycle='custom')}
        result = demo.call('execute', request)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(demo.call('execute', request), result)
        with self.assertRaises(ValueError):
            demo.call('execute', {**request, 'job': str(uuid.uuid4())})
        demo.call('execute', {'job': str(uuid.uuid4()), 'kind': 'environment_apply',
            'params': {'action': 'override', 'revision': 1, 'weather': 'rain', 'minutes': 1}})
        self.assertEqual(demo.call('environment')['weather'], 'rain')
        demo.environment_state['override']['expires'] = 0
        self.assertNotIn('override', demo.call('environment'))


if __name__ == '__main__':
    unittest.main()
