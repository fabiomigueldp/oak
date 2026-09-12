"""Local, encrypted restic storage behind Oak's bounded recovery operations.

The catalog is private evidence, not a second copy of repository contents. Every
restore is read through restic's authenticated storage and Oak's archive checks.
No user-controlled repository, command, password or filesystem path is accepted.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
import zipfile

GIB = 1024 ** 3
SNAPSHOT = re.compile(r'[a-f0-9]{64}\Z')
DEFAULT_POLICY = {'enabled': False, 'interval_minutes': 180, 'budget_gib': 20,
                  'check_days': 7, 'boot_days': 7}


def game_version(source):
    """Read the archived launcher target, not stale cached version directories."""
    target = source / 'server.jar'
    launcher = source / 'fabric-server-launcher.properties'
    if launcher.is_file():
        values = dict(line.split('=', 1) for line in launcher.read_text().splitlines() if '=' in line and not line.startswith('#'))
        candidate = source / values.get('serverJar', 'server.jar')
        if candidate.resolve().is_relative_to(source.resolve()):
            target = candidate
    if not target.is_file():
        return None
    try:
        with zipfile.ZipFile(target) as archive:
            if archive.getinfo('version.json').file_size > 65536:
                return None
            version = json.loads(archive.read('version.json')).get('id')
            return version if isinstance(version, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', version) else None
    except (zipfile.BadZipFile, KeyError, ValueError):
        return None


def validate_policy(value):
    if not isinstance(value, dict) or value.keys() != DEFAULT_POLICY.keys():
        raise ValueError('Invalid backup policy fields.')
    if type(value['enabled']) is not bool:
        raise ValueError('Enabled must be a boolean.')
    for key, low, high in [('interval_minutes', 5, 525600),
                           ('budget_gib', 1, 100000), ('check_days', 1, 365), ('boot_days', 1, 365)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'Invalid {key}: expected {low}..{high}.')
    return dict(value)


def retention(points, policy):
    """Oldest-first eviction candidates; actual reclaimed bytes decide eviction."""
    return [p['id'] for p in sorted(points, key=lambda p: p['created'])[:-1]]


class Repository:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.control / 'repository'
        self.password = runtime.control / 'repository.key'
        self.catalog = runtime.control / 'catalog'
        self.policy_path = runtime.control / 'backup-policy.json'
        self.health_path = runtime.control / 'repository-health.json'

    @property
    def ready(self):
        return (self.root / 'config').is_file() and self.password.is_file()

    def write(self, path, value):
        from .runtime import atomic
        atomic(path, json.dumps(value))

    def read(self, path, default):
        return json.loads(path.read_text()) if path.exists() else default

    def command(self, *args, cwd=None, output=None, timeout=900):
        env = dict(os.environ, GOMAXPROCS='2')
        # Never inherit alternate repositories, credentials or remote commands.
        env = {k: v for k, v in env.items() if not k.startswith('RESTIC_')}
        result = subprocess.run(['restic', '--repo', str(self.root), '--password-file', str(self.password),
                                 '--no-cache', *args], cwd=cwd, env=env,
                                stdout=output if output is not None else subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            # CLI diagnostics can contain private paths and runtime data.
            from .runtime import atomic
            atomic(self.runtime.control / 'repository-last-error.log', result.stderr[-32768:])
            raise RuntimeError(f'Repository {args[0]} failed (exit {result.returncode}). Inspect the host repository before retrying.')
        return result.stdout

    def initialize(self):
        if self.ready:
            return
        if self.root.exists() or self.password.exists():
            raise RuntimeError('Partial repository initialization requires operator inspection.')
        from .runtime import atomic
        atomic(self.password, secrets.token_urlsafe(48))
        self.command('init', '--repository-version', '2')
        self.root.chmod(0o700)
        self.write(self.policy_path, {'revision': 1, **DEFAULT_POLICY})

    def policy(self):
        saved = self.read(self.policy_path, {})
        return {'revision': saved.get('revision', 0), **{k: saved.get(k, v) for k, v in DEFAULT_POLICY.items()}}

    def points(self):
        if not self.ready:
            return []
        return sorted((self.read(p, {}) for p in self.catalog.glob('*.json')), key=lambda p: p['created'], reverse=True)

    def point(self, identifier):
        if not isinstance(identifier, str) or not SNAPSHOT.fullmatch(identifier):
            raise ValueError('Invalid snapshot identifier.')
        path = self.catalog / (identifier + '.json')
        if not path.is_file():
            raise ValueError('Recovery point no longer exists.')
        return self.read(path, {})

    def environment(self, fresh=True):
        """Fingerprint dependencies outside the restored game directory."""
        cached = getattr(self.runtime, '_backup_environment', None)
        if not fresh and cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        from .runtime import sha256
        paths = [Path('/etc/systemd/system/oak.service'), Path('/etc/systemd/system/oak.service.d'),
                 self.runtime.root / 'crossplay' / 'bridge',
                 self.runtime.root / 'crossplay' / 'geyser']
        entries = {}
        for root in paths:
            for p in sorted(root.rglob('*')) if root.is_dir() else [root]:
                if p.is_file() and not p.is_symlink() and (p.suffix in ('.conf', '.service', '.py', '.jar', '.yml', '.yaml', '.pem', '.key') or p == root):
                    if not any(part in ('logs', '__pycache__', 'cache') for part in p.parts):
                        entries[str(p)] = sha256(p)
        digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        self.runtime._backup_environment = (time.monotonic(), digest)
        return digest

    def compatible(self, point, environment=None):
        return bool(point.get('manifest', {}).get('environment') == (environment or self.environment()))

    def status(self):
        points, policy = self.points(), self.policy()
        health = self.read(self.health_path, {})
        used = health.get('bytes', 0)
        environment = self.environment(fresh=False) if points else None
        for p in points:
            p['compatible'] = self.compatible(p, environment)
            # Actual extraction authenticates the selected data before replacement.
            p['restorable'] = p.get('manifest', {}).get('includes_runtime', False)
        newest = points[0]['created'] if points else None
        external = self.read(self.runtime.control / 'external-copy.json', {})
        external_at = external.get('verified_at', 0)
        return {'ready': self.ready, 'engine': 'restic', 'sampled_at': time.time(), 'backups': points,
                'policy': policy, 'bytes': used, 'logical_bytes': sum(p['bytes'] for p in points),
                'free_bytes': shutil.disk_usage(self.runtime.root).free, 'reserve_bytes': self.runtime.free_reserve,
                'next_run': (max(newest or 0, health.get('last_attempt', 0)) + policy['interval_minutes'] * 60) if policy['enabled'] else None,
                'health': health, 'external_copy': bool(external_at and 0 <= time.time() - external_at < 48 * 3600),
                'external_copy_verified_at': external_at or None, 'prunable': retention(points, policy),
                'recovery_pending': (self.runtime.control / 'restore-pending.json').exists()}

    def update_policy(self, params):
        policy = self.policy()
        if params['revision'] != policy['revision']:
            raise ValueError('A política mudou. Atualize antes de salvar.')
        if not self.ready:
            raise ValueError('O repositório ainda não foi instalado.')
        updated = {'revision': policy['revision'] + 1, **validate_policy(params['policy'])}
        self.write(self.policy_path, updated)
        self.publish()
        return updated

    def publish(self):
        points, policy = self.points(), self.policy()
        self.write(self.runtime.control / 'backup-public.json', {
            'last': points[0]['created'] if points else None, 'count': len(points),
            'bytes': self.read(self.health_path, {}).get('bytes', 0), 'enabled': policy['enabled'],
            'interval_minutes': policy['interval_minutes'], 'engine': 'restic'})

    def measure(self, **extra):
        value = self.read(self.health_path, {})
        value.update(bytes=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()), sampled_at=time.time(), **extra)
        self.write(self.health_path, value)
        self.publish()
        return value

    def cleanup_recovery(self):
        """Remove only explicitly completed, expired recovery workspaces."""
        if (self.runtime.control / 'restore-pending.json').exists():
            return
        for name in ('rollback', 'restores', 'discarded'):
            parent = (self.runtime.control / name).resolve()
            for folder in parent.glob('*'):
                if folder.is_symlink() or not folder.is_dir() or not re.fullmatch(r'[a-f0-9-]{36}', folder.name):
                    continue
                marker = folder / 'completed.json'
                if marker.is_file() and self.read(marker, {}).get('at', time.time()) < time.time() - 86400:
                    if folder.resolve().parent == parent and parent.parent == self.runtime.control.resolve():
                        shutil.rmtree(folder)

    def content_signature(self, source, root=None):
        """Ignore empty-server clock/lock churn, but detect world and runtime edits."""
        from .runtime import sha256
        from .world_metadata import fingerprint
        root = root or self.runtime.server
        cached = self.read(self.runtime.control / 'source-fingerprints.json', {})
        updated = {}
        entries = {}
        for name, metadata in source.items():
            if name in ('world/level.dat_old', 'world/session.lock'):
                continue
            previous = cached.get(name)
            if previous and previous[:2] == list(metadata):
                entries[name] = previous[2]
            else:
                entries[name] = fingerprint(root / name) if name == 'world/level.dat' else sha256(root / name)
            updated[name] = [*metadata, entries[name]]
        self.write(self.runtime.control / 'source-fingerprints.json', updated)
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()

    def capture(self, job, name, progress, *, automatic=False, activity_at=0, reclaim=True):
        from .runtime import atomic, sha256
        r = self.runtime
        stage = r.control / 'staging' / job
        if stage.exists():
            raise RuntimeError('Capture staging already exists. Inspect the operation receipt.')
        stage.mkdir(parents=True, mode=0o700)
        marker = r.control / 'saving-disabled.json'
        started = time.time()
        try:
            with r.legacy_backup_lock():
                source = r.inventory(r.server)
                size = sum(v[0] for v in source.values())
                signature = self.content_signature(source)
                previous = self.points()
                health = self.read(self.health_path, {})
                environment = self.environment()
                if (automatic and previous and health.get('last_snapshot') == previous[0]['id'] and signature == health.get('content_signature')
                        and environment == previous[0].get('manifest', {}).get('environment')
                        and activity_at <= health.get('activity_at', 0)):
                    self.measure(last_attempt=time.time(), last_skipped=time.time())
                    return {'skipped': True, 'reason': 'Sem alterações', 'backup': previous[0]['id']}
                if reclaim:
                    self.compact(progress, workspace=size * 2.1)
                r.require_space(size * 2.1)
                initial_repository_bytes = self.measure()['bytes']
                live = r.active()
                progress('Salvando mundo', 'Preparing a stable, separate copy.')
                try:
                    if live:
                        atomic(marker, json.dumps({'job': job, 'created': time.time()}))
                        r.rcon.command('save-off')
                        if 'Saved' not in r.rcon.command('save-all flush'):
                            raise RuntimeError('World flush was not confirmed.')
                    source = r.stable_copy(stage, progress)
                finally:
                    if marker.exists():
                        r.rcon.command('save-on')
                        marker.unlink()
                captured = time.time()
                atomic(r.control / 'last-save.json', json.dumps({'at': captured, 'source': 'backup', 'job': job}))
                manifest = {'format': 2, 'captured': captured, 'method': 'flush-and-stable-copy' if live else 'stopped-copy',
                            'versions': sorted(p.name for p in (stage / 'versions').iterdir()) if (stage / 'versions').exists() else [],
                            'files': len(source), 'source_bytes': sum(v[0] for v in source.values()),
                            'environment': self.environment(), 'includes_runtime': (stage / 'fabric-server-launch.jar').is_file() and (stage / 'libraries').is_dir(),
                            'mods': {p.name: sha256(p) for p in (stage / 'mods').glob('*.jar')}}
                launcher = stage / 'fabric-server-launcher.properties'
                match = re.search(r'versions/([^/\s]+)/', launcher.read_text()) if launcher.exists() else None
                manifest['version'] = game_version(stage) or (match[1] if match else ', '.join(manifest['versions']))
                manifest.update(name=name, job=job)
                atomic(stage / 'oak-manifest.json', json.dumps(manifest))
                progress('Gravando alterações', 'Deduplicating and encrypting the staged copy.')
                output = self.command('backup', '--json', '--host', 'oak', '--tag', 'oak-control-v2', '--tag', 'job:' + job, '.', cwd=stage)
                summary = next((json.loads(line) for line in output.splitlines() if json.loads(line).get('message_type') == 'summary'), None)
                if not summary or not summary.get('snapshot_id'):
                    raise RuntimeError('Repository did not confirm a snapshot.')
                # The full ID, not an abbreviated display ID, binds all later actions.
                snaps = json.loads(self.command('snapshots', '--json', '--tag', 'job:' + job))
                if len(snaps) != 1 or not SNAPSHOT.fullmatch(snaps[0]['id']):
                    raise RuntimeError('Snapshot identity could not be confirmed.')
                identifier = snaps[0]['id']
                point = {'id': identifier, 'fingerprint': identifier, 'name': name, 'created': captured,
                         'bytes': manifest['source_bytes'], 'added_bytes': summary.get('data_added_packed', summary.get('data_added', 0)),
                         'duration': round(time.time() - started, 1), 'source': 'repository', 'integrity': False,
                         'restoration': {}, 'pinned': False, 'manifest': manifest, 'job': job, 'replicated': False}
                self.write(self.catalog / (identifier + '.json'), point)
                progress('Verificando índice', 'Checking repository structure after capture.')
                self.command('check')
                point['integrity'] = True
                point['duration'] = round(time.time() - started, 1)
                self.write(self.catalog / (identifier + '.json'), point)
                # Retention never runs inside a restore's safety capture: its selected
                # source must remain available until replacement finishes.
                measured = self.measure(structure_checked=time.time(), last_attempt=time.time(), last_snapshot=identifier,
                                        content_signature=self.content_signature(source, stage), activity_at=max(activity_at, captured))
                # restic 0.16 summary data_added is logical, not stored bytes.
                point['added_bytes'] = max(0, measured['bytes'] - initial_repository_bytes)
                self.write(self.catalog / (identifier + '.json'), point)
                if reclaim:
                    self.compact(progress)
                return {'backup': identifier, 'sha256': identifier, 'bytes': point['bytes'], 'added_bytes': point['added_bytes'],
                        'duration': point['duration'], 'integrity': True, 'replicated': False}
        finally:
            if stage.is_dir() and stage.parent == r.control / 'staging':
                shutil.rmtree(stage)

    @contextmanager
    def archive(self, identifier):
        point = self.point(identifier)
        self.runtime.require_space(point['bytes'] * 2.2)
        import tempfile
        # Materialize only for a recovery drill or restore, never for routine capture.
        with tempfile.TemporaryDirectory(prefix='repository-', dir=self.runtime.control) as folder:
            path = Path(folder) / 'recovery.tar'
            with path.open('xb') as stream:
                self.command('dump', '--archive', 'tar', identifier, '/', output=stream)
            yield path

    def annotate(self, params):
        point = self.point(params['backup'])
        if 'name' in params:
            point['name'] = params['name']
        if 'pinned' in params:
            point['pinned'] = params['pinned']
        self.write(self.catalog / (point['id'] + '.json'), point)
        return {'backup': point['id'], 'name': point['name'], 'pinned': point['pinned']}

    def delete(self, identifier):
        self.point(identifier)
        self.command('forget', identifier)
        (self.catalog / (identifier + '.json')).unlink()
        self.measure(needs_reclaim=True)
        return {'deleted': identifier, 'space_reclaimed': False}

    def compact(self, progress, revision=None, workspace=0):
        """Reclaim on demand, never erase the final automatic recovery point."""
        self.cleanup_recovery()
        budget = self.policy()['budget_gib'] * GIB
        measured = self.measure()
        removed = 0
        def pressure():
            return measured['bytes'] > budget or shutil.disk_usage(self.runtime.root).free < self.runtime.free_reserve + workspace
        if measured.get('needs_reclaim') or pressure():
            progress('Liberando espaço', 'Reclaiming unreferenced storage before expiring recovery points.')
            self.command('prune', '--max-unused', '0', '--max-repack-size', '1G')
            measured = self.measure(needs_reclaim=False)
        points = self.points()
        if pressure() and len(points) > 1 and (measured.get('check_failed') or not points[0].get('integrity')):
            # Before expiring alternatives, authenticate the point that will remain.
            with open(os.devnull, 'wb') as sink:
                self.command('dump', '--archive', 'tar', points[0]['id'], '/', output=sink)
        while pressure():
            candidates = retention(self.points(), self.policy())
            if not candidates:
                break
            self.delete(candidates[0])
            removed += 1
            self.command('prune', '--max-unused', '0', '--max-repack-size', '1G')
            measured = self.measure(needs_reclaim=False)
        return {'removed': removed, **self.measure(compacted=time.time(),
                capacity_limited=measured['bytes'] > budget)}

    def check(self, progress):
        progress('Verificando dados', 'Reading and authenticating every repository pack.')
        try:
            self.command('check', '--read-data')
        except Exception:
            self.measure(check_failed=time.time())
            raise
        # Reconcile a capture interrupted between restic commit and catalog write,
        # or a deletion interrupted between forget and catalog removal. Unknown
        # snapshots default to pinned so catalog recovery cannot erase evidence.
        snapshots = json.loads(self.command('snapshots', '--json', '--tag', 'oak-control-v2'))
        ids = {s['id'] for s in snapshots}
        for snapshot in snapshots:
            identifier = snapshot['id']
            if not SNAPSHOT.fullmatch(identifier):
                raise RuntimeError('Invalid repository snapshot identity.')
            path = self.catalog / (identifier + '.json')
            if not path.exists():
                manifest = json.loads(self.command('dump', identifier, '/oak-manifest.json'))
                self.write(path, {'id': identifier, 'fingerprint': identifier, 'name': manifest.get('name', 'Ponto recuperado'),
                                  'created': manifest['captured'], 'bytes': manifest['source_bytes'], 'source': 'repository',
                                  'integrity': True, 'restoration': {}, 'pinned': True, 'manifest': manifest, 'replicated': False})
        for point in self.points():
            path = self.catalog / (point['id'] + '.json')
            if point['id'] not in ids:
                path.unlink()
            elif not point['integrity']:
                point['integrity'] = True
                self.write(path, point)
        return self.measure(data_checked=time.time(), check_failed=None)
