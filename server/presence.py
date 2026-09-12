"""Bounded, sampled player history published as static JSON; no client database API."""
import base64
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import time
import uuid

UTC = timezone.utc
DAY = 86400
MAX_PLAYERS = 100
TEXTURE = re.compile(r'https?://textures\.minecraft\.net/texture/([a-f0-9]{32,64})')


def utc_day(stamp):
    return datetime.fromtimestamp(stamp, UTC).strftime('%Y-%m-%d')


def day_start(day):
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=UTC).timestamp())


def identity(data):
    """Select only public, bounded rendering fields from an untrusted record."""
    name = data.get('name')
    if not isinstance(name, str) or not 1 <= len(name) <= 32 or not name.isprintable() or name != name.strip():
        raise ValueError('Invalid player name.')
    try:
        uid = str(uuid.UUID(str(data.get('uuid', ''))))
    except (ValueError, AttributeError):
        uid = None
    skin = data.get('skin', '')
    if not isinstance(skin, str) or not re.fullmatch('[a-f0-9]{32,64}', skin):
        skin = ''
    return {'id': 'uuid:' + uid if uid else 'name:' + name.casefold(), 'uuid': uid, 'name': name, 'skin': skin}


def texture_hash(data):
    try:
        encoded = data.get('textures', '')
        if not isinstance(encoded, str) or len(encoded) > 8192:
            return ''
        profile = json.loads(base64.b64decode(encoded, validate=True))
        url = profile.get('textures', {}).get('SKIN', {}).get('url', '')
        match = TEXTURE.fullmatch(url) if isinstance(url, str) else None
        return match[1] if match else ''
    except (ValueError, TypeError, AttributeError):
        return ''


class PlayerMetadata:
    """Best-effort local appearance lookup, independent of the private admin app."""
    def __init__(self, usercache='/srv/oak/server/usercache.json', telemetry='/run/oak-telemetry/positions.sock'):
        self.usercache, self.telemetry = Path(usercache), Path(telemetry)
        self.cache, self.refreshed = {}, None

    def resolve(self, names, now=None):
        now = time.time() if now is None else now
        if self.refreshed is None or now - self.refreshed >= 30:
            self.refreshed = now
            self.refresh(now)
        return [identity(dict(self.cache.get(name.casefold(), {}), name=name)) for name in names]

    def refresh(self, now):
        try:
            if self.usercache.stat().st_size <= 2 * 1024 * 1024:
                records = json.loads(self.usercache.read_text(encoding='utf-8'))
                if isinstance(records, list) and len(records) <= 10000:
                    for raw in records:
                        try:
                            player = identity(raw)
                            previous = self.cache.get(player['name'].casefold(), {})
                            player['skin'] = previous.get('skin', '')
                            self.cache[player['name'].casefold()] = player
                        except (ValueError, TypeError, AttributeError):
                            continue
        except (OSError, ValueError):
            pass
        try:
            if not hasattr(socket, 'AF_UNIX') or not self.telemetry.exists():
                return
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(.1)
                sock.connect(str(self.telemetry))
                with sock.makefile('rb') as stream:
                    raw = stream.readline(65537)
            if len(raw) > 65536 or not raw.endswith(b'\n'):
                return
            frame = json.loads(raw)
            stamp = frame.get('sampled_at')
            players = frame.get('players')
            if frame.get('version') != 1 or type(stamp) not in (int, float) or not math.isfinite(stamp) or not -2 <= now - stamp < 3:
                return
            if not isinstance(players, list) or len(players) > MAX_PLAYERS:
                return
            for raw in players:
                try:
                    player = identity(raw)
                    key = player['name'].casefold()
                    player['skin'] = texture_hash(raw.get('appearance', {})) or self.cache.get(key, {}).get('skin', '')
                    self.cache[key] = player
                except (ValueError, TypeError, AttributeError):
                    continue
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        finally:
            # Usercache is bounded too; do not accumulate every historical rename.
            if len(self.cache) > 10000:
                self.cache = dict(list(self.cache.items())[-10000:])


class PresenceHistory:
    """One collector owns the SQLite writer; transactions and publication are throttled."""
    def __init__(self, state='/var/lib/oak-presence', public='/srv/oak/web/data/activity',
                 retention_days=90, flush_seconds=30, gap_seconds=20,
                 max_sessions=10000, max_identities=256, max_coverage=2000):
        self.state, self.public = Path(state), Path(public)
        self.state.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / 'history.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, name TEXT NOT NULL, uuid TEXT, skin TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS player_name ON players(name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL,
                start INTEGER NOT NULL, end INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0, reason TEXT);
            CREATE INDEX IF NOT EXISTS session_window ON sessions(end, start);
            CREATE TABLE IF NOT EXISTS coverage (id INTEGER PRIMARY KEY AUTOINCREMENT, start INTEGER NOT NULL,
                end INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS coverage_window ON coverage(end, start);
            CREATE TABLE IF NOT EXISTS limits (day TEXT PRIMARY KEY, truncated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS day_players (day TEXT NOT NULL, player TEXT NOT NULL, PRIMARY KEY(day, player));
            CREATE TABLE IF NOT EXISTS publication (day TEXT PRIMARY KEY);
        ''')
        self.retention_days, self.flush_seconds, self.gap_seconds = retention_days, flush_seconds, gap_seconds
        self.max_sessions, self.max_identities, self.max_coverage = max_sessions, max_identities, max_coverage
        meta = dict(self.db.execute('SELECT key, value FROM meta'))
        self.last_sample = int(meta['last_sample']) if meta.get('last_sample') else None
        self.last_event = int(meta['last_event']) if meta.get('last_event') else None
        self.observed_since = int(meta['observed_since']) if meta.get('observed_since') else None
        self.collecting = meta.get('collecting') == '1'
        self.active = {row['player']: dict(row) for row in self.db.execute('SELECT * FROM sessions WHERE closed=0')}
        row = self.db.execute('SELECT * FROM coverage WHERE closed=0 ORDER BY id DESC LIMIT 1').fetchone()
        self.coverage = dict(row) if row else None
        self.last_flush = self.last_event
        # The journal shares the data transaction, so a restart repairs only shards
        # interrupted between their database commit and atomic JSON publication.
        self.dirty = {row[0] for row in self.db.execute('SELECT day FROM publication')}
        self.needs_recovery = bool(self.dirty)
        self.current_day, self.counts = None, None

    def _touch(self, start, end):
        first = max(start // DAY, (self.last_event or end) // DAY - self.retention_days + 1)
        for day in range(first, end // DAY + 1):
            self.dirty.add(utc_day(day * DAY))

    def _day(self, stamp):
        day = utc_day(stamp)
        if day != self.current_day:
            self.current_day = day
            start = day_start(day)
            self.counts = {
                'sessions': self.db.execute('SELECT COUNT(*) FROM sessions WHERE start>=? AND start<?', (start, start + DAY)).fetchone()[0],
                'coverage': self.db.execute('SELECT COUNT(*) FROM coverage WHERE start>=? AND start<?', (start, start + DAY)).fetchone()[0],
            }
            self.db.execute('INSERT OR IGNORE INTO limits(day) VALUES(?)', (day,))
            self.dirty.add(day)
            cutoff = start - (self.retention_days - 1) * DAY
            self.db.execute('DELETE FROM sessions WHERE end<? AND closed=1', (cutoff,))
            self.db.execute('DELETE FROM coverage WHERE end<? AND closed=1', (cutoff,))
            self.db.execute('DELETE FROM players WHERE id NOT IN (SELECT DISTINCT player FROM sessions)')
            cutoff_day = utc_day(cutoff)
            self.db.execute('DELETE FROM limits WHERE day<?', (cutoff_day,))
            self.db.execute('DELETE FROM day_players WHERE day<?', (cutoff_day,))
            self.db.execute('DELETE FROM publication WHERE day<?', (cutoff_day,))
            for path in self.public.glob('????-??-??.json'):
                if path.stem < cutoff_day:
                    path.unlink()
        return day

    def _truncated(self):
        self.db.execute('UPDATE limits SET truncated=1 WHERE day=?', (self.current_day,))
        self.dirty.add(self.current_day)

    def _close(self, reason):
        cutoff = ((self.last_event or 0) // DAY - self.retention_days + 1) * DAY
        pruned = False
        for row in self.active.values():
            if row['end'] < cutoff:
                self.db.execute('DELETE FROM sessions WHERE id=?', (row['id'],))
                pruned = True
            else:
                self.db.execute('UPDATE sessions SET closed=1, reason=? WHERE id=?', (reason, row['id']))
            self._touch(row['end'], row['end'])
        self.active.clear()
        if self.coverage:
            if self.coverage['end'] < cutoff:
                self.db.execute('DELETE FROM coverage WHERE id=?', (self.coverage['id'],))
            else:
                self.db.execute('UPDATE coverage SET closed=1 WHERE id=?', (self.coverage['id'],))
            self._touch(self.coverage['end'], self.coverage['end'])
            self.coverage = None
        if pruned:
            self.db.execute('DELETE FROM players WHERE id NOT IN (SELECT DISTINCT player FROM sessions)')

    def observe(self, players, now=None):
        """None means unknown; an empty validated list is an observed empty server."""
        stamp = time.time() if now is None else now
        if type(stamp) not in (float, int) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError('Invalid observation timestamp.')
        stamp = int(stamp)
        if self.last_event is not None and stamp < self.last_event:
            raise ValueError('Observation clock moved backwards.')
        if players is not None:
            if not isinstance(players, list) or len(players) > MAX_PLAYERS:
                raise ValueError('Invalid observation size.')
            players = [identity(player) for player in players]
            for player in players:
                if player['uuid'] is None:
                    previous = self.db.execute('SELECT uuid FROM players WHERE name=? COLLATE NOCASE AND uuid IS NOT NULL LIMIT 1', (player['name'],)).fetchone()
                    if previous:
                        player.update(uuid=previous['uuid'], id='uuid:' + previous['uuid'])
            if len({p['id'] for p in players}) != len(players):
                raise ValueError('Duplicate player identity.')
        previous_event = self.last_event
        self.last_event = stamp
        day = self._day(stamp)
        transition = previous_event is None or self.collecting != (players is not None)
        if self.last_sample is not None and stamp - self.last_sample > self.gap_seconds:
            transition = transition or bool(self.active) or self.coverage is not None
            self._close('unknown')
        self.collecting = players is not None
        if players is None:
            self._close('unknown')
        else:
            if self.observed_since is None:
                self.observed_since = stamp
            allowed = {}
            for player in players:
                fallback = 'name:' + player['name'].casefold()
                if player['uuid'] and self.db.execute('SELECT 1 FROM players WHERE id=?', (fallback,)).fetchone():
                    extent = self.db.execute('SELECT MIN(start),MAX(end) FROM sessions WHERE player=?', (fallback,)).fetchone()
                    if extent[0] is not None:
                        self._touch(extent[0], extent[1])
                    self.db.execute('UPDATE sessions SET player=? WHERE player=?', (player['id'], fallback))
                    self.db.execute('INSERT OR IGNORE INTO day_players SELECT day,? FROM day_players WHERE player=?', (player['id'], fallback))
                    self.db.execute('DELETE FROM day_players WHERE player=?', (fallback,))
                    self.db.execute('DELETE FROM players WHERE id=?', (fallback,))
                    if fallback in self.active:
                        self.active[player['id']] = self.active.pop(fallback)
                        self.active[player['id']]['player'] = player['id']
                known_today = self.db.execute('SELECT 1 FROM day_players WHERE day=? AND player=?', (day, player['id'])).fetchone()
                if not known_today:
                    count = self.db.execute('SELECT COUNT(*) FROM day_players WHERE day=?', (day,)).fetchone()[0]
                    if count >= self.max_identities:
                        self._truncated()
                        continue
                    self.db.execute('INSERT INTO day_players VALUES(?, ?)', (day, player['id']))
                old = self.db.execute('SELECT * FROM players WHERE id=?', (player['id'],)).fetchone()
                if not player['skin'] and old:
                    player['skin'] = old['skin']
                if old is None or any(old[key] != player[key] for key in ('name', 'uuid', 'skin')):
                    self.db.execute('INSERT OR REPLACE INTO players VALUES(?, ?, ?, ?)', tuple(player[key] for key in ('id', 'name', 'uuid', 'skin')))
                    self.dirty.add(day)
                allowed[player['id']] = player
            for player_id in self.active.keys() - allowed.keys():
                row = self.active.pop(player_id)
                self.db.execute('UPDATE sessions SET closed=1, reason=? WHERE id=?', ('left', row['id']))
                self._touch(row['end'], row['end'])
                transition = True
            for player_id in allowed:
                row = self.active.get(player_id)
                if row:
                    self._touch(row['end'], stamp)
                    self.db.execute('UPDATE sessions SET end=? WHERE id=?', (stamp, row['id']))
                    row['end'] = stamp
                elif self.counts['sessions'] < self.max_sessions:
                    cursor = self.db.execute('INSERT INTO sessions(player,start,end) VALUES(?,?,?)', (player_id, stamp, stamp))
                    self.active[player_id] = {'id': cursor.lastrowid, 'player': player_id, 'start': stamp, 'end': stamp}
                    self.counts['sessions'] += 1
                    self.dirty.add(day)
                    transition = True
                else:
                    self._truncated()
            if self.coverage:
                self._touch(self.coverage['end'], stamp)
                self.db.execute('UPDATE coverage SET end=? WHERE id=?', (stamp, self.coverage['id']))
                self.coverage['end'] = stamp
            elif self.counts['coverage'] < self.max_coverage:
                cursor = self.db.execute('INSERT INTO coverage(start,end) VALUES(?,?)', (stamp, stamp))
                self.coverage = {'id': cursor.lastrowid, 'start': stamp, 'end': stamp}
                self.counts['coverage'] += 1
                self.dirty.add(day)
            else:
                self._truncated()
            self.last_sample = stamp
        values = {'last_sample': self.last_sample, 'last_event': stamp, 'observed_since': self.observed_since,
                  'collecting': int(self.collecting)}
        self.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', [(key, str(value) if value is not None else '') for key, value in values.items()])
        if self.needs_recovery or transition or self.last_flush is None or stamp - self.last_flush >= self.flush_seconds or (previous_event is not None and utc_day(previous_event) != day):
            self.flush(stamp)

    @staticmethod
    def _atomic(path, data):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        temporary.chmod(0o644)
        temporary.replace(path)

    def daily(self, day, updated):
        start = day_start(day)
        end = start + DAY
        rows = self.db.execute('''SELECT s.*, p.name, p.uuid, p.skin FROM sessions s JOIN players p ON p.id=s.player
            WHERE s.start<? AND s.end>=? ORDER BY s.start,s.id LIMIT ?''', (end, start, self.max_sessions + 1)).fetchall()
        flag = self.db.execute('SELECT truncated FROM limits WHERE day=?', (day,)).fetchone()
        truncated = bool(flag and flag[0]) or len(rows) > self.max_sessions
        players = {}
        for row in rows[:self.max_sessions]:
            if row['player'] not in players:
                if len(players) >= self.max_identities:
                    truncated = True
                    continue
                players[row['player']] = {key: row[key] for key in ('name', 'uuid', 'skin')}
                players[row['player']].update(id=row['player'], sessions=[])
            players[row['player']]['sessions'].append({
                'id': row['id'], 'start': max(start, row['start']), 'end': min(end, row['end']),
                'open': not bool(row['closed']) and row['end'] < end,
                'continuedBefore': row['start'] < start, 'continuesAfter': row['end'] >= end,
                'endReason': row['reason'] if row['end'] < end else None,
            })
        coverage = self.db.execute('SELECT start,end FROM coverage WHERE start<? AND end>=? ORDER BY start LIMIT ?', (end, start, self.max_coverage + 1)).fetchall()
        truncated = truncated or len(coverage) > self.max_coverage
        return {'version': 1, 'day': day, 'updated': updated, 'start': start, 'end': end,
                'coverage': [[max(start, row['start']), min(end, row['end'])] for row in coverage[:self.max_coverage]],
                'players': sorted(players.values(), key=lambda player: (player['name'].casefold(), player['id'])),
                'truncated': truncated}

    def flush(self, now=None):
        if self.last_event is None:
            return
        stamp = self.last_event if now is None else int(now)
        cutoff = utc_day(stamp - (self.retention_days - 1) * DAY)
        # The current day also gets a fresh heartbeat during unknown intervals.
        self.dirty.add(utc_day(stamp))
        self.db.executemany('INSERT OR IGNORE INTO publication VALUES(?)', [(day,) for day in self.dirty if day >= cutoff])
        self.needs_recovery = True
        self.db.commit()
        for day in sorted(self.dirty):
            if day >= cutoff:
                self._atomic(self.public / (day + '.json'), self.daily(day, stamp))
        days = [row[0] for row in self.db.execute('SELECT day FROM limits WHERE day>=? ORDER BY day', (cutoff,))]
        self._atomic(self.public / 'index.json', {
            'version': 1, 'updated': stamp, 'observedSince': self.observed_since,
            'sampleSeconds': 5, 'gapSeconds': self.gap_seconds, 'retentionDays': self.retention_days,
            'latestDay': utc_day(stamp), 'days': days, 'collecting': self.collecting,
        })
        self.db.executemany('DELETE FROM publication WHERE day=?', [(day,) for day in self.dirty])
        self.db.commit()
        self.dirty.clear()
        self.needs_recovery = False
        self.last_flush = stamp

    def close(self):
        self.flush()
        self.db.close()
