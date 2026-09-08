"""Synthetic wire frames only; never contact the game or real players."""
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.telemetry import decode


class FrameTests(unittest.TestCase):
    def frame(self):
        return {'version': 1, 'tick': 2, 'sampled_at': time.time(), 'players': [
            {'uuid': '00000000-0000-4000-8000-000000000001', 'name': 'Synthetic',
             'position': [1, 64, 2], 'dimension': 'minecraft:overworld', 'yaw': 90}]}

    def test_valid_frame_and_empty_presence(self):
        data = self.frame()
        self.assertEqual(decode(json.dumps(data))['source'], 'fabric')
        data['players'] = []
        self.assertEqual(decode(json.dumps(data))['players'], [])

    def test_expired_invalid_and_duplicate_frames(self):
        for mutate in (
            lambda d: d.update(sampled_at=time.time() - 10),
            lambda d: d.update(sampled_at=float('nan')),
            lambda d: d['players'][0].update(position=[float('nan'), 0, 0]),
            lambda d: d['players'][0].update(position=[True, 0, 0]),
            lambda d: d['players'].append(d['players'][0].copy()),
        ):
            data = self.frame()
            mutate(data)
            with self.assertRaises(ValueError):
                decode(json.dumps(data))


if __name__ == '__main__':
    unittest.main()
