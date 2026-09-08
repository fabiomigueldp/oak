"""Run ONLY inside the systemd private-network restore drill sandbox."""
import json
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time


def command(text, password):
    def exact(sock, count):
        value = b''
        while len(value) < count:
            part = sock.recv(count - len(value))
            if not part:
                raise ConnectionError('Drill RCON closed.')
            value += part
        return value

    def packet(sock, ident, kind, body):
        raw = struct.pack('<ii', ident, kind) + body.encode() + b'\0\0'
        sock.sendall(struct.pack('<i', len(raw)) + raw)
        length, = struct.unpack('<i', exact(sock, 4))
        if not 10 <= length <= 1048576:
            raise ValueError('Invalid drill response.')
        response = exact(sock, length)
        rid, = struct.unpack('<i', response[:4])
        if rid != ident:
            raise ValueError('Drill command was not confirmed.')
        return response[8:-2].decode(errors='replace')

    with socket.create_connection(('127.0.0.1', 25575), timeout=3) as sock:
        packet(sock, 1, 3, password)
        return packet(sock, 2, 2, text)


def main(directory):
    root = Path(directory).resolve()
    if root.parent.name != 'drills' or not (root / 'world/level.dat').is_file():
        raise ValueError('Expected a separate restored drill directory.')
    # Fail closed when accidentally called outside the private network namespace.
    if Path('/proc/self/ns/net').readlink() == Path('/proc/1/ns/net').readlink():
        raise RuntimeError('A private network namespace is required.')
    config_path = root / 'server.properties'
    config = dict(line.split('=', 1) for line in config_path.read_text().splitlines() if '=' in line and not line.startswith('#'))
    password = secrets.token_urlsafe(32)
    config.update({'server-ip': '127.0.0.1', 'server-port': '25565', 'enable-rcon': 'true', 'rcon.port': '25575', 'rcon.password': password,
                   'management-server-enabled': 'false', 'enable-query': 'false', 'online-mode': 'false', 'white-list': 'true',
                   'view-distance': '2', 'simulation-distance': '2', 'max-players': '1', 'level-name': 'world'})
    config_path.write_text('\n'.join(k + '=' + v for k, v in config.items()) + '\n')
    started = time.monotonic()
    with (root / 'drill-output.log').open('wb') as output:
        process = subprocess.Popen(['/usr/bin/java', '-Xms256M', '-Xmx2G', '-jar', 'fabric-server-launch.jar', '--nogui'], cwd=root, stdout=output, stderr=subprocess.STDOUT)
        try:
            while time.monotonic() - started < 180:
                if process.poll() is not None:
                    raise RuntimeError('The recovered game exited before becoming ready.')
                try:
                    result = command('list', password)
                    if 'players online' in result:
                        try:
                            command('stop', password)
                        except (OSError, ValueError):
                            # Shutdown can close RCON before its response arrives.
                            pass
                        process.wait(timeout=30)
                        if process.returncode != 0:
                            raise RuntimeError('The recovered game did not stop cleanly.')
                        print(json.dumps({'boot_verified': True, 'seconds': round(time.monotonic() - started, 1)}))
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(2)
            raise RuntimeError('Recovery boot verification timed out.')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main(sys.argv[1])
