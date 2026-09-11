"""Permission-limited Unix socket agent with durable execution receipts.

This service is private to the administration process. It never listens on TCP,
accepts shell commands, or accepts caller-provided filesystem paths.
"""
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import signal
import threading
import time

from .domain import validate, operation_resources
from .runtime import Runtime, atomic


class OperationCancelled(RuntimeError):
    pass


class AgentClient:
    def __init__(self, path):
        self.path = path

    def call(self, method, data=None, progress=None):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1200 if method == 'execute' else 120)
            sock.connect(self.path)
            with sock.makefile('rwb') as file:
                file.write(json.dumps({'method': method, 'data': data or {}}).encode() + b'\n')
                file.flush()
                while True:
                    line = file.readline(2 * 1024 * 1024)
                    if not line or not line.endswith(b'\n'):
                        raise ConnectionError('Agent connection interrupted. Execution may still be running.')
                    message = json.loads(line)
                    if message.get('type') == 'progress':
                        if progress:
                            progress(message['step'], message.get('detail', ''))
                        continue
                    if message.get('type') == 'error':
                        raise RuntimeError(message['error'])
                    return message['result']


class AgentServer(getattr(socketserver, 'ThreadingUnixStreamServer', object)):
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, path, runtime):
        if not hasattr(socketserver, 'ThreadingUnixStreamServer'):
            raise RuntimeError('The live host agent requires Linux. Use the local simulator on Windows.')
        self.runtime = runtime
        self.receipts = runtime.control / 'receipts'
        self.receipts.mkdir(mode=0o700, exist_ok=True)
        self.execution_locks = {name: threading.Lock() for name in ('world', 'repository')}
        self.receipt_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(path, AgentHandler)


class AgentHandler(socketserver.StreamRequestHandler):
    def send(self, payload):
        try:
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode() + b'\n')
            self.wfile.flush()
        except OSError:
            # A browser/API disconnect must not abort cleanup or world operations.
            pass

    def handle(self):
        if not self.server.slots.acquire(blocking=False):
            self.send({'type': 'error', 'error': 'Agent is busy.'})
            return
        try:
            self.connection.settimeout(10)
            raw = self.rfile.readline(65537)
            if len(raw) > 65536 or not raw.endswith(b'\n'):
                raise ValueError('Invalid agent request.')
            request = json.loads(raw)
            method, data = request.get('method'), request.get('data', {})
            if not isinstance(data, dict):
                raise ValueError('Expected object parameters.')
            runtime = self.server.runtime
            if method == 'snapshot':
                result = runtime.snapshot()
            elif method == 'environment':
                result = runtime.environment()
            elif method == 'aviary':
                result = runtime.aviary(data.get('port'))
            elif method == 'backups':
                result = runtime.backups()
            elif method == 'backup_status':
                result = runtime.backup_status()
            elif method == 'configuration':
                result = runtime.configuration()
            elif method == 'logs':
                result = runtime.logs(data.get('service'))
            elif method == 'preview':
                result = runtime.preview(data.get('kind'), data.get('params'))
            elif method == 'receipt':
                jid = data.get('job', '')
                if not re.fullmatch(r'[a-f0-9-]{36}', jid):
                    raise ValueError('Invalid receipt.')
                path = self.server.receipts / (jid + '.json')
                result = json.loads(path.read_text()) if path.exists() else None
            elif method == 'cancel':
                jid = data.get('job', '')
                if not re.fullmatch(r'[a-f0-9-]{36}', jid):
                    raise ValueError('Invalid operation identifier.')
                atomic(self.server.receipts / (jid + '.cancel'), 'requested')
                result = {'requested': True}
            elif method == 'execute':
                result = self.execute(data)
            else:
                raise ValueError('Unsupported agent capability.')
            self.send({'type': 'result', 'result': result})
        except Exception as exc:
            safe = str(exc) if isinstance(exc, (ValueError, PermissionError, RuntimeError, ConnectionError)) else 'Host operation failed. Consult the restricted service journal.'
            self.send({'type': 'error', 'error': safe[:1000]})
        finally:
            self.server.slots.release()

    def execute(self, data):
        jid, kind = data.get('job', ''), data.get('kind')
        if not re.fullmatch(r'[a-f0-9-]{36}', jid):
            raise ValueError('Invalid operation identifier.')
        params = validate(kind, data.get('params'))
        path = self.server.receipts / (jid + '.json')
        acquired = []
        for resource in sorted(operation_resources(kind)):
            lock = self.server.execution_locks[resource]
            if not lock.acquire(blocking=False):
                for held in reversed(acquired):
                    held.release()
                raise RuntimeError('O recurso está ocupado. A operação pode ser tentada novamente.')
            acquired.append(lock)
        try:
            with self.server.receipt_lock:
                if path.exists():
                    receipt = json.loads(path.read_text())
                    if receipt['kind'] != kind or receipt['params'] != params:
                        raise ValueError('Operation identifier already used for different input.')
                    if receipt['state'] in ('completed', 'cancelled'):
                        return receipt['result']
                    raise RuntimeError('This operation already reached the host. Inspect its receipt; it will not be replayed.')
                receipt = {'job': jid, 'kind': kind, 'params': params, 'state': 'running', 'started': time.time()}
                atomic(path, json.dumps(receipt))
            def progress(step, detail=''):
                if (self.server.receipts / (jid + '.cancel')).exists():
                    raise OperationCancelled('Operação cancelada no próximo ponto seguro.')
                receipt.update(step=step, updated=time.time())
                atomic(path, json.dumps(receipt))
                self.send({'type': 'progress', 'step': step, 'detail': detail})
            try:
                result = self.server.runtime.execute(jid, kind, params, progress)
                receipt.update(state='completed', result=result, finished=time.time())
                atomic(path, json.dumps(receipt))
                return result
            except OperationCancelled:
                result = {'cancelled': True}
                receipt.update(state='cancelled', result=result, finished=time.time())
                atomic(path, json.dumps(receipt))
                return result
            except Exception as exc:
                receipt.update(state='failed', error=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__, finished=time.time())
                atomic(path, json.dumps(receipt))
                raise
        finally:
            for held in reversed(acquired):
                held.release()


def main():
    import grp
    path = Path(os.environ.get('OAK_ADMIN_SOCKET', '/run/oak-control/agent.sock'))
    if path.exists():
        if not path.is_socket():
            raise RuntimeError('Refusing to replace a non-socket path.')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                pass
            else:
                raise RuntimeError('A host agent already owns this socket.')
        path.unlink()
    path.parent.mkdir(mode=0o750, exist_ok=True)
    runtime = Runtime()
    runtime.recover_saving()
    with AgentServer(str(path), runtime) as server:
        os.chown(path, 0, grp.getgrnam('oak-control').gr_gid)
        path.chmod(0o660)
        # Stop accepting work, then let in-flight handlers finish their cleanup.
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        server.serve_forever()


if __name__ == '__main__':
    main()
