"""Narrow host operations. Only this module accesses the game and its files.

The web process talks to the local agent and never imports a live Runtime.
Paths are configured by the operator, never by HTTP parameters. Tests use
temporary roots and mocked command delivery.
"""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import struct
import subprocess
import tarfile
import time
import uuid

from .domain import BACKUP, FIELDS, NAME, validate

GIB = 1024 ** 3
SERVICES = ('oak.service', 'oak-map.service', 'oak-backup.service', 'oak-geyser.service', 'oak-bedrock-bridge.service', 'oak-chat.service', 'oak-web-collector.service')
INCLUDED = ('world', 'config', 'mods', 'libraries', 'versions', '.fabric', 'fabric-server-launch.jar', 'fabric-server-launcher.properties', 'server.jar', 'server.properties', 'eula.txt', 'whitelist.json', 'ops.json', 'banned-players.json', 'banned-ips.json')


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as file:
            file.write(data if isinstance(data, bytes) else data.encode())
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def process_identity():
    """Kernel process start time prevents a reused PID from authorizing a boot."""
    return {'pid': os.getpid(), 'start': Path('/proc/self/stat').read_text().split()[21],
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()} if os.name == 'posix' else {}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def properties(path):
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line and not line.lstrip().startswith(('#', '!')) and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().replace('\\:', ':')
    return values


def run(args, timeout=30, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    if result.returncode:
        # Arbitrary subprocess output can contain secrets; return a bounded generic error.
        raise RuntimeError(f'{Path(args[0]).name} failed with exit status {result.returncode}.')
    return result.stdout


class Rcon:
    def __init__(self, path, timeout=10):
        self.path, self.timeout = path, timeout

    @staticmethod
    def exact(sock, size):
        result = b''
        while len(result) < size:
            part = sock.recv(size - len(result))
            if not part:
                raise ConnectionError('RCON closed before confirming the command.')
            result += part
        return result

    def packet(self, sock, number, kind, command):
        payload = struct.pack('<ii', number, kind) + command.encode() + b'\0\0'
        sock.sendall(struct.pack('<i', len(payload)) + payload)
        size, = struct.unpack('<i', self.exact(sock, 4))
        if not 10 <= size <= 1024 * 1024:
            raise ValueError('Invalid RCON packet.')
        response = self.exact(sock, size)
        rid, = struct.unpack('<i', response[:4])
        return rid, response[8:-2].decode(errors='replace')

    def command(self, command):
        config = properties(self.path)
        with socket.create_connection(('127.0.0.1', int(config['rcon.port'])), timeout=self.timeout) as sock:
            if self.packet(sock, 1, 3, config['rcon.password'])[0] != 1:
                raise PermissionError('RCON authentication failed.')
            rid, text = self.packet(sock, 2, 2, command)
            if rid != 2:
                raise ConnectionError('RCON delivery could not be confirmed.')
            # Commands are bounded; long output is explicitly truncated in the receipt.
            return text[:16000]


class Runtime:
    def __init__(self, root=Path('/srv/oak'), *, free_reserve=20 * GIB):
        self.root = Path(root).resolve()
        self.server = self.root / 'server'
        self.control = self.root / 'control'
        self.backups_dir = self.control / 'backups'
        self.free_reserve = free_reserve
        self.rcon = Rcon(self.server / 'server.properties', timeout=180)
        self.control.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.position_cursor = 0
        self.positions = {}

    @contextmanager
    def lock(self):
        import fcntl
        with (self.control / 'world.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Another world operation is running.') from exc
            yield

    @contextmanager
    def legacy_backup_lock(self):
        import fcntl
        with (self.root / 'backups' / '.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('The existing backup service is busy.') from exc
            yield

    def service(self, action, name='oak.service'):
        if name not in SERVICES + ('oak-map.timer', 'oak-backup.timer') or action not in ('start', 'stop', 'restart', 'is-active'):
            raise ValueError('Unsupported service operation.')
        return run(['systemctl', action, name], timeout=180)

    def active(self, name='oak.service'):
        try:
            return self.service('is-active', name).strip() == 'active'
        except RuntimeError:
            return False

    def recover_saving(self):
        marker = self.control / 'saving-disabled.json'
        if marker.exists():
            if self.active():
                self.rcon.command('save-on')
            marker.unlink()

    def configuration(self):
        path = self.server / 'server.properties'
        values, config = {}, properties(path)
        for key, field in FIELDS.items():
            if key not in config:
                values[key] = None
            elif field['type'] == 'number':
                try:
                    values[key] = int(config[key])
                except ValueError:
                    values[key] = None
            elif field['type'] == 'boolean':
                values[key] = config[key].lower() == 'true'
            else:
                values[key] = config[key]
        return {'values': values, 'fields': FIELDS, 'revision': sha256(path), 'management_enabled': config.get('management-server-enabled') == 'true', 'pending_restart': (self.control / 'pending-restart.json').exists()}

    def snapshot(self):
        now = time.time()
        try:
            public = json.loads((self.root / 'web/status.json').read_text())
        except (OSError, ValueError):
            public = {}
        fresh = now - public.get('updated', 0) < 30
        players = [{'name': name, 'platform': 'bedrock' if name.startswith('.') else 'java', 'position': None, 'dimension': None} for name in public.get('players', []) if isinstance(name, str) and NAME.fullmatch(name)]
        # Read-only, bounded RCON sampling. No sampling when nobody is online.
        if fresh and players:
            probe = Rcon(self.server / 'server.properties', timeout=.75)
            deadline = time.monotonic() + 3
            count = min(12, len(players))
            chosen = [players[(self.position_cursor + i) % len(players)] for i in range(count)]
            for player in chosen:
                if time.monotonic() >= deadline:
                    break
                self.position_cursor = (self.position_cursor + 1) % len(players)
                try:
                    response = probe.command('data get entity ' + player['name'] + ' Pos')
                    match = re.search(r'\[\s*(-?[\d.]+)d?,\s*(-?[\d.]+)d?,\s*(-?[\d.]+)d?\s*\]', response)
                    if match:
                        player['position'] = [float(v) for v in match.groups()]
                        player['sampled_at'] = time.time()
                        dim = probe.command('data get entity ' + player['name'] + ' Dimension')
                        dimension = re.search(r'minecraft:(overworld|the_nether|the_end)', dim)
                        player['dimension'] = dimension.group() if dimension else None
                        self.positions[player['name']] = dict(player)
                except (OSError, ValueError, PermissionError):
                    pass
            for player in players:
                cached = self.positions.get(player['name'])
                if not player.get('position') and cached and now - cached['sampled_at'] < 30:
                    player.update(cached)
            self.positions = {p['name']: p for p in players if p.get('position')}
        services = []
        for name in SERVICES:
            try:
                output = run(['systemctl', 'show', name, '--property=ActiveState,SubState,Result,MemoryCurrent,MemoryMax,CPUQuotaPerSecUSec,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStatus'], timeout=5)
                values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
                services.append({'id': name, **values})
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                services.append({'id': name, 'ActiveState': 'unknown'})
        disk = shutil.disk_usage(self.root)
        mem = {}
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                key, value = line.split(':', 1)
                if key in ('MemTotal', 'MemAvailable'):
                    mem[key] = int(value.split()[0]) * 1024
        except OSError:
            pass
        config = properties(self.server / 'server.properties')
        versions = sorted(p.name for p in (self.server / 'versions').iterdir() if p.is_dir()) if (self.server / 'versions').exists() else []
        observed_version = None
        try:
            with (self.server / 'logs/latest.log').open(errors='replace') as log:
                startup = log.read(65536)
            match = re.search(r'(?:Loading Minecraft |Starting minecraft server version )([0-9][A-Za-z0-9.+-]*)', startup)
            observed_version = match[1] if match else None
        except OSError:
            pass
        metadata = self.control / 'last-save.json'
        saved = json.loads(metadata.read_text()) if metadata.exists() else None
        render = next((s for s in services if s['id'] == 'oak-map.service'), {})
        return {'sampled_at': now, 'source_updated': public.get('updated'), 'fresh': fresh, 'online': bool(public.get('online') and fresh), 'players': players, 'max_players': int(config.get('max-players', 12)), 'version': observed_version, 'installed_versions': versions, 'loader': 'Fabric' if (self.server / 'fabric-server-launch.jar').exists() else 'Minecraft', 'mods': sorted(p.name for p in (self.server / 'mods').glob('*.jar')), 'services': services, 'disk': {'total': disk.total, 'free': disk.free, 'used': disk.used}, 'memory': {'total': mem.get('MemTotal'), 'available': mem.get('MemAvailable')}, 'cpu_count': os.cpu_count(), 'load': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None, 'tps': None, 'mspt': None, 'last_save': saved, 'map': {'running': render.get('ActiveState') == 'activating', 'last_finished': render.get('ExecMainExitTimestamp'), 'result': render.get('Result'), 'blocked': (self.root / 'web/map-warning.json').exists(), 'url': '/map/', 'telemetry': 'bounded-rcon'}, 'warnings': public.get('warnings', []), 'demo': False}

    def backups(self):
        items = []
        for directory in (self.root / 'backups', self.backups_dir):
            for path in directory.glob('*.tar.gz'):
                if path.is_symlink() or not BACKUP.fullmatch(path.name):
                    continue
                stat = path.stat()
                meta_path = path.with_suffix('.json')
                meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                if meta.get('size') != stat.st_size or meta.get('mtime_ns') != stat.st_mtime_ns:
                    meta.pop('sha256', None)
                verification = self.control / 'verifications' / (path.name + '.json')
                checked = json.loads(verification.read_text()) if verification.exists() else None
                if checked and (checked.get('size') != stat.st_size or checked.get('mtime_ns') != stat.st_mtime_ns):
                    checked = None
                items.append({'id': path.name, 'name': meta.get('name', 'Backup do servidor'), 'created': meta.get('created', stat.st_mtime), 'bytes': stat.st_size, 'source': 'oak' if directory == self.backups_dir else 'existing', 'integrity': bool(meta.get('sha256') or checked), 'restoration': checked, 'replicated': False, 'manifest': meta.get('manifest'), 'fingerprint': meta.get('sha256') or (checked or {}).get('sha256')})
        return sorted(items, key=lambda item: item['created'], reverse=True)

    def backup_path(self, name):
        if not isinstance(name, str) or not BACKUP.fullmatch(name):
            raise ValueError('Invalid backup identifier.')
        for directory in (self.backups_dir, self.root / 'backups'):
            path = directory / name
            if path.is_file() and not path.is_symlink() and path.resolve().parent == directory.resolve():
                return path
        raise FileNotFoundError('Recovery point no longer exists.')

    def inventory(self, source):
        entries = {}
        for name in INCLUDED:
            path = source / name
            if not path.exists():
                continue
            paths = [path, *path.rglob('*')] if path.is_dir() else [path]
            for item in paths:
                if item.is_symlink():
                    raise ValueError('Snapshot input contains a symbolic link.')
                if item.is_file():
                    stat = item.stat()
                    entries[item.relative_to(source).as_posix()] = (stat.st_size, stat.st_mtime_ns)
        return entries

    def require_space(self, size):
        if shutil.disk_usage(self.root).free < self.free_reserve + size:
            raise RuntimeError('Insufficient disk space after preserving the recovery reserve.')

    def stable_copy(self, stage, progress, attempts=8):
        """Reconcile background chunk/entity writes without accepting a mixed copy."""
        before, copied = self.inventory(self.server), {}
        deadline = time.monotonic() + 120
        for attempt in range(attempts):
            for filename in copied.keys() - before.keys():
                (stage / filename).unlink(missing_ok=True)
            copied = {name: value for name, value in copied.items() if name in before}
            for filename, metadata in before.items():
                if time.monotonic() > deadline:
                    raise RuntimeError('The consistent copy exceeded its 120-second saving budget.')
                if copied.get(filename) == metadata:
                    continue
                destination = stage / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(self.server / filename, destination)
                    copied[filename] = metadata
                except FileNotFoundError:
                    # A disappearing source is reconciled in the next complete inventory.
                    destination.unlink(missing_ok=True)
                    copied.pop(filename, None)
            after = self.inventory(self.server)
            if before == after and copied == after:
                return after
            progress('Sincronizando gravações em andamento', f'Consistency pass {attempt + 1}; reconciling changed files only.')
            before = after
            time.sleep(.25)
        raise RuntimeError('Source files did not settle within the consistent-copy budget. Retry at a quieter time.')

    def backup(self, job, name, progress):
        target = self.backups_dir / ('control-' + job + '.tar.gz')
        if target.exists():
            raise RuntimeError('This operation already created an archive; inspect its receipt.')
        stage = self.control / 'staging' / job
        stage.mkdir(parents=True, mode=0o700)
        partial = target.with_suffix('.partial')
        marker = self.control / 'saving-disabled.json'
        try:
            with self.legacy_backup_lock():
                live = self.active()
                progress('Preparando cópia', 'Checking disk reserve and coordinating world saving.')
                initial = self.inventory(self.server)
                if sum(value[0] for value in initial.values()) > 20 * GIB:
                    raise RuntimeError('The world and runtime exceed the 20 GiB local archive budget.')
                self.require_space(sum(value[0] for value in initial.values()) * 2.1)
                try:
                    if live:
                        # Persist cleanup intent before a command with potentially uncertain delivery.
                        atomic(marker, json.dumps({'job': job, 'created': time.time()}))
                        self.rcon.command('save-off')
                        response = self.rcon.command('save-all flush')
                        if 'Saved' not in response:
                            raise RuntimeError('The world flush was not confirmed.')
                    progress('Copiando estado', 'Copying to a separate staging directory.')
                    before = self.stable_copy(stage, progress)
                finally:
                    if marker.exists():
                        self.rcon.command('save-on')
                        marker.unlink()
                atomic(self.control / 'last-save.json', json.dumps({'at': time.time(), 'source': 'backup', 'job': job}))
                progress('Compactando', 'World saving is enabled; the archive uses the staging copy.')
                manifest = {'format': 1, 'captured': time.time(), 'method': 'flush-and-stable-copy' if live else 'stopped-copy', 'files': len(before), 'source_bytes': sum(v[0] for v in before.values()), 'configuration_revision': sha256(stage / 'server.properties'), 'mods': {p.name: sha256(p) for p in (stage / 'mods').glob('*.jar')}, 'versions': sorted(p.name for p in (stage / 'versions').iterdir()) if (stage / 'versions').exists() else []}
                atomic(stage / 'oak-manifest.json', json.dumps(manifest))
                with tarfile.open(partial, 'w:gz', compresslevel=1) as archive:
                    for item in sorted(stage.iterdir()):
                        archive.add(item, arcname=item.name)
                progress('Verificando integridade', 'Reading the archive completely and recording its SHA-256.')
                self.inspect_archive(partial)
                fingerprint = sha256(partial)
                with partial.open('r+b') as durable:
                    os.fsync(durable.fileno())
                partial.replace(target)
                target.chmod(0o600)
                stat = target.stat()
                atomic(target.with_suffix('.json'), json.dumps({'name': name, 'created': time.time(), 'sha256': fingerprint, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'manifest': manifest}))
                retained = sorted(self.backups_dir.glob('control-*.tar.gz'), key=lambda p: p.stat().st_mtime)
                pruned = []
                while len(retained) > 1 and (len(retained) > 14 or sum(p.stat().st_size for p in retained) > 20 * GIB):
                    oldest = retained.pop(0)
                    if oldest.is_symlink() or oldest.parent != self.backups_dir:
                        raise RuntimeError('Unexpected archive path in the retention set.')
                    oldest.unlink()
                    oldest.with_suffix('.json').unlink(missing_ok=True)
                    pruned.append(oldest.name)
                return {'backup': target.name, 'sha256': fingerprint, 'bytes': target.stat().st_size, 'integrity': True, 'replicated': False, 'restoration_tested': False, 'pruned': pruned}
        finally:
            partial.unlink(missing_ok=True)
            # Only this operation's validated, generated staging path is removed.
            if stage.is_dir() and stage.parent == self.control / 'staging':
                shutil.rmtree(stage)

    def inspect_archive(self, path, destination=None):
        total, files, seen, has_level = 0, 0, set(), False
        allowed = set(INCLUDED) | {'oak-manifest.json'}
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '\\' in member.name or '..' in name.parts or not name.parts or name.parts[0] not in allowed or ':' in member.name:
                    raise ValueError('Archive contains an unsafe or unsupported path.')
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError('Archive links and special files are not supported.')
                if member.name in seen:
                    raise ValueError('Archive contains duplicate entries.')
                seen.add(member.name)
                total += member.size
                files += 1
                if total > 40 * GIB or files > 1000000:
                    raise ValueError('Archive exceeds the restore budget.')
                if member.name == 'world/level.dat':
                    has_level = True
                if destination is not None:
                    out = destination.joinpath(*name.parts)
                    if member.isdir():
                        out.mkdir(parents=True, exist_ok=True)
                    else:
                        self.require_space(member.size)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as src, out.open('xb') as dst:
                            shutil.copyfileobj(src, dst)
                            dst.flush()
                            os.fsync(dst.fileno())
                        out.chmod(0o600)
        if not has_level:
            raise ValueError('Archive is missing world/level.dat.')
        # Tar EOF is earlier than gzip EOF; read the latter to validate its CRC.
        with gzip.open(path, 'rb') as file:
            while file.read(1024 * 1024):
                pass
        if destination is not None:
            for directory in sorted((p for p in destination.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                sync_directory(directory)
            sync_directory(destination)
        return {'files': files, 'expanded_bytes': total, 'includes_runtime': 'fabric-server-launch.jar' in seen and 'server.properties' in seen and any(name.startswith('libraries/') for name in seen)}

    def verify_backup(self, job, name, progress, boot=False):
        path = self.backup_path(name)
        progress('Inspecionando arquivo', 'Checking supported paths, file types and recovery budget.')
        summary = self.inspect_archive(path)
        self.require_space(summary['expanded_bytes'])
        stage = self.control / 'drills' / job
        stage.mkdir(parents=True, mode=0o700)
        try:
            progress('Restaurando em área isolada', 'Production world files remain untouched.')
            self.inspect_archive(path, stage)
            # level.dat must be a readable compressed NBT document, not just a matching filename.
            with gzip.open(stage / 'world/level.dat', 'rb') as file:
                if file.read(1) != b'\x0a':
                    raise ValueError('The restored world metadata is not an NBT compound.')
            progress('Verificando mundo recuperado', 'Validated extraction and world metadata. No game instance was started.')
            stat = path.stat()
            result = {'at': time.time(), 'level': 'extraction', 'playable_boot_tested': False, 'sha256': sha256(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, **summary}
            if boot:
                memory = Path('/proc/meminfo').read_text()
                available = re.search(r'^MemAvailable:\s+(\d+)', memory, re.M)
                if not available or int(available[1]) * 1024 < 4 * GIB:
                    raise RuntimeError('An isolated boot requires at least 4 GiB of available host memory.')
                if not (stage / 'fabric-server-launch.jar').exists() or not (stage / 'libraries').exists():
                    raise RuntimeError('This archive does not include the runtime required for an isolated boot test. Create a new Oak recovery point first.')
                import pwd
                identity = pwd.getpwnam('oak')
                self.control.chmod(0o711)
                stage.parent.chmod(0o711)
                for item in [stage, *stage.rglob('*')]:
                    os.chown(item, identity.pw_uid, identity.pw_gid, follow_symlinks=False)
                progress('Testando inicialização isolada', 'Starting the restored game in a private network namespace with CPU, memory and time limits.')
                runner = Path(__file__).with_name('drill.py').resolve()
                output = run(['systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit=oak-drill-' + job,
                              '--property=User=oak', '--property=Group=oak', '--property=PrivateNetwork=yes',
                              '--property=NoNewPrivileges=yes', '--property=ProtectSystem=strict', '--property=ProtectHome=yes', '--property=PrivateTmp=yes',
                              '--property=MemoryMax=3G', '--property=CPUQuota=50%', '--property=RuntimeMaxSec=240',
                              '--property=ReadWritePaths=' + str(stage), '--property=WorkingDirectory=' + str(stage),
                              '/usr/bin/python3', str(runner), str(stage)], timeout=270)
                boot_result = json.loads(output.strip().splitlines()[-1])
                if not boot_result.get('boot_verified'):
                    raise RuntimeError('The isolated game did not confirm successful recovery.')
                result.update(level='boot', playable_boot_tested=True, boot_seconds=boot_result['seconds'])
            atomic(self.control / 'verifications' / (name + '.json'), json.dumps(result))
            return result
        finally:
            if stage.is_dir() and stage.parent == self.control / 'drills':
                shutil.rmtree(stage)

    def preview(self, kind, params):
        params = validate(kind, params)
        result = {'kind': kind, 'params': params, 'steps': [], 'impact': '', 'requires_confirmation': True}
        if kind == 'restore_backup':
            path = self.backup_path(params['backup'])
            fingerprint = sha256(path)
            if fingerprint != params['fingerprint']:
                raise ValueError('The recovery point has changed.')
            summary = self.inspect_archive(path)
            if not summary['includes_runtime']:
                raise ValueError('This legacy archive lacks the complete runtime. Verify extraction and use operator-assisted migration; only complete Oak checkpoints can be restored by the panel.')
            result.update(impact='O mundo atual será substituído. O servidor ficará indisponível durante a recuperação.', steps=['Verificar o ponto selecionado', 'Preservar o estado atual', 'Pausar servidor e renderização', 'Substituir mundo e configuração', 'Inicializar e verificar o servidor'], summary=summary)
        elif kind == 'settings_apply':
            current = self.configuration()
            if current['revision'] != params['revision']:
                raise ValueError('Configuration changed. Reload and review it again.')
            result.update(impact='As alterações serão gravadas e precisarão de reinício para entrar em vigor.', changes=[{'key': key, 'label': FIELDS[key]['label'], 'before': current['values'][key], 'after': value} for key, value in params['changes'].items()], steps=['Verificar a revisão atual', 'Preservar a configuração anterior', 'Gravar alterações atomicamente'])
        elif kind == 'console':
            result.update(impact='O comando será enviado ao Minecraft com permissões administrativas.', command=params['command'], steps=['Enviar uma única vez', 'Registrar a resposta do servidor'])
        elif kind == 'player_action':
            result.update(impact='A ação será aplicada ao jogador ' + params['player'] + '.', steps=['Verificar a identidade selecionada', 'Aplicar ' + params['action'], 'Registrar a resposta'])
        elif kind in ('server_control', 'maintenance'):
            result.update(impact='Jogadores podem ser desconectados durante esta operação.', steps=['Preservar o progresso', 'Executar a mudança de estado solicitada', 'Verificar o estado observado'])
        return result

    def apply_settings(self, job, params, progress):
        path = self.server / 'server.properties'
        if sha256(path) != params['revision']:
            raise ValueError('Configuration changed after review. Reload and review again.')
        previous = path.read_bytes()
        changes = {k: str(v).lower() if type(v) is bool else str(v) for k, v in params['changes'].items()}
        lines, done = [], set()
        for line in previous.decode().splitlines():
            key = line.split('=', 1)[0].strip()
            if '=' in line and key in changes and not line.lstrip().startswith(('#', '!')):
                if key not in done:
                    lines.append(key + '=' + changes[key])
                    done.add(key)
            else:
                lines.append(line)
        lines.extend(key + '=' + value for key, value in changes.items() if key not in done)
        progress('Gravando configuração', 'Preserving unknown keys and requiring an explicit restart.')
        atomic(self.control / 'config-revisions' / (job + '.properties'), previous)
        stat = path.stat()
        atomic(path, '\n'.join(lines) + '\n', stat.st_mode & 0o777)
        if hasattr(os, 'chown'):
            os.chown(path, stat.st_uid, stat.st_gid)
        atomic(self.control / 'pending-restart.json', json.dumps({'at': time.time(), 'revision': sha256(path)}))
        return {'revision': sha256(path), 'changes': params['changes'], 'restart_required': True}

    def restore(self, job, params, progress):
        path = self.backup_path(params['backup'])
        if sha256(path) != params['fingerprint']:
            raise ValueError('Recovery point changed after review.')
        summary = self.inspect_archive(path)
        if not summary['includes_runtime']:
            raise ValueError('A complete runtime is required for panel restoration.')
        self.require_space(summary['expanded_bytes'] * 2.2)
        # Preserve a fresh recovery point before stopping any production component.
        safety = self.backup(str(uuid.uuid4()), 'Antes da restauração', progress)
        stage = self.control / 'restores' / job
        old = self.control / 'rollback' / job
        stage.mkdir(parents=True, mode=0o700)
        old.mkdir(parents=True, mode=0o700)
        self.inspect_archive(path, stage)
        with gzip.open(stage / 'world/level.dat', 'rb') as file:
            if file.read(1) != b'\x0a':
                raise ValueError('Invalid world metadata.')
        was_online = self.active()
        timers = {name: self.active(name) for name in ('oak-map.timer', 'oak-backup.timer')}
        installed, engaged = [], False
        journal_path = self.control / 'restore-pending.json'
        journal = {'job': job, 'online': was_online, 'timers': timers, 'phase': 'prepared', 'identity': process_identity(),
                   'entries': {name: (self.server / name).exists() for name in INCLUDED if (stage / name).exists() or name in ('world', 'mods', 'config')}}
        try:
            with self.legacy_backup_lock():
                atomic(journal_path, json.dumps(journal))
                engaged = True
                for timer, active in timers.items():
                    if active:
                        self.service('stop', timer)
                self.service('stop', 'oak-map.service')
                progress('Pausando servidor', 'World and configuration will be replaced as a single controlled operation.')
                self.service('stop')
                for name in INCLUDED:
                    source = stage / name
                    if not source.exists():
                        # Legacy archives may omit runtime libraries. Keep those; never keep a newer world/mod/config tree.
                        if name not in ('world', 'mods', 'config'):
                            continue
                    target = self.server / name
                    if target.exists():
                        target.rename(old / name)
                        sync_directory(self.server)
                        sync_directory(old)
                    if source.exists():
                        source.rename(target)
                        sync_directory(stage)
                        sync_directory(self.server)
                        installed.append(name)
                # Restore ownership to the existing game identity, without following symlinks.
                import pwd
                identity = pwd.getpwnam('oak')
                for name in installed:
                    target = self.server / name
                    for item in [target, *target.rglob('*')] if target.is_dir() else [target]:
                        os.chown(item, identity.pw_uid, identity.pw_gid, follow_symlinks=False)
                if was_online:
                    progress('Validando inicialização', 'Waiting for the game to answer RCON.')
                    journal['phase'] = 'validating'
                    atomic(journal_path, json.dumps(journal))
                    self.service('start')
                    self.wait_game()
                    (self.control / 'pending-restart.json').unlink(missing_ok=True)
                journal_path.unlink()
                sync_directory(self.control)
                return {'safety_backup': safety['backup'], 'restored': params['backup'], 'online': was_online, 'rollback_retained': job, 'map_requires_refresh': True}
        except BaseException:
            if journal_path.exists():
                self.recover_restore()
            raise
        finally:
            if engaged and not journal_path.exists():
                for timer, active in timers.items():
                    if active:
                        self.service('start', timer)
            # Retain restore staging and previous state for deliberate operator recovery.

    def guard_start(self):
        """Fail closed after a restore process/host crash, before Java opens the world."""
        marker = self.control / 'restore-pending.json'
        if not marker.exists():
            return
        journal = json.loads(marker.read_text())
        identity = journal.get('identity', {})
        try:
            same_process = (Path('/proc/' + str(int(identity['pid'])) + '/stat').read_text().split()[21] == identity['start']
                            and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == identity['boot'])
        except (OSError, ValueError, KeyError):
            same_process = False
        if journal['phase'] != 'validating' or not same_process:
            raise RuntimeError('Interrupted restore. Run the documented recover-restore command before starting Minecraft.')

    def recover_restore(self):
        """Roll back a persisted move intent; safe to repeat after any individual rename."""
        marker = self.control / 'restore-pending.json'
        if not marker.exists():
            return
        journal = json.loads(marker.read_text())
        job = journal['job']
        if not re.fullmatch(r'[a-f0-9-]{36}', job) or set(journal['entries']) - set(INCLUDED):
            raise RuntimeError('Invalid restore journal. Manual operator recovery is required.')
        journal['phase'] = 'rollback'
        atomic(marker, json.dumps(journal))
        for timer in journal['timers']:
            self.service('stop', timer)
        self.service('stop', 'oak-map.service')
        self.service('stop')
        old, stage = self.control / 'rollback' / job, self.control / 'restores' / job
        discarded = self.control / 'discarded' / job
        discarded.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, existed in journal['entries'].items():
            target, previous = self.server / name, old / name
            if previous.exists() or (not existed and not (stage / name).exists()):
                if target.exists():
                    target.rename(discarded / (name + '-' + uuid.uuid4().hex))
                    sync_directory(self.server)
                    sync_directory(discarded)
                if previous.exists():
                    previous.rename(target)
                    sync_directory(self.server)
                    sync_directory(old)
        marker.unlink()
        sync_directory(self.control)
        if journal['online']:
            self.service('start')
        for timer, active in journal['timers'].items():
            if active:
                self.service('start', timer)

    def wait_game(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                response = Rcon(self.server / 'server.properties', timeout=3).command('list')
                if 'players online' in response:
                    return
            except (OSError, PermissionError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError('The game did not become responsive before the verification deadline.')

    def execute(self, job, kind, params, progress):
        if not re.fullmatch(r'[a-f0-9-]{36}', job):
            raise ValueError('Invalid operation identifier.')
        params = validate(kind, params)
        with self.lock():
            if (self.control / 'restore-pending.json').exists():
                raise RuntimeError('An interrupted restoration needs operator recovery before further world operations.')
            self.recover_saving()
            if kind == 'backup':
                return self.backup(job, params['name'], progress)
            if kind == 'verify_backup':
                return self.verify_backup(job, params['backup'], progress, params['boot'])
            if kind == 'restore_backup':
                return self.restore(job, params, progress)
            if kind == 'settings_apply':
                return self.apply_settings(job, params, progress)
            if kind == 'save':
                progress('Salvando mundo', 'Waiting for Minecraft to confirm the flush.')
                response = self.rcon.command('save-all flush')
                if 'Saved' not in response:
                    raise RuntimeError('The world flush was not confirmed.')
                atomic(self.control / 'last-save.json', json.dumps({'at': time.time(), 'job': job, 'source': 'manual'}))
                return {'response': response, 'saved_at': time.time()}
            if kind == 'map_refresh':
                progress('Preparando mapa', 'Requesting the existing adapted snapshot/render pipeline.')
                run(['/usr/local/sbin/oak-map-update'], timeout=240)
                return {'requested': True, 'render_complete': False, 'note': 'Rendering continues in oak-map.service; consult its current status.'}
            if kind == 'console':
                progress('Executando comando', 'Non-idempotent delivery will never be retried automatically.')
                return {'response': self.rcon.command(params['command']), 'response_may_be_partial': True}
            if kind == 'player_action':
                action, player = params['action'], params['player']
                commands = {'whitelist_add': 'whitelist add', 'whitelist_remove': 'whitelist remove', 'teleport': 'tp'}
                command = commands.get(action, action) + ' ' + player
                if action in ('kick', 'ban'):
                    command += ' ' + params['reason']
                if action == 'teleport':
                    command += ' ' + params['target']
                progress('Aplicando ação', 'Sending the reviewed action to Minecraft once.')
                return {'response': self.rcon.command(command), 'player': player}
            if kind == 'maintenance':
                safety = self.backup(job, 'Antes da manutenção', progress)
                if params['restart']:
                    progress('Reiniciando servidor', 'Restarting only the Minecraft service.')
                    self.service('restart')
                    self.wait_game()
                    (self.control / 'pending-restart.json').unlink(missing_ok=True)
                return {'backup': safety['backup'], 'restarted': params['restart']}
            if kind == 'server_control':
                action = params['action']
                if action in ('stop', 'restart') and self.active():
                    progress('Salvando progresso', 'Flushing the world before changing the service state.')
                    response = self.rcon.command('save-all flush')
                    if 'Saved' not in response:
                        raise RuntimeError('The world flush was not confirmed.')
                progress('Alterando estado do servidor', action)
                self.service(action)
                if action != 'stop':
                    self.wait_game()
                    (self.control / 'pending-restart.json').unlink(missing_ok=True)
                elif self.active():
                    raise RuntimeError('The server is still running.')
                return {'action': action, 'online': action != 'stop'}
        raise ValueError('Unsupported operation.')

    def logs(self, service):
        if service not in SERVICES:
            raise ValueError('Unknown service.')
        text = run(['journalctl', '--unit', service, '--lines', '100', '--no-pager', '--output=short-iso'], timeout=10)
        # Avoid credential-bearing lines and IP addresses in the operational view.
        lines = [line for line in text.splitlines() if not re.search(r'password|secret|token|private.?key|authorization', line, re.I)]
        return {'service': service, 'lines': [re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[address]', line)[:1500] for line in lines], 'sampled_at': time.time()}
