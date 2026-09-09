"""Explicit migration of the existing Oak VM to the unified backup policy.

prepare records exact obsolete archives and initializes local storage. activate
requires a tested new point, redirects the existing SSH helper and disables its
old timer. cleanup removes only unchanged files recorded by prepare. No game,
map, network, world or crossplay service is restarted by this script.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.backup_repository import Repository
from admin.runtime import Runtime, atomic


def main(action):
    if os.geteuid() != 0:
        raise RuntimeError('Run this migration as root on the Oak host.')
    runtime = Runtime()
    repo = Repository(runtime)
    record = runtime.control / 'backup-migration.json'
    with runtime.lock(), runtime.legacy_backup_lock():
        if (runtime.control / 'restore-pending.json').exists() or runtime.active('oak-backup.service'):
            raise RuntimeError('A recovery or legacy backup is active.')
        if action == 'prepare':
            if not record.exists():
                targets = list((runtime.root/'backups').glob('oak-*.tar.gz')) + list(runtime.backups_dir.glob('control-*.tar.gz'))
                targets += list((runtime.root/'archives').rglob('*.tar.gz'))
                offline = runtime.root/'upgrades/26.3-pre-3/pre2-offline.tar.gz'
                if offline.is_file(): targets.append(offline)
                files = []
                for path in targets:
                    if path.is_symlink() or not path.resolve().is_relative_to(runtime.root.resolve()):
                        raise RuntimeError('Unexpected legacy archive path.')
                    stat = path.stat()
                    files.append({'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
                atomic(record, json.dumps({'prepared': time.time(), 'files': files}))
            repo.initialize()
            print('Repository initialized. Existing archives retained until a verified boot and full data check.')
            return
        state = json.loads(record.read_text())
        points = repo.points()
        if not any(p.get('restoration', {}).get('playable_boot_tested') and repo.compatible(p) for p in points):
            raise RuntimeError('A compatible isolated boot test is required before migration.')
        health = repo.read(repo.health_path, {})
        if not health.get('data_checked') or health.get('check_failed'):
            raise RuntimeError('A successful complete repository check is required.')
        if action == 'activate':
            helper = Path('/usr/local/sbin/oak-admin')
            source = helper.read_text()
            original = "if sys.argv[1:]==['backup']: backup()"
            replacement = "if sys.argv[1:]==['backup']: subprocess.run(['/usr/sbin/runuser','-u','oak-control','--','/usr/bin/env','OAK_ADMIN_STATE=/var/lib/oak-control','/opt/oak-control/current/.venv/bin/python','-m','admin','backup'],check=True)"
            if original not in source and replacement not in source:
                raise RuntimeError('SSH helper differs from the reviewed migration input.')
            # Persist the recovery material before changing operational settings.
            atomic(runtime.control/'legacy-oak-admin.py', source, 0o600)
            subprocess.run(['systemctl','disable','--now','oak-backup.timer'], check=True)
            unit = Path('/etc/systemd/system/oak-backup.service')
            atomic(runtime.control/'legacy-oak-backup.service', unit.read_bytes())
            template = Path(__file__).resolve().parents[1]/'deploy/systemd/oak-backup.service'
            atomic(unit, template.read_bytes(), 0o644)
            subprocess.run(['systemctl','daemon-reload'], check=True)
            atomic(helper, source.replace(original, replacement), 0o750)
            policy = repo.policy()
            repo.update_policy({'revision': policy['revision'], 'policy': {k: (True if k == 'enabled' else v) for k,v in policy.items() if k != 'revision'}})
            state['activated'] = time.time()
            atomic(record, json.dumps(state))
            print('Unified policy enabled; legacy timer disabled; SSH backup helper queues the same engine.')
        elif action == 'cleanup':
            if not state.get('activated'):
                raise RuntimeError('Activate the unified policy first.')
            removed, freed = [], 0
            for entry in state['files']:
                path = Path(entry['path'])
                if not path.exists(): continue
                if path.is_symlink() or not path.resolve().is_relative_to(runtime.root.resolve()) or path.suffixes[-2:] != ['.tar','.gz']:
                    raise RuntimeError('Unexpected cleanup path.')
                stat = path.stat()
                if stat.st_size != entry['size'] or stat.st_mtime_ns != entry['mtime_ns']:
                    raise RuntimeError('An old archive changed after migration preparation; it was preserved.')
                path.unlink()
                # Remove only metadata belonging to the exact deleted archive.
                if path.parent == runtime.backups_dir:
                    path.with_suffix('.json').unlink(missing_ok=True)
                (runtime.control/'verifications'/(path.name+'.json')).unlink(missing_ok=True)
                removed.append(str(path)); freed += stat.st_size
            state.update(cleaned=time.time(), removed=removed, freed_bytes=freed)
            atomic(record, json.dumps(state))
            repo.publish()
            print(json.dumps({'removed_archives': len(removed), 'freed_bytes': freed}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','activate','cleanup'])
    main(parser.parse_args().action)
