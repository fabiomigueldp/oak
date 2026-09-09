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

GIB = 1024 ** 3
SNAPSHOT = re.compile(r'[a-f0-9]{64}\Z')
DEFAULT_POLICY = {'enabled': False, 'interval_minutes': 180, 'keep_recent': 16,
                  'keep_daily': 7, 'keep_weekly': 4, 'budget_gib': 20,
                  'check_days': 7, 'boot_days': 7}


def validate_policy(value):
    if not isinstance(value, dict) or value.keys() != DEFAULT_POLICY.keys():
        raise ValueError('Invalid backup policy fields.')
    if type(value['enabled']) is not bool:
        raise ValueError('Enabled must be a boolean.')
    for key, low, high in [('interval_minutes', 30, 10080), ('keep_recent', 2, 96),
                           ('keep_daily', 0, 90), ('keep_weekly', 0, 52),
                           ('budget_gib', 5, 80), ('check_days', 1, 30), ('boot_days', 1, 30)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'Invalid {key}: expected {low}..{high}.')
    return dict(value)


def retention(points, policy):
    """Union of recent points, calendar buckets, pins and last successful boot."""
    points = sorted(points, key=lambda p: p['created'], reverse=True)
    keep = {p['id'] for p in points[:policy['keep_recent']]}
    keep.update(p['id'] for p in points if p.get('pinned'))
    tested = next((p for p in points if p.get('restoration', {}).get('playable_boot_tested')), None)
    if tested:
        keep.add(tested['id'])
    for key, pattern in [('keep_daily', '%Y-%m-%d'), ('keep_weekly', '%G-%V')]:
        buckets = set()
        for point in points:
            bucket = datetime.fromtimestamp(point['created'], timezone(timedelta(hours=-3))).strftime(pattern)
            if bucket not in buckets and len(buckets) < policy[key]:
                buckets.add(bucket)
                keep.add(point['id'])
    return [p['id'] for p in points if p['id'] not in keep]


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
        return self.read(self.policy_path, {'revision': 0, **DEFAULT_POLICY})

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
                if p.is_file() and not p.is_symlink() and (p.suffix in ('.conf', '.service', '.py', '.jar', '.yml', '.yaml') or p == root):
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
            p['restorable'] = p['compatible'] and p.get('integrity', False) and not health.get('check_failed') and p.get('manifest', {}).get('includes_runtime', False)
        newest = points[0]['created'] if points else None
        return {'ready': self.ready, 'engine': 'restic', 'sampled_at': time.time(), 'backups': points,
                'policy': policy, 'bytes': used, 'logical_bytes': sum(p['bytes'] for p in points),
                'free_bytes': shutil.disk_usage(self.runtime.root).free, 'reserve_bytes': 20 * GIB,
                'next_run': (newest + policy['interval_minutes'] * 60) if newest and policy['enabled'] else None,
                'health': health, 'external_copy': False, 'prunable': retention(points, policy)}

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

    def capture(self, job, name, progress):
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
                if size > 20 * GIB:
                    raise RuntimeError('Source exceeds the 20 GiB capture limit.')
                r.require_space(size * 2.1)
                if self.measure()['bytes'] >= self.policy()['budget_gib'] * GIB:
                    raise RuntimeError('O repositório atingiu o orçamento. Revise a retenção ou libere espaço.')
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
                manifest['version'] = match[1] if match else ', '.join(manifest['versions'])
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
                self.measure(structure_checked=time.time())
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
        point, points = self.point(identifier), self.points()
        tested = next((p for p in points if (p.get('restoration') or {}).get('playable_boot_tested')), None)
        if point.get('pinned') or points[0]['id'] == identifier or tested and tested['id'] == identifier:
            raise ValueError('Este ponto está protegido: mais recente, fixado ou último teste bem-sucedido.')
        self.command('forget', identifier)
        (self.catalog / (identifier + '.json')).unlink()
        return {'deleted': identifier, 'space_reclaimed': False}

    def compact(self, progress, revision):
        if revision != self.policy()['revision']:
            raise ValueError('A retenção mudou. Revise novamente.')
        self.runtime.require_space(2 * GIB)
        progress('Conferindo repositório', 'Checking structure before applying retention.')
        self.command('check')
        removed = retention(self.points(), self.policy())
        for identifier in removed:
            self.delete(identifier)
        progress('Liberando espaço', 'Repacking unreferenced data with a bounded workspace.')
        self.command('prune', '--max-repack-size', '1G')
        self.command('check')
        return {'removed': len(removed), **self.measure(structure_checked=time.time(), compacted=time.time())}

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
