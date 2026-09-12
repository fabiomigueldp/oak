"""Native bridge client behavior without Minecraft or real players."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak_operator.client import OperatorError
from oak_operator.world import World


class WorldTests(unittest.TestCase):
    def test_inspection_paginates_and_stops_on_restart(self):
        world = World('/fixture')
        pages = [{'epoch': 'one', 'has_more': True, 'next_cursor': 5},
                 {'epoch': 'one', 'has_more': False, 'next_cursor': 6}]
        with patch.object(world, 'call', side_effect=pages) as call:
            self.assertEqual(len(list(world.inspect([0, 0, 0], [1, 1, 1]))), 2)
        self.assertEqual(call.call_args_list[1].args[1]['cursor'], 5)
        with patch.object(world, 'call', side_effect=[pages[0], {**pages[1], 'epoch': 'two'}]):
            with self.assertRaises(OperatorError):
                list(world.inspect([0, 0, 0], [1, 1, 1]))

    def test_apply_uses_per_page_receipts_and_keeps_item_errors(self):
        world = World('/fixture')
        pages = [{'epoch': 'one', 'has_more': True, 'next_cursor': 1, 'results': [{'error': 'Conflict'}]},
                 {'epoch': 'one', 'has_more': False, 'next_cursor': 2, 'results': [{'changed': True}]}]
        with patch.object(world, 'call', side_effect=pages) as call:
            result = list(world.apply([{'position': [0, 0, 0], 'state': 'minecraft:stone'}] * 2, idempotency='job-1'))
        self.assertEqual([c.kwargs['idempotency'] for c in call.call_args_list], ['job-1:0', 'job-1:1'])
        self.assertEqual(call.call_args_list[1].args[1]['expected_epoch'], 'one')
        self.assertEqual(result[0]['results'][0]['error'], 'Conflict')

    def test_no_progress_fails_instead_of_repeating_a_mutation(self):
        world = World('/fixture')
        with patch.object(world, 'call', return_value={'epoch': 'one', 'has_more': True, 'next_cursor': 0}) as call:
            with self.assertRaises(OperatorError):
                list(world.apply([{'position': [0, 0, 0], 'state': 'minecraft:stone'}]))
        self.assertEqual(call.call_count, 1)

    def test_unavailable_is_not_a_command_fallback(self):
        world = World('/this-fixture-does-not-exist/operator.sock')
        self.assertFalse(world.available())
        with self.assertRaises(OperatorError):
            world.call('discover')

    def test_invalid_native_payload_rejected_before_transport(self):
        world = World('/fixture')
        for method, data in [('command\n', {}), ('command', []), ('command', {'x': float('nan')})]:
            with self.assertRaises(ValueError):
                world.call(method, data)


if __name__ == '__main__':
    unittest.main()
