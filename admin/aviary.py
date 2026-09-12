"""Typed Aviary administration over its private Unix socket."""
import json
import math
import re
import socket


COLORS = frozenset(('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray',
                    'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black'))
STYLES = frozenset(('oak', 'spruce', 'birch'))
PORT_FIELDS = frozenset(('action', 'port', 'revision', 'name', 'shared', 'departureYaw', 'arrivalYaw'))
PERCH_FIELDS = frozenset(('color', 'style', 'birdName', 'hub'))
POLICY_FIELDS = frozenset(('action', 'revision', 'fieldPickup', 'discoverPublic', 'maxOwnedPerches'))
TRAVEL_FIELDS = frozenset(('maxFlights', 'shortcutDistance'))


def _text(value, maximum, label):
    if (not isinstance(value, str) or not value.strip()
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            or len(value.encode('utf-16-le')) // 2 > maximum):
        raise ValueError(f'Use {label} between 1 and {maximum} characters.')
    return value.strip()


def validate_aviary(params):
    if not isinstance(params, dict):
        raise ValueError('A typed Aviary operation is required.')
    if type(params.get('revision')) is not int or not 0 <= params['revision'] <= 9007199254740991:
        raise ValueError('Invalid Aviary revision.')
    if params.get('action') == 'policy':
        if not POLICY_FIELDS <= params.keys() or params.keys() - POLICY_FIELDS - TRAVEL_FIELDS:
            raise ValueError('A complete network policy is required.')
        if any(type(params[key]) is not bool for key in ('fieldPickup', 'discoverPublic')):
            raise ValueError('Invalid network policy.')
        if type(params['maxOwnedPerches']) is not int or not 1 <= params['maxOwnedPerches'] <= 16:
            raise ValueError('Use a limit between 1 and 16 perches per player.')
        if 'maxFlights' in params and (type(params['maxFlights']) is not int or not 1 <= params['maxFlights'] <= 4):
            raise ValueError('Use a limit between 1 and 4 simultaneous flights.')
        if 'shortcutDistance' in params and (type(params['shortcutDistance']) not in (int, float)
                or not math.isfinite(params['shortcutDistance']) or not 100 <= params['shortcutDistance'] <= 500):
            raise ValueError('Use a transition distance between 100 and 500 blocks.')
        return dict(params)
    if not PORT_FIELDS <= params.keys() or params.keys() - PORT_FIELDS - PERCH_FIELDS:
        raise ValueError('A complete aviport edit is required.')
    if params['action'] != 'edit' or not isinstance(params['port'], str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', params['port']):
        raise ValueError('Invalid aviport.')
    result = {**params, 'name': _text(params['name'], 48, 'a name')}
    if type(params['shared']) is not bool:
        raise ValueError('Invalid access setting.')
    for key in ('departureYaw', 'arrivalYaw'):
        value = params[key]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not -180 <= value <= 180):
            raise ValueError('Use a heading between -180 and 180 degrees, or automatic.')
    for key, choices in (('color', COLORS), ('style', STYLES)):
        if key in params and (not isinstance(params[key], str) or params[key] not in choices):
            raise ValueError(f'Invalid perch {key}.')
    if 'birdName' in params:
        result['birdName'] = _text(params['birdName'], 32, 'a bird name')
    if 'hub' in params and type(params['hub']) is not bool:
        raise ValueError('Invalid public destination setting.')
    return result


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
