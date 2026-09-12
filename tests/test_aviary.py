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

    def policy(self, **changes):
        return {'action': 'policy', 'revision': 0, 'fieldPickup': True, 'discoverPublic': True,
                'maxOwnedPerches': 4, **changes}

    def test_edits_cannot_change_coordinates_or_execute_commands(self):
        for change in ({'x': 100}, {'command': 'stop'}, {'action': 'delete'}, {'shared': 1}, {'name': 'A\nB'}, {'revision': True}, {'port': '../settings'}, {'arrivalYaw': float('nan')}, {'departureYaw': 181}, {'arrivalYaw': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate('aviary_edit', self.request(**change))
        self.assertEqual(validate('aviary_edit', self.request())['arrivalYaw'], -90)

    def test_edits_require_administrative_role(self):
        for role in ('moderator', 'observer'):
            for params in (self.request(), self.policy()):
                with self.subTest(role=role, action=params['action']), self.assertRaises(PermissionError):
                    validate('aviary_edit', params, role)

    def test_network_contract_rejects_world_writes_and_coercion(self):
        for changes in ({'fieldPickup': 1}, {'discoverPublic': 'true'}, {'maxOwnedPerches': True},
                        {'maxOwnedPerches': 0}, {'maxOwnedPerches': 17}, {'maxOwnedPerches': 4.5},
                        {'maxFlights': 0}, {'maxFlights': 5}, {'maxFlights': True}, {'maxFlights': 2.5},
                        {'shortcutDistance': float('inf')}, {'shortcutDistance': True},
                        {'shortcutDistance': 99}, {'shortcutDistance': 501}, {'shortcutDistance': '500'},
                        {'port': 'harbor'}, {'enabled': False}, {'packUrl': 'https://example.com/pack.zip'},
                        {'command': 'stop'}, {'revision': -1}, {'revision': 2**53}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate('aviary_edit', self.policy(**changes))
        missing = self.policy()
        missing.pop('fieldPickup')
        with self.assertRaises(ValueError):
            validate('aviary_edit', missing)
        self.assertEqual(validate('aviary_edit', self.policy(maxFlights=4, shortcutDistance=250.5))['shortcutDistance'], 250.5)

    def test_perch_edits_cannot_replace_identity_or_invitations(self):
        for changes in ({'guests': []}, {'owner': str(uuid.uuid4())}, {'perch': {'active': False}},
                        {'color': 'gold'}, {'color': []}, {'style': 'acacia'}, {'style': {}},
                        {'hub': 1}, {'birdName': ''}, {'birdName': 'A\x7fB'}, {'birdName': 'A\x85B'},
                        {'birdName': 'A\ud800B'}, {'birdName': '🦅' * 17}, {'name': 'A\x7fB'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate('aviary_edit', self.request(**changes))
        result = validate('aviary_edit', self.request(color='light_blue', style='birch', birdName=' Fern ', hub=True))
        self.assertEqual(result['birdName'], 'Fern')
        self.assertTrue(result['hub'])

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

    @patch('admin.demo.time.sleep')
    def test_network_policy_preserves_active_routes_and_existing_destinations(self, _sleep):
        demo = DemoAgent()
        demo.aviary_state['flights'] = [{'origin': 'harbor', 'destination': 'ridge', 'phase': 'flight'}]
        demo.aviary_state['ports'][0]['busy'] = True
        original = demo.call('aviary')
        job = {'job': str(uuid.uuid4()), 'kind': 'aviary_edit', 'params': self.policy(
            fieldPickup=False, discoverPublic=False, maxOwnedPerches=1, maxFlights=1, shortcutDistance=250)}
        result = demo.call('execute', job)
        self.assertEqual(result['ports'], original['ports'])
        self.assertEqual(result['flights'], original['flights'])
        self.assertEqual(result['network'], {'fieldPickup': False, 'discoverPublic': False, 'maxOwnedPerches': 1})
        self.assertEqual((result['maxFlights'], result['shortcutDistance']), (1, 250))
        self.assertEqual(demo.call('execute', job)['revision'], 1)
        with self.assertRaises(ValueError):
            demo.call('execute', {**job, 'job': str(uuid.uuid4())})

    @patch('admin.demo.time.sleep')
    def test_appearance_update_preserves_perch_anchor_and_does_not_upgrade_legacy(self, _sleep):
        demo = DemoAgent()
        initial = demo.call('aviary')
        request = self.request(port='ridge', name='Ridge', color='blue', style='birch', birdName='Blue', hub=True)
        demo.call('execute', {'job': str(uuid.uuid4()), 'kind': 'aviary_edit', 'params': request})
        port = demo.call('aviary')['ports'][1]
        self.assertEqual(port['perch']['birdName'], 'Blue')
        for key in ('x', 'y', 'z', 'guests', 'active'):
            self.assertEqual(port['perch'][key], initial['ports'][1]['perch'][key])
        before = demo.call('aviary')
        with self.assertRaises(ValueError):
            demo.call('execute', {'job': str(uuid.uuid4()), 'kind': 'aviary_edit',
                                 'params': self.request(revision=1, color='green')})
        self.assertEqual(demo.call('aviary')['ports'], before['ports'])
        self.assertEqual(demo.call('aviary')['revision'], before['revision'])

    @patch('admin.demo.time.sleep')
    def test_busy_perch_keeps_its_configuration(self, _sleep):
        demo = DemoAgent()
        demo.aviary_state['ports'][1]['busy'] = True
        before = demo.call('aviary')
        with self.assertRaises(ValueError):
            demo.call('execute', {'job': str(uuid.uuid4()), 'kind': 'aviary_edit',
                                 'params': self.request(port='ridge', name='Changed', birdName='Other')})
        self.assertEqual(demo.call('aviary')['ports'], before['ports'])
        self.assertEqual(demo.call('aviary')['revision'], before['revision'])

    @patch('admin.demo.time.sleep')
    def test_landing_evidence_is_invalidated_by_network_revision(self, _sleep):
        demo = DemoAgent()
        inspected = demo.call('aviary', {'port': 'ridge'})
        self.assertTrue(inspected['ports'][1]['check']['clear'])
        demo.call('execute', {'job': str(uuid.uuid4()), 'kind': 'aviary_edit', 'params': self.policy()})
        self.assertNotIn('check', demo.call('aviary')['ports'][1])


if __name__ == '__main__':
    unittest.main()
