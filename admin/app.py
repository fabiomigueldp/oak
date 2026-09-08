"""Authenticated administration API and static application entry point."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import re
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentClient
from .auth import Auth
from .domain import OPERATIONS, ROLES, clean_text, place, validate
from .settings import Settings
from .store import Conflict, Store, digest, encode
from .worker import Worker
from .telemetry import LivePositions

STATIC = Path(__file__).resolve().parents[1] / 'public' / 'admin'
API = '/admin/api'


def create_app(settings=None, agent=None, *, background=True):
    settings = settings or Settings.from_env()
    store = Store(settings.state / 'control.sqlite3')
    if agent is None:
        if settings.demo:
            from .demo import DemoAgent
            agent = DemoAgent()
        else:
            agent = AgentClient(settings.socket)
    auth = Auth(store, settings)
    worker = Worker(store, agent, settings.poll_seconds)
    login_attempts = deque(maxlen=200)
    streams = asyncio.Semaphore(12)
    position_streams = asyncio.Semaphore(12)
    positions = LivePositions()

    @asynccontextmanager
    async def lifespan(app):
        if background:
            worker.start()
        yield
        if background:
            worker.close()

    app = FastAPI(title='Oak Control', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.agent, app.state.settings = store, agent, settings

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.url.path.startswith(API):
            if request.method not in ('GET', 'HEAD', 'POST', 'DELETE', 'PATCH'):
                return JSONResponse({'error': 'Método não permitido.'}, status_code=405)
            if request.method not in ('GET', 'HEAD'):
                if request.headers.get('origin') != settings.origin:
                    return JSONResponse({'error': 'Origem da solicitação inválida.'}, status_code=403)
                if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                    return JSONResponse({'error': 'Envie dados no formato JSON.'}, status_code=415)
                try:
                    length = int(request.headers.get('content-length', '0'))
                except ValueError:
                    return JSONResponse({'error': 'Tamanho inválido.'}, status_code=400)
                if length < 0 or length > 65536:
                    return JSONResponse({'error': 'Solicitação muito grande.'}, status_code=413)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        return JSONResponse({'error': 'Solicitação muito grande.'}, status_code=413)
                request._body = bytes(body)
                if request.url.path.startswith(API + '/auth/'):
                    now = time.monotonic()
                    while login_attempts and login_attempts[0] < now - 60:
                        login_attempts.popleft()
                    if len(login_attempts) >= 30:
                        return JSONResponse({'error': 'Muitas tentativas. Aguarde um minuto.'}, status_code=429, headers={'Retry-After': '60'})
                    login_attempts.append(now)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        demo_frame = settings.demo and request.url.path == '/admin/assets/demo-map.html'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN' if demo_frame else 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        if demo_frame:
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'error': 'Revise os dados e tente novamente.', 'detail': str(exc)}, status_code=409 if isinstance(exc, Conflict) else 400)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        if 'Sign in again' in str(exc):
            return JSONResponse({'error': 'Por segurança, saia e entre novamente antes de confirmar esta ação.'}, status_code=403)
        return JSONResponse({'error': 'Esta ação não está disponível para sua sessão.', 'detail': str(exc)}, status_code=403)

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse({'error': 'O agente não conseguiu concluir a solicitação.', 'detail': str(exc)}, status_code=503)

    @app.exception_handler(OSError)
    async def disconnected(request, exc):
        return JSONResponse({'error': 'O agente está indisponível. Confira o serviço e tente novamente.'}, status_code=503)

    def current(request, level=0, *, recent=False):
        session = store.current_session(request.cookies.get(settings.cookie))
        if not session:
            raise HTTPException(401, 'Entre para continuar.')
        if ROLES[session['role']] < level:
            raise PermissionError('Insufficient account permissions.')
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-oak-csrf', ''), session['csrf']):
            raise PermissionError('Invalid CSRF token.')
        if recent and time.time() - session['verified'] > 600:
            raise PermissionError('Sign in again before this sensitive action.')
        return session

    async def body(request):
        try:
            data = await request.json()
        except Exception as exc:
            raise ValueError('Invalid JSON.') from exc
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object.')
        return data

    def logged_in(uid):
        token, csrf = store.session(uid, settings.session_seconds)
        user = store.one('SELECT id,name,role FROM users WHERE id=?', (uid,))
        response = JSONResponse({'user': user, 'csrf': csrf, 'demo': settings.demo})
        response.set_cookie(settings.cookie, token, max_age=settings.session_seconds, path='/admin', secure=settings.secure, httponly=True, samesite='strict')
        return response

    def visible_jobs(user):
        return [job for job in store.jobs() if job['kind'] != 'console' or user['role'] == 'owner']

    def overview(user):
        snapshot = store.get('snapshot')
        if snapshot:
            snapshot['fresh'] = bool(snapshot.get('fresh') and time.time() - snapshot.get('sampled_at', 0) < 30)
        return {'snapshot': snapshot, 'collector_error': store.get('collector_error'), 'jobs': visible_jobs(user)[:12], 'events': store.events(limit=30), 'backups': store.get('backups', []), 'capabilities': {k: v for k, v in OPERATIONS.items() if ROLES[user['role']] >= v['role']}, 'demo': settings.demo}

    @app.get(API + '/health')
    def health():
        return {'service': 'oak-control', 'ready': True}

    @app.get(API + '/session')
    def session(request: Request):
        user = store.current_session(request.cookies.get(settings.cookie))
        if not user:
            return {'user': None, 'demo': settings.demo, 'passkeys': True}
        return {'user': {'id': user['user_id'], 'name': user['name'], 'role': user['role']}, 'csrf': user['csrf'], 'demo': settings.demo, 'expires': user['expires']}

    @app.post(API + '/auth/options')
    def login_options():
        return auth.login_options()

    @app.post(API + '/auth/verify')
    async def login_verify(request: Request):
        data = await body(request)
        try:
            uid = auth.login_verify(data.get('challenge_id', ''), data.get('credential'))
        except Exception as exc:
            raise PermissionError('Passkey verification failed. Start again.') from exc
        return logged_in(uid)

    @app.post(API + '/auth/enroll/options')
    async def enroll_options(request: Request):
        data = await body(request)
        if data.get('token'):
            return auth.register_options(token=clean_text(data['token'], 100, 20))
        user = current(request, recent=True)
        return auth.register_options(user=user)

    @app.post(API + '/auth/enroll/verify')
    async def enroll_verify(request: Request):
        data = await body(request)
        try:
            uid = auth.register_verify(data.get('challenge_id', ''), data.get('credential'), clean_text(data.get('name', 'Minha chave de acesso'), 80, 1))
        except Exception as exc:
            raise PermissionError('Enrollment verification failed. Start again.') from exc
        return logged_in(uid)

    @app.post(API + '/auth/demo')
    def demo_login():
        if not settings.demo:
            raise HTTPException(404)
        uid = '00000000-0000-4000-8000-000000000001'
        with store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO users(id,name,role,created) VALUES(?,?,?,?)', (uid, 'Você', 'owner', time.time()))
        # Usable immediately even before the first collector tick.
        store.set('snapshot', agent.call('snapshot'))
        store.set('backups', agent.call('backups'))
        store.set('configuration', agent.call('configuration'))
        return logged_in(uid)

    @app.post(API + '/logout')
    def logout(request: Request):
        user = current(request)
        with store.transaction() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (user['token'],))
        response = JSONResponse({'ok': True})
        response.delete_cookie(settings.cookie, path='/admin', secure=settings.secure, httponly=True, samesite='strict')
        return response

    @app.get(API + '/overview')
    def get_overview(request: Request):
        return overview(current(request))

    @app.get(API + '/stream')
    async def stream(request: Request):
        user = current(request)
        if streams.locked():
            raise HTTPException(503, 'Muitas conexões abertas.')
        await streams.acquire()
        token = request.cookies.get(settings.cookie)
        async def events():
            try:
                last = ''
                while not await request.is_disconnected():
                    active = store.current_session(token)
                    if not active:
                        yield 'event: session-ended\ndata: {}\n\n'
                        break
                    data = encode(overview(active))
                    if data != last:
                        yield 'data: ' + data + '\n\n'
                        last = data
                    else:
                        yield ': heartbeat\n\n'
                    await asyncio.sleep(3)
            finally:
                streams.release()
        return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.get(API + '/positions/stream')
    async def position_stream(request: Request):
        current(request)
        if position_streams.locked():
            raise HTTPException(503, 'Muitas conexões abertas.')
        await position_streams.acquire()
        token = request.cookies.get(settings.cookie)
        async def events():
            positions.subscribe()
            try:
                last, checked, heartbeat = None, 0, 0
                while not await request.is_disconnected():
                    now = time.monotonic()
                    if now - checked >= 1:
                        if not store.current_session(token):
                            yield 'event: session-ended\ndata: {}\n\n'
                            break
                        checked = now
                    frame = positions.frame
                    if frame and time.time() - frame['sampled_at'] >= 3:
                        frame = None
                    if frame is not last or now - heartbeat >= 1:
                        yield 'data: ' + encode(frame or {'fresh': False, 'players': []}) + '\n\n'
                        last, heartbeat = frame, now
                    await asyncio.sleep(.05)
            finally:
                await positions.unsubscribe()
                position_streams.release()
        return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.get(API + '/events')
    def history(request: Request, after: int = 0, limit: int = 100):
        current(request)
        return {'events': store.events(max(0, after), max(1, limit))}

    @app.get(API + '/samples')
    def samples(request: Request, hours: int = 1):
        current(request)
        rows = store.rows('SELECT created,data FROM samples WHERE created>? ORDER BY id DESC LIMIT 720', (time.time() - min(24, max(1, hours)) * 3600,))
        return {'samples': [{'at': row['created'], **json.loads(row['data'])} for row in reversed(rows)]}

    @app.get(API + '/players')
    def players(request: Request):
        current(request)
        return {'players': (store.get('snapshot') or {}).get('players', []), 'sessions': store.rows('SELECT * FROM player_sessions ORDER BY id DESC LIMIT 200')}

    @app.get(API + '/jobs')
    def jobs(request: Request):
        return {'jobs': visible_jobs(current(request))}

    @app.get(API + '/jobs/{jid}')
    def job(request: Request, jid: str):
        user = current(request)
        result = store.job(jid)
        if not result or result['kind'] == 'console' and user['role'] != 'owner':
            raise HTTPException(404)
        return result

    @app.post(API + '/reviews')
    async def review(request: Request):
        user = current(request, recent=True)
        data = await body(request)
        kind = data.get('kind')
        params = validate(kind, data.get('params', {}), user['role'])
        preview = await asyncio.to_thread(agent.call, 'preview', {'kind': kind, 'params': params})
        identifier = secrets.token_urlsafe(24)
        with store.transaction() as db:
            db.execute('DELETE FROM reviews WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,0)', (identifier, user['user_id'], kind, encode(params), encode(preview), time.time() + 300))
        return {'id': identifier, **preview, 'expires_in': 300}

    @app.post(API + '/jobs', status_code=202)
    async def submit(request: Request):
        user = current(request)
        data = await body(request)
        kind = data.get('kind')
        params = validate(kind, data.get('params', {}), user['role'])
        key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', key):
            raise ValueError('A unique idempotency key is required.')
        if OPERATIONS[kind]['review'] and not data.get('review'):
            raise ValueError('Review this action before running it.')
        if OPERATIONS[kind]['review']:
            current(request, recent=True)
        return store.create_job(user['user_id'], kind, OPERATIONS[kind]['label'], params, key, data.get('review'))

    @app.post(API + '/jobs/{jid}/cancel')
    def cancel(request: Request, jid: str):
        current(request, 2)
        store.cancel(jid)
        return {'ok': True}

    @app.post(API + '/jobs/{jid}/reconcile')
    async def reconcile(request: Request, jid: str):
        user = current(request, 2)
        row = store.job(jid)
        if row and row['kind'] == 'console' and user['role'] != 'owner':
            raise HTTPException(404)
        if not row or row['state'] != 'interrupted':
            raise ValueError('Only interrupted jobs require reconciliation.')
        receipt = await asyncio.to_thread(agent.call, 'receipt', {'job': jid})
        if receipt and receipt['state'] in ('completed', 'failed'):
            store.finish(jid, receipt['state'], result=receipt.get('result'), error=receipt.get('error'))
        return {'job': store.job(jid), 'receipt_state': receipt['state'] if receipt else 'not_found'}

    @app.get(API + '/backups')
    async def backups(request: Request):
        current(request)
        points = await asyncio.to_thread(agent.call, 'backups')
        store.set('backups', points)
        return {'backups': points, 'policy': {'location': 'Oracle VM', 'external_copy': False, 'max_points': 14, 'budget_bytes': 20 * 1024 ** 3, 'free_reserve_bytes': 20 * 1024 ** 3, 'legacy_managed_separately': True}}

    @app.get(API + '/configuration')
    async def configuration(request: Request):
        current(request, 2)
        result = await asyncio.to_thread(agent.call, 'configuration')
        store.set('configuration', result)
        return result

    @app.get(API + '/logs/{service}')
    async def logs(request: Request, service: str):
        current(request, 2)
        return await asyncio.to_thread(agent.call, 'logs', {'service': service})

    @app.get(API + '/places')
    def get_places(request: Request):
        current(request)
        return {'places': store.rows('SELECT * FROM places ORDER BY name')}

    @app.post(API + '/places', status_code=201)
    async def save_place(request: Request):
        user = current(request, 1)
        data = place(await body(request))
        uid = str(uuid.uuid4())
        with store.transaction() as db:
            if db.execute('SELECT count(*) FROM places').fetchone()[0] >= 500:
                raise ValueError('The place limit has been reached.')
            db.execute('INSERT INTO places VALUES(?,?,?,?,?,?,?,?,?)', (uid, data['name'], data['dimension'], data['x'], data['y'], data['z'], data['note'], time.time(), user['user_id']))
            store.event('place', 'Place added', user['user_id'], {'place_id': uid, 'name': data['name']}, db)
        return {'id': uid, **data}

    @app.delete(API + '/places/{pid}')
    def remove_place(request: Request, pid: str):
        user = current(request, 1)
        with store.transaction() as db:
            db.execute('DELETE FROM places WHERE id=?', (pid,))
            store.event('place', 'Place removed', user['user_id'], {'place_id': pid}, db)
        return {'ok': True}

    @app.get(API + '/schedules')
    def schedules(request: Request):
        current(request, 2)
        rows = store.rows('SELECT * FROM schedules ORDER BY next_run')
        for row in rows:
            row['params'] = json.loads(row['params'])
        return {'schedules': rows, 'existing_backup_schedule': '05:00 America/Sao_Paulo (systemd; managed separately)'}

    @app.post(API + '/schedules', status_code=201)
    async def schedule(request: Request):
        user = current(request, 2)
        data = await body(request)
        kind = data.get('kind')
        params = validate(kind, data.get('params', {}), user['role'])
        if not OPERATIONS[kind]['schedule']:
            raise ValueError('This action cannot run unattended.')
        minutes = data.get('interval_minutes')
        if type(minutes) is not int or not 15 <= minutes <= 10080:
            raise ValueError('Choose an interval between 15 minutes and 7 days.')
        label = clean_text(data.get('label', OPERATIONS[kind]['label']), 80, 1)
        sid = str(uuid.uuid4())
        with store.transaction() as db:
            if db.execute('SELECT count(*) FROM schedules').fetchone()[0] >= 20:
                raise ValueError('The schedule limit has been reached.')
            db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?)', (sid, label, kind, encode(params), minutes, time.time() + minutes * 60, 1, user['user_id']))
            store.event('schedule', 'Schedule created', user['user_id'], {'schedule_id': sid, 'label': label}, db)
        return {'id': sid}

    @app.patch(API + '/schedules/{sid}')
    async def toggle_schedule(request: Request, sid: str):
        user = current(request, 2)
        data = await body(request)
        if type(data.get('enabled')) is not bool:
            raise ValueError('Enabled must be a boolean.')
        with store.transaction() as db:
            db.execute('UPDATE schedules SET enabled=?,next_run=? + interval_minutes*60 WHERE id=?', (int(data['enabled']), time.time(), sid))
            store.event('schedule', 'Schedule changed', user['user_id'], {'schedule_id': sid, 'enabled': data['enabled']}, db)
        return {'ok': True}

    @app.delete(API + '/schedules/{sid}')
    def delete_schedule(request: Request, sid: str):
        user = current(request, 2)
        with store.transaction() as db:
            db.execute('DELETE FROM schedules WHERE id=?', (sid,))
            store.event('schedule', 'Schedule removed', user['user_id'], {'schedule_id': sid}, db)
        return {'ok': True}

    @app.get(API + '/access')
    def access(request: Request):
        user = current(request)
        result = {'credentials': store.rows('SELECT id,name,created FROM credentials WHERE user_id=?', (user['user_id'],))}
        if user['role'] == 'owner':
            result['users'] = store.rows('SELECT u.id,u.name,u.role,u.created,u.disabled,(SELECT count(*) FROM credentials c WHERE c.user_id=u.id) AS credential_count FROM users u ORDER BY u.created')
        return result

    @app.post(API + '/access/invite')
    async def invite(request: Request):
        current(request, 3, recent=True)
        data = await body(request)
        role = data.get('role', 'observer')
        if role not in ROLES:
            raise ValueError('Unknown role.')
        name = clean_text(data.get('name'), 60, 1)
        token = store.invite(name, role)
        return {'url': settings.origin + '/admin/#enroll=' + token, 'expires_in': 900}

    @app.patch(API + '/access/users/{uid}')
    async def update_user(request: Request, uid: str):
        actor = current(request, 3, recent=True)
        if uid == actor['user_id']:
            raise ValueError('Use a second owner to change your own access.')
        data = await body(request)
        role, disabled = data.get('role'), data.get('disabled', False)
        if role not in ROLES or type(disabled) is not bool:
            raise ValueError('Invalid account settings.')
        with store.transaction() as db:
            db.execute('UPDATE users SET role=?,disabled=? WHERE id=?', (role, int(disabled), uid))
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            db.execute('DELETE FROM invites WHERE user_id=?', (uid,))
            db.execute('UPDATE schedules SET enabled=0 WHERE actor=?', (uid,))
            db.execute("UPDATE jobs SET state='cancelled' WHERE actor=? AND state='queued'", (uid,))
            store.event('access', 'Account permissions changed', actor['user_id'], {'user_id': uid, 'role': role, 'disabled': disabled}, db)
        return {'ok': True}

    @app.delete(API + '/access/credentials/{cid}')
    def delete_credential(request: Request, cid: str):
        user = current(request, recent=True)
        with store.transaction() as db:
            if db.execute('SELECT count(*) FROM credentials WHERE user_id=?', (user['user_id'],)).fetchone()[0] <= 1:
                raise ValueError('Keep at least one passkey on your account.')
            db.execute('DELETE FROM credentials WHERE id=? AND user_id=?', (cid, user['user_id']))
            store.event('access', 'Passkey removed', user['user_id'], db=db)
        return {'ok': True}

    @app.get('/admin/')
    def index():
        return FileResponse(STATIC / 'index.html')

    app.mount('/admin/assets', StaticFiles(directory=STATIC, check_dir=False), name='admin-assets')
    return app
