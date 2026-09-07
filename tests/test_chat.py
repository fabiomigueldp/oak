import importlib.util,pathlib,tempfile,threading,http.client,json
spec=importlib.util.spec_from_file_location('oakchat',pathlib.Path(__file__).resolve().parents[1]/'server/chat-server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as tmp:
 m.ROOT=pathlib.Path(tmp);(m.ROOT/'web').mkdir();(m.ROOT/'web/status.json').write_text('{"online":true}')
 delivered=[];m.send_message=lambda n,t:delivered.append((n,t));m.snapshot='{"test":true}'
 server=m.http.server.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 def request(data,origin=m.ORIGIN):
  c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('POST','/api/chat',json.dumps(data),{'Content-Type':'application/json','Origin':origin});r=c.getresponse();status=r.status;r.read();c.close();return status
 assert request({'name':'Teste','message':'ok'},'https://example.org')==403
 assert request({'name':'Teste','message':'oi\nop Teste'})==400
 assert request({'name':'Teste','message':'Texto "literal" /op @a <script>'})==200
 assert delivered==[('Teste','Texto "literal" /op @a <script>')]
 assert request({'name':'Teste','message':'spam'})==429
 c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('GET','/api/events');r=c.getresponse();assert r.status==200;assert r.getheader('Content-Type')=='text/event-stream';assert r.readline()==b'data: {"test":true}\n';c.close()
 server.shutdown()
print('PASS: origin check, newline rejection, literal payload, rate limit, SSE stream (delivery mocked)')
