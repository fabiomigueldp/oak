"""Bounded, read-only Java status query shared with the external crossplay bridge."""
import json
import socket
import struct

MAX_PACKET = 1024 * 1024


def varint(value):
    value &= 0xffffffff
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise EOFError('Truncated status response')
    return data


def read_varint(stream):
    value = 0
    for index in range(5):
        byte = read_exact(stream, 1)[0]
        if index == 4 and byte > 15:
            raise ValueError('Invalid status VarInt')
        value |= (byte & 127) << (7 * index)
        if not byte & 128:
            return value
    raise ValueError('Invalid status VarInt')


def query(host='127.0.0.1', port=25565):
    import io
    address = host.encode('utf-8')
    handshake = b'\x00' + varint(-1) + varint(len(address)) + address + struct.pack('>H', port) + b'\x01'
    with socket.create_connection((host, port), timeout=3) as sock:
        sock.sendall(varint(len(handshake)) + handshake + b'\x01\x00')
        with sock.makefile('rb') as stream:
            length = read_varint(stream)
            if not 0 < length <= MAX_PACKET:
                raise ValueError('Invalid status packet length')
            packet = io.BytesIO(read_exact(stream, length))
    if read_varint(packet) != 0:
        raise ValueError('Unexpected status packet')
    length = read_varint(packet)
    if not 0 < length <= MAX_PACKET:
        raise ValueError('Invalid status JSON length')
    status = json.loads(read_exact(packet, length))
    if packet.read(1) or not isinstance(status, dict):
        raise ValueError('Invalid status response')
    players = status.get('players', {})
    for key in ('online', 'max'):
        if type(players.get(key)) is not int or players[key] < 0:
            raise ValueError('Invalid player count')
    if not isinstance(status.get('version'), dict):
        raise ValueError('Missing server version')
    return status


def compatibility_status():
    status = query()
    # Only the loopback bridge speaks 26.2; preserve the backend MOTD and counts.
    status['version'] = {'name': '26.2', 'protocol': 776}
    return status


def query_bedrock(host='127.0.0.1', port=19132):
    import time
    magic = bytes.fromhex('00ffff00fefefefefdfdfdfd12345678')
    stamp = struct.pack('>q', time.time_ns() // 1000000)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.connect((host, port))
        sock.send(b'\x01' + stamp + magic + struct.pack('>q', 1))
        data = sock.recv(4096)
    if len(data) < 35 or data[0] != 0x1c or data[1:9] != stamp or data[17:33] != magic:
        raise ValueError('Invalid Bedrock pong')
    length = struct.unpack('>H', data[33:35])[0]
    if length != len(data) - 35:
        raise ValueError('Truncated Bedrock status')
    fields = data[35:].decode('utf-8').split(';')
    if len(fields) < 9 or fields[0] != 'MCPE':
        raise ValueError('Invalid Bedrock status')
    return {'name': fields[1], 'version': fields[3], 'online': int(fields[4]),
            'max': int(fields[5]), 'description': fields[7], 'gamemode': fields[8]}


if __name__ == '__main__':
    import sys
    status = query(*(sys.argv[1:2]), **({'port': int(sys.argv[2])} if len(sys.argv) > 2 else {}))
    print(json.dumps({key: value for key, value in status.items() if key not in ('favicon', 'players')}
                     | {'players': {key: status['players'][key] for key in ('online', 'max')},
                        'has_icon': 'favicon' in status}, ensure_ascii=False))
