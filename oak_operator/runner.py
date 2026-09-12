"""Execute Minecraft commands under the existing world-operation lock."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/srv/oak')
    parser.add_argument('--job', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    spec = json.loads(Path(args.job).read_text(encoding='utf-8'))
    if args.execute and sys.stdin.buffer.readline() != b'go\n':
        raise SystemExit('Execution handshake was not received.')
    if spec['kind'] != 'command':
        suffix = {'shell': '.ps1' if os.name == 'nt' else '.sh', 'python': '.py', 'javascript': '.mjs'}[spec['kind']]
        script = Path(args.job).with_suffix(suffix)
        script.write_text(spec['source'], encoding='utf-8')
        script.chmod(0o600)
        if spec['kind'] == 'python':
            argv = [sys.executable, '-u', str(script)]
        elif spec['kind'] == 'javascript':
            executable = shutil.which('node')
            if not executable:
                raise SystemExit('Node.js is not installed or is not on PATH.')
            argv = [executable, str(script)]
        elif os.name == 'nt':
            executable = shutil.which('pwsh') or shutil.which('powershell')
            argv = [executable, '-NoProfile', '-NonInteractive', '-File', str(script)]
        else:
            argv = ['/bin/bash' if Path('/bin/bash').exists() else '/bin/sh', str(script)]
        if os.name == 'posix':
            os.execvpe(argv[0], argv, os.environ)
        raise SystemExit(subprocess.call(argv))
    from admin.runtime import Runtime
    runtime = Runtime(Path(args.root))
    with runtime.lock():
        for line in spec['source'].splitlines():
            if line.strip():
                print(runtime.rcon.command(line.lstrip('/')), flush=True)


if __name__ == '__main__':
    main()
