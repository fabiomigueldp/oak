"""Persistent operator execution, automation and project records.

The transport authenticates operators. This module deliberately applies no
command allowlist: trusted execution has the authority of the daemon process.
"""
from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

from .store import Store, encode

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
MAX_TEXT = 1024 * 1024
MAX_PAGE = 65536
_WINDOWS_JOBS = {}
_WINDOWS_JOB_LOCK = threading.RLock()


def attach_windows_job(process):
    """Put the gated child in a kill-on-close Job Object before it can spawn."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('reads', 'writes', 'others', 'read_bytes', 'write_bytes', 'other_bytes')]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', IoCounters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
    win = ctypes.WinDLL('kernel32', use_last_error=True)
    win.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    win.CreateJobObjectW.restype = wintypes.HANDLE
    win.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    win.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    win.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = win.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not win.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not win.AssignProcessToJobObject(handle, int(process._handle)):
        error = ctypes.get_last_error()
        win.CloseHandle(handle)
        raise ctypes.WinError(error)
    with _WINDOWS_JOB_LOCK:
        _WINDOWS_JOBS[process.pid] = handle


def close_windows_job(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        with _WINDOWS_JOB_LOCK:
            handle = _WINDOWS_JOBS.pop(pid, None)
        if handle:
            win = ctypes.WinDLL('kernel32', use_last_error=True)
            win.CloseHandle.argtypes = [wintypes.HANDLE]
            win.CloseHandle(handle)


def atomic(path, content):
    """Replace a private file without exposing partial writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        with temporary.open('xb') as stream:
            stream.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def bounded_int(value, name, low=0, high=MAX_PAGE):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}.')
    return value


def text_field(value, name, limit=MAX_TEXT, empty=False):
    if not isinstance(value, str) or (not value and not empty) or len(value.encode('utf-8')) > limit or '\0' in value:
        raise ValueError(f'{name} must be valid text within {limit} bytes.')
    return value


def number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be a finite number at least {minimum}.')
    return value


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('A valid UUID id is required.') from exc


def process_identity(pid):
    if os.name != 'posix':
        return None
    try:
        # comm may contain spaces or parentheses; fields follow its final ')'.
        tail = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'start': tail[19], 'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, IndexError):
        return None


def terminate_group(pid, force=False):
    try:
        if os.name == 'posix':
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        else:
            import ctypes
            from ctypes import wintypes
            with _WINDOWS_JOB_LOCK:
                handle = _WINDOWS_JOBS.get(pid)
            if handle:
                win = ctypes.WinDLL('kernel32', use_last_error=True)
                win.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                win.TerminateJobObject(handle, 1)
            else:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=10, check=False)
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        pass


class Kernel:
    def __init__(self, state, runtime=None, demo=False, *, max_workers=4, poll_seconds=.2):
        self.store = Store(state)
        self.state = self.store.root
        self.runtime = runtime
        self.demo = bool(demo)
        from .packages import Packages
        from .services import Services
        self.packages = Packages(self.state, runtime, self.demo)
        self.services = Services(self.state, self.demo)
        from .integration import NativeIntegration
        self.native = NativeIntegration(self)
        self.max_workers = bounded_int(max_workers, 'max_workers', 1, 64)
        self.poll_seconds = number(poll_seconds, 'poll_seconds', .01)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads = []
        self._processes = {}
        self._process_lock = threading.RLock()
        self._file_lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._started = False
        self._closed = False
        self._lockfile = None
        (self.state / 'workspace').mkdir(exist_ok=True, mode=0o700)

    def start(self):
        with self._lifecycle:
            if self._closed:
                raise RuntimeError('The operator kernel is closed.')
            if self._started:
                return self
            lockfile = (self.state / 'daemon.lock').open('a+b')
            try:
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    import msvcrt
                    lockfile.seek(0)
                    lockfile.write(b'0')
                    lockfile.flush()
                    lockfile.seek(0)
                    msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            except (OSError, BlockingIOError):
                lockfile.close()
                raise RuntimeError('Another operator kernel owns this state directory.') from None
            self._lockfile = lockfile
            self._recover()
            self._started = True
            for index in range(self.max_workers):
                thread = threading.Thread(target=self._worker, name=f'oak-operator-worker-{index}', daemon=True)
                self._threads.append(thread)
                thread.start()
            scheduler = threading.Thread(target=self._scheduler, name='oak-operator-scheduler', daemon=True)
            self._threads.append(scheduler)
            scheduler.start()
            if not self.demo:
                relay = threading.Thread(target=self.native.run, name='oak-operator-native-events', daemon=True)
                self._threads.append(relay)
                relay.start()
            return self

    def close(self):
        with self._lifecycle:
            if self._closed:
                return
            self._stop.set()
            self._wake.set()
            with self._process_lock:
                for process in self._processes.values():
                    terminate_group(process.pid, force=True)
            for thread in self._threads:
                thread.join(timeout=15)
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError('An operator worker did not stop; keep its state locked.')
            self.store.close()
            if self._lockfile:
                self._lockfile.close()
            self._closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    def _event(self, db, name, data, actor):
        cursor = db.execute('INSERT INTO events(name,data,actor,created) VALUES(?,?,?,?)', (name, encode(data), actor, time.time()))
        return cursor.lastrowid

    def _recover(self):
        with self.store.transaction() as db:
            for row in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                if row['pid'] and row['identity']:
                    saved = json.loads(row['identity'])
                    if saved and saved == process_identity(row['pid']):
                        terminate_group(row['pid'], force=True)
                db.execute("UPDATE jobs SET status='interrupted',ended=?,error=? WHERE id=?", (time.time(), 'The executor stopped before confirming completion; this job was not replayed.', row['id']))
                self._event(db, 'job.interrupted', {'id': row['id']}, 'system:recovery')

    def call(self, method, data=None, actor='operator'):
        if self._closed:
            raise RuntimeError('The operator kernel is closed.')
        if not isinstance(data if data is not None else {}, dict):
            raise ValueError('data must be an object.')
        data = data or {}
        actor = text_field(actor, 'actor', 256)
        if method == 'describe':
            from .catalog import describe
            return describe(data.get('method'))
        if isinstance(method, str) and method.startswith(('packages.', 'services.')):
            provider = self.packages if method.startswith('packages.') else self.services
            mutation = method.rsplit('.', 1)[-1] not in ('list', 'get', 'logs')
            if mutation:
                with self.store.transaction() as db:
                    self._event(db, method + '.requested', {'id': data.get('id'), 'name': data.get('name')}, actor)
            try:
                result = provider.call(method, data, actor)
            except Exception as exc:
                if mutation:
                    with self.store.transaction() as db:
                        self._event(db, method + '.failed', {'id': data.get('id'), 'name': data.get('name'), 'error': str(exc)[:2000]}, actor)
                raise
            if mutation:
                with self.store.transaction() as db:
                    self._event(db, method, {'id': data.get('id'), 'name': data.get('name')}, actor)
            return result
        handlers = {
            'discover': self._discover, 'jobs.list': self._jobs_list,
            'jobs.get': self._jobs_get, 'jobs.submit': self._jobs_submit,
            'jobs.cancel': self._jobs_cancel, 'routines.list': self._routines_list,
            'routines.upsert': self._routines_upsert, 'routines.delete': self._routines_delete,
            'routines.run': self._routines_run, 'notebooks.list': self._notebooks_list,
            'notebooks.get': self._notebooks_get, 'notebooks.save': self._notebooks_save,
            'notebooks.delete': self._notebooks_delete, 'events.list': self._events_list,
            'events.publish': self._events_publish, 'files.list': self._files_list,
            'files.read': self._files_read, 'files.write': self._files_write,
            'minecraft.query': self._minecraft_query,
            'world.call': self.native.call,
        }
        handler = handlers.get(method)
        if not handler:
            raise ValueError('Unknown operator method: ' + str(method))
        return handler(data, actor)

    def _discover(self, data, actor):
        return {'name': 'Oak Operator', 'version': 1, 'demo': self.demo,
                'authority': 'demo' if self.demo else 'host-process', 'actor': actor,
                'execution': {'kinds': ['shell', 'python', 'javascript', 'command'],
                              'max_workers': self.max_workers, 'running': self._started,
                              'state': str(self.state), 'default_cwd': str(self.state / 'workspace'),
                              'default_timeout_seconds': None, 'unlimited_timeout': None,
                              'output_page_bytes': MAX_PAGE},
                'native': {'socket_present': self.native.world.available() if not self.demo else False,
                           'event_prefix': 'minecraft.', 'event_relay': not self.demo and self._started},
                'capabilities': ['jobs', 'routines', 'notebooks', 'events', 'files', 'minecraft.query', 'packages', 'services', 'world.call'],
                'methods': ['discover', 'describe', 'jobs.list', 'jobs.get', 'jobs.submit', 'jobs.cancel',
                            'routines.list', 'routines.upsert', 'routines.delete', 'routines.run',
                            'notebooks.list', 'notebooks.get', 'notebooks.save', 'notebooks.delete',
                            'events.list', 'events.publish', 'files.list', 'files.read', 'files.write',
                            'minecraft.query', 'packages.list', 'packages.get', 'packages.install',
                            'packages.activate', 'packages.deactivate', 'services.list',
                            'services.upsert', 'services.control', 'services.logs', 'services.delete', 'world.call'],
                'scheduling': {'triggers': ['interval', 'once', 'event'], 'missed_intervals': 'coalesce',
                               'event_replay': 'durable-cursor', 'job_recovery': 'interrupt-without-replay'}}

    def _job_spec(self, data):
        if not isinstance(data, dict):
            raise ValueError('job must be an object.')
        kind = data.get('kind', 'shell')
        if kind not in ('shell', 'python', 'javascript', 'command'):
            raise ValueError('kind must be shell, python, javascript or command.')
        source = text_field(data.get('source'), 'source')
        label = text_field(data.get('label', kind), 'label', 300)
        timeout = data.get('timeout_seconds')
        if timeout is not None:
            number(timeout, 'timeout_seconds', .01)
        environment = data.get('environment', {})
        if not isinstance(environment, dict) or len(environment) > 256:
            raise ValueError('environment must be an object with at most 256 entries.')
        for key, value in environment.items():
            text_field(key, 'environment key', 256)
            if '=' in key:
                raise ValueError('Environment keys cannot contain =.')
            text_field(value, 'environment value', MAX_TEXT, empty=True)
        resources = data.get('resources', [])
        if not isinstance(resources, list) or len(resources) > 64:
            raise ValueError('resources must be an array of at most 64 names.')
        resources = sorted(set(text_field(value, 'resource', 256) for value in resources))
        if kind == 'command' and 'minecraft:commands' not in resources:
            resources.append('minecraft:commands')
        cwd = data.get('cwd') or str(self.state / 'workspace')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError('cwd must be an absolute path.')
        spec = {'kind': kind, 'source': source, 'label': label, 'cwd': cwd,
                'timeout_seconds': timeout, 'environment': environment, 'resources': resources}
        if len(encode(spec).encode()) > MAX_TEXT:
            raise ValueError('The complete job must fit within 1 MiB.')
        return spec

    def _job(self, row):
        result = dict(row)
        result['spec'] = json.loads(result['spec'])
        result['state'] = result['status']
        result['label'] = result['spec']['label']
        result['kind'] = result['spec']['kind']
        result['cancel_requested'] = bool(result['cancel_requested'])
        result.pop('identity', None)
        result['demo'] = self.demo
        return result

    def _enqueue(self, db, spec, actor, idempotency=None, routine_id=None):
        if idempotency is not None:
            text_field(idempotency, 'idempotency', 256)
            existing = db.execute('SELECT * FROM jobs WHERE idempotency=?', (idempotency,)).fetchone()
            if existing:
                if json.loads(existing['spec']) != spec or existing['actor'] != actor:
                    raise ValueError('The idempotency key was already used with a different job or actor.')
                return self._job(existing)
        job_id = str(uuid.uuid4())
        db.execute('INSERT INTO jobs(id,spec,actor,status,created,idempotency,routine_id) VALUES(?,?,?,?,?,?,?)',
                   (job_id, encode(spec), actor, 'queued', time.time(), idempotency, routine_id))
        self._event(db, 'job.queued', {'id': job_id, 'routine_id': routine_id}, actor)
        return self._job(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def _jobs_submit(self, data, actor):
        spec = self._job_spec(data)
        with self.store.transaction() as db:
            result = self._enqueue(db, spec, actor, data.get('idempotency'))
        self._wake.set()
        return result

    def _jobs_list(self, data, actor):
        limit = bounded_int(data.get('limit', 50), 'limit', 1, 200)
        offset = bounded_int(data.get('offset', 0), 'offset', 0, 2 ** 31)
        with self.store.transaction() as db:
            if data.get('status'):
                rows = db.execute('SELECT * FROM jobs WHERE status=? ORDER BY created DESC LIMIT ? OFFSET ?', (data['status'], limit + 1, offset)).fetchall()
            else:
                rows = db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
        jobs = []
        for row in rows[:limit]:
            value = self._job(row)
            spec = value['spec']
            value['source_bytes'] = len(spec.pop('source').encode())
            value['environment_keys'] = sorted(spec.pop('environment'))
            jobs.append(value)
        return {'jobs': jobs, 'next_offset': offset + len(jobs), 'has_more': len(rows) > limit}

    def _jobs_get(self, data, actor):
        job_id = identifier(data.get('id'))
        offset = bounded_int(data.get('offset', 0), 'offset', 0, 2 ** 63 - 1)
        limit = bounded_int(data.get('limit', MAX_PAGE), 'limit', 1, MAX_PAGE)
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError('Job not found.')
            result = self._job(row)
        path = self.store.outputs / (job_id + '.log')
        try:
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                offset = min(offset, size)
                stream.seek(offset)
                output = stream.read(limit)
        except FileNotFoundError:
            output, size, offset = b'', 0, 0
        result.update(output=output.decode('utf-8', errors='replace'), offset=offset,
                      next_offset=offset + len(output), output_size=size,
                      output_path=str(path), has_more=offset + len(output) < size)
        return result

    def _jobs_cancel(self, data, actor):
        job_id = identifier(data.get('id'))
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError('Job not found.')
            if row['status'] not in TERMINAL:
                if row['status'] == 'queued':
                    db.execute("UPDATE jobs SET cancel_requested=1,status='cancelled',ended=? WHERE id=?", (time.time(), job_id))
                else:
                    db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
                self._event(db, 'job.cancel_requested', {'id': job_id}, actor)
            result = self._job(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        self._wake.set()
        return result

    def _claim(self):
        with self.store.transaction() as db:
            occupied = set()
            for row in db.execute("SELECT spec FROM jobs WHERE status='running'"):
                occupied.update(json.loads(row['spec'])['resources'])
            for row in db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1000").fetchall():
                if occupied.intersection(json.loads(row['spec'])['resources']):
                    continue
                db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row['id']))
                self._event(db, 'job.started', {'id': row['id']}, row['actor'])
                return self._job(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
        return None

    def _worker(self):
        while not self._stop.is_set():
            try:
                job = self._claim()
                if job:
                    self._execute(job)
                    continue
            except Exception as exc:
                try:
                    with self.store.transaction() as db:
                        self._event(db, 'worker.error', {'error': str(exc)[:1000]}, 'system:worker')
                except Exception:
                    pass
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _execute(self, job):
        job_id, spec = job['id'], job['spec']
        path = self.store.outputs / (job_id + '.log')
        status, error, exit_code = 'failed', None, None
        process = None
        try:
            with path.open('wb') as output:
                path.chmod(0o600)
                if self.demo:
                    output.write(('[demo] Execution simulated; no command was delivered.\n' + spec['source'] + '\n').encode())
                    status, exit_code = 'succeeded', 0
                elif spec['kind'] == 'command' and self.runtime is not None and not hasattr(self.runtime, 'root'):
                    # Injected test runtime; production uses the cancellable child.
                    with self.runtime.lock() if hasattr(self.runtime, 'lock') else nullcontext():
                        for line in spec['source'].splitlines():
                            if line.strip():
                                output.write((str(self.runtime.rcon.command(line.lstrip('/'))) + '\n').encode())
                    status, exit_code = 'succeeded', 0
                else:
                    job_path = self.store.outputs / (job_id + '.json')
                    atomic(job_path, encode(spec))
                    from . import runner
                    argv = [sys.executable, '-u', str(Path(runner.__file__).resolve()), '--execute', '--job', str(job_path)]
                    if self.runtime is not None and hasattr(self.runtime, 'root'):
                        argv.extend(['--root', str(self.runtime.root)])
                    kwargs = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                    package_root = str(Path(__file__).resolve().parents[1])
                    python_path = os.pathsep.join(filter(None, (package_root, os.environ.get('PYTHONPATH'))))
                    process = subprocess.Popen(argv, cwd=spec['cwd'], env={**os.environ, 'PYTHONPATH': python_path, **spec['environment']},
                                               stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, **kwargs)
                    attach_windows_job(process)
                    with self._process_lock:
                        self._processes[job_id] = process
                    with self.store.transaction() as db:
                        db.execute('UPDATE jobs SET pid=?,identity=? WHERE id=?', (process.pid, encode(process_identity(process.pid)), job_id))
                    # The child cannot execute source before its identity is durable.
                    process.stdin.write(b'go\n')
                    process.stdin.close()
                    started = time.monotonic()
                    while process.poll() is None:
                        with self.store.transaction() as db:
                            cancel = db.execute('SELECT cancel_requested FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
                        if self._stop.is_set() or cancel or (spec['timeout_seconds'] is not None and time.monotonic() - started >= spec['timeout_seconds']):
                            status = 'interrupted' if self._stop.is_set() else ('cancelled' if cancel else 'timed_out')
                            terminate_group(process.pid)
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                terminate_group(process.pid, force=True)
                                process.wait(timeout=5)
                            break
                        self._stop.wait(.05)
                    exit_code = process.wait()
                    if status not in ('interrupted', 'cancelled', 'timed_out'):
                        status = 'succeeded' if exit_code == 0 else 'failed'
                    # Background descendants belong to this job, not unmanaged daemons.
                    # Persistent services should be registered through systemd.
                    terminate_group(process.pid, force=True)
                output.flush()
                os.fsync(output.fileno())
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:2000]
        finally:
            if process is not None and process.poll() is None:
                terminate_group(process.pid, force=True)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                close_windows_job(process.pid)
            with self._process_lock:
                self._processes.pop(job_id, None)
            with self.store.transaction() as db:
                cancel = db.execute('SELECT cancel_requested FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
                if self._stop.is_set():
                    status = 'interrupted'
                if cancel and status == 'succeeded':
                    status = 'cancelled'
                db.execute('UPDATE jobs SET status=?,ended=?,exit_code=?,error=? WHERE id=?', (status, time.time(), exit_code, error, job_id))
                self._event(db, 'job.' + status, {'id': job_id, 'exit_code': exit_code, 'error': error}, job['actor'])
            self._wake.set()

    def _routine(self, row):
        result = dict(row)
        result.update(json.loads(result.pop('spec')))
        result['enabled'] = bool(result['enabled'])
        return result

    def _routines_list(self, data, actor):
        limit = bounded_int(data.get('limit', 100), 'limit', 1, 200)
        offset = bounded_int(data.get('offset', 0), 'offset', 0, 2 ** 31)
        with self.store.transaction() as db:
            rows = db.execute('SELECT * FROM routines ORDER BY updated DESC LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
            values, size = [], 0
            for row in rows[:limit]:
                value = self._routine(row)
                item_size = len(encode(value).encode())
                if values and size + item_size > 2 * MAX_TEXT:
                    break
                values.append(value)
                size += item_size
            return {'routines': values, 'next_offset': offset + len(values), 'has_more': len(rows) > len(values)}

    def _routines_upsert(self, data, actor):
        routine_id = identifier(data['id']) if data.get('id') else str(uuid.uuid4())
        name = text_field(data.get('name'), 'name', 300)
        trigger = data.get('trigger')
        if not isinstance(trigger, dict):
            raise ValueError('trigger must be an object.')
        kind = trigger.get('type')
        now = time.time()
        if kind == 'interval':
            seconds = number(trigger.get('seconds'), 'seconds', .1)
            trigger, next_at = {'type': kind, 'seconds': seconds}, now + seconds
        elif kind == 'once':
            at = number(trigger.get('at'), 'at')
            trigger, next_at = {'type': kind, 'at': at}, at
        elif kind == 'event':
            trigger, next_at = {'type': kind, 'name': text_field(trigger.get('name'), 'event name', 256)}, None
        else:
            raise ValueError('trigger.type must be interval, once or event.')
        enabled = data.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('enabled must be a boolean.')
        spec = {'trigger': trigger, 'job': self._job_spec(data.get('job'))}
        with self.store.transaction() as db:
            current = db.execute('SELECT * FROM routines WHERE id=?', (routine_id,)).fetchone()
            if current and data.get('revision') != current['revision']:
                raise ValueError('Routine revision conflict; read the current version before saving.')
            if not current and data.get('id'):
                raise KeyError('Routine not found.')
            revision = current['revision'] + 1 if current else 1
            event_cursor = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            if current and json.loads(current['spec'])['trigger'] == trigger:
                if current['enabled'] and enabled:
                    event_cursor = current['event_cursor']
                    next_at = current['next_at']
            db.execute('INSERT INTO routines VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,spec=excluded.spec,enabled=excluded.enabled,revision=excluded.revision,next_at=excluded.next_at,event_cursor=excluded.event_cursor,actor=excluded.actor,updated=excluded.updated',
                       (routine_id, name, encode(spec), int(enabled), revision, next_at, event_cursor, actor, current['created'] if current else now, now))
            self._event(db, 'routine.saved', {'id': routine_id, 'revision': revision}, actor)
            result = self._routine(db.execute('SELECT * FROM routines WHERE id=?', (routine_id,)).fetchone())
        self._wake.set()
        return result

    def _routines_delete(self, data, actor):
        routine_id = identifier(data.get('id'))
        with self.store.transaction() as db:
            row = db.execute('SELECT revision FROM routines WHERE id=?', (routine_id,)).fetchone()
            if not row:
                raise KeyError('Routine not found.')
            if data.get('revision') != row['revision']:
                raise ValueError('Routine revision conflict.')
            db.execute('DELETE FROM routines WHERE id=?', (routine_id,))
            self._event(db, 'routine.deleted', {'id': routine_id}, actor)
        return {'id': routine_id, 'deleted': True}

    def _routines_run(self, data, actor):
        routine_id = identifier(data.get('id'))
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM routines WHERE id=?', (routine_id,)).fetchone()
            if not row:
                raise KeyError('Routine not found.')
            result = self._enqueue(db, json.loads(row['spec'])['job'], actor, data.get('idempotency'), routine_id)
        self._wake.set()
        return result

    def _scheduler(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                with self.store.transaction() as db:
                    self._event(db, 'scheduler.error', {'error': str(exc)[:1000]}, 'system:scheduler')
            self._stop.wait(self.poll_seconds)

    def _tick(self):
        now = time.time()
        with self.store.transaction() as db:
            high_water = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            for row in db.execute('SELECT * FROM routines WHERE enabled=1').fetchall():
                spec = json.loads(row['spec'])
                trigger = spec['trigger']
                actor = row['actor']
                if trigger['type'] == 'event':
                    events = db.execute('SELECT * FROM events WHERE id>? AND id<=? AND name=? ORDER BY id LIMIT 100', (row['event_cursor'], high_water, trigger['name'])).fetchall()
                    for event in events:
                        job_spec = {**spec['job'], 'environment': {**spec['job']['environment'], 'OAK_EVENT': encode({'id': event['id'], 'name': event['name'], 'data': json.loads(event['data'])})}}
                        self._enqueue(db, job_spec, actor, f"event:{row['id']}:{row['revision']}:{event['id']}", row['id'])
                    cursor = events[-1]['id'] if len(events) == 100 else high_water
                    db.execute('UPDATE routines SET event_cursor=? WHERE id=?', (cursor, row['id']))
                elif row['next_at'] is not None and row['next_at'] <= now:
                    self._enqueue(db, spec['job'], actor, f"timer:{row['id']}:{row['revision']}:{row['next_at']}", row['id'])
                    if trigger['type'] == 'once':
                        db.execute('UPDATE routines SET enabled=0,next_at=NULL WHERE id=?', (row['id'],))
                    else:
                        # A downtime coalesces missed occurrences into one durable job.
                        next_at = row['next_at'] + (math.floor((now - row['next_at']) / trigger['seconds']) + 1) * trigger['seconds']
                        db.execute('UPDATE routines SET next_at=? WHERE id=?', (next_at, row['id']))

    def _notebooks_list(self, data, actor):
        with self.store.transaction() as db:
            rows = db.execute('SELECT id,title,revision,actor,created,updated FROM notebooks ORDER BY updated DESC').fetchall()
            return {'notebooks': [dict(row) for row in rows]}

    def _notebooks_get(self, data, actor):
        notebook_id = identifier(data.get('id'))
        with self.store.transaction() as db:
            if data.get('revision') is not None:
                row = db.execute('SELECT * FROM notebook_versions WHERE id=? AND revision=?', (notebook_id, bounded_int(data['revision'], 'revision', 1, 2 ** 31))).fetchone()
            else:
                row = db.execute('SELECT * FROM notebooks WHERE id=?', (notebook_id,)).fetchone()
            if not row:
                raise KeyError('Notebook not found.')
            result = dict(row)
            result['versions'] = [dict(version) for version in db.execute('SELECT revision,actor,created FROM notebook_versions WHERE id=? ORDER BY revision DESC', (notebook_id,))]
            result['path'] = str(self.store.notebooks / notebook_id / f"{row['revision']}.md")
            return result

    def _notebooks_save(self, data, actor):
        notebook_id = identifier(data['id']) if data.get('id') else str(uuid.uuid4())
        title = text_field(data.get('title'), 'title', 300)
        content = text_field(data.get('content', ''), 'content', empty=True)
        now = time.time()
        with self.store.transaction() as db:
            current = db.execute('SELECT * FROM notebooks WHERE id=?', (notebook_id,)).fetchone()
            if current and data.get('revision') != current['revision']:
                raise ValueError('Notebook revision conflict; read the current version before saving.')
            if not current and data.get('id'):
                raise KeyError('Notebook not found.')
            revision = current['revision'] + 1 if current else 1
            atomic(self.store.notebooks / notebook_id / f'{revision}.md', content)
            db.execute('INSERT INTO notebooks VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,content=excluded.content,revision=excluded.revision,actor=excluded.actor,updated=excluded.updated', (notebook_id, title, content, revision, actor, current['created'] if current else now, now))
            db.execute('INSERT INTO notebook_versions VALUES(?,?,?,?,?,?)', (notebook_id, revision, title, content, actor, now))
            self._event(db, 'notebook.saved', {'id': notebook_id, 'revision': revision}, actor)
        return self._notebooks_get({'id': notebook_id}, actor)

    def _notebooks_delete(self, data, actor):
        notebook_id = identifier(data.get('id'))
        with self.store.transaction() as db:
            row = db.execute('SELECT revision FROM notebooks WHERE id=?', (notebook_id,)).fetchone()
            if not row:
                raise KeyError('Notebook not found.')
            if data.get('revision') != row['revision']:
                raise ValueError('Notebook revision conflict.')
            db.execute('DELETE FROM notebooks WHERE id=?', (notebook_id,))
            self._event(db, 'notebook.deleted', {'id': notebook_id}, actor)
        return {'id': notebook_id, 'deleted': True, 'history_retained': True}

    def _events_list(self, data, actor):
        after = bounded_int(data.get('after', 0), 'after', 0, 2 ** 63 - 1)
        limit = bounded_int(data.get('limit', 100), 'limit', 1, 500)
        with self.store.transaction() as db:
            if data.get('name'):
                rows = db.execute('SELECT * FROM events WHERE id>? AND name=? ORDER BY id LIMIT ?', (after, text_field(data['name'], 'name', 256), limit)).fetchall()
            else:
                rows = db.execute('SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?', (after, limit)).fetchall()
        events, size = [], 0
        for row in rows:
            value = {**dict(row), 'data': json.loads(row['data'])}
            item_size = len(encode(value).encode())
            if events and size + item_size > 2 * MAX_TEXT:
                break
            events.append(value)
            size += item_size
        return {'events': events, 'next_cursor': events[-1]['id'] if events else after,
                'has_more': len(events) < len(rows) or len(rows) == limit}

    def _events_publish(self, data, actor):
        name = text_field(data.get('name'), 'name', 256)
        payload = data.get('data', {})
        if len(encode(payload).encode()) > 32768:
            raise ValueError('Event data must fit within 32768 bytes.')
        with self.store.transaction() as db:
            event_id = self._event(db, name, payload, actor)
        self._wake.set()
        return {'id': event_id, 'name': name, 'data': payload}

    def _path(self, data):
        value = text_field(data.get('path'), 'path', 32768)
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('path must be absolute.')
        path = path.resolve()
        if self.demo:
            # Demo must never read or mutate arbitrary host files.
            if not path.is_relative_to(self.state / 'workspace'):
                raise ValueError('Demo files must stay within its workspace.')
        return path

    def _files_list(self, data, actor):
        path = self._path(data)
        limit = bounded_int(data.get('limit', 200), 'limit', 1, 1000)
        offset = bounded_int(data.get('offset', 0), 'offset', 0, 2 ** 31)
        entries = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        files = []
        for entry in entries[offset:offset + limit]:
            try:
                stat = entry.stat()
                files.append({'name': entry.name, 'path': str(entry), 'type': 'directory' if entry.is_dir() else 'file', 'size': stat.st_size, 'modified': stat.st_mtime, 'symlink': entry.is_symlink()})
            except OSError:
                files.append({'name': entry.name, 'path': str(entry), 'type': 'unavailable'})
        return {'path': str(path), 'files': files, 'next_offset': offset + len(files), 'has_more': offset + len(files) < len(entries)}

    def _files_read(self, data, actor):
        path = self._path(data)
        offset = bounded_int(data.get('offset', 0), 'offset', 0, 2 ** 63 - 1)
        limit = bounded_int(data.get('limit', MAX_PAGE), 'limit', 1, MAX_PAGE)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with self._file_lock, os.fdopen(descriptor, 'rb') as stream:
            snapshot = os.fstat(stream.fileno())
            regular = stat.S_ISREG(snapshot.st_mode)
            size = snapshot.st_size
            digest = None
            if regular and size > 0 and data.get('include_sha256', True):
                digest = hashlib.sha256()
                remaining = size
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    digest.update(block)
                    remaining -= len(block)
            if stream.seekable():
                offset = min(offset, size) if size else offset
                stream.seek(offset)
            elif offset:
                raise ValueError('This stream does not support an offset.')
            content = stream.read(limit) or b''
            if regular and not size and not content:
                digest = hashlib.sha256()
        return {'path': str(path), 'content': content.decode('utf-8', errors='replace'),
                'sha256': digest.hexdigest() if digest else None, 'size': size,
                'offset': offset, 'next_offset': offset + len(content),
                'has_more': offset + len(content) < size, 'regular_file': regular}

    def _files_write(self, data, actor):
        path = self._path(data)
        content = text_field(data.get('content'), 'content', empty=True)
        with self._file_lock:
            expected = data.get('expected_sha256', data.get('expected_hash'))
            if expected is not None:
                current = self._files_read({'path': str(path), 'limit': 1}, actor)['sha256'] if path.exists() else ''
                if expected != current:
                    raise ValueError('File hash conflict; read the current file before writing.')
            atomic(path, content)
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self.store.transaction() as db:
            self._event(db, 'file.written', {'path': str(path), 'sha256': digest, 'bytes': len(content.encode())}, actor)
        return {'path': str(path), 'sha256': digest, 'size': len(content.encode())}

    def _minecraft_query(self, data, actor):
        if self.demo:
            return {'demo': True, 'players': [], 'message': 'No game connection in demo mode.'}
        if self.runtime is None:
            raise RuntimeError('Minecraft runtime is not configured.')
        if data.get('command'):
            # General commands are durable jobs, including read-oriented queries.
            return self._jobs_submit({'kind': 'command', 'source': text_field(data['command'], 'command'), 'label': 'Minecraft query'}, actor)
        return self.runtime.snapshot()
