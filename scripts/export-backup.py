"""Stream a locked recovery repository and individually captured control state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tarfile
import tempfile
import time

CONTROL = Path('/srv/oak/control')
ADMIN_STATE = Path('/var/lib/oak-control')
OPERATOR_STATE = Path('/var/lib/oak-operator')


def export(output):
    import fcntl
    with (CONTROL / 'repository.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        locked_at = time.time()
        if not (CONTROL / 'repository/config').is_file() or not (CONTROL / 'repository.key').is_file():
            raise RuntimeError('Restic repository and recovery key are required.')
        with tempfile.TemporaryDirectory(prefix='oak-export-') as directory:
            stage = Path(directory)
            captures = {}
            sources = [(CONTROL / name, 'control/' + name) for name in ('repository', 'repository.key', 'catalog', 'backup-policy.json', 'repository-health.json') if (CONTROL / name).exists()]
            for source, name in ((ADMIN_STATE / 'control.sqlite3', 'admin/control.sqlite3'),
                                 (OPERATOR_STATE / 'operator.sqlite3', 'operator/operator.sqlite3')):
                if source.exists():
                    destination = stage / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    started = time.time()
                    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
                    writer = sqlite3.connect(destination)
                    try:
                        reader.backup(writer)
                    finally:
                        writer.close()
                        reader.close()
                    captures[name] = {'started_at': started, 'completed_at': time.time(), 'method': 'sqlite-backup'}
                    sources.append((destination, name))
            for name in ('packages', 'services', 'notebooks'):
                source = OPERATOR_STATE / name
                if source.exists():
                    sources.append((source, 'operator/' + name))
            files = []
            for source, prefix in sources:
                if source.is_symlink():
                    raise RuntimeError('Symlink in recovery inventory: ' + str(source))
                candidates = source.rglob('*') if source.is_dir() else [source]
                for path in candidates:
                    if path.is_symlink():
                        raise RuntimeError('Symlink in recovery inventory: ' + str(path))
                    if path.is_file():
                        name = prefix + '/' + path.relative_to(source).as_posix() if source.is_dir() else prefix
                        files.append((path, name))
            manifest = {'created_at': time.time(), 'format': 2, 'files': {}, 'entry_captures': captures,
                        'scope': 'restic repository, recovery key, control databases and operator packages; not a VM image',
                        'consistency': {'platform_snapshot': False, 'repository_locked_since': locked_at,
                                        'repository': 'Repository files are captured under its exclusive operational lock.',
                                        'databases': 'Each SQLite database has an independent consistent snapshot.',
                                        'operator_files': 'Each entry is captured separately. Files and databases can represent different times.'},
                        'recovery': {'quarantine_required': True, 'guidance': [
                            'Restore into a private staging location with Oak API, workers, operator daemon and managed services stopped.',
                            'Invalidate restored administrative sessions before exposing the API.',
                            'Review and disable restored schedules and routines, cancel unwanted queued work, and reconcile interrupted receipts before starting workers.',
                            'Compare notebook revisions and package/service manifests with captured files; regenerate only reviewed service units.',
                            'Verify the restic repository with its recovery key and select an explicit recovery point before restoring Minecraft.'
                        ]}}
            with tarfile.open(fileobj=output, mode='w|') as archive:
                for path, name in files:
                    # Hash exactly the bytes archived through a private staging copy.
                    temporary = stage / 'entry'
                    digest = hashlib.sha256()
                    started = time.time()
                    with path.open('rb') as reader, temporary.open('wb') as writer:
                        while chunk := reader.read(1024 * 1024):
                            writer.write(chunk)
                            digest.update(chunk)
                    captures.setdefault(name, {'started_at': started, 'completed_at': time.time(),
                                               'method': 'locked-repository-copy' if name.startswith('control/') else 'individual-file-copy'})
                    info = archive.gettarinfo(str(temporary), arcname=name)
                    info.mode, info.uid, info.gid, info.uname, info.gname = 0o600, 0, 0, '', ''
                    with temporary.open('rb') as reader:
                        archive.addfile(info, reader)
                    manifest['files'][name] = {'sha256': digest.hexdigest(), 'bytes': info.size}
                manifest_path = stage / 'manifest.json'
                manifest['completed_at'] = time.time()
                manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
                archive.add(manifest_path, arcname='manifest.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ack-sha256')
    parser.add_argument('--bytes', type=int)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root is required.')
    os.umask(0o077)
    if args.ack_sha256:
        if not re.fullmatch('[a-f0-9]{64}', args.ack_sha256) or not args.bytes or args.bytes < 1:
            parser.error('Provide SHA-256 and positive archive size.')
        from oak_operator.kernel import atomic
        atomic(CONTROL / 'external-copy.json', json.dumps({'verified_at': time.time(), 'sha256': args.ack_sha256, 'bytes': args.bytes, 'destination': 'operator-windows'}).encode())
        print('External copy acknowledgement recorded.')
    else:
        export(sys.stdout.buffer)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
