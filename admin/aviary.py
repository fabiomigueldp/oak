"""Typed Aviary administration over its private Unix socket."""
import json
import math
import re
import socket


def validate_aviary(params):
    if not isinstance(params, dict) or set(params) != {'action', 'port', 'revision', 'name', 'shared', 'departureYaw', 'arrivalYaw'}:
        raise ValueError('A complete aviport edit is required.')
    if params['action'] != 'edit' or not isinstance(params['port'], str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', params['port']):
        raise ValueError('Invalid aviport.')
    if type(params['revision']) is not int or params['revision'] < 0:
        raise ValueError('Invalid aviport revision.')
    if not isinstance(params['name'], str) or not params['name'].strip() or len(params['name']) > 48 or any(ord(c) < 32 for c in params['name']):
        raise ValueError('Use a name between 1 and 48 characters.')
    if type(params['shared']) is not bool:
        raise ValueError('Invalid access setting.')
    for key in ('departureYaw', 'arrivalYaw'):
        value = params[key]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not -180 <= value <= 180):
            raise ValueError('Use a heading between -180 and 180 degrees, or automatic.')
    return {**params, 'name': params['name'].strip()}


def aviary_call(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(12)
        sock.connect('/run/oak-telemetry/aviary.sock')
        sock.sendall(json.dumps(request).encode() + b'\n')
        with sock.makefile('rb') as stream:
            raw = stream.readline(131073)
        if len(raw) > 131072 or not raw.endswith(b'\n'):
            raise ConnectionError('Aviary response interrupted.')
        result = json.loads(raw)
        if result.get('error') and request.get('action') != 'status':
            raise ValueError(result['error'])
        return result
