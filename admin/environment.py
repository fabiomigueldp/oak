"""Typed environment contract; administrative input never becomes command text."""
import json
import socket

DEFAULTS = {'cycle': 'native', 'day': 10, 'dusk': 1, 'night': 8, 'dawn': 1,
            'weather': 'native', 'clear_min': 30, 'clear_max': 60, 'rain_min': 3,
            'rain_max': 8, 'storm_chance': 15, 'storm_max': 3, 'storm_gap': 60, 'rules': {}}
RULES = {
    'players_sleeping_percentage': {'label': 'Jogadores necessários para dormir', 'min': 0, 'max': 100, 'unit': '%'},
    'keep_inventory': {'label': 'Manter inventário após morrer'},
    'pvp': {'label': 'Combate entre jogadores'},
    'spawn_monsters': {'label': 'Geração de monstros'},
    'spawn_phantoms': {'label': 'Geração de phantoms'},
    'spawn_patrols': {'label': 'Patrulhas de saqueadores'},
    'raids': {'label': 'Invasões de vilas'},
    'mob_griefing': {'label': 'Mobs podem modificar o mundo', 'description': 'Também afeta interações de mobs e o funcionamento de algumas fazendas.'},
    'random_tick_speed': {'label': 'Ticks aleatórios', 'min': 0, 'max': 12, 'description': 'Valores maiores podem aumentar a carga. Não acelera toda a simulação.'},
}

LABELS = {'cycle': 'Ciclo', 'day': 'Dia · min', 'dusk': 'Entardecer · min', 'night': 'Noite · min',
          'dawn': 'Amanhecer · min', 'weather': 'Clima', 'clear_min': 'Céu limpo · mínimo em min',
          'clear_max': 'Céu limpo · máximo em min', 'rain_min': 'Chuva · mínimo em min',
          'rain_max': 'Chuva · máximo em min', 'storm_chance': 'Chance de tempestade · %',
          'storm_max': 'Tempestade · limite em min', 'storm_gap': 'Intervalo entre tempestades · min'}


def environment_changes(current, params):
    def display(value):
        if type(value) is bool:
            return 'Ativado' if value else 'Desativado'
        return {'native': 'Minecraft', 'custom': 'Personalizado', 'paused': 'Pausado',
                'managed': 'Controlado pelo Oak', 'clear': 'Céu limpo', 'rain': 'Chuva',
                'thunder': 'Tempestade'}.get(str(value), str(value))
    if params['action'] == 'override':
        return [{'label': 'Clima', 'before': display(current['weather']), 'after': display(params['weather'])},
                {'label': 'Duração', 'before': '—', 'after': str(params['minutes']) + ' minutos corridos'}]
    if params['action'] == 'release':
        return []
    changes = [{'key': key, 'label': LABELS[key], 'before': display(current['policy'][key]), 'after': display(value)}
               for key, value in params['policy'].items() if key != 'rules' and current['policy'][key] != value]
    changes += [{'key': key, 'label': RULES[key]['label'], 'before': display(current['rules'][key]), 'after': display(value)}
                for key, value in params['policy']['rules'].items() if current['rules'][key] != value]
    return changes


def validate_environment(params):
    if set(params) - {'action', 'revision', 'policy', 'weather', 'minutes'}:
        raise ValueError('Unexpected environment parameters.')
    action = params.get('action')
    if action not in ('configure', 'override', 'release') or type(params.get('revision')) is not int or params['revision'] < 0:
        raise ValueError('Invalid environment action or revision.')
    result = {'action': action, 'revision': params['revision']}
    if action == 'configure':
        policy = params.get('policy')
        if not isinstance(policy, dict) or set(policy) != set(DEFAULTS):
            raise ValueError('A complete supported environment policy is required.')
        if policy['cycle'] not in ('native', 'custom', 'paused') or policy['weather'] not in ('native', 'managed'):
            raise ValueError('Invalid environment mode.')
        for key in ('day', 'dusk', 'night', 'dawn', 'clear_min', 'clear_max', 'rain_min', 'rain_max', 'storm_max', 'storm_gap'):
            if type(policy[key]) not in (int, float) or not .25 <= policy[key] <= 240:
                raise ValueError('Durations must be between 0.25 and 240 minutes.')
        if type(policy['storm_chance']) is not int or not 0 <= policy['storm_chance'] <= 100:
            raise ValueError('Invalid storm probability.')
        if policy['clear_min'] > policy['clear_max'] or policy['rain_min'] > policy['rain_max']:
            raise ValueError('Minimum duration exceeds maximum duration.')
        rules = policy['rules']
        if not isinstance(rules, dict) or rules.keys() - RULES.keys():
            raise ValueError('Unsupported world rule.')
        for key, value in rules.items():
            spec = RULES[key]
            if 'min' in spec:
                if type(value) is not int or not spec['min'] <= value <= spec['max']:
                    raise ValueError('World rule outside its supported range.')
            elif type(value) is not bool:
                raise ValueError('Boolean rule expected.')
        result['policy'] = policy
    elif action == 'override':
        if params.get('weather') not in ('clear', 'rain', 'thunder') or type(params.get('minutes')) is not int or not 1 <= params['minutes'] <= 120:
            raise ValueError('Choose weather and a duration between 1 and 120 minutes.')
        result.update(weather=params['weather'], minutes=params['minutes'])
    return result


def environment_call(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(12)
        sock.connect('/run/oak-telemetry/control.sock')
        sock.sendall(json.dumps(request).encode() + b'\n')
        with sock.makefile('rb') as file:
            raw = file.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise ConnectionError('Environment response interrupted.')
        result = json.loads(raw)
        if result.get('error') and request.get('action') != 'status':
            raise ValueError(result['error'])
        return result
