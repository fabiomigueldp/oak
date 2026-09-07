#!/usr/bin/python3
"""Fetch and deploy a main-branch commit. Install this launcher outside the checkout."""
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = Path('/srv/oak/site-repo')
REMOTE = 'https://github.com/fabiomigueldp/oak.git'

def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args], text=True).strip()

def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo.')
    if len(sys.argv) != 2 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[1]):
        raise RuntimeError('Usage: oak-site-deploy <full-40-character-commit-sha>')
    with open('/run/lock/oak-site-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not REPO.exists():
            subprocess.run(['git', 'clone', REMOTE, str(REPO)], check=True)
        if git('remote', 'get-url', 'origin') != REMOTE:
            raise RuntimeError('Unexpected repository origin.')
        if git('status', '--porcelain'):
            raise RuntimeError('Deployment checkout has local changes; inspect it manually.')
        git('fetch', '--prune', 'origin', 'main')
        sha = git('rev-parse', '--verify', sys.argv[1] + '^{commit}')
        git('merge-base', '--is-ancestor', sha, 'origin/main')
        git('checkout', '--detach', sha)
        subprocess.run([sys.executable, str(REPO / 'scripts/deploy.py'), sha], check=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Deployment failed: {exc}', file=sys.stderr)
        sys.exit(1)
