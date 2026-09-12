"""Owner API for the private operator daemon; browser credentials never reach jobs."""
import asyncio
import os
import threading
from fastapi import Request, HTTPException


class DemoOperator:
    """Open simulator state only when the demo operator workspace is used."""
    def __init__(self, state):
        self.state, self.kernel = state, None
        self.lock = threading.RLock()

    def start(self):
        return self

    def call(self, method, data=None, actor='demo'):
        with self.lock:
            if self.kernel is None:
                from oak_operator.kernel import Kernel
                self.kernel = Kernel(self.state, demo=True)
                self.kernel.start()
            return self.kernel.call(method, data or {}, actor=actor)

    def close(self):
        with self.lock:
            if self.kernel:
                self.kernel.close()
                self.kernel = None


def register_operator(app, settings, current, body, client=None):
    if client is None:
        if settings.demo:
            client = DemoOperator(settings.state / 'operator-demo')
        else:
            from oak_operator.client import Client
            client = Client(socket_path=os.environ.get('OAK_OPERATOR_SOCKET', '/run/oak-operator/operator.sock'))
    app.state.operator = client
    prefix = '/admin/api/operator'

    async def invoke(request, method, data=None):
        user = current(request, 3)
        # Socket membership authenticates this gateway. Actor is attribution only.
        from oak_operator.client import OperatorError
        try:
            return await asyncio.to_thread(client.call, method, data or {}, actor='portal:' + user['user_id'])
        except OperatorError as exc:
            status = {'invalid_input': 400, 'invalid_method': 400, 'invalid_data': 400,
                      'not_found': 404, 'conflict': 409, 'permission_denied': 403}.get(exc.code, 503)
            raise HTTPException(status, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get(prefix + '/discover')
    async def discover(request: Request):
        return await invoke(request, 'discover')

    @app.get(prefix + '/jobs')
    async def jobs(request: Request):
        return await invoke(request, 'jobs.list')

    @app.post(prefix + '/jobs', status_code=202)
    async def submit(request: Request):
        data = await body(request)
        if request.headers.get('idempotency-key'):
            data['idempotency'] = request.headers['idempotency-key']
        return await invoke(request, 'jobs.submit', data)

    @app.get(prefix + '/jobs/{identifier}')
    async def job(request: Request, identifier: str, offset: int = 0):
        return await invoke(request, 'jobs.get', {'id': identifier, 'offset': offset})

    @app.post(prefix + '/jobs/{identifier}/cancel')
    async def cancel(request: Request, identifier: str):
        return await invoke(request, 'jobs.cancel', {'id': identifier})

    @app.get(prefix + '/routines')
    async def routines(request: Request):
        return await invoke(request, 'routines.list')

    @app.post(prefix + '/routines')
    async def create_routine(request: Request):
        return await invoke(request, 'routines.upsert', await body(request))

    @app.patch(prefix + '/routines/{identifier}')
    async def update_routine(request: Request, identifier: str):
        data = await body(request)
        data['id'] = identifier
        return await invoke(request, 'routines.upsert', data)

    @app.post(prefix + '/routines/{identifier}/run', status_code=202)
    async def run_routine(request: Request, identifier: str):
        return await invoke(request, 'routines.run', {'id': identifier})

    @app.delete(prefix + '/routines/{identifier}')
    async def delete_routine(request: Request, identifier: str):
        return await invoke(request, 'routines.delete', {**await body(request), 'id': identifier})

    @app.get(prefix + '/notebooks')
    async def notebooks(request: Request):
        return await invoke(request, 'notebooks.list')

    @app.get(prefix + '/notebooks/{identifier}')
    async def notebook(request: Request, identifier: str):
        return await invoke(request, 'notebooks.get', {'id': identifier})

    @app.post(prefix + '/notebooks')
    async def create_notebook(request: Request):
        return await invoke(request, 'notebooks.save', await body(request))

    @app.patch(prefix + '/notebooks/{identifier}')
    async def update_notebook(request: Request, identifier: str):
        data = await body(request)
        data['id'] = identifier
        return await invoke(request, 'notebooks.save', data)

    @app.delete(prefix + '/notebooks/{identifier}')
    async def delete_notebook(request: Request, identifier: str):
        return await invoke(request, 'notebooks.delete', {**await body(request), 'id': identifier})

    @app.get(prefix + '/events')
    async def events(request: Request, after: int = 0):
        return await invoke(request, 'events.list', {'after': after})

    @app.post(prefix + '/call')
    async def call(request: Request):
        data = await body(request)
        # The owner receives the complete daemon API; authentication remains here.
        return await invoke(request, data.get('method'), data.get('data', {}))

    return client
