"""Install an exact operator release; keep Minecraft and network configuration unchanged."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/opt/oak-operator')
UNIT = Path('/etc/systemd/system/oak-operator.service')
LAUNCHER = Path('/usr/local/bin/oak-operator')


def run(*args, timeout=300):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(Path(args[0]).name + ' failed: ' + result.stderr[-2000:])
    return result.stdout.strip()


def assert_idle():
    if LAUNCHER.exists() and (BASE / 'current').exists():
        for status in ('running', 'queued'):
            observed = json.loads(run(str(LAUNCHER), 'call', 'jobs.list', json.dumps({'status': status, 'limit': 1})))['result']
            if observed.get('jobs'):
                raise RuntimeError('Finish or cancel operator jobs before upgrading.')


def install(commit):
    if os.geteuid() != 0 or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Run as root with an exact reviewed commit.')
    if run('git', '-C', str(ROOT), 'rev-parse', 'HEAD') != commit or run('git', '-C', str(ROOT), 'status', '--porcelain'):
        raise ValueError('Checkout must be clean and match the commit.')
    run('getent', 'group', 'oak-control')
    assert_idle()
    release = BASE / 'releases' / commit
    if release.exists():
        raise RuntimeError('Release already exists; inspect it or use a new commit.')
    release.mkdir(parents=True, mode=0o755)
    for name in ('oak_operator', 'admin', 'skills', 'scripts'):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, release / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (release / 'docs').mkdir()
    for name in ('operator.md', 'operator-agents.md', 'operator-world.md'):
        shutil.copy2(ROOT / 'docs' / name, release / 'docs' / name)
    shutil.copy2(ROOT / 'requirements-operator.txt', release / 'requirements-operator.txt')
    run('/usr/bin/python3', '-m', 'venv', str(release / '.venv'))
    run(str(release / '.venv/bin/pip'), 'install', '--disable-pip-version-check', '-r', str(release / 'requirements-operator.txt'), timeout=600)
    previous = (BASE / 'current').resolve() if (BASE / 'current').exists() else None
    old_unit = UNIT.read_bytes() if UNIT.exists() else None
    old_launcher = LAUNCHER.read_bytes() if LAUNCHER.exists() else None
    try:
        if previous:
            assert_idle()
            run('systemctl', 'stop', 'oak-operator')
        next_link = BASE / 'next'
        next_link.unlink(missing_ok=True)
        next_link.symlink_to(release)
        next_link.replace(BASE / 'current')
        UNIT.write_bytes((ROOT / 'deploy/systemd/oak-operator.service').read_bytes())
        UNIT.chmod(0o644)
        LAUNCHER.write_text('#!/bin/sh\ncd /opt/oak-operator/current || exit 1\nexec .venv/bin/python -m oak_operator "$@"\n')
        LAUNCHER.chmod(0o755)
        run('systemctl', 'daemon-reload')
        run('systemd-analyze', 'verify', str(UNIT))
        run('systemctl', 'enable', '--now', 'oak-operator')
        for attempt in range(20):
            try:
                observed = json.loads(run(str(LAUNCHER), 'discover'))
                if not observed.get('ok') or not observed.get('result'):
                    raise RuntimeError('Operator discovery is empty.')
                break
            except (RuntimeError, json.JSONDecodeError):
                if attempt == 19:
                    raise
                time.sleep(1)
        print('Oak operator installed: ' + commit)
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'oak-operator'], capture_output=True)
        if old_unit is None:
            subprocess.run(['systemctl', 'disable', 'oak-operator'], capture_output=True)
        for path, content in ((UNIT, old_unit), (LAUNCHER, old_launcher)):
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        (BASE / 'current').unlink(missing_ok=True)
        if previous:
            (BASE / 'current').symlink_to(previous)
        run('systemctl', 'daemon-reload')
        if previous:
            run('systemctl', 'start', 'oak-operator')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('commit')
    install(parser.parse_args().commit)
