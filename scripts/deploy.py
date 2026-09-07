"""Deploy tracked website files only; preserve game and generated runtime data."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
STATE = Path('/srv/oak/deployments')
TARGETS = {
    'public/index.html': Path('/srv/oak/web/index.html'),
    'public/style.css': Path('/srv/oak/web/style.css'),
    'public/app.js': Path('/srv/oak/web/app.js'),
    'server/chat-server.py': Path('/srv/oak/chat-server.py'),
    'server/collect.py': Path('/srv/oak/collect.py'),
    'deploy/nginx.conf': Path('/srv/oak/nginx.conf'),
    'deploy/systemd/oak-chat.service': Path('/etc/systemd/system/oak-chat.service'),
    'deploy/systemd/oak-web-collector.service': Path('/etc/systemd/system/oak-web-collector.service'),
}

def run(*args):
    subprocess.run(args, check=True, timeout=120)

def install_file(path, content, mode, uid, gid):
    if path == Path('/srv/oak/nginx.conf'):
        # Docker binds this file's inode; replacing it would leave a stale mount.
        path.write_bytes(content)
        os.chmod(path, mode)
        os.chown(path, uid, gid)
    else:
        temporary = path.with_name('.' + path.name + '.deploy.tmp')
        temporary.write_bytes(content)
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        temporary.replace(path)

def activate(changed):
    if any(name.endswith('.service') for name in changed):
        run('systemctl', 'daemon-reload')
    if {'server/chat-server.py', 'deploy/systemd/oak-chat.service'} & changed:
        run('systemctl', 'restart', 'oak-chat')
    if {'server/collect.py', 'deploy/systemd/oak-web-collector.service'} & changed:
        run('systemctl', 'restart', 'oak-web-collector')
    if 'deploy/nginx.conf' in changed:
        run('docker', 'exec', 'oak-web', 'nginx', '-t')
        run('docker', 'exec', 'oak-web', 'nginx', '-s', 'reload')

def health():
    for attempt in range(5):
        try:
            run('systemctl', 'is-active', '--quiet', 'oak-chat', 'oak-web-collector')
            with urllib.request.urlopen('https://oak.fabiomigueldp.me/', timeout=10) as response:
                if b'Oak' not in response.read():
                    raise RuntimeError('Unexpected dashboard response.')
            with urllib.request.urlopen('https://oak.fabiomigueldp.me/status.json', timeout=10) as response:
                data = json.load(response)
                if time.time() - data['updated'] > 30:
                    raise RuntimeError('Stale status collector.')
            with urllib.request.urlopen('https://oak.fabiomigueldp.me/api/events', timeout=10) as response:
                if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                    raise RuntimeError('SSE endpoint is unavailable.')
                first_line = response.readline()
                if not first_line.startswith(b'data: '):
                    raise RuntimeError('SSE endpoint did not return a snapshot.')
                snapshot = json.loads(first_line[6:])
                if 'players' not in snapshot:
                    raise RuntimeError('SSE snapshot is not ready.')
            return
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2)

def main(sha):
    if os.geteuid() != 0:
        raise RuntimeError('Run through sudo oak-site-deploy.')
    run(sys.executable, str(REPO / 'scripts/check.py'))
    # Validate the candidate configuration without changing the live container.
    run('docker', 'run', '--rm', '--network', 'none', '--memory', '64m',
        '-v', str(REPO / 'deploy/nginx.conf') + ':/etc/nginx/conf.d/default.conf:ro',
        'nginx:1.28-alpine', 'nginx', '-t')
    run('systemd-analyze', 'verify',
        str(REPO / 'deploy/systemd/oak-chat.service'),
        str(REPO / 'deploy/systemd/oak-web-collector.service'))
    changed = {name for name, target in TARGETS.items()
               if target.read_bytes() != (REPO / name).read_bytes()}
    STATE.mkdir(mode=0o750, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = STATE / (stamp + '-' + sha[:12])
    backup.mkdir(mode=0o750)
    manifest = {}
    for name, target in TARGETS.items():
        stat = target.stat()
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(target.read_bytes())
        manifest[name] = {'mode': stat.st_mode & 0o777, 'uid': stat.st_uid, 'gid': stat.st_gid}
    current = STATE / 'current.json'
    previous = json.loads(current.read_text()) if current.exists() else None
    (backup / 'manifest.json').write_text(json.dumps({'previous': previous, 'files': manifest}, indent=2))
    try:
        # Assets precede HTML so newly referenced assets exist when HTML is served.
        for name in sorted(changed, key=lambda value: value == 'public/index.html'):
            metadata = manifest[name]
            install_file(TARGETS[name], (REPO / name).read_bytes(), **metadata)
        activate(changed)
        health()
    except Exception:
        for name in changed:
            install_file(TARGETS[name], (backup / name).read_bytes(), **manifest[name])
        activate(changed)
        health()
        print('Deployment rejected; previous files restored.', file=sys.stderr)
        raise
    result = {'commit': sha, 'deployed_at': stamp, 'previous': previous.get('commit') if previous else None}
    temporary = current.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.replace(current)
    # Only prune directories created by this deployer; never touch game backups.
    snapshots = sorted(p for p in STATE.iterdir() if p.is_dir() and (p / 'manifest.json').exists())
    for old in snapshots[:-5]:
        shutil.rmtree(old)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main(sys.argv[1])
