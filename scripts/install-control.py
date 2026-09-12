"""Install an exact, clean, locally reviewed commit on the existing Oracle VM.

This is a separate opt-in administrative service installation. It does not
restart Minecraft, the map renderer, the website, or change network policy.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/opt/oak-control')
CONTROL_STATE = Path('/var/lib/oak-control')
RUNTIME_CONTROL = Path('/srv/oak/control')
UNIT_ROOT = Path('/etc/systemd/system')


def run(*args, timeout=300, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(Path(args[0]).name + ' failed: ' + result.stderr[-2000:])
    return result.stdout.strip()


def assert_idle():
    if (RUNTIME_CONTROL / 'restore-pending.json').exists():
        raise RuntimeError('Recover the interrupted restore before upgrading the agent.')
    for path in (RUNTIME_CONTROL / 'receipts').glob('*.json'):
        if json.loads(path.read_text())['state'] == 'running':
            raise RuntimeError('Resolve running host receipts before upgrading the agent.')


def worker_state():
    result = subprocess.run(['systemctl', 'show', 'oak-control-worker.service', '-p', 'ActiveState', '-p', 'UnitFileState'], capture_output=True, text=True)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return {'active': values.get('ActiveState') in ('active', 'reloading', 'activating'),
            'enabled': values.get('UnitFileState') in ('enabled', 'enabled-runtime')}


def rollback(current, previous, units, had_worker, previous_worker):
    """Attempt every recovery step, even when a newly introduced unit is absent."""
    errors = []
    def attempt(label, operation):
        try:
            operation()
        except BaseException as exc:
            errors.append(label + ': ' + str(exc))
    def stop_services():
        result = subprocess.run(['systemctl', 'stop', 'oak-control.service', 'oak-control-worker.service', 'oak-control-agent.service'], capture_output=True)
        if result.returncode not in (0, 5):
            raise RuntimeError('Service stop exited with status ' + str(result.returncode))
    attempt('Stop candidate services', stop_services)
    if not had_worker and (UNIT_ROOT / 'oak-control-worker.service').exists():
        attempt('Disable new worker', lambda: run('systemctl', 'disable', 'oak-control-worker.service'))
    for target, content in units.items():
        attempt('Restore ' + str(target), lambda target=target, content=content: target.unlink(missing_ok=True) if content is None else target.write_bytes(content))
    def restore_link():
        current.unlink(missing_ok=True)
        if previous:
            current.symlink_to(previous, target_is_directory=True)
    attempt('Restore release link', restore_link)
    attempt('Reload service definitions', lambda: run('systemctl', 'daemon-reload'))
    if had_worker:
        attempt('Restore worker boot policy', lambda: run('systemctl', 'enable' if previous_worker['enabled'] else 'disable', 'oak-control-worker.service'))
    if previous:
        attempt('Start previous services', lambda: run('systemctl', 'start', 'oak-control-agent.service', 'oak-control.service'))
        if had_worker and previous_worker['active']:
            attempt('Start previous worker', lambda: run('systemctl', 'start', 'oak-control-worker.service'))
    return errors


def install(sha):
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{40}', sha):
        raise RuntimeError('Run as root with the full reviewed commit SHA.')
    if run('git', '-C', str(ROOT), 'rev-parse', 'HEAD') != sha or run('git', '-C', str(ROOT), 'status', '--porcelain'):
        raise RuntimeError('The source checkout must be clean and match the reviewed commit exactly.')
    assert_idle()
    try:
        pwd.getpwnam('oak-control')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', '/var/lib/oak-control', '--shell', '/usr/sbin/nologin', 'oak-control')
    identity = pwd.getpwnam('oak-control')
    release = BASE / 'releases' / sha
    if release.exists():
        raise RuntimeError('Release already exists; use a new reviewed commit or the documented rollback procedure.')
    release.mkdir(parents=True, mode=0o755)
    for folder in ('admin', 'scripts', 'deploy', 'oak_operator'):
        shutil.copytree(ROOT / folder, release / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'requirements-admin.txt', release / 'requirements-admin.txt')
    run('/usr/bin/python3', '-m', 'venv', str(release / '.venv'))
    run(str(release / '.venv/bin/pip'), 'install', '--disable-pip-version-check', '-r', str(release / 'requirements-admin.txt'), timeout=600)
    run(str(release / '.venv/bin/python'), str(ROOT / 'tests/test_admin.py'))
    run(str(release / '.venv/bin/python'), str(ROOT / 'tests/test_backup_repository.py'))
    for directory, mode in ((CONTROL_STATE, 0o700), (RUNTIME_CONTROL, 0o700)):
        directory.mkdir(parents=True, exist_ok=True, mode=mode)
        directory.chmod(mode)
    os.chown(CONTROL_STATE, identity.pw_uid, identity.pw_gid)
    current = BASE / 'current'
    previous = current.resolve() if current.exists() else None
    had_worker = (UNIT_ROOT / 'oak-control-worker.service').exists()
    previous_worker = worker_state() if had_worker else {'active': False, 'enabled': False}
    if previous:
        try:
            run('systemctl', 'stop', 'oak-control.service')
            if had_worker:
                run('systemctl', 'stop', 'oak-control-worker.service')
            assert_idle()
        except BaseException:
            resume = ['oak-control.service']
            if had_worker and previous_worker['active']:
                resume.append('oak-control-worker.service')
            for service in resume:
                try:
                    run('systemctl', 'start', service)
                except Exception as exc:
                    print('Intake recovery issue: ' + str(exc), file=sys.stderr)
            raise
    units = {}
    targets = {ROOT / 'deploy/systemd' / name: UNIT_ROOT / name for name in ('oak-control.service', 'oak-control-agent.service', 'oak-control-worker.service')}
    targets[ROOT / 'deploy/systemd/oak-restore-guard.conf'] = UNIT_ROOT / 'oak.service.d/90-oak-restore-guard.conf'
    try:
        # Stop intake first. The agent has a long graceful shutdown budget for cleanup.
        run('systemctl', 'stop', 'oak-control.service', 'oak-control-agent.service') if previous else None
        next_link = BASE / 'next'
        if next_link.is_symlink():
            next_link.unlink()
        next_link.symlink_to(release, target_is_directory=True)
        next_link.replace(current)
        for source, target in targets.items():
            units[target] = target.read_bytes() if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o644)
        run('systemctl', 'daemon-reload')
        run('systemd-analyze', 'verify', '/etc/systemd/system/oak-control.service', '/etc/systemd/system/oak-control-agent.service', '/etc/systemd/system/oak-control-worker.service')
        run('systemctl', 'enable', '--now', 'oak-control-agent.service', 'oak-control-worker.service', 'oak-control.service')
        for attempt in range(15):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/admin/api/session', timeout=3) as response:
                    if json.load(response).get('user') is not None:
                        raise RuntimeError('Unauthenticated session unexpectedly has a user.')
                break
            except OSError:
                if attempt == 14:
                    raise
                time.sleep(1)
        run('systemctl', 'is-active', 'oak-control.service', 'oak-control-agent.service', 'oak-control-worker.service')
        print(run('runuser', '-u', 'oak-control', '--', str(release / '.venv/bin/python'), '-m', 'admin', 'doctor', cwd=str(release)))
        print('Oak Control installed: ' + sha + '. Minecraft was not restarted.')
    except BaseException:
        for error in rollback(current, previous, units, had_worker, previous_worker):
            print('Rollback issue: ' + error, file=sys.stderr)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('commit')
    install(parser.parse_args().commit)
