"""Read-only private Fabric telemetry; bounded frames, no live database writes."""
import asyncio
import json
import math
import socket
import time
import uuid
import base64
import re

SOCKET = '/run/oak-telemetry/positions.sock'
LIMIT = 65536


def appearance(data):
    """Keep only bounded rendering fields; never forward profile signatures/URLs."""
    result = {'skin': '', 'cape': '', 'slim': False, 'equipment': {}, 'main_arm': 'RIGHT'}
    try:
        encoded = data.get('textures', '')
        if isinstance(encoded, str) and len(encoded) <= 8192:
            profile = json.loads(base64.b64decode(encoded))
            skin = profile.get('textures', {}).get('SKIN', {})
            match = re.fullmatch(r'https?://textures\.minecraft\.net/texture/([a-f0-9]{32,64})', skin.get('url', ''))
            if match:
                result.update(skin=match[1], slim=skin.get('metadata', {}).get('model') == 'slim')
            cape = profile.get('textures', {}).get('CAPE', {})
            match = re.fullmatch(r'https?://textures\.minecraft\.net/texture/([a-f0-9]{32,64})', cape.get('url', ''))
            if match:
                result['cape'] = match[1]
    except (ValueError, TypeError, AttributeError):
        pass
    if data.get('main_arm') == 'LEFT':
        result['main_arm'] = 'LEFT'
    for slot in ('head', 'chest', 'legs', 'feet', 'mainhand', 'offhand'):
        item = data.get('equipment', {}).get(slot)
        if not isinstance(item, dict):
            continue
        identifier = item.get('id', '')
        asset = item.get('asset', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9_]+:[a-z0-9_/.-]{1,100}', identifier):
            continue
        result['equipment'][slot] = {'id': identifier, 'asset': asset if isinstance(asset, str) and re.fullmatch(r'minecraft:[a-z0-9_]{1,60}', asset) else '',
            'color': item.get('color', 0xA06540) if type(item.get('color')) is int and 0 <= item['color'] <= 0xFFFFFF else 0xA06540,
            'enchanted': item.get('enchanted') is True}
    return result


def decode(raw):
    data = json.loads(raw)
    stamp = data.get('sampled_at')
    if data.get('version') != 1 or not isinstance(stamp, (float, int)) or not math.isfinite(stamp) or not -2 <= time.time() - stamp < 3:
        raise ValueError('Expired telemetry frame.')
    players = data.get('players')
    if not isinstance(players, list) or len(players) > 100:
        raise ValueError('Invalid player list.')
    seen = set()
    for player in players:
        uid = str(uuid.UUID(player['uuid']))
        if uid in seen:
            raise ValueError('Duplicate identity.')
        seen.add(uid)
        pos = player.get('position')
        if not isinstance(pos, list) or len(pos) != 3 or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 30000000 for v in pos):
            raise ValueError('Invalid coordinates.')
        if not isinstance(player.get('name'), str) or not 1 <= len(player['name']) <= 32 or not isinstance(player.get('dimension'), str):
            raise ValueError('Invalid identity or dimension.')
        if not isinstance(player.get('yaw'), (int, float)) or not math.isfinite(player['yaw']):
            raise ValueError('Invalid rotation.')
        player['sampled_at'] = stamp
        player['platform'] = 'bedrock' if player['name'].startswith('.') else 'java'
        if 'appearance' in player:
            player['appearance'] = appearance(player['appearance'])
        for key in ('body_yaw', 'pitch', 'swing'):
            value = player.get(key, 0)
            player[key] = value if type(value) in (float, int) and math.isfinite(value) else 0
    return {'players': players, 'sampled_at': stamp, 'fresh': True, 'source': 'fabric', 'tick': data.get('tick')}


def snapshot(path=SOCKET):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(.25)
            sock.connect(path)
            with sock.makefile('rb') as file:
                raw = file.readline(LIMIT + 1)
            if len(raw) > LIMIT or not raw.endswith(b'\n'):
                return None
            return decode(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


class LivePositions:
    """One host connection shared by all authenticated map viewers."""
    def __init__(self, path=SOCKET):
        self.path, self.task, self.frame = path, None, None
        self.viewers = 0

    def subscribe(self):
        self.viewers += 1
        if self.task is None:
            self.task = asyncio.create_task(self.receive())

    async def unsubscribe(self):
        self.viewers -= 1
        if not self.viewers and self.task:
            task, self.task = self.task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.frame = None

    async def receive(self):
        while True:
            writer = None
            try:
                reader, writer = await asyncio.open_unix_connection(self.path, limit=LIMIT)
                appearances = {}
                while True:
                    raw = await asyncio.wait_for(reader.readline(), timeout=3)
                    if not raw or len(raw) > LIMIT:
                        raise ValueError('Telemetry disconnected.')
                    frame = decode(raw)
                    for player in frame['players']:
                        uid = player['uuid']
                        if 'appearance' in player:
                            appearances[uid] = player['appearance']
                        elif uid in appearances:
                            player['appearance'] = appearances[uid]
                    online = {p['uuid'] for p in frame['players']}
                    appearances = {k: v for k, v in appearances.items() if k in online}
                    self.frame = frame
            except (OSError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError):
                self.frame = None
            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            await asyncio.sleep(1)
