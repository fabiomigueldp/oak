"""Native effects are mocked; event replay is checked against persistent storage."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak_operator.kernel import Kernel


def page(epoch='a', cursor=1, oldest=1):
    return {'epoch': epoch, 'next': cursor, 'oldest': oldest, 'latest': cursor,
            'events': [{'id': cursor, 'name': 'player.join', 'at': 1234, 'data': {'uuid': 'test'}}]}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.kernel = Kernel(Path(self.temp.name), runtime=SimpleNamespace(lock=Mock(side_effect=nullcontext)))
        self.addCleanup(self.kernel.close)
        self.kernel.native.world = Mock()
        self.kernel.native.poller = Mock()

    def events(self):
        return self.kernel.call('events.list')['events']

    def test_native_call_preserves_key_and_audits_mutation(self):
        self.kernel.native.world.call.return_value = {'epoch': 'a', 'tick': 7, 'results': []}
        self.kernel.call('world.call', {'method': 'blocks.apply', 'data': {'blocks': []}, 'idempotency': 'k'}, actor='test')
        self.kernel.runtime.lock.assert_called_once()
        self.kernel.native.world.call.assert_called_once_with('blocks.apply', {'blocks': []}, idempotency='k')
        self.assertEqual([e['name'] for e in self.events()], ['world.call.requested', 'world.call.completed'])
        self.assertEqual(self.events()[-1]['actor'], 'test')
        self.kernel.native.world.call.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            self.kernel.call('world.call', {'method': 'command', 'data': {'command': 'time query daytime'}})
        self.assertEqual(self.events()[-1]['name'], 'world.call.unconfirmed')

    def test_cursor_and_event_are_durable_and_not_replayed(self):
        self.kernel.native.poller.call.return_value = page()
        self.kernel.native.poll()
        self.kernel.native.poll()
        self.assertEqual(len(self.events()), 1)
        self.kernel.close()
        self.kernel = Kernel(Path(self.temp.name))
        self.addCleanup(self.kernel.close)
        self.kernel.native.poller = Mock()
        self.kernel.native.poller.call.return_value = page()
        self.kernel.native.poll()
        self.assertEqual(len(self.events()), 1)
        self.kernel.native.poller.call.assert_called_once_with('events', {'after': 1, 'limit': 256})

    def test_restart_resets_cursor_and_ring_gap_is_visible(self):
        self.kernel.native.poller.call.return_value = page(cursor=4000, oldest=3990)
        self.kernel.native.poll()
        self.assertEqual(self.events()[0]['name'], 'minecraft.events_gap')
        self.kernel.native.poller.call.side_effect = [page('b', 1), page('b', 1)]
        self.kernel.native.poll()
        self.assertEqual([e['name'] for e in self.events()][-2:], ['minecraft.restarted', 'minecraft.player.join'])
        self.assertEqual(self.kernel.native.poller.call.call_args.args[1]['after'], 0)

    def test_event_ingestion_rolls_back_cursor_on_failure(self):
        self.kernel.native.poller.call.return_value = page()
        original = self.kernel._event
        self.kernel._event = Mock(side_effect=RuntimeError('disk'))
        with self.assertRaises(RuntimeError):
            self.kernel.native.poll()
        self.kernel._event = original
        self.kernel.native.poll()
        self.assertEqual(len(self.events()), 1)
        self.kernel.native.poller.call.assert_called_with('events', {'after': 0, 'limit': 256})

    def test_native_event_triggers_routine_with_data_environment(self):
        self.kernel.call('routines.upsert', {'name': 'join', 'trigger': {'type': 'event', 'name': 'minecraft.player.join'},
                         'job': {'kind': 'python', 'source': 'print("offline test")'}})
        self.kernel.native.poller.call.return_value = page()
        self.kernel.native.poll()
        self.kernel._tick()
        jobs = self.kernel.call('jobs.list')['jobs']
        self.assertEqual(len(jobs), 1)
        job = self.kernel.call('jobs.get', {'id': jobs[0]['id']})
        self.assertIn('minecraft.player.join', job['spec']['environment']['OAK_EVENT'])
        self.kernel._tick()
        self.assertEqual(len(self.kernel.call('jobs.list')['jobs']), 1)

    def test_demo_native_never_contacts_socket(self):
        self.kernel.demo = True
        self.assertTrue(self.kernel.call('world.call', {'method': 'command'})['simulated'])
        self.kernel.native.world.call.assert_not_called()

    def test_python_job_can_import_sdk_from_separate_workspace(self):
        self.kernel.native.poller.available.return_value = False
        self.kernel.start()
        job = self.kernel.call('jobs.submit', {'kind': 'python', 'source': 'from oak_operator.sdk import Oak\nfrom oak_operator.world import World\nprint("sdk-import-ok")'})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.kernel.call('jobs.get', {'id': job['id']})
            if result['status'] in ('succeeded', 'failed'):
                break
            time.sleep(.1)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn('sdk-import-ok', result['output'])


if __name__ == '__main__':
    unittest.main()
