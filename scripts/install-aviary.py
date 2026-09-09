"""Stage an exact reviewed Aviary release. Minecraft restart is a separate action."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def atomic(path, content, mode=0o644, uid=0, gid=0):
    pending = path.with_name(path.name + '.pending')
    with pending.open('wb') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(pending, mode)
    os.chown(pending, uid, gid)
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('commit')
    parser.add_argument('--jdk', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{40}', args.commit):
        raise RuntimeError('Root and a full reviewed commit are required.')
    if run('git', '-C', str(ROOT), 'rev-parse', 'HEAD') != args.commit or run('git', '-C', str(ROOT), 'status', '--porcelain'):
        raise RuntimeError('Checkout must be clean and match the reviewed commit.')
    if Path('/srv/oak/control/restore-pending.json').exists():
        raise RuntimeError('Resolve the pending restore first.')
    runtime = Path('/srv/oak/server')
    if (runtime/'mods/oak-aviary.jar').exists() and subprocess.run(['systemctl', 'is-active', '--quiet', 'oak']).returncode == 0:
        raise RuntimeError('Stop Minecraft before upgrading an existing Aviary installation to prevent concurrent policy writes.')
    state = runtime/'config/oak-aviary'
    if (state/'journeys').exists() and any((state/'journeys').glob('*.json')):
        raise RuntimeError('Finish or recover active Aviary journeys before staging.')
    release = Path('/opt/oak-aviary/releases')/args.commit
    release.mkdir(parents=True, exist_ok=False)
    account = pwd.getpwnam('oak')
    with tempfile.TemporaryDirectory(prefix='oak-aviary-release-') as output:
        print(run('/usr/bin/python3', str(ROOT/'aviary/build.py'), '--server', str(runtime), '--jdk', args.jdk, '--output', output))
        built = Path(output)
        manifest = json.loads((built/'build.json').read_text())
        with zipfile.ZipFile(built/'oak-aviary.jar') as jar:
            metadata = json.loads(jar.read('fabric.mod.json'))
            if metadata['entrypoints']['main'] != ['me.oak.aviary.Aviary'] or any('Smoke' in name for name in jar.namelist()):
                raise RuntimeError('Test artifacts must never be deployed.')
        sha = manifest['pack_sha1']
        packs = Path('/srv/oak/web/packs')
        packs.mkdir(exist_ok=True)
        pack_name = 'oak-aviary-' + sha + '.zip'
        atomic(packs/pack_name, (built/'oak-aviary-pack.zip').read_bytes())
        url = 'https://oak.fabiomigueldp.me/packs/' + pack_name
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = response.read(1_048_577)
        if hashlib.sha1(payload).hexdigest() != sha:
            raise RuntimeError('Published resource pack verification failed.')
        state.mkdir(exist_ok=True)
        os.chown(state, account.pw_uid, account.pw_gid)
        state.chmod(0o700)
        policy_path = state/'settings.json'
        policy = {'revision': 0, 'settings': {'enabled': True, 'shortcutDistance': 500, 'maxFlights': 2}, 'ports': []}
        if policy_path.exists():
            shutil.copy2(policy_path, release/'previous-settings.json')
            (release/'previous-settings.json').chmod(0o600)
            policy = json.loads(policy_path.read_text())
        policy['revision'] += 1
        policy['settings'].update(packUrl=url, packSha1=sha)
        target = runtime/'mods/oak-aviary.jar'
        if target.exists():
            shutil.copy2(target, release/'previous.jar')
        for name in ('oak-aviary.jar', 'oak-aviary-pack.zip', 'build.json'):
            shutil.copy2(built/name, release/name)
        atomic(policy_path, (json.dumps(policy, indent=2)+'\n').encode(), 0o600, account.pw_uid, account.pw_gid)
        atomic(target, (built/'oak-aviary.jar').read_bytes())
    print('Aviary staged: ' + args.commit + '. Restart Minecraft separately to load it.')
    print('Resource pack: ' + url)


if __name__ == '__main__':
    main()
