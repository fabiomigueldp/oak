"""Build the pinned server mod and deterministic vanilla resource pack."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--server', type=Path, required=True)
parser.add_argument('--jdk', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--smoke', action='store_true', help='Build an isolated-test artifact; never install it in production.')
args = parser.parse_args()
out, server = args.output.resolve(), args.server.resolve()
if out == server or server in out.parents:
    raise SystemExit('Output must be outside the game runtime.')
out.mkdir(parents=True, exist_ok=True)
class_directory = tempfile.TemporaryDirectory(prefix='classes-',dir=out)
classes = Path(class_directory.name)
jars = sorted((server/'libraries').rglob('*.jar'))
apis = list((server/'mods').glob('fabric-api*.jar'))
if len(apis) != 1:
    raise SystemExit('Exactly one installed Fabric API is required.')
with zipfile.ZipFile(apis[0]) as api:
    for entry in json.loads(api.read('fabric.mod.json'))['jars']:
        path = out/Path(entry['file']).name
        path.write_bytes(api.read(entry['file']))
        jars.append(path)
jars.append(server/'versions/26.3-rc-1/server-26.3-rc-1.jar')
sources=sorted((ROOT/'src').rglob('*.java'))
if args.smoke:
    sources+=sorted((ROOT/'tests').rglob('*.java'))
subprocess.run([str(args.jdk/'bin/javac'),'-proc:none','--release','25','-cp',':'.join(map(str,jars)),
                '-d',str(classes),*map(str,sources)],check=True)


def archive(path, entries):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,content in sorted(entries.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,content)


entries={p.relative_to(classes).as_posix():p.read_bytes() for p in classes.rglob('*.class')}
for name in ('fabric.mod.json','oak-aviary.mixins.json'):
    entries[name]=(ROOT/name).read_bytes()
if args.smoke:
    metadata=json.loads(entries['fabric.mod.json'])
    metadata['entrypoints']['main'].append('me.oak.aviary.Smoke')
    metadata['version']+='-smoke'
    entries['fabric.mod.json']=json.dumps(metadata).encode()
entries['condor-rig.json']=(ROOT/'art/condor-rig.json').read_bytes()
entries['condor-motion.json']=(ROOT/'art/condor-motion.json').read_bytes()
archive(out/'oak-aviary.jar',entries)
entries={p.relative_to(ROOT/'pack').as_posix():p.read_bytes() for p in (ROOT/'pack').rglob('*') if p.is_file()}
entries['pack.mcmeta']=json.dumps({'pack':{'description':'Oak Aviary','min_format':[97,1],'max_format':[97,1]}}).encode()
# Supported native post-effects: no core shader overrides or custom GLSL.
for step in range(1,17):
    t=step/16
    brightness=1-t*t*(3-2*t)
    passes=[]
    for source,target,factor in [('minecraft:main','swap',brightness),('swap','minecraft:main',1)]:
        passes.append({'vertex_shader':'minecraft:core/screenquad','fragment_shader':'minecraft:post/blit',
            'inputs':[{'sampler_name':'In','target':source}],'output':target,
            'uniforms':{'BlitConfig':[{'name':'ColorModulate','type':'vec4','value':[factor,factor,factor,1]}]}})
    entries[f'assets/oak_aviary/post_effect/fade_{step}.json']=json.dumps({'targets':{'swap':{}},'passes':passes}).encode()
pack=out/'oak-aviary-pack.zip';archive(pack,entries)
sha=hashlib.sha1(pack.read_bytes()).hexdigest()
manifest={'pack_sha1':sha,'pack_bytes':pack.stat().st_size,'mod_sha256':hashlib.sha256((out/'oak-aviary.jar').read_bytes()).hexdigest()}
(out/'build.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest))
class_directory.cleanup()
