"""Versioned text bundles and optional datapack activation. Runtime data stays private."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import time
import zipfile


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


class Packages:
    def __init__(self, state, runtime=None, demo=False):
        self.root = Path(state) / 'packages'
        self.runtime, self.demo = runtime, demo
        self.lock = threading.RLock()

    def call(self, method, data, actor):
        with self.lock:
            if method == 'packages.list':
                return {'packages': [json.loads(p.read_text()) for p in sorted(self.root.glob('*/current.json'))]}
            name = data.get('name', '')
            if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
                raise ValueError('Package name must use lowercase letters, digits, underscores or hyphens.')
            base = self.root / name
            current = base / 'current.json'
            previous = json.loads(current.read_text()) if current.exists() else None
            if method == 'packages.get':
                if not previous:
                    raise ValueError('Package not found.')
                return {**previous, 'file_contents': {path: (Path(previous['path']) / path).read_text(encoding='utf-8') for path in previous['files']}}
            if method == 'packages.install':
                kind = data.get('kind', 'script')
                if kind not in ('script', 'datapack'):
                    raise ValueError('Package kind must be script or datapack.')
                version = data.get('version', '')
                if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version):
                    raise ValueError('Version must be a nonempty identifier.')
                files = data.get('files')
                if not isinstance(files, dict) or not files:
                    raise ValueError('Provide files as relative paths mapped to UTF-8 text.')
                checked = {}
                for path, content in files.items():
                    part = PurePosixPath(path)
                    if not path or part.is_absolute() or '..' in part.parts or '\\' in path or ':' in path or str(part) != path or not isinstance(content, str):
                        raise ValueError('Package files require canonical relative paths and text content.')
                    checked[path] = content
                if kind == 'datapack':
                    meta = json.loads(checked.get('pack.mcmeta', '{}'))
                    if not isinstance(meta.get('pack'), dict):
                        raise ValueError('Datapack requires valid pack.mcmeta.')
                encoded = json.dumps({'kind': kind, 'files': checked}, sort_keys=True, ensure_ascii=False).encode()
                digest = hashlib.sha256(encoded).hexdigest()
                release = base / 'releases' / version
                manifest = release / 'manifest.json'
                if release.exists():
                    if not manifest.exists() or json.loads(manifest.read_text())['sha256'] != digest:
                        raise ValueError('Version already exists with different content. Use a new version.')
                    self.verify(json.loads(manifest.read_text()))
                if previous and previous.get('active') and (previous['version'] != version or previous['kind'] != kind):
                    raise ValueError('Deactivate the installed datapack before replacing its version.')
                value = {'name': name, 'version': version, 'kind': kind, 'sha256': digest,
                         'files': sorted(checked), 'path': str(release / 'files'), 'actor': actor,
                         'installed_at': time.time(), 'active': bool(previous and previous.get('active')),
                         'demo': self.demo}
                if value['active']:
                    value.update({key: previous[key] for key in ('target', 'archive_sha256', 'reload_confirmed', 'reload_result') if key in previous})
                if not release.exists():
                    release.parent.mkdir(parents=True, exist_ok=True)
                    stage = Path(tempfile.mkdtemp(prefix='.package-', dir=release.parent))
                    try:
                        for path, content in checked.items():
                            atomic(stage / 'files' / path, content.encode())
                        atomic(stage / 'manifest.json', json.dumps(value).encode())
                        stage.replace(release)
                    finally:
                        if stage.exists():
                            if not stage.resolve().is_relative_to(release.parent.resolve()) or not stage.name.startswith('.package-'):
                                raise RuntimeError('Package staging path escaped its release directory.')
                            shutil.rmtree(stage)
                atomic(current, json.dumps(value).encode())
                return value
            if not previous:
                raise ValueError('Package not found.')
            if method in ('packages.activate', 'packages.deactivate'):
                if previous['kind'] != 'datapack':
                    raise ValueError('Script packages run through jobs; only datapacks need activation.')
                if self.demo:
                    previous['active'] = method.endswith('.activate')
                    atomic(current, json.dumps(previous).encode())
                    return previous
                if self.runtime is None:
                    raise RuntimeError('Minecraft runtime is unavailable.')
                from admin.runtime import properties
                world = properties(self.runtime.server / 'server.properties').get('level-name', 'world')
                target = self.runtime.server / world / 'datapacks' / ('oak-' + name + '.zip')
                with self.runtime.lock():
                    if target.exists():
                        observed = hashlib.sha256(target.read_bytes()).hexdigest()
                        if not previous.get('active') or observed != previous.get('archive_sha256'):
                            raise ValueError('Installed datapack changed outside this package. Inspect it before replacement.')
                    if method == 'packages.activate':
                        self.verify(previous)
                        output = io.BytesIO()
                        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                            for path in previous['files']:
                                archive.writestr(path, (Path(previous['path']) / path).read_bytes())
                        payload = output.getvalue()
                        atomic(target, payload)
                        target.chmod(0o644)
                        previous.update(active=True, target=str(target), archive_sha256=hashlib.sha256(payload).hexdigest())
                    else:
                        target.unlink(missing_ok=True)
                        previous['active'] = False
                    # Record actual disk state before reload, which can fail after delivery.
                    previous['reload_confirmed'] = False
                    atomic(current, json.dumps(previous).encode())
                    result = self.runtime.rcon.command('reload')
                    previous.update(reload_confirmed=True, reload_result=result)
                    atomic(current, json.dumps(previous).encode())
                    return previous
            raise ValueError('Unknown package method.')

    @staticmethod
    def verify(value):
        root = Path(value['path'])
        files = {}
        for path in value['files']:
            target = root / path
            if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                raise ValueError('Package content contains a symlink.')
            files[path] = target.read_text(encoding='utf-8')
        digest = hashlib.sha256(json.dumps({'kind': value['kind'], 'files': files}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != value['sha256']:
            raise ValueError('Package files changed after installation. Install a new version.')
