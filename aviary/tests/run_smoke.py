"""Run the test-only mod in a disposable world and private network namespace."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

source, artifact, host_namespace = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
if not host_namespace.startswith('net:[') or str(Path('/proc/self/ns/net').readlink()) == host_namespace:
    raise RuntimeError('A separate network namespace is mandatory.')
subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
with tempfile.TemporaryDirectory(prefix='oak-aviary-smoke-') as directory:
    root = Path(directory)
    for name in ('libraries', 'versions'):
        shutil.copytree(source/name, root/name)
    for path in source.glob('*.jar'):
        shutil.copy2(path, root/path.name)
    launcher = source/'fabric-server-launcher.properties'
    if launcher.exists():
        shutil.copy2(launcher, root/launcher.name)
    (root/'mods').mkdir()
    for path in (source/'mods').glob('fabric-api*.jar'):
        shutil.copy2(path, root/'mods'/path.name)
    shutil.copy2(artifact, root/'mods'/artifact.name)
    (root/'eula.txt').write_text('eula=true\n')
    (root/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25565\nonline-mode=false\nview-distance=2\nsimulation-distance=2\nlevel-type=minecraft:flat\nspawn-protection=0\npause-when-empty-seconds=-1\nsync-chunk-writes=false\n')
    command=['/usr/bin/java','-Xms256M','-Xmx1500M','-Doak.aviary.smoke=isolated','-jar','fabric-server-launch.jar','--nogui']
    with (root/'output.log').open('w') as output:
        process=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill();process.wait()
    result=(root/'output.log').read_text()
    if process.returncode or 'AVIARY_SMOKE PASS' not in result or 'AVIARY_SMOKE FAIL' in result:
        print(result[-18000:])
        raise SystemExit('Aviary native smoke failed.')
    print('\n'.join(line for line in result.splitlines() if 'AVIARY_SMOKE' in line))
