"""Private Unix-socket transport for the trusted Oak operator kernel."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import stat
import struct
import threading

from .kernel import Kernel

MAX_REQUEST = 2 * 1024 * 1024


def error_response(exc):
    message = str(exc)[:2000]
    if isinstance(exc, PermissionError):
        code = 'permission_denied'
    elif isinstance(exc, (KeyError, FileNotFoundError)):
        code = 'not_found'
    elif isinstance(exc, ValueError):
        conflict = ('revision conflict', 'hash conflict', 'already used with a different', 'different content')
        code = 'conflict' if any(phrase in message.lower() for phrase in conflict) else 'invalid_input'
    else:
        code = 'unavailable'
    return {'ok': False, 'error': {'code': code, 'message': message}, 'type': type(exc).__name__}


def reject_constant(value):
    raise ValueError('Non-finite JSON values are invalid.')


def control_identity():
    import pwd
    account = pwd.getpwnam('oak-control')
    return account.pw_uid, account.pw_gid


def peer_identity(connection):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise PermissionError('Unix peer credentials are required.')
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))


def authorize_peer(connection, control_uid):
    pid, uid, gid = peer_identity(connection)
    if uid not in (0, control_uid):
        raise PermissionError('Only root and the oak-control service account may operate this socket.')
    return {'pid': pid, 'uid': uid, 'gid': gid}


def dispatch(kernel, request, peer):
    if not isinstance(request, dict) or not isinstance(request.get('method'), str):
        raise ValueError('A method and an object data payload are required.')
    data = request.get('data', {})
    if not isinstance(data, dict):
        raise ValueError('data must be an object.')
    claimed_actor = request.get('actor', 'operator')
    if not isinstance(claimed_actor, str) or not claimed_actor or len(claimed_actor.encode()) > 220 or '\0' in claimed_actor:
        raise ValueError('actor must be nonempty text within 220 bytes.')
    # An actor label describes an authenticated session; it never grants authority.
    actor = f"uid:{peer['uid']}/{claimed_actor}"
    return kernel.call(request['method'], data, actor=actor)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            peer = authorize_peer(self.connection, self.server.control_uid)
            line = self.rfile.readline(MAX_REQUEST + 1)
            if not line or len(line) > MAX_REQUEST or not line.endswith(b'\n'):
                raise ValueError('A newline-terminated request within 2 MiB is required.')
            request = json.loads(line, parse_constant=reject_constant)
            result = dispatch(self.server.kernel, request, peer)
            response = {'ok': True, 'result': result}
        except Exception as exc:
            response = error_response(exc)
        try:
            self.wfile.write((json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n').encode())
        except (OSError, ValueError):
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer if hasattr(socketserver, 'UnixStreamServer') else socketserver.TCPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, path, kernel, control_uid):
        self.kernel = kernel
        self.control_uid = control_uid
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(str(path), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def prepare_socket(path, gid):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    path.parent.chmod(0o750)
    os.chown(path.parent, 0, gid)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not stat.S_ISSOCK(path.lstat().st_mode):
            raise RuntimeError('Refusing to replace a non-socket operator path.')
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink(missing_ok=True)
        else:
            raise RuntimeError('An operator daemon is already listening.')
        finally:
            probe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/oak-operator'))
    parser.add_argument('--socket', type=Path, default=Path('/run/oak-operator/operator.sock'))
    parser.add_argument('--root', type=Path, default=Path('/srv/oak'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    if os.name != 'posix' or os.geteuid() != 0:
        parser.error('The production Unix daemon must run as root on Linux.')
    os.umask(0o077)
    control_uid, control_gid = control_identity()
    prepare_socket(args.socket, control_gid)
    runtime = None
    if not args.demo:
        from admin.runtime import Runtime
        runtime = Runtime(args.root)
    kernel = Kernel(args.state, runtime=runtime, demo=args.demo, max_workers=args.workers)
    try:
        kernel.start()
        with Server(args.socket, kernel, control_uid) as server:
            os.chown(args.socket, 0, control_gid)
            args.socket.chmod(0o660)
            def stop(signum, frame):
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            server.serve_forever(poll_interval=.2)
    finally:
        kernel.close()
        args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
