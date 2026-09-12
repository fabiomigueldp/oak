"""Synthetic presence observations; never contact Minecraft, profiles or players."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import collect as collector
from collect import online_players
from presence import DAY, PlayerMetadata, PresenceHistory, day_start, identity, texture_hash, utc_day

BASE = day_start('2026-09-10') + 12 * 3600
PLAYER = {'name': 'OakPlayer', 'uuid': '12345678-1234-1234-1234-123456789abc'}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.history = self.make()

    def tearDown(self):
        if self.history:
            self.history.close()
        self.temporary.cleanup()

    def make(self, **kwargs):
        return PresenceHistory(self.root / 'state', self.root / 'public', **kwargs)

    def day(self, stamp=BASE):
        self.history.flush()
        return json.loads((self.root / 'public' / (utc_day(stamp) + '.json')).read_text())

    def sessions(self, stamp=BASE):
        return [session for player in self.day(stamp)['players'] for session in player['sessions']]

    def test_observed_join_and_leave_use_last_confirmed_presence(self):
        self.history.observe([], BASE)
        self.history.observe([PLAYER], BASE + 5)
        self.history.observe([PLAYER], BASE + 10)
        self.history.observe([], BASE + 15)
        session, = self.sessions()
        self.assertEqual((session['start'], session['end']), (BASE + 5, BASE + 10))
        self.assertEqual(session['endReason'], 'left')
        self.assertFalse(session['open'])
        self.assertEqual(self.day()['coverage'], [[BASE, BASE + 15]])

    def test_failure_is_unknown_and_does_not_bridge_played_time(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 5)
        self.history.observe(None, BASE + 10)
        self.history.observe([PLAYER], BASE + 15)
        self.history.observe([PLAYER], BASE + 20)
        first, second = self.sessions()
        self.assertEqual((first['end'], first['endReason']), (BASE + 5, 'unknown'))
        self.assertEqual(second['start'], BASE + 15)
        self.assertTrue(second['open'])
        self.assertEqual(self.day()['coverage'], [[BASE, BASE + 5], [BASE + 15, BASE + 20]])

    def test_excessive_sampling_gap_splits_even_with_same_player(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 20)
        self.history.observe([PLAYER], BASE + 41)
        first, second = self.sessions()
        self.assertEqual(first['end'], BASE + 20)
        self.assertEqual(second['start'], BASE + 41)
        self.assertEqual(first['endReason'], 'unknown')

    def test_short_restart_keeps_session_identity(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 5)
        session_id = self.sessions()[0]['id']
        self.history.close()
        self.history = self.make()
        self.history.observe([PLAYER], BASE + 10)
        session, = self.sessions()
        self.assertEqual(session['id'], session_id)
        self.assertEqual((session['start'], session['end']), (BASE, BASE + 10))

    def test_long_restart_closes_at_last_observation(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 5)
        self.history.close()
        self.history = self.make()
        self.history.observe([PLAYER], BASE + 100)
        first, second = self.sessions()
        self.assertEqual(first['end'], BASE + 5)
        self.assertEqual(first['endReason'], 'unknown')
        self.assertEqual(second['start'], BASE + 100)

    def test_crash_loses_only_uncommitted_observations_without_inventing_time(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 5)
        self.history.db.close()
        self.history = self.make()
        self.history.observe([PLAYER], BASE + 35)
        first, second = self.sessions()
        self.assertEqual(first['end'], BASE)
        self.assertEqual(second['start'], BASE + 35)

    def test_midnight_clips_one_session_into_consistent_daily_shards(self):
        midnight = day_start('2026-09-11')
        for stamp in (midnight - 10, midnight - 5, midnight, midnight + 5):
            self.history.observe([PLAYER], stamp)
        before = self.sessions(midnight - 1)[0]
        after = self.sessions(midnight)[0]
        self.assertEqual(before['id'], after['id'])
        self.assertEqual(before['end'], midnight)
        self.assertTrue(before['continuesAfter'])
        self.assertFalse(before['open'])
        self.assertEqual(after['start'], midnight)
        self.assertTrue(after['continuedBefore'])
        self.assertTrue(after['open'])
        self.assertEqual(self.day(midnight)['coverage'], [[midnight, midnight + 5]])

    def test_retention_removes_public_files_and_closed_database_rows(self):
        self.history.close()
        self.history = self.make(retention_days=3)
        for offset in range(5):
            self.history.observe([PLAYER], BASE + offset * DAY)
            self.history.observe([], BASE + offset * DAY + 5)
        index = json.loads((self.root / 'public/index.json').read_text())
        self.assertEqual(index['days'], ['2026-09-12', '2026-09-13', '2026-09-14'])
        self.assertFalse((self.root / 'public/2026-09-10.json').exists())
        self.assertEqual(self.history.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 3)

    def test_publication_is_throttled_except_transitions(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([PLAYER], BASE + 5)
        path = self.root / 'public/index.json'
        self.assertEqual(json.loads(path.read_text())['updated'], BASE)
        for offset in range(10, 31, 5):
            self.history.observe([PLAYER], BASE + offset)
        self.assertEqual(json.loads(path.read_text())['updated'], BASE + 30)
        self.history.observe([], BASE + 35)
        self.assertEqual(json.loads(path.read_text())['updated'], BASE + 35)

    def test_restart_after_retention_discards_expired_open_session(self):
        self.history.observe([PLAYER], BASE)
        self.history.close()
        self.history = self.make()
        self.history.observe([], BASE + 100 * DAY)
        self.assertEqual(self.history.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(self.history.db.execute('SELECT COUNT(*) FROM players').fetchone()[0], 0)

    def test_session_bounds_are_honest_and_reset_next_day(self):
        self.history.close()
        self.history = self.make(max_sessions=2)
        for offset in range(3):
            self.history.observe([PLAYER], BASE + offset * 10)
            self.history.observe([], BASE + offset * 10 + 5)
        self.assertEqual(len(self.sessions()), 2)
        self.assertTrue(self.day()['truncated'])
        self.history.observe([PLAYER], BASE + DAY)
        self.assertEqual(len(self.sessions(BASE + DAY)), 1)
        self.assertFalse(self.day(BASE + DAY)['truncated'])

    def test_coverage_and_identity_bounds_mark_truncation(self):
        self.history.close()
        self.history = self.make(max_coverage=1, max_identities=1)
        self.history.observe([PLAYER, {'name': 'AnotherPlayer'}], BASE)
        self.history.observe(None, BASE + 5)
        self.history.observe([PLAYER], BASE + 10)
        document = self.day()
        self.assertEqual(len(document['players']), 1)
        self.assertEqual(document['coverage'], [[BASE, BASE]])
        self.assertTrue(document['truncated'])

    def test_late_uuid_resolution_preserves_existing_session(self):
        self.history.observe([{'name': PLAYER['name']}], BASE)
        session_id = self.sessions()[0]['id']
        self.history.observe([PLAYER], BASE + 5)
        document = self.day()
        self.assertEqual(len(document['players']), 1)
        self.assertEqual(document['players'][0]['id'], 'uuid:' + PLAYER['uuid'])
        self.assertEqual(document['players'][0]['sessions'][0]['id'], session_id)

    def test_empty_initial_history_does_not_invent_prior_observation(self):
        self.history.observe(None, BASE)
        index = json.loads((self.root / 'public/index.json').read_text())
        self.assertIsNone(index['observedSince'])
        self.assertFalse(index['collecting'])
        self.assertEqual(self.day()['coverage'], [])
        self.assertEqual(self.day()['players'], [])

    def test_missing_optional_metadata_keeps_previously_known_uuid(self):
        self.history.observe([PLAYER], BASE)
        self.history.observe([{'name': PLAYER['name']}], BASE + 5)
        player, = self.day()['players']
        self.assertEqual(player['uuid'], PLAYER['uuid'])
        self.assertEqual(len(player['sessions']), 1)

    def test_rejects_invalid_snapshots_without_changing_last_good_state(self):
        self.history.observe([PLAYER], BASE)
        for players in ([PLAYER] * 101, [PLAYER, PLAYER], [{'name': 'line\nbreak'}]):
            with self.subTest(players=players), self.assertRaises(ValueError):
                self.history.observe(players, BASE + 5)
        with self.assertRaises(ValueError):
            self.history.observe([], BASE - 1)
        self.assertEqual(self.sessions()[0]['end'], BASE)

    def test_restart_recovers_json_interruption_after_database_commit(self):
        self.history.observe([PLAYER], BASE)
        with patch.object(self.history, '_atomic', side_effect=OSError('Synthetic publication failure')):
            with self.assertRaises(OSError):
                self.history.observe([], BASE + 5)
        self.history.db.close()
        self.history = self.make()
        self.history.observe([], BASE + 10)
        self.assertFalse(self.sessions()[0]['open'])

    def test_normal_restart_does_not_republish_historical_shards(self):
        for offset in range(3):
            self.history.observe([PLAYER], BASE + offset * DAY)
            self.history.observe([], BASE + offset * DAY + 5)
        self.history.close()
        self.history = self.make()
        self.assertEqual(self.history.dirty, set())
        historical = {path: path.read_bytes() for path in (self.root / 'public').glob('2026-09-1[01].json')}
        with patch.object(self.history, '_atomic', wraps=self.history._atomic) as publication:
            self.history.observe([], BASE + 2 * DAY + 10)
            self.history.flush()
        self.assertEqual({call.args[0].name for call in publication.call_args_list}, {'2026-09-12.json', 'index.json'})
        self.assertTrue(all(path.read_bytes() == raw for path, raw in historical.items()))

    def test_failed_publication_retries_on_next_sample_without_restart(self):
        self.history.observe([PLAYER], BASE)
        with patch.object(self.history, '_atomic', side_effect=OSError('Synthetic publication interruption')):
            with self.assertRaises(OSError):
                self.history.observe([], BASE + 5)
        self.history.observe([], BASE + 10)
        index = json.loads((self.root / 'public/index.json').read_text())
        self.assertEqual(index['updated'], BASE + 10)
        self.assertFalse(self.history.needs_recovery)

    def test_publication_journal_recovers_midnight_without_advertising_missing_days(self):
        midnight = day_start('2026-09-11')
        self.history.observe([PLAYER], midnight - 10)
        self.history.observe([PLAYER], midnight - 5)
        with patch.object(self.history, '_atomic', side_effect=OSError('Synthetic midnight interruption')):
            with self.assertRaises(OSError):
                self.history.observe([PLAYER], midnight)
        index = json.loads((self.root / 'public/index.json').read_text())
        self.assertEqual(index['days'], ['2026-09-10'])
        self.assertFalse((self.root / 'public/2026-09-11.json').exists())
        self.history.db.close()
        self.history = self.make()
        self.assertEqual(self.history.dirty, {'2026-09-10', '2026-09-11'})
        self.history.observe([PLAYER], midnight + 5)
        self.assertFalse(self.history.needs_recovery)
        self.assertEqual(self.history.db.execute('SELECT COUNT(*) FROM publication').fetchone()[0], 0)
        index = json.loads((self.root / 'public/index.json').read_text())
        self.assertEqual(index['days'], ['2026-09-10', '2026-09-11'])
        self.assertTrue(all((self.root / 'public' / (day + '.json')).exists() for day in index['days']))
        before, after = self.sessions(midnight - 1)[0], self.sessions(midnight)[0]
        self.assertFalse(before['open'])
        self.assertTrue(before['continuesAfter'])
        self.assertTrue(after['open'])
        self.assertEqual(before['id'], after['id'])


class InputTests(unittest.TestCase):
    def test_status_log_failure_does_not_prevent_presence_collection(self):
        response = subprocess.CompletedProcess([], 0, 'There are 1 of a max of 12 players online: OakPlayer')
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, 'PUBLIC', Path(directory)), \
                patch.object(collector.subprocess, 'run', return_value=response), \
                patch.object(collector, 'PresenceHistory') as history, patch.object(collector, 'PlayerMetadata') as metadata, \
                patch.object(collector, 'SkinCache') as skins, patch.object(collector.signal, 'signal'), \
                patch.object(collector, 'collect_status', side_effect=OSError('Synthetic log failure')), \
                patch.object(collector.time, 'sleep', side_effect=SystemExit), patch('builtins.print'):
            metadata.return_value.resolve.return_value = [PLAYER]
            skins.return_value.metadata.return_value = [PLAYER]
            with self.assertRaises(SystemExit):
                collector.main()
            history.return_value.observe.assert_called_once_with([PLAYER])
            history.return_value.close.assert_called_once()
            skins.return_value.close.assert_called_once()

    def test_rcon_timeout_records_unknown_without_real_server_contact(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, 'PUBLIC', Path(directory)), \
                patch.object(collector.subprocess, 'run', side_effect=subprocess.TimeoutExpired('mock', 5)), \
                patch.object(collector, 'PresenceHistory') as history, patch.object(collector.signal, 'signal'), \
                patch.object(collector, 'collect_status'), patch.object(collector, 'SkinCache') as skins, \
                patch.object(collector.time, 'sleep', side_effect=SystemExit), patch('builtins.print'):
            with self.assertRaises(SystemExit):
                collector.main()
            history.return_value.observe.assert_called_once_with(None)
            skins.assert_not_called()

    def test_rcon_error_or_incomplete_list_is_unknown(self):
        for code, text in ((1, 'There are 0 of a max of 12 players online:'), (0, 'connection refused'),
                           (0, 'There are 2 of a max of 12 players online: OakPlayer'),
                           (0, 'There are 2 of a max of 12 players online: Same, Same')):
            with self.subTest(code=code, text=text):
                self.assertIsNone(online_players(subprocess.CompletedProcess([], code, text)))
        result = subprocess.CompletedProcess([], 0, 'There are 0 of a max of 12 players online:')
        self.assertEqual(online_players(result), {'players': [], 'maximum': 12})

    def test_bedrock_display_name_is_preserved(self):
        result = subprocess.CompletedProcess([], 0, 'There are 2 of a max of 12 players online: OakPlayer, .Bedrock Player')
        self.assertEqual(online_players(result)['players'], ['OakPlayer', '.Bedrock Player'])

    def test_only_allowlisted_texture_hash_leaves_profile(self):
        digest = 'a' * 64
        def encoded(url):
            return {'textures': base64.b64encode(json.dumps({'textures': {'SKIN': {'url': url}}, 'secret': 'synthetic'}).encode()).decode()}
        self.assertEqual(texture_hash(encoded('https://textures.minecraft.net/texture/' + digest)), digest)
        for url in ('https://example.com/' + digest, 'https://textures.minecraft.net.evil/texture/' + digest,
                    'https://textures.minecraft.net/texture/' + digest + '?token=secret'):
            self.assertEqual(texture_hash(encoded(url)), '')
        self.assertEqual(texture_hash({'textures': '!invalid!'}), '')
        self.assertEqual(set(identity(dict(PLAYER, addresses=['private'], textures='private'))), {'id', 'uuid', 'name', 'skin'})

    def test_usercache_lookup_is_bounded_and_does_not_require_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'usercache.json').write_text(json.dumps([dict(PLAYER, expiresOn='private')]))
            resolver = PlayerMetadata(path / 'usercache.json', path / 'absent.sock')
            player, = resolver.resolve([PLAYER['name']], BASE)
            self.assertEqual(player['uuid'], PLAYER['uuid'])
            self.assertNotIn('expiresOn', player)
            (path / 'usercache.json').write_text('not json')
            self.assertEqual(resolver.resolve([PLAYER['name']], BASE + 35)[0]['uuid'], PLAYER['uuid'])


if __name__ == '__main__':
    unittest.main()
