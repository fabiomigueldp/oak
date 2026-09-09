import collections,datetime,http.client,http.server,json,pathlib,re,socket,struct,threading,time
ROOT=pathlib.Path('/srv/oak')
ORIGIN='https://oak.fabiomigueldp.me'
condition=threading.Condition(); snapshot='{}'; revision=0
web_messages=collections.deque(maxlen=60)
rate_lock=threading.Lock();recent={};global_rate=collections.deque()
streams=threading.BoundedSemaphore(32)
admin_streams=threading.BoundedSemaphore(128)
MAX_MESSAGE=10000
MAX_NAME=64
MAX_BODY=131072

def chat_commands(name,message):
 # Budget the JSON-escaped command, including RCON's 14 framing bytes.
 def command(text):
  return 'tellraw @a '+json.dumps([{'text':'[Web] ','color':'gray'},{'text':name+': ','color':'green'},{'text':text,'color':'white'}],ensure_ascii=True,separators=(',',':'))
 budget=1446-len(command(''));chunk=[];size=0
 for char in message:
  width=len(json.dumps(char,ensure_ascii=True))-2
  if size+width>budget:
   yield command(''.join(chunk));chunk=[];size=0
  chunk.append(char);size+=width
 if chunk:yield command(''.join(chunk))
# Floodgate adds a dot prefix to Bedrock names; keep system/private logs excluded.
GAME_CHAT=re.compile(r'^\[([\d:]+)\] \[Server thread/INFO\]: (?:\[Not Secure\] )?<([.]?[A-Za-z0-9_]{1,16})> (.*)$')
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
 with socket.create_connection(('127.0.0.1',int(props['rcon.port'])),timeout=5) as s:
  if packet(s,1,3,props['rcon.password'])[0]!=1:raise ConnectionError('Authentication failed')
  for command in chat_commands(name,message):
   rid,result=packet(s,2,2,command)
   if rid!=2 or result.strip():raise ConnectionError('Command failed')
def validate(data):
 name=data.get('name','');message=data.get('message','')
 if not isinstance(name,str) or not 1<=len(name.strip())<=MAX_NAME:raise ValueError('Informe um nome de até 64 caracteres.')
 if not isinstance(message,str) or not 1<=len(message.strip())<=MAX_MESSAGE:raise ValueError('Escreva uma mensagem de até 10.000 caracteres.')
 # Reject malformed Unicode, but allow all valid text through JSON escaping.
 try:(name+message).encode('utf-8')
 except UnicodeEncodeError:raise ValueError('O texto contém um caractere Unicode inválido.')
 return name.strip(),message.strip()
def monitor():
 global snapshot,revision
 while True:
  try:
   data=json.loads((ROOT/'web/status.json').read_text());messages=[]
   with (ROOT/'server/logs/latest.log').open(errors='replace') as f:
    f.seek(0,2);size=f.tell();f.seek(max(0,size-256000))
    for line in f:
     m=GAME_CHAT.match(line)
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
 def admin_proxy(self):
  # Fixed loopback destination: no caller-controlled host or internal path rewrite.
  if not self.path.startswith('/admin/api/'):return self.result(404,{'error':'Not found'})
  if not admin_streams.acquire(False):return self.result(503,{'error':'Painel ocupado. Tente novamente.'})
  connection=http.client.HTTPConnection('127.0.0.1',8092,timeout=150)
  sent=False
  try:
   self.connection.settimeout(15)
   if self.headers.get('Transfer-Encoding'):return self.result(400,{'error':'Unsupported transfer encoding'})
   size=int(self.headers.get('Content-Length','0'))
   if not 0<=size<=65536:return self.result(413,{'error':'Solicitação muito grande.'})
   data=self.rfile.read(size) if size else None
   headers={name:self.headers[name] for name in ('Origin','Content-Type','Cookie','X-Oak-CSRF','Idempotency-Key','Accept') if name in self.headers}
   headers['X-Real-IP']=self.headers.get('X-Oak-Client-IP',self.client_address[0]).split(',')[-1].strip()
   connection.request(self.command,self.path,body=data,headers=headers)
   response=connection.getresponse()
   self.send_response(response.status)
   for name,value in response.getheaders():
    if name.lower() not in ('connection','transfer-encoding','server','date','content-length'):self.send_header(name,value)
   self.send_header('Connection','close');self.end_headers();sent=True;self.close_connection=True
   while True:
    chunk=response.read1(16384)
    if not chunk:break
    self.wfile.write(chunk);self.wfile.flush()
  except (OSError,ValueError,http.client.HTTPException):
   if not sent:self.result(503,{'error':'O painel está indisponível no momento.'})
  finally:
   connection.close();admin_streams.release()
 def do_PATCH(self):return self.admin_proxy()
 def do_DELETE(self):return self.admin_proxy()
 def log_message(self,*args):pass
 def result(self,status,data):
  body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path.startswith('/admin/api/'):return self.admin_proxy()
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
  if self.path.startswith('/admin/api/'):return self.admin_proxy()
  if self.path!='/api/chat':return self.result(404,{'error':'Not found'})
  if self.headers.get('Origin')!=ORIGIN or self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.result(403,{'error':'Origem inválida.'})
  try:
   self.connection.settimeout(5);n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=MAX_BODY:return self.result(413,{'error':'O envio excede 128 KB. Reduza o texto.'})
   data=json.loads(self.rfile.read(n))
   if not isinstance(data,dict):raise ValueError('Mensagem inválida.')
   name,message=validate(data)
  except (json.JSONDecodeError,UnicodeDecodeError,TypeError):return self.result(400,{'error':'Formato de mensagem inválido.'})
  except ValueError as error:return self.result(400,{'error':str(error)})
  ip=self.headers.get('X-Forwarded-For',self.client_address[0]).split(',')[0].strip();now=time.monotonic()
  with rate_lock:
   for key in list(recent):
    while recent[key] and now-recent[key][0]>=60:recent[key].popleft()
    if not recent[key]:recent.pop(key)
   while global_rate and now-global_rate[0]>60:global_rate.popleft()
   if len(recent.get(ip,()))>=60 or len(global_rate)>=300:return self.result(429,{'error':'Muitas mensagens no último minuto. Aguarde um pouco e tente novamente.'})
   recent.setdefault(ip,collections.deque()).append(now);global_rate.append(now)
  try:
   if not json.loads((ROOT/'web/status.json').read_text()).get('online'):raise ConnectionError()
   send_message(name,message)
  except Exception:return self.result(503,{'error':'Não foi possível confirmar o envio. Confira o jogo antes de tentar novamente.'})
  with condition:web_messages.append({'time':datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S'),'player':name,'text':message,'source':'web'})
  self.result(200,{'ok':True})
if __name__=='__main__':
 threading.Thread(target=monitor,daemon=True).start()
 http.server.ThreadingHTTPServer(('172.19.0.1',8091),Handler).serve_forever()
