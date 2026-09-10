"""Compile against the installed, unobfuscated 26.3-rc-1 server; never start it."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--server', type=Path, required=True)
parser.add_argument('--jdk', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
output = args.output.resolve()
server = args.server.resolve()
if output == server or server in output.parents:
    raise SystemExit('Build output must be outside the Minecraft directory.')
output.mkdir(parents=True, exist_ok=True)
jars = sorted((server / 'libraries').rglob('*.jar'))
# Fabric retains processed modules from previous releases. Compile only against
# modules declared by the currently installed API, never that stale cache.
apis = list((server / 'mods').glob('fabric-api*.jar'))
if len(apis) != 1:
    raise SystemExit('Exactly one installed Fabric API jar is required.')
api_output = output / 'fabric-api'
api_output.mkdir(exist_ok=True)
with zipfile.ZipFile(apis[0]) as api:
    for entry in json.loads(api.read('fabric.mod.json'))['jars']:
        name = entry['file']
        target = api_output / Path(name).name
        target.write_bytes(api.read(name))
        jars.append(target)
jars.append(server / 'versions/26.3-rc-1/server-26.3-rc-1.jar')
classes = output / 'classes'
classes.mkdir(exist_ok=True)
subprocess.run([str(args.jdk / 'bin/javac'), '--release', '25', '-cp', ':'.join(map(str, jars)), '-d', str(classes), *map(str, sorted((root / 'src').rglob('*.java')))], check=True)
artifact = output / 'oak-telemetry-1.0.0.jar'
with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as jar:
    for path in sorted(classes.rglob('*.class')):
        jar.write(path, path.relative_to(classes).as_posix())
    jar.write(root / 'fabric.mod.json', 'fabric.mod.json')
print(str(artifact) + ' sha256=' + hashlib.sha256(artifact.read_bytes()).hexdigest())
