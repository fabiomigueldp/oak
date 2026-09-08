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
    dropin.write_text('[Service]\nReadWritePaths=/run/oak-telemetry\n')
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
    print('Telemetry staged: ' + args.commit + '. Minecraft must be restarted separately to load the mod.')


if __name__ == '__main__':
    main()
