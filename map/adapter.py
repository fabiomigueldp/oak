"""Read-only source adapter: new string block palettes -> legacy compounds.
Only writes an isolated renderer snapshot. Never writes the Minecraft world.
"""
import io,struct,zlib,gzip,pathlib,shutil,os,json
import argparse,hashlib,tempfile
FORMATS={1:'b',2:'h',3:'i',4:'q',5:'f',6:'d'}
def read_tag(f,t):
 def n(fmt):return struct.unpack('>'+fmt,f.read(struct.calcsize('>'+fmt)))[0]
 def s():return f.read(n('H')).decode('utf-8')
 if t in FORMATS:return n(FORMATS[t])
 if t==8:return s()
 if t in (7,11,12):
  size=n('i');width={7:1,11:4,12:8}[t];return (size,f.read(size*width))
 if t==9:
  subtype=n('b');return (subtype,[read_tag(f,subtype) for _ in range(n('i'))])
 if t==10:
  out={}
  while (typ:=n('b'))!=0:
   key=s();out[key]=(typ,read_tag(f,typ))
  return out
 raise ValueError(t)
def write_tag(f,t,v):
 def n(fmt,v):f.write(struct.pack('>'+fmt,v))
 def s(v):
  b=v.encode('utf-8');n('H',len(b));f.write(b)
 if t in FORMATS:n(FORMATS[t],v)
 elif t==8:s(v)
 elif t in (7,11,12):n('i',v[0]);f.write(v[1])
 elif t==9:
  n('b',v[0]);n('i',len(v[1]))
  for item in v[1]:write_tag(f,v[0],item)
 elif t==10:
  for key,(typ,item) in v.items():n('b',typ);s(key);write_tag(f,typ,item)
  n('b',0)
 else:raise ValueError(t)
DEFAULTS={}

def validate_paths(source, destination):
 source=source.resolve(strict=True);destination=destination.resolve()
 if not source.is_dir():raise ValueError('Source must be a region directory')
 if source==destination or source in destination.parents or destination in source.parents:
  raise ValueError('Source and destination must be separate, non-overlapping directories')
 if destination.exists() and not destination.is_dir():raise ValueError('Destination must be a directory')
 if destination.exists() and any(p.is_symlink() for p in destination.iterdir()):
  raise ValueError('Destination must not contain symlinks')
 return source,destination

def load_defaults(report):
 data=report.read_bytes();blocks=json.loads(data)
 defaults={}
 for name,block in blocks.items():
  states=[state for state in block['states'] if state.get('default')]
  if len(states)!=1:raise ValueError('Expected one default state for '+name)
  defaults[name]=states[0].get('properties',{})
 DEFAULTS.clear();DEFAULTS.update(defaults)
 return hashlib.sha256(data).hexdigest()
def adapt(raw):
 f=io.BytesIO(raw);typ=f.read(1)[0];length=struct.unpack('>H',f.read(2))[0];name=f.read(length)
 root=read_tag(f,typ)
 for section in root.get('sections',(9,(10,[])))[1][1]:
  states=section.get('block_states',(10,{}))[1]
  palette=states.get('palette')
  if palette and palette[1][0]==8:
   converted=[]
   for block in palette[1][1]:
    namepart,sep,props=block.partition('[')
    item={'Name':(8,namepart)}
    if sep:
     item['Properties']=(10,{k:(8,v) for k,v in (p.split('=',1) for p in props.rstrip(']').split(','))})
    converted.append(item)
   states['palette']=(9,(10,converted))
  elif palette and palette[1][0]==10:
   for item in palette[1][1]:
    if '' in item:item['Name']=item.pop('')
    if 'id' in item:item['Name']=item.pop('id')
    if 'properties' in item:item['Properties']=item.pop('properties')
  # Since 26.3, compact and partial block states omit default properties.
  # BlueMap's legacy matcher requires the complete state, including defaults.
  if palette:
   for item in states['palette'][1][1]:
    namepart=item['Name'][1]
    if namepart in DEFAULTS:
     props={k:(8,v) for k,v in DEFAULTS[namepart].items()}
     props.update(item.get('Properties',(10,{}))[1])
     if props:item['Properties']=(10,props)
 out=io.BytesIO();out.write(bytes([typ])+struct.pack('>H',length)+name);write_tag(out,typ,root);return out.getvalue()
def region(src,dst):
 if src.resolve()==dst.resolve():raise ValueError('Refusing source overwrite')
 b=src.read_bytes()
 if len(b)<8192:raise ValueError('Truncated region header')
 head=bytearray(8192);head[4096:8192]=b[4096:8192];body=bytearray();sector=2
 for i in range(1024):
  off=int.from_bytes(b[i*4:i*4+3],'big')*4096
  if not off:continue
  allocated=b[i*4+3]*4096
  if off<8192 or allocated==0 or off+allocated>len(b):raise ValueError('Invalid region location')
  size=int.from_bytes(b[off:off+4],'big');kind=b[off+4]
  if not 1<=size<=allocated-4:raise ValueError('Invalid chunk length')
  payload=b[off+5:off+4+size]
  if kind==2:raw=zlib.decompress(payload)
  elif kind==1:raw=gzip.decompress(payload)
  elif kind==3:raw=payload
  else:raise ValueError('Unsupported region compression '+str(kind))
  packed=zlib.compress(adapt(raw),1);record=struct.pack('>I',len(packed)+1)+b'\x02'+packed
  count=(len(record)+4095)//4096
  if count>255:raise ValueError('Oversized chunk')
  head[i*4:i*4+4]=sector.to_bytes(3,'big')+bytes([count]);body.extend(record);body.extend(bytes(count*4096-len(record)));sector+=count
 dst.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile(dir=dst.parent,prefix='.adapter-',delete=False) as f:
  tmp=pathlib.Path(f.name)
  try:f.write(head+body)
  except BaseException:
   tmp.unlink(missing_ok=True);raise
 try:tmp.replace(dst)
 finally:tmp.unlink(missing_ok=True)

def main(argv=None):
 parser=argparse.ArgumentParser(description='Adapt Minecraft block palettes in an isolated MCA region directory for BlueMap.')
 parser.add_argument('--source',type=pathlib.Path,required=True,help='Read-only input region directory')
 parser.add_argument('--destination',type=pathlib.Path,required=True,help='Separate output region directory')
 parser.add_argument('--blocks-report',type=pathlib.Path,required=True,help='Exact Minecraft version reports/blocks.json')
 parser.add_argument('--min-free-gib',type=float,default=1,help='Free-space reserve before each region (default: 1 GiB)')
 parser.add_argument('--force',action='store_true',help='Ignore incremental conversion stamps')
 args=parser.parse_args(argv)
 if not 0<=args.min_free_gib<1000000:raise ValueError('Invalid free-space reserve')
 source,destination=validate_paths(args.source,args.destination)
 report_hash=load_defaults(args.blocks_report)
 files=sorted(source.glob('r.*.*.mca'))
 if not files:raise ValueError('No MCA region files found in source')
 destination.mkdir(parents=True,exist_ok=True)
 converted=skipped=0
 for src in files:
  if src.is_symlink():raise ValueError('Source region symlinks are not supported')
  dst=destination/src.name
  stamp=dst.with_suffix('.source-mtime')
  stat=src.stat();key=f'portable-v1:{report_hash}:{stat.st_mtime_ns}:{stat.st_size}'
  if not args.force and dst.exists() and stamp.exists() and stamp.read_text()==key:
   skipped+=1;continue
  if shutil.disk_usage(destination).free<args.min_free_gib*1024**3:
   raise RuntimeError('Destination free-space reserve reached')
  region(src,dst)
  if (src.stat().st_mtime_ns,src.stat().st_size)==(stat.st_mtime_ns,stat.st_size):
   stamp.write_text(key)
  else:
   stamp.unlink(missing_ok=True)
   print('Source changed during conversion; retry required:',src.name,flush=True)
  converted+=1
 print(f'Converted {converted} regions; skipped {skipped} unchanged regions.')

if __name__=='__main__':
 main()
