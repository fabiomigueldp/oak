"""Durable sessions, jobs and history with short, serialized SQLite transactions."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,created REAL NOT NULL,disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),public_key TEXT NOT NULL,counter INTEGER NOT NULL,name TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,verified REAL NOT NULL);
CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,actor TEXT,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,created REAL NOT NULL,updated REAL NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,params TEXT NOT NULL,state TEXT NOT NULL,step TEXT NOT NULL,result TEXT,error TEXT,idempotency TEXT NOT NULL,UNIQUE(actor,idempotency));
CREATE TABLE IF NOT EXISTS job_steps(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL REFERENCES jobs(id),created REAL NOT NULL,step TEXT NOT NULL,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY,name TEXT NOT NULL,dimension TEXT NOT NULL,x REAL NOT NULL,y REAL NOT NULL,z REAL NOT NULL,note TEXT NOT NULL,created REAL NOT NULL,actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,params TEXT NOT NULL,interval_minutes INTEGER NOT NULL,next_run REAL NOT NULL,enabled INTEGER NOT NULL,actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS player_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,joined REAL NOT NULL,left_at REAL,platform TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,actor TEXT NOT NULL,kind TEXT NOT NULL,params TEXT NOT NULL,preview TEXT NOT NULL,expires REAL NOT NULL,consumed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,created);
CREATE INDEX IF NOT EXISTS events_created ON events(created);
CREATE INDEX IF NOT EXISTS samples_created ON samples(created);
PRAGMA user_version=1;
'''


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = self.connect()
        try:
            if db.execute('PRAGMA user_version').fetchone()[0] > 1:
                raise RuntimeError('This database belongs to a newer Oak Control release. Review migration compatibility.')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
        finally:
            db.close()
        path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        db = self.connect()
        try:
            return [dict(row) for row in db.execute(sql, args).fetchall()]
        finally:
            db.close()

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def set(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, encode(value)))

    def get(self, key, default=None):
        row = self.one('SELECT value FROM kv WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def event(self, kind, title, actor=None, data=None, db=None):
        args = (time.time(), kind, title, actor, encode(data or {}))
        if db is not None:
            return db.execute('INSERT INTO events(created,kind,title,actor,data) VALUES(?,?,?,?,?)', args).lastrowid
        with self.transaction() as conn:
            return self.event(kind, title, actor, data, conn)

    def events(self, after=0, limit=100):
        rows = self.rows('SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?', (after, min(limit, 300)))
        for row in rows:
            row['data'] = json.loads(row['data'])
        return rows

    def invite(self, name, role='owner', minutes=15):
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            user = db.execute('SELECT * FROM users WHERE name=? AND disabled=0', (name,)).fetchone()
            uid = user['id'] if user else str(uuid.uuid4())
            if user and user['role'] != role:
                raise Conflict('Existing user has a different role.')
            if not user:
                db.execute('INSERT INTO users(id,name,role,created) VALUES(?,?,?,?)', (uid, name, role, time.time()))
            db.execute('DELETE FROM invites WHERE user_id=?', (uid,))
            db.execute('INSERT INTO invites VALUES(?,?,?)', (digest(token), uid, time.time() + minutes * 60))
            self.event('access', 'Enrollment link issued', uid, db=db)
        return token

    def session(self, uid, seconds):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (digest(token), uid, csrf, now, now + seconds, now))
        return token, csrf

    def current_session(self, token):
        return self.one('SELECT s.*,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.disabled=0', (digest(token), time.time())) if token else None

    def challenge(self, kind, data):
        cid = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute('DELETE FROM challenges WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO challenges VALUES(?,?,?,?)', (digest(cid), kind, encode(data), time.time() + 180))
        return cid

    def consume_challenge(self, cid, kind):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM challenges WHERE id=? AND kind=? AND expires>?', (digest(cid), kind, time.time())).fetchone()
            db.execute('DELETE FROM challenges WHERE id=?', (digest(cid),))
        if not row:
            raise ValueError('Challenge expired. Start again.')
        return json.loads(row['data'])

    def create_job(self, actor, kind, label, params, key, review=None):
        now, jid = time.time(), str(uuid.uuid4())
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM jobs WHERE actor=? AND idempotency=?', (actor, key)).fetchone()
            if previous:
                if previous['kind'] != kind or previous['params'] != encode(params):
                    raise Conflict('Idempotency key was used for a different action.')
                return self.decode_job(dict(previous))
            if review:
                row = db.execute('SELECT * FROM reviews WHERE id=? AND actor=? AND expires>? AND consumed=0', (review, actor, now)).fetchone()
                if not row or row['kind'] != kind or row['params'] != encode(params):
                    raise Conflict('Review expired or action changed. Review it again.')
                db.execute('UPDATE reviews SET consumed=1 WHERE id=?', (review,))
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 20:
                raise Conflict('The operation queue is full.')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (jid, now, now, actor, kind, label, encode(params), 'queued', 'Queued', None, None, key))
            self.event('operation', label, actor, {'job_id': jid, 'state': 'queued'}, db)
        return self.job(jid)

    @staticmethod
    def decode_job(row):
        row['params'] = json.loads(row['params'])
        row['result'] = json.loads(row['result']) if row['result'] else None
        return row

    def job(self, jid):
        row = self.one('SELECT * FROM jobs WHERE id=?', (jid,))
        if row:
            self.decode_job(row)
            row['steps'] = self.rows('SELECT created,step,detail FROM job_steps WHERE job_id=? ORDER BY id', (jid,))
        return row

    def jobs(self):
        return [self.decode_job(row) for row in self.rows('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]

    def claim(self):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',updated=?,step='Starting' WHERE id=?", (time.time(), row['id']))
        return self.job(row['id']) if row else None

    def progress(self, jid, step, detail=''):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET updated=?,step=? WHERE id=?', (time.time(), step, jid))
            db.execute('INSERT INTO job_steps(job_id,created,step,detail) VALUES(?,?,?,?)', (jid, time.time(), step, detail[:2000]))

    def finish(self, jid, state, result=None, error=None):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET updated=?,state=?,result=?,error=?,step=? WHERE id=?', (time.time(), state, encode(result) if result is not None else None, error, state, jid))
            row = db.execute('SELECT actor,label FROM jobs WHERE id=?', (jid,)).fetchone()
            self.event('operation', row['label'], row['actor'], {'job_id': jid, 'state': state, 'error': error}, db)

    def cancel(self, jid):
        with self.transaction() as db:
            changed = db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=? AND state='queued'", (time.time(), jid)).rowcount
            if not changed:
                raise Conflict('Only queued operations can be cancelled.')
            self.event('operation', 'Operation cancelled', data={'job_id': jid}, db=db)

    def recover(self):
        # Unknown delivery is never replayed automatically.
        with self.transaction() as db:
            rows = db.execute("SELECT id FROM jobs WHERE state='running'").fetchall()
            db.execute("UPDATE jobs SET state='interrupted',step='Reconciliation required',error='Worker restarted. Check the agent receipt before retrying.',updated=? WHERE state='running'", (time.time(),))
            for row in rows:
                self.event('operation', 'Interrupted operation needs reconciliation', data={'job_id': row['id']}, db=db)

    def sample(self, snapshot):
        now = time.time()
        names = {p['name']: p for p in snapshot.get('players', [])}
        with self.transaction() as db:
            db.execute('INSERT INTO samples(created,data) VALUES(?,?)', (now, encode(snapshot)))
            db.execute('DELETE FROM samples WHERE created<?', (now - 24 * 3600,))
            db.execute('DELETE FROM events WHERE created<?', (now - 90 * 86400,))
            # Stale data cannot establish joins or departures.
            if snapshot.get('fresh'):
                online = {r['name']: r for r in db.execute('SELECT * FROM player_sessions WHERE left_at IS NULL')}
                for name, player in names.items():
                    if name not in online:
                        db.execute('INSERT INTO player_sessions(name,joined,platform) VALUES(?,?,?)', (name, now, player.get('platform', 'java')))
                        self.event('player', 'Player joined', data={'player': name}, db=db)
                for name, row in online.items():
                    if name not in names:
                        db.execute('UPDATE player_sessions SET left_at=? WHERE id=?', (now, row['id']))
                        self.event('player', 'Player left', data={'player': name}, db=db)

    def tick_schedules(self):
        now = time.time()
        unified = self.get('backup_status', {}).get('ready', False)
        with self.transaction() as db:
            for row in db.execute('SELECT * FROM schedules WHERE enabled=1 AND next_run<=?', (now,)).fetchall():
                if unified and row['kind'] == 'backup':
                    db.execute('UPDATE schedules SET enabled=0 WHERE id=?', (row['id'],))
                    continue
                # Coalesce downtime into one run; never accumulate a catch-up storm.
                db.execute('UPDATE schedules SET next_run=? WHERE id=?', (now + row['interval_minutes'] * 60, row['id']))
                busy = db.execute("SELECT 1 FROM jobs WHERE kind=? AND state IN ('queued','running')", (row['kind'],)).fetchone()
                if busy:
                    continue
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 20:
                    continue
                jid = str(uuid.uuid4())
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (jid, now, now, row['actor'], row['kind'], row['label'], row['params'], 'queued', 'Queued', None, None, 'schedule:' + row['id'] + ':' + str(int(row['next_run']))))
                self.event('operation', row['label'], row['actor'], {'job_id': jid, 'state': 'queued', 'scheduled': True}, db)

    def tick_backups(self):
        """One host policy, coalesced downtime and durable failure backoff."""
        status = self.get('backup_status', {})
        now = time.time()
        if not status.get('ready') or now - status.get('sampled_at', 0) > 60:
            return
        policy = status['policy']
        if not policy['enabled']:
            return
        points = status['backups']
        health = status.get('health', {})
        due = status.get('next_run') or 0
        boot = max((p.get('restoration', {}).get('at', 0) for p in points if p.get('restoration', {}).get('playable_boot_tested')), default=0)
        candidates = []
        if status.get('bytes', 0) >= policy['budget_gib'] * 1024**3 and health.get('compacted', 0) + 86400 <= now:
            candidates.append(('backup_compact', {'revision': policy['revision']}, 'Retenção automática'))
        if due <= now:
            candidates.append(('backup', {'name': 'Automático'}, 'Backup automático'))
        if points and (health.get('check_failed') or health.get('data_checked', 0) + policy['check_days'] * 86400 <= now):
            candidates.append(('backup_check', {}, 'Verificação automática'))
        if points and not health.get('check_failed') and boot + policy['boot_days'] * 86400 <= now:
            point = next((p for p in points if p.get('compatible') and p.get('integrity') and p.get('manifest', {}).get('includes_runtime')), None)
            if point:
                candidates.append(('verify_backup', {'backup': point['id'], 'boot': True}, 'Teste automático de recuperação'))
        if points and health.get('compacted', 0) + 86400 <= now:
            candidates.append(('backup_compact', {'revision': policy['revision']}, 'Retenção automática'))
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
                return
            for kind, params, label in candidates:
                last = db.execute("SELECT updated FROM jobs WHERE actor='backup-policy' AND kind=? ORDER BY created DESC LIMIT 1", (kind,)).fetchone()
                if last and now - last['updated'] < 1800:
                    continue
                jid = str(uuid.uuid4())
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (jid, now, now, 'backup-policy', kind, label, encode(params), 'queued', 'Queued', None, None, 'policy:' + jid))
                self.event('operation', label, 'backup-policy', {'job_id': jid, 'state': 'queued', 'scheduled': True}, db)
                break
