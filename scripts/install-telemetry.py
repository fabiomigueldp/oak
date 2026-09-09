"""Stage the reviewed telemetry mod and runtime permissions; never restart Minecraft."""
import argparse
import grp
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


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
    uid, gid = pwd.getpwnam('oak').pw_uid, grp.getgrnam('oak-control').gr_gid
    directory = Path('/run/oak-telemetry')
    directory.mkdir(exist_ok=True)
    os.chown(directory, uid, gid)
    directory.chmod(0o2750)
    Path('/etc/tmpfiles.d/oak-telemetry.conf').write_text('d /run/oak-telemetry 2750 oak oak-control -\n')
    dropin = Path('/etc/systemd/system/oak.service.d/95-telemetry.conf')
    # The existing stop helper is root-only; do not broaden its file permissions.
    dropin.write_text('[Service]\nReadWritePaths=/run/oak-telemetry\n')
    # Sort after crossplay.conf, which otherwise appends an unprivileged stop.
    Path('/etc/systemd/system/oak.service.d/zz-oak-stop.conf').write_text(
        '[Service]\nExecStop=\nExecStop=+/usr/local/sbin/oak-admin stop\n')
    release = Path('/opt/oak-telemetry/releases') / args.commit
    release.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='oak-telemetry-') as output:
        print(run('/usr/bin/python3', str(ROOT / 'telemetry/build.py'), '--server', '/srv/oak/server', '--jdk', args.jdk, '--output', output))
        artifact = Path(output) / 'oak-telemetry-1.0.0.jar'
        shutil.copy2(artifact, release / artifact.name)
        target = Path('/srv/oak/server/mods/oak-telemetry-1.0.0.jar')
        if target.exists():
            shutil.copy2(target, release / 'previous.jar')
        staged = target.with_suffix('.pending')
        shutil.copy2(artifact, staged)
        staged.chmod(0o644)
        staged.replace(target)
    run('systemctl', 'daemon-reload')
    # Runtime Minecraft textures stay outside Git and the public website.
    textures = Path('/var/lib/oak-control/avatar-assets')
    control_uid = pwd.getpwnam('oak-control').pw_uid
    with zipfile.ZipFile('/srv/oak/map-test/data/minecraft-client-26.3-pre-3.jar') as jar:
        prefix = 'assets/minecraft/textures/'
        for name in jar.namelist():
            relative = name.removeprefix(prefix)
            if not name.startswith(prefix) or not re.fullmatch(r'(?:item|block|entity/equipment/humanoid|entity/equipment/humanoid_leggings|entity/equipment/wings|entity/player/(?:wide|slim))/[a-z0-9_]+\.png', relative):
                continue
            path = textures / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(jar.read(name))
            os.chown(path, control_uid, gid)
            path.chmod(0o600)
    print('Telemetry staged: ' + args.commit + '. Minecraft must be restarted separately to load the mod.')


if __name__ == '__main__':
    main()
