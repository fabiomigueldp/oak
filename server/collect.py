"""Publish the existing status snapshot and sampled, static player history."""
import json
import pathlib
import re
import shutil
import signal
import subprocess
import time

from activity_skins import SkinCache
from presence import MAX_PLAYERS, PlayerMetadata, PresenceHistory

ROOT = pathlib.Path('/srv/oak')
PUBLIC = ROOT / 'web'


def online_players(result):
    """A failed or incomplete RCON result is unknown, never an empty server."""
    if result.returncode != 0:
        return None
    match = re.search(r'There are (\d+) of a max of (\d+) players online:(.*)', result.stdout)
    if not match:
        return None
    names = [name.strip() for name in match[3].split(',') if name.strip()]
    if len(names) != int(match[1]) or len(names) > MAX_PLAYERS or len(set(names)) != len(names):
        return None
    if any(not 1 <= len(name) <= 32 or not name.isprintable() for name in names):
        return None
    return {'players': names, 'maximum': int(match[2])}


def collect_status(online):
    messages = []
    with (ROOT / 'server/logs/latest.log').open(errors='replace') as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - 256000))
        for line in file:
            chat = re.search(r'^\[([\d:]+)\] \[Server thread/INFO\]: (?:\[Not Secure\] )?<([A-Za-z0-9_]{1,16})> (.*)$', line)
            if chat:
                messages.append({'time': chat[1], 'player': chat[2], 'text': chat[3][:1000]})
    disk = shutil.disk_usage(ROOT)
    backups = sorted((ROOT / 'backups').glob('oak-*.tar.gz'))
    data = {'updated': time.time(), 'online': online is not None, 'players': online['players'] if online else [],
            'maxPlayers': online['maximum'] if online else 12, 'version': '26.3-rc-2', 'chat': messages[-60:],
            'disk': {'used': disk.used, 'total': disk.total, 'free': disk.free},
            'backup': {'last': backups[-1].stat().st_mtime if backups else None, 'count': len(backups),
                       'bytes': sum(path.stat().st_size for path in backups)}, 'warnings': []}
    if disk.free < 30 * 1024 ** 3:
        data['warnings'].append('Pouco espaço livre na VM')
    if not backups or time.time() - backups[-1].stat().st_mtime > 27 * 3600:
        data['warnings'].append('Backup atrasado')
    unified = ROOT / 'control/backup-public.json'
    if unified.exists():
        evidence = json.loads(unified.read_text())
        data['backup'] = evidence
        data['warnings'] = [warning for warning in data['warnings'] if warning != 'Backup atrasado']
        if evidence.get('enabled') and (not evidence.get('last') or time.time() - evidence['last'] > evidence['interval_minutes'] * 60 + 1800):
            data['warnings'].append('Backup atrasado')
    if (PUBLIC / 'map-warning.json').exists():
        data['warnings'].append('Mapa pausado para preservar armazenamento')
    temporary = PUBLIC / 'status.tmp'
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.chmod(0o644)
    temporary.replace(PUBLIC / 'status.json')


def main():
    PUBLIC.mkdir(exist_ok=True)
    history = None
    skins = None
    metadata = PlayerMetadata()

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            online = None
            try:
                result = subprocess.run(['/usr/local/sbin/oak-admin', 'list'], capture_output=True, text=True, timeout=5)
                online = online_players(result)
            except (OSError, subprocess.SubprocessError) as error:
                print(type(error).__name__, str(error), flush=True)
            # Recording is independent of log, backup and disk snapshot availability.
            try:
                if history is None:
                    history = PresenceHistory()
                observed = metadata.resolve(online['players']) if online is not None else None
                if observed is not None:
                    try:
                        if skins is None:
                            skins = SkinCache(history.state, history.public / 'skins')
                        skins.submit(observed)
                        observed = skins.metadata(observed)
                    except Exception as error:
                        print('Activity skins:', type(error).__name__, str(error), flush=True)
                        observed = [dict(player, skin='') for player in observed]
                history.observe(observed)
            except Exception as error:
                print('Presence:', type(error).__name__, str(error), flush=True)
            try:
                collect_status(online)
            except Exception as error:
                print(type(error).__name__, str(error), flush=True)
            time.sleep(5)
    finally:
        try:
            if history is not None:
                history.close()
        finally:
            if skins is not None:
                skins.close()


if __name__ == '__main__':
    main()
