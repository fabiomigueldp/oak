"""Bounded background skin assets for static public history; no visitor lookups."""
import base64
import http.client
import json
import os
from pathlib import Path
import queue
import re
import struct
import threading
import time
import urllib.request
import uuid
import zlib

HASH = re.compile(r'[a-f0-9]{32,64}')
TEXTURE = re.compile(r'https?://textures\.minecraft\.net/texture/([a-f0-9]{32,64})')
DAY = 86400
PNG_LIMIT = 65536
PROFILE_LIMIT = 32768


def skin_hash(value):
    return value if isinstance(value, str) and HASH.fullmatch(value) else ''


def player_uuid(player):
    try:
        return str(uuid.UUID(str(player.get('uuid', ''))))
    except (ValueError, AttributeError):
        return ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, limit):
    """Callers construct these two fixed HTTPS endpoints from validated identifiers."""
    if not re.fullmatch(r'https://(?:textures\.minecraft\.net/texture/[a-f0-9]{32,64}|'
                        r'sessionserver\.mojang\.com/session/minecraft/profile/[a-f0-9]{32})', url):
        raise ValueError('Invalid asset endpoint.')
    request = urllib.request.Request(url, headers={'User-Agent': 'Oak-Activity/1.0'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=4) as response:
        if response.geturl() != url or response.status != 200:
            raise ValueError('Unexpected asset response.')
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Asset exceeds size limit.')
    return raw


def validate_png(raw):
    """Validate the bounded PNG header; browsers perform full image decoding."""
    if len(raw) < 33 or len(raw) > PNG_LIMIT or raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid skin PNG.')
    if raw[8:16] != b'\x00\x00\x00\x0dIHDR':
        raise ValueError('Invalid skin header.')
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', raw[16:29])
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (width != 64 or height not in (32, 64) or depth not in depths.get(color, ())
            or compression != 0 or filtering != 0 or interlace not in (0, 1)
            or zlib.crc32(raw[12:29]) != int.from_bytes(raw[29:33], 'big')):
        raise ValueError('Unsupported skin PNG.')
    return raw


def profile_skin(raw, uid):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('id') != uid.replace('-', ''):
        raise ValueError('Unexpected profile identity.')
    properties = data.get('properties', [])
    if not isinstance(properties, list) or len(properties) > 16:
        raise ValueError('Invalid profile properties.')
    for prop in properties:
        if not isinstance(prop, dict) or prop.get('name') != 'textures':
            continue
        value = prop.get('value')
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError('Invalid texture property.')
        texture = json.loads(base64.b64decode(value, validate=True))
        url = texture.get('textures', {}).get('SKIN', {}).get('url', '')
        match = TEXTURE.fullmatch(url) if isinstance(url, str) else None
        if match:
            return match[1]
    raise ValueError('No supported profile skin.')


class SkinCache:
    """One optional worker; collector methods only enqueue or read bounded memory."""
    def __init__(self, state_dir, public_dir, *, max_files=512, max_pending=100,
                 clock=time.time, start_worker=True):
        self.state, self.public = Path(state_dir), Path(public_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)
        self.path = self.state / 'skin-metadata.json'
        self.clock, self.max_files, self.max_pending = clock, max_files, max_pending
        self.lock = threading.Lock()
        self.queue = queue.Queue(maxsize=max_pending)
        self.pending, self.files, self.profiles, self.failures = set(), {}, {}, {}
        self.stop, self.wake = threading.Event(), threading.Event()
        self.dirty = False
        self._load()
        self._maintain()
        self.thread = threading.Thread(target=self._worker, name='oak-activity-skins', daemon=True) if start_worker else None
        if self.thread:
            self.thread.start()

    def _load(self):
        now = self.clock()
        for path in self.public.glob('*.png'):
            if skin_hash(path.stem) and path.is_file() and not path.is_symlink():
                try:
                    if path.stat().st_size > PNG_LIMIT:
                        raise ValueError('Oversized cached asset.')
                    validate_png(path.read_bytes())
                    self.files[path.stem] = path.stat().st_mtime
                except (OSError, ValueError):
                    path.unlink(missing_ok=True)
        try:
            if self.path.stat().st_size > 256000:
                return
            data = json.loads(self.path.read_text(encoding='utf-8'))
            for uid, entry in list(data.get('profiles', {}).items())[:512]:
                if (player_uuid({'uuid': uid}) == uid and isinstance(entry, dict)
                        and type(entry.get('checked')) in (int, float)
                        and 0 <= now - entry['checked'] < 90 * DAY):
                    key = skin_hash(entry.get('skin'))
                    self.profiles[uid] = {'skin': key, 'checked': entry['checked'], 'source': 'cached', 'failed': entry.get('failed') is True}
            for key, stamp in list(data.get('failures', {}).items())[:512]:
                if skin_hash(key) and type(stamp) in (int, float) and now < stamp <= now + DAY:
                    self.failures[key] = stamp
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            pass

    def _enqueue(self, kind, key):
        task = kind, key
        if task in self.pending or len(self.pending) >= self.max_pending:
            return
        self.pending.add(task)
        self.queue.put_nowait(task)
        self.wake.set()

    def submit(self, players):
        now = self.clock()
        with self.lock:
            for player in players[:100]:
                uid, key = player_uuid(player), skin_hash(player.get('skin'))
                entry = self.profiles.get(uid, {})
                if key and uid:
                    if entry.get('skin') != key or now - entry.get('checked', 0) >= DAY:
                        self.profiles[uid] = {'skin': key, 'checked': now, 'source': 'local'}
                        self.dirty = True
                elif uid:
                    key = skin_hash(entry.get('skin'))
                    ttl = 7 * DAY if key and not entry.get('failed') else DAY / 4
                    if not entry or now - entry['checked'] >= ttl:
                        self._enqueue('profile', uid)
                if key in self.files:
                    self.files[key] = now
                elif key and self.failures.get(key, 0) <= now:
                    self._enqueue('skin', key)
            # Local metadata must be bounded even when a server sees many identities.
            if len(self.profiles) > 512:
                self.profiles = dict(sorted(self.profiles.items(), key=lambda item: item[1]['checked'])[-512:])
            if self.dirty:
                self.wake.set()

    def metadata(self, players):
        with self.lock:
            result = []
            for player in players:
                key = skin_hash(player.get('skin')) or self.profiles.get(player_uuid(player), {}).get('skin', '')
                result.append(dict(player, skin=key if key in self.files else ''))
            return result

    def _download(self, key):
        with self.lock:
            if key in self.files or self.failures.get(key, 0) > self.clock():
                return
        try:
            raw = validate_png(fetch('https://textures.minecraft.net/texture/' + key, PNG_LIMIT))
            path = self.public / (key + '.png')
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(raw)
            temporary.chmod(0o644)
            temporary.replace(path)
            with self.lock:
                self.files[key] = self.clock()
                self.failures.pop(key, None)
                self.dirty = True
        except (OSError, ValueError, http.client.HTTPException):
            with self.lock:
                self.failures[key] = self.clock() + DAY / 4
                self.dirty = True

    def _process(self, task):
        kind, key = task
        if kind == 'skin':
            self._download(key)
            return
        started = self.clock()
        with self.lock:
            previous = self.profiles.get(key, {})
            if previous.get('source') == 'local' and started - previous.get('checked', 0) < 7 * DAY:
                return
        try:
            raw = fetch('https://sessionserver.mojang.com/session/minecraft/profile/' + key.replace('-', ''), PROFILE_LIMIT)
            texture = profile_skin(raw, key)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, http.client.HTTPException):
            texture = ''
        with self.lock:
            previous = self.profiles.get(key, {})
            if previous.get('source') == 'local' and previous.get('checked', 0) >= started:
                return
            # Preserve the last good appearance while transient profile lookups fail.
            self.profiles[key] = {'skin': texture or previous.get('skin', ''), 'checked': self.clock(), 'source': 'remote', 'failed': not bool(texture)}
            self.dirty = True
        if texture:
            self._download(texture)

    def _run_once(self):
        try:
            task = self.queue.get_nowait()
        except queue.Empty:
            return False
        try:
            self._process(task)
        finally:
            with self.lock:
                self.pending.discard(task)
            self.queue.task_done()
        self._maintain()
        return True

    def _maintain(self):
        now = self.clock()
        with self.lock:
            ordered = sorted(self.files, key=self.files.get)
            remove = set(ordered[:max(0, len(ordered) - self.max_files)])
            remove.update(key for key in ordered if now - self.files[key] >= 90 * DAY)
            for key in remove:
                try:
                    (self.public / (key + '.png')).unlink(missing_ok=True)
                    self.files.pop(key, None)
                except OSError:
                    pass
            self.failures = dict(sorted(((key, stamp) for key, stamp in self.failures.items() if stamp > now), key=lambda item: item[1])[-512:])
            self.profiles = dict(sorted(self.profiles.items(), key=lambda item: item[1]['checked'])[-512:])
            snapshot = {'profiles': self.profiles.copy(), 'failures': self.failures.copy()} if self.dirty else None
            self.dirty = False
            used = self.files.copy()
        # Persist use at most daily per image, keeping old history assets for 90 days.
        for key, stamp in used.items():
            path = self.public / (key + '.png')
            try:
                if stamp - path.stat().st_mtime >= DAY:
                    os.utime(path, (stamp, stamp))
            except OSError:
                pass
        if snapshot is not None:
            try:
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(snapshot, separators=(',', ':')), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(self.path)
            except OSError:
                with self.lock:
                    self.dirty = True

    def _worker(self):
        while not self.stop.is_set():
            self.wake.wait(60)
            self.wake.clear()
            while not self.stop.is_set() and self._run_once():
                pass
            self._maintain()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=.1)
