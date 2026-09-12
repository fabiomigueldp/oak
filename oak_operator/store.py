"""Private durable storage for operator work, schedules and notebooks."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.outputs = self.root / 'outputs'
        self.outputs.mkdir(exist_ok=True, mode=0o700)
        self.notebooks = self.root / 'notebooks'
        self.notebooks.mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'operator.sqlite3', timeout=30, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, spec TEXT NOT NULL, actor TEXT NOT NULL,
                status TEXT NOT NULL, created REAL NOT NULL, started REAL, ended REAL,
                exit_code INTEGER, error TEXT, pid INTEGER, identity TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                idempotency TEXT UNIQUE, routine_id TEXT
            );
            CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created);
            CREATE TABLE IF NOT EXISTS routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
                enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
                next_at REAL, event_cursor INTEGER NOT NULL, actor TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notebooks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                revision INTEGER NOT NULL, actor TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notebook_versions (
                id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
                content TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(id, revision)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                data TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_name_id ON events(name, id);
            CREATE TABLE IF NOT EXISTS source_cursors (
                source TEXT PRIMARY KEY, epoch TEXT NOT NULL, cursor INTEGER NOT NULL
            );
        ''')
        if os.name == 'posix':
            (self.root / 'operator.sqlite3').chmod(0o600)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def close(self):
        with self.lock:
            self.db.close()
