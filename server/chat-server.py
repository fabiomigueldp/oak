import collections,datetime,http.server,json,pathlib,re,socket,struct,threading,time
ROOT=pathlib.Path('/srv/oak')
ORIGIN='https://oak.fabiomigueldp.me'
condition=threading.Condition(); snapshot='{}'; revision=0
web_messages=collections.deque(maxlen=60)
rate_lock=threading.Lock();recent={};global_rate=collections.deque()
streams=threading.BoundedSemaphore(32)
def exact(s,n):
 b=b''
 while len(b)<n:
  v=s.recv(n-len(b))
  if not v:raise ConnectionError('RCON closed')
  b+=v
 return b
def packet(s,i,t,msg):
 p=struct.pack('<ii',i,t)+msg.encode()+b'\0\0';s.sendall(struct.pack('<i',len(p))+p)
 n=struct.unpack('<i',exact(s,4))[0]
 if not 10<=n<=1048576:raise ValueError('Invalid packet')
 r=exact(s,n);return struct.unpack('<i',r[:4])[0],r[8:-2].decode(errors='replace')
def send_message(name,message):
 props=dict(l.split('=',1) for l in (ROOT/'server/server.properties').read_text().splitlines() if '=' in l and not l.startswith('#'))
 text=[{'text':'[Web] ','color':'gray'},{'text':name+': ','color':'green'},{'text':message,'color':'white'}]
 with socket.create_connection(('127.0.0.1',int(props['rcon.port'])),timeout=5) as s:
  if packet(s,1,3,props['rcon.password'])[0]!=1:raise ConnectionError('Authentication failed')
  rid,result=packet(s,2,2,'tellraw @a '+json.dumps(text,ensure_ascii=True))
  if rid!=2 or 'Incorrect' in result or 'Unknown' in result:raise ConnectionError('Command failed')
def validate(data):
 name=data.get('name','');message=data.get('message','')
 if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_]{3,16}',name):raise ValueError('Use um nome de 3 a 16 letras, números ou _.')
 if not isinstance(message,str) or not 1<=len(message.strip())<=240 or any(ord(c)<32 or c=='§' for c in message):raise ValueError('Escreva uma mensagem de até 240 caracteres, em uma linha.')
 return name,message.strip()
def monitor():
 global snapshot,revision
 while True:
  try:
   data=json.loads((ROOT/'web/status.json').read_text());messages=[]
   with (ROOT/'server/logs/latest.log').open(errors='replace') as f:
    f.seek(0,2);size=f.tell();f.seek(max(0,size-256000))
    for line in f:
     m=re.match(r'^\[([\d:]+)\] \[Server thread/INFO\]: (?:\[Not Secure\] )?<([A-Za-z0-9_]{1,16})> (.*)$',line)
     if m:messages.append({'time':m[1],'player':m[2],'text':m[3][:1000],'source':'game'})
   with condition:
    messages.extend(list(web_messages))
    # UTC clock order relative to now also works across midnight.
    now=datetime.datetime.now(datetime.timezone.utc);seconds=now.hour*3600+now.minute*60+now.second
    def age(m):
     h,mi,s=map(int,m['time'].split(':'));return (seconds-h*3600-mi*60-s)%86400
    data['chat']=sorted(messages,key=age,reverse=True)[-60:]
    encoded=json.dumps(data,ensure_ascii=False)
    if encoded!=snapshot:snapshot=encoded;revision+=1;condition.notify_all()
  except Exception as e:print(type(e).__name__,flush=True)
  time.sleep(1)
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def result(self,status,data):
  body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path!='/api/events':return self.result(404,{'error':'Not found'})
  if not streams.acquire(False):return self.result(503,{'error':'Muitas conexões. Tente novamente.'})
  try:
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('X-Accel-Buffering','no');self.end_headers();seen=-1
   self.connection.settimeout(20)
   while True:
    with condition:
     if revision==seen:condition.wait(timeout=15)
     current=revision;payload=snapshot
    event=('data: '+payload+'\n\n') if current!=seen else ': heartbeat\n\n'
    self.wfile.write(event.encode());self.wfile.flush();seen=current
  except (OSError,TimeoutError):pass
  finally:streams.release()
 def do_POST(self):
  if self.path!='/api/chat':return self.result(404,{'error':'Not found'})
  if self.headers.get('Origin')!=ORIGIN or self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.result(403,{'error':'Origem inválida.'})
  try:
   self.connection.settimeout(5);n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=2048:raise ValueError('Mensagem inválida.')
   data=json.loads(self.rfile.read(n))
   if not isinstance(data,dict):raise ValueError('Mensagem inválida.')
   name,message=validate(data)
  except (ValueError,TypeError):return self.result(400,{'error':'Use um nome de 3–16 letras/números e uma mensagem de até 240 caracteres.'})
  ip=self.headers.get('X-Forwarded-For',self.client_address[0]).split(',')[0].strip();now=time.monotonic()
  with rate_lock:
   for key in list(recent):
    if now-recent[key]>60:recent.pop(key)
   while global_rate and now-global_rate[0]>60:global_rate.popleft()
   if now-recent.get(ip,-100)<10 or len(global_rate)>=15:return self.result(429,{'error':'Aguarde alguns segundos antes de enviar novamente.'})
   recent[ip]=now;global_rate.append(now)
  try:
   if not json.loads((ROOT/'web/status.json').read_text()).get('online'):raise ConnectionError()
   send_message(name,message)
  except Exception:return self.result(503,{'error':'Não foi possível confirmar o envio. Confira o jogo antes de tentar novamente.'})
  with condition:web_messages.append({'time':datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S'),'player':name,'text':message,'source':'web'})
  self.result(200,{'ok':True})
if __name__=='__main__':
 threading.Thread(target=monitor,daemon=True).start()
 http.server.ThreadingHTTPServer(('172.19.0.1',8091),Handler).serve_forever()
