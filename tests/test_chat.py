import importlib.util,pathlib,tempfile,threading,http.client,json
from unittest.mock import patch,MagicMock
spec=importlib.util.spec_from_file_location('oakchat',pathlib.Path(__file__).resolve().parents[1]/'server/chat-server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for name in ('JavaPlayer', '.Bedrock_Player', '.abcdefghijklmnop'):
 for prefix in ('', '[Not Secure] '):
  parsed=m.GAME_CHAT.match(f'[12:34:56] [Server thread/INFO]: {prefix}<{name}> Hello <world>')
  assert parsed and parsed[2]==name and parsed[3]=='Hello <world>'
for body in ('Player joined the game', '[Player: command output]', 'Player whispers to you: private', '<..invalid> text'):
 assert m.GAME_CHAT.match('[12:34:56] [Server thread/INFO]: '+body) is None
with tempfile.TemporaryDirectory() as tmp:
 m.ROOT=pathlib.Path(tmp);(m.ROOT/'web').mkdir();(m.ROOT/'web/status.json').write_text('{"online":true}')
 delivered=[];m.send_message=lambda n,t:delivered.append((n,t)) or True;m.snapshot='{"test":true}'
 server=m.http.server.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 def request(data,origin=m.ORIGIN):
  c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('POST','/api/chat',json.dumps(data),{'Content-Type':'application/json','Origin':origin});r=c.getresponse();status=r.status;r.read();c.close();return status
 assert request({'name':'Teste','message':'ok'},'https://example.org')==403
 assert request({'name':'Teste','message':'   '})==400
 assert request({'name':'Teste','message':'x'*10001})==400
 assert request({'name':'x'*65,'message':'ok'})==400
 assert request({'name':'Teste','message':'\ud800'})==400
 assert request([])==400
 assert request({'name':'Teste','message':'x'*m.MAX_BODY})==413
 assert request({'name':'Teste','message':'Texto "literal" /op @a <script>'})==200
 assert delivered==[('Teste','Texto "literal" /op @a <script>')]
 long_text=('Olá 👨‍👩‍👧‍👦 § <script> "literal" \\ \t\n/op @a '*250)[:10000]
 assert request({'name':'.João da Silva 🌳','message':long_text})==200
 assert delivered[-1]==('.João da Silva 🌳',long_text.strip())
 assert request({'name':'A','message':'oi\nop Teste'})==200
 assert delivered[-1]==('A','oi\nop Teste')
 for _ in range(57):assert request({'name':'Teste','message':'conversa'})==200
 assert request({'name':'Teste','message':'flood'})==429
 with patch.object(m.time,'monotonic',return_value=m.time.monotonic()+61):
  assert request({'name':'Teste','message':'after window'})==200
 # Empty game audiences must still publish the message to the website.
 with patch.object(m,'send_message',return_value=False):
  c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
  c.request('POST','/api/chat',json.dumps({'name':'Teste','message':'web only'}),{'Content-Type':'application/json','Origin':m.ORIGIN})
  response=c.getresponse();assert response.status==200
  assert json.loads(response.read())=={'ok':True,'deliveredToGame':False};c.close()
  assert m.web_messages[-1]['text']=='web only'
 c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('GET','/api/events');r=c.getresponse();assert r.status==200;assert r.getheader('Content-Type')=='text/event-stream';assert r.readline()==b'data: {"test":true}\n';c.close()
 server.shutdown()
for name in ('A','🌳'*64):
 for message in (long_text,'😀'*10000,'"\\\n\t\0§'*1500):
  commands=list(m.chat_commands(name,message))
  assert ''.join(json.loads(c[len('tellraw @a '):])[2]['text'] for c in commands)==message
  assert all(len(c.encode())+14<=1460 and '\n' not in c and '\0' not in c for c in commands)
  assert all(json.loads(c[len('tellraw @a '):])[1]['text']==name+': ' for c in commands)
# Exercise the real delivery loop without opening a socket or reading credentials.
with patch.object(pathlib.Path,'read_text',return_value='rcon.port=25575\nrcon.password=fake'), patch.object(m.socket,'create_connection',return_value=MagicMock()), patch.object(m,'packet',side_effect=lambda s,i,t,msg:(i,'')) as packet:
 # The HTTP tests replace send_message; reload its original implementation.
 real=importlib.util.module_from_spec(spec);spec.loader.exec_module(real)
 with patch.object(real,'packet',packet):assert real.send_message('Teste',long_text) is True
 assert [call.args[3] for call in packet.call_args_list[1:]]==list(m.chat_commands('Teste',long_text))
 with patch.object(real,'packet',side_effect=[(1,''),(2,'No player was found')]) as empty:
  assert real.send_message('Teste',long_text) is False
  assert empty.call_count==2
 for reply in ((2,'Unknown command'),(2,'Invalid text component'),(99,'')):
  with patch.object(real,'packet',side_effect=[(1,''),reply]):
   try:real.send_message('Teste','hello')
   except ConnectionError:pass
   else:raise AssertionError('RCON errors must not be reported as success')
print('PASS: Unicode and multiline chat, long payloads, RCON chunking, origin check, flood ceilings, SSE (delivery mocked)')
