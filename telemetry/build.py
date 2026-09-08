"""Compile against the installed, unobfuscated 26.3-pre-2 server; never start it."""
import argparse
import hashlib
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
jars = sorted((server / 'libraries').rglob('*.jar')) + sorted((server / '.fabric/processedMods').glob('*.jar'))
jars.append(server / 'versions/26.3-pre-2/server-26.3-pre-2.jar')
classes = output / 'classes'
classes.mkdir(exist_ok=True)
subprocess.run([str(args.jdk / 'bin/javac'), '--release', '25', '-cp', ':'.join(map(str, jars)), '-d', str(classes), str(root / 'src/me/oak/telemetry/OakTelemetry.java')], check=True)
artifact = output / 'oak-telemetry-1.0.0.jar'
with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as jar:
    for path in sorted(classes.rglob('*.class')):
        jar.write(path, path.relative_to(classes).as_posix())
    jar.write(root / 'fabric.mod.json', 'fabric.mod.json')
print(str(artifact) + ' sha256=' + hashlib.sha256(artifact.read_bytes()).hexdigest())
