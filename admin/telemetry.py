"""Read-only private Fabric telemetry; bounded frames, no live database writes."""
import asyncio
import json
import math
import socket
import time
import uuid

SOCKET = '/run/oak-telemetry/positions.sock'
LIMIT = 65536


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
                while True:
                    raw = await asyncio.wait_for(reader.readline(), timeout=3)
                    if not raw or len(raw) > LIMIT:
                        raise ValueError('Telemetry disconnected.')
                    self.frame = decode(raw)
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
