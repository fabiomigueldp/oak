"""Skin traffic, cache bounds and hostile asset handling; all network calls mocked."""
import base64
import http.client
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zlib

spec = importlib.util.spec_from_file_location('activity_skins', Path(__file__).resolve().parents[1] / 'server/activity_skins.py')
skins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skins)
UID = '00000000-0000-4000-8000-000000000001'
KEY = 'a' * 64


def png(width=64, height=64, color=6):
    header = b'IHDR' + struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + header + struct.pack('>I', zlib.crc32(header))


def profile(key=KEY, uid=UID, url=None):
    value = base64.b64encode(json.dumps({'textures': {'SKIN': {'url': url or 'https://textures.minecraft.net/texture/' + key}}}).encode()).decode()
    return json.dumps({'id': uid.replace('-', ''), 'properties': [{'name': 'textures', 'value': value}]}).encode()


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


class NetworkTests(unittest.TestCase):
    def test_fixed_endpoints_only(self):
        for url in ('http://textures.minecraft.net/texture/' + KEY,
                    'https://textures.minecraft.net.evil.test/texture/' + KEY,
                    'https://textures.minecraft.net@evil.test/texture/' + KEY,
                    'https://textures.minecraft.net/texture/../secret',
                    'https://127.0.0.1/texture/' + KEY):
            with self.subTest(url=url), patch.object(skins.urllib.request, 'build_opener') as opener:
                with self.assertRaises(ValueError):
                    skins.fetch(url, 64)
                opener.assert_not_called()

    def test_size_and_redirect_response_are_rejected(self):
        url = 'https://textures.minecraft.net/texture/' + KEY
        for body, result_url in ((b'x' * 65, url), (png(), 'https://evil.test/skin.png')):
            with self.subTest(result_url=result_url), patch.object(skins.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = Response(body, result_url)
                with self.assertRaises(ValueError):
                    skins.fetch(url, 64)
                self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 4)
        self.assertIsNone(skins.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_redirect_handler_rejects_http_redirect(self):
        opener = skins.urllib.request.build_opener(skins.NoRedirect)
        req = skins.urllib.request.Request('https://textures.minecraft.net/texture/' + KEY)
        with self.assertRaises(urllib.error.HTTPError):
            # Exercise the complete error-handler chain without opening a socket.
            opener.error('http', req, io.BytesIO(), 302, 'redirect', {'location': 'https://evil.test/skin'})

    def test_only_supported_png_headers(self):
        for height in (32, 64):
            self.assertEqual(skins.validate_png(png(height=height)), png(height=height))
        for body in (b'not a png', png(width=128), png(height=128), png(color=5),
                     png()[:-1] + b'\x00', png() + b'x' * skins.PNG_LIMIT):
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                skins.validate_png(body)

    def test_profile_identity_and_texture_host(self):
        self.assertEqual(skins.profile_skin(profile(), UID), KEY)
        for body in (profile(uid='00000000-0000-4000-8000-000000000002'),
                     profile(url='https://textures.minecraft.net.evil.test/texture/' + KEY),
                     profile(url='https://textures.minecraft.net:443/texture/' + KEY),
                     profile(url='https://textures.minecraft.net/texture/' + KEY + '?redirect=1'),
                     b'{}'):
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                skins.profile_skin(body, UID)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1800000000.0
        self.cache = self.make_cache()
        self.player = {'id': 'uuid:' + UID, 'uuid': UID, 'name': 'SyntheticPlayer', 'skin': ''}

    def make_cache(self, **kwargs):
        return skins.SkinCache(self.root / 'state', self.root / 'public', clock=lambda: self.now,
                               start_worker=False, **kwargs)

    def drain(self):
        while self.cache._run_once():
            pass

    def test_local_hash_fetches_once_and_only_publishes_after_success(self):
        player = dict(self.player, skin=KEY)
        with patch.object(skins, 'fetch', return_value=png()) as fetch:
            self.cache.submit([player])
            self.cache.submit([player])
            self.assertEqual(self.cache.queue.qsize(), 1)
            self.assertEqual(self.cache.metadata([player])[0]['skin'], '')
            self.drain()
            self.assertEqual(self.cache.metadata([player])[0]['skin'], KEY)
            self.assertEqual(player['skin'], KEY)
            self.cache.submit([player])
            self.drain()
            fetch.assert_called_once_with('https://textures.minecraft.net/texture/' + KEY, skins.PNG_LIMIT)
            self.assertEqual((self.root / 'public' / (KEY + '.png')).read_bytes(), png())

    def test_profile_and_skin_are_persistent_and_deduplicated(self):
        with patch.object(skins, 'fetch', side_effect=[profile(), png()]) as fetch:
            self.cache.submit([self.player] * 10)
            self.drain()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(self.cache.metadata([self.player])[0]['skin'], KEY)
        restored = self.make_cache()
        restored.submit([self.player])
        self.assertEqual(restored.queue.qsize(), 0)
        self.assertEqual(restored.metadata([self.player])[0]['skin'], KEY)
        self.now += 7 * skins.DAY + 1
        restored.submit([self.player])
        self.assertEqual(restored.queue.qsize(), 1)

    def test_missing_or_invalid_uuid_never_requests_a_profile(self):
        self.cache.submit([dict(self.player, uuid=None), dict(self.player, uuid='https://evil.test')])
        self.assertEqual(self.cache.queue.qsize(), 0)

    def test_failed_profile_and_png_have_six_hour_cooldowns(self):
        with patch.object(skins, 'fetch', side_effect=OSError('synthetic unavailable')) as fetch:
            self.cache.submit([self.player, dict(self.player, uuid=None, skin=KEY)])
            self.drain()
            self.assertEqual(fetch.call_count, 2)
            restored = self.make_cache()
            restored.submit([self.player, dict(self.player, uuid=None, skin=KEY)])
            self.assertEqual(restored.queue.qsize(), 0)
            self.now += 6 * 3600 + 1
            restored.submit([self.player, dict(self.player, uuid=None, skin=KEY)])
            self.assertEqual(restored.queue.qsize(), 2)

    def test_failed_refresh_preserves_previous_skin_and_retries_in_six_hours(self):
        with patch.object(skins, 'fetch', side_effect=[profile(), png()]):
            self.cache.submit([self.player])
            self.drain()
        self.now += 7 * skins.DAY + 1
        with patch.object(skins, 'fetch', side_effect=OSError('synthetic unavailable')):
            self.cache.submit([self.player])
            self.drain()
        self.assertEqual(self.cache.metadata([self.player])[0]['skin'], KEY)
        self.now += 6 * 3600 + 1
        self.cache.submit([self.player])
        self.assertEqual(self.cache.queue.qsize(), 1)

    def test_queue_and_asset_count_are_bounded(self):
        self.cache = self.make_cache(max_pending=3, max_files=2)
        players = [dict(self.player, uuid=None, skin=f'{number:064x}') for number in range(200)]
        self.cache.submit(players)
        self.assertEqual(len(self.cache.pending), 3)
        with patch.object(skins, 'fetch', return_value=png()):
            self.drain()
        self.assertEqual(len(list((self.root / 'public').glob('*.png'))), 2)
        self.assertEqual(len(self.cache.files), 2)

    def test_invalid_png_never_reaches_public_path(self):
        with patch.object(skins, 'fetch', return_value=png(width=512)):
            self.cache.submit([dict(self.player, skin=KEY)])
            self.drain()
        self.assertEqual(self.cache.metadata([self.player])[0]['skin'], '')
        self.assertFalse(list((self.root / 'public').glob('*.png')))

    def test_interrupted_response_does_not_stop_later_work(self):
        second = 'b' * 64
        self.cache.submit([dict(self.player, uuid=None, skin=KEY), dict(self.player, uuid=None, skin=second)])
        with patch.object(skins, 'fetch', side_effect=[http.client.IncompleteRead(b'partial'), png()]):
            self.drain()
        self.assertIn(KEY, self.cache.failures)
        self.assertIn(second, self.cache.files)

    def test_deep_invalid_profile_is_throttled(self):
        self.cache.submit([self.player])
        with patch.object(skins, 'fetch', return_value=b'[' * 1100 + b'0' + b']' * 1100):
            self.drain()
        self.cache.submit([self.player])
        self.assertEqual(self.cache.queue.qsize(), 0)
        self.assertEqual(self.cache.metadata([self.player])[0]['skin'], '')

    def test_local_metadata_supersedes_queued_remote_lookup(self):
        self.cache.submit([self.player])
        self.cache.submit([dict(self.player, skin=KEY)])
        with patch.object(skins, 'fetch', return_value=png()) as fetch:
            self.drain()
            fetch.assert_called_once_with('https://textures.minecraft.net/texture/' + KEY, skins.PNG_LIMIT)

    def test_background_worker_finishes_without_collector_io(self):
        import threading
        finished = threading.Event()
        self.cache = skins.SkinCache(self.root / 'thread-state', self.root / 'thread-public')
        self.addCleanup(self.cache.close)
        def download(*args):
            finished.set()
            return png()
        with patch.object(skins, 'fetch', side_effect=download):
            self.cache.submit([dict(self.player, skin=KEY)])
            self.assertTrue(finished.wait(2))
            self.cache.queue.join()
        self.assertEqual(self.cache.metadata([self.player])[0]['skin'], KEY)


if __name__ == '__main__':
    unittest.main()
