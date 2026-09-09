"""Explicit game maintenance, independent of website deployment. No world edits."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

from server_status import query, query_bedrock

REPO = Path(__file__).resolve().parents[1]
ROOT = Path('/srv/oak')
MOTD = r'Oak\n\u00a77Minecraft 26.3-pre-3'
SERVICES = ('oak-geyser', 'oak-bedrock-bridge', 'oak')
TIMERS = ('oak-map.timer', 'oak-backup.timer')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=240)


def replace_once(content, pattern, replacement):
    result, count = re.subn(pattern, lambda match: replacement, content, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('Expected exactly one setting: ' + pattern)
    return result


def prepare(properties, geyser, bridge):
    properties = replace_once(properties, r'^motd=.*$', 'motd=' + MOTD)
    for key, value in {'primary-motd': 'Oak', 'secondary-motd': 'Minecraft 26.3-pre-3',
                       'passthrough-motd': 'true', 'passthrough-player-counts': 'true',
                       'server-name': 'Oak'}.items():
        geyser = replace_once(geyser, r'^  ' + key + r':.*$', '  ' + key + ': ' + value)
    old = """            downstream.send(0, string(json.dumps({'version': {'name':'Oak 26.3-pre-3 / compatibility bridge','protocol':776},
                'players':{'max':1,'online':0}, 'description':{'text':'Oak crossplay'}})))"""
    new = """            from server_status import compatibility_status
            downstream.send(0, string(json.dumps(compatibility_status())))"""
    if bridge.count(old) != 1:
        raise ValueError('Unexpected crossplay status implementation')
    bridge = bridge.replace(old, new)
    compile(bridge, 'auth_bridge.py', 'exec')
    return properties.encode(), geyser.encode(), bridge.encode()


def write(path, content, metadata):
    temporary = path.with_name('.' + path.name + '.presentation.tmp')
    temporary.write_bytes(content)
    os.chmod(temporary, metadata['mode'])
    os.chown(temporary, metadata['uid'], metadata['gid'])
    temporary.replace(path)


def main():
    if sys.argv[1:] != ['--apply'] or os.geteuid() != 0:
        raise RuntimeError('Run explicitly as root with --apply from a reviewed checkout.')
    if run('git', '-C', str(REPO), 'status', '--porcelain').stdout.strip():
        raise RuntimeError('The reviewed checkout must be clean.')
    live = query()
    if live['version'].get('protocol') != 1073742159 or live['players']['online']:
        raise RuntimeError('Requires Minecraft pre-3 with no players online.')
    icon = (REPO / 'public/brand/oak-64.png').read_bytes()
    if icon[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', icon[16:24]) != (64, 64):
        raise ValueError('Invalid server icon')
    props = ROOT / 'server/server.properties'
    geyser = ROOT / 'crossplay/geyser/config.yml'
    bridge = ROOT / 'crossplay/bridge/auth_bridge.py'
    targets = dict(zip((props, geyser, bridge), prepare(props.read_text(), geyser.read_text(), bridge.read_text())))
    targets[ROOT / 'server/server-icon.png'] = icon
    targets[ROOT / 'crossplay/bridge/server_status.py'] = (REPO / 'scripts/server_status.py').read_bytes()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / 'presentation-releases' / stamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    metadata = {}
    owner = props.stat()
    for path in targets:
        stat = path.stat() if path.exists() else owner
        metadata[path] = {'mode': stat.st_mode & 0o777, 'uid': stat.st_uid, 'gid': stat.st_gid,
                          'existed': path.exists()}
        if path.exists():
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, saved)
    manifest = {'commit': run('git', '-C', str(REPO), 'rev-parse', 'HEAD').stdout.strip(),
                'files': {str(p.relative_to(ROOT)): m for p, m in metadata.items()},
                'sha256': {str(p.relative_to(ROOT)): hashlib.sha256(b).hexdigest() for p, b in targets.items()}}
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    active_timers = [timer for timer in TIMERS if subprocess.run(['systemctl', 'is-active', '--quiet', timer]).returncode == 0]
    stopped = False
    try:
        if active_timers:
            run('systemctl', 'stop', *active_timers)
        for service in ('oak-map.service', 'oak-backup.service'):
            state = run('systemctl', 'show', service, '-p', 'ActiveState', '--value').stdout.strip()
            if state not in ('inactive', 'failed'):
                raise RuntimeError('Wait for the current map or backup job to finish.')
        if query()['players']['online']:
            raise RuntimeError('A player joined; maintenance postponed.')
        print('Stopping game services for presentation maintenance.', flush=True)
        stopped = True
        run('systemctl', 'stop', *SERVICES)
        for path, content in targets.items():
            write(path, content, metadata[path])
        run('systemctl', 'start', 'oak', 'oak-bedrock-bridge', 'oak-geyser')
        for attempt in range(60):
            try:
                java = query()
                bedrock = query_bedrock()
                description = java['description']
                if isinstance(description, dict):
                    description = description.get('text', '')
                if 'Oak' not in description or '26.3-pre-3' not in description or 'favicon' not in java:
                    raise RuntimeError('Java presentation is not ready.')
                if 'Oak' not in bedrock['name'] or '26.3-pre-3' not in bedrock['description']:
                    raise RuntimeError('Bedrock presentation is not ready.')
                if (bedrock['online'], bedrock['max']) != (java['players']['online'], java['players']['max']):
                    raise RuntimeError('Bedrock player counts have not refreshed.')
                break
            except (OSError, ValueError, EOFError, RuntimeError):
                if attempt == 59:
                    raise
                time.sleep(1)
        print(json.dumps({'backup': str(backup), 'java': description, 'bedrock': bedrock,
                          'icon_sha256': hashlib.sha256(icon).hexdigest()}, ensure_ascii=False), flush=True)
    except Exception:
        if stopped:
            run('systemctl', 'stop', *SERVICES)
            for path, meta in metadata.items():
                if meta['existed']:
                    write(path, (backup / path.relative_to(ROOT)).read_bytes(), meta)
                else:
                    path.unlink(missing_ok=True)
            run('systemctl', 'start', 'oak', 'oak-bedrock-bridge', 'oak-geyser')
            print('Previous presentation restored.', file=sys.stderr)
        raise
    finally:
        if active_timers:
            run('systemctl', 'start', *active_timers)


if __name__ == '__main__':
    main()
