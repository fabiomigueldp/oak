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
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/opt/oak-control')


def run(*args, timeout=300, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(Path(args[0]).name + ' failed: ' + result.stderr[-2000:])
    return result.stdout.strip()


def assert_idle():
    if (Path('/srv/oak/control') / 'restore-pending.json').exists():
        raise RuntimeError('Recover the interrupted restore before upgrading the agent.')
    for path in Path('/srv/oak/control/receipts').glob('*.json'):
        if json.loads(path.read_text())['state'] == 'running':
            raise RuntimeError('Resolve running host receipts before upgrading the agent.')


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
    for folder in ('admin', 'scripts', 'deploy'):
        shutil.copytree(ROOT / folder, release / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'requirements-admin.txt', release / 'requirements-admin.txt')
    run('/usr/bin/python3', '-m', 'venv', str(release / '.venv'))
    run(str(release / '.venv/bin/pip'), 'install', '--disable-pip-version-check', '-r', str(release / 'requirements-admin.txt'), timeout=600)
    run(str(release / '.venv/bin/python'), str(ROOT / 'tests/test_admin.py'))
    for directory, mode in ((Path('/var/lib/oak-control'), 0o700), (Path('/srv/oak/control'), 0o700)):
        directory.mkdir(parents=True, exist_ok=True, mode=mode)
        directory.chmod(mode)
    os.chown('/var/lib/oak-control', identity.pw_uid, identity.pw_gid)
    current = BASE / 'current'
    previous = current.resolve() if current.exists() else None
    if previous:
        run('systemctl', 'stop', 'oak-control.service')
        try:
            assert_idle()
        except BaseException:
            run('systemctl', 'start', 'oak-control.service')
            raise
    units = {}
    targets = {ROOT / 'deploy/systemd' / name: Path('/etc/systemd/system') / name for name in ('oak-control.service', 'oak-control-agent.service')}
    targets[ROOT / 'deploy/systemd/oak-restore-guard.conf'] = Path('/etc/systemd/system/oak.service.d/90-oak-restore-guard.conf')
    try:
        # Stop intake first. The agent has a long graceful shutdown budget for cleanup.
        run('systemctl', 'stop', 'oak-control.service', 'oak-control-agent.service') if previous else None
        next_link = BASE / 'next'
        next_link.symlink_to(release, target_is_directory=True)
        next_link.replace(current)
        for source, target in targets.items():
            units[target] = target.read_bytes() if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o644)
        run('systemctl', 'daemon-reload')
        run('systemd-analyze', 'verify', '/etc/systemd/system/oak-control.service', '/etc/systemd/system/oak-control-agent.service')
        run('systemctl', 'enable', '--now', 'oak-control-agent.service', 'oak-control.service')
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
        run('systemctl', 'is-active', 'oak-control.service', 'oak-control-agent.service')
        print(run('runuser', '-u', 'oak-control', '--', str(release / '.venv/bin/python'), '-m', 'admin', 'doctor', cwd=str(release)))
        print('Oak Control installed: ' + sha + '. Minecraft was not restarted.')
    except BaseException:
        run('systemctl', 'stop', 'oak-control.service', 'oak-control-agent.service')
        for target, content in units.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        current.unlink(missing_ok=True)
        if previous:
            current.symlink_to(previous, target_is_directory=True)
        run('systemctl', 'daemon-reload')
        if previous:
            run('systemctl', 'start', 'oak-control-agent.service', 'oak-control.service')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('commit')
    install(parser.parse_args().commit)
