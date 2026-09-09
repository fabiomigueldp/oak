"""Shared validation at the HTTP and privileged agent boundaries."""
import math
import re

ROLES = {'owner': 3, 'administrator': 2, 'moderator': 1, 'observer': 0}


def operation_resources(kind):
    if kind in ('backup', 'restore_backup', 'maintenance', 'recover_restore'):
        return {'world', 'repository'}
    if kind.startswith('backup_') or kind == 'verify_backup':
        return {'repository'}
    return {'world'}


OPERATIONS = {
    'backup_policy': {'label': 'Configurar backups', 'role': 2, 'review': False, 'schedule': False},
    'backup_edit': {'label': 'Editar ponto', 'role': 2, 'review': False, 'schedule': False},
    'backup_delete': {'label': 'Excluir backup', 'role': 2, 'review': True, 'schedule': False},
    'backup_check': {'label': 'Verificar repositório', 'role': 2, 'review': False, 'schedule': False},
    'backup_compact': {'label': 'Liberar espaço', 'role': 2, 'review': False, 'schedule': False},
    'environment_apply': {'label': 'Ajustar ambiente', 'role': 2, 'review': False, 'schedule': False},
    'save': {'label': 'Salvar mundo', 'role': 2, 'review': False, 'schedule': True},
    'backup': {'label': 'Criar backup', 'role': 2, 'review': False, 'schedule': True},
    'verify_backup': {'label': 'Verificar backup', 'role': 2, 'review': False, 'schedule': False},
    'restore_backup': {'label': 'Restaurar mundo', 'role': 2, 'review': True, 'schedule': False},
    'recover_restore': {'label': 'Reverter restauração interrompida', 'role': 2, 'review': True, 'schedule': False},
    'map_refresh': {'label': 'Atualizar mapa', 'role': 2, 'review': False, 'schedule': True},
    'console': {'label': 'Executar comando', 'role': 3, 'review': False, 'schedule': False},
    'player_action': {'label': 'Administrar jogador', 'role': 1, 'review': False, 'schedule': False},
    'server_control': {'label': 'Controlar servidor', 'role': 2, 'review': True, 'schedule': False},
    'settings_apply': {'label': 'Aplicar configurações', 'role': 2, 'review': False, 'schedule': False},
    'maintenance': {'label': 'Preparar manutenção', 'role': 2, 'review': True, 'schedule': False},
}

FIELDS = {
    'difficulty': {'label': 'Dificuldade', 'type': 'select', 'options': ['peaceful', 'easy', 'normal', 'hard'], 'description': 'Define o desafio de combate e sobrevivência.', 'restart': True},
    'gamemode': {'label': 'Modo de jogo padrão', 'type': 'select', 'options': ['survival', 'creative', 'adventure', 'spectator'], 'description': 'Modo inicial dos novos jogadores.', 'restart': True},
    'max-players': {'label': 'Limite de jogadores', 'type': 'number', 'min': 1, 'max': 100, 'description': 'Número máximo de conexões simultâneas.', 'restart': True},
    'view-distance': {'label': 'Distância de visão', 'type': 'number', 'min': 2, 'max': 32, 'unit': 'chunks', 'description': 'Distâncias maiores aumentam o uso de memória e rede.', 'restart': True},
    'simulation-distance': {'label': 'Distância de simulação', 'type': 'number', 'min': 2, 'max': 32, 'unit': 'chunks', 'description': 'Área ao redor do jogador em que o mundo permanece ativo.', 'restart': True},
    'white-list': {'label': 'Lista de acesso', 'type': 'boolean', 'description': 'Permite entrar apenas a jogadores autorizados.', 'restart': True},
    'enforce-whitelist': {'label': 'Aplicar lista a jogadores conectados', 'type': 'boolean', 'description': 'Revalida o acesso quando a lista é recarregada.', 'restart': True},
    'spawn-protection': {'label': 'Proteção do ponto inicial', 'type': 'number', 'min': 0, 'max': 256, 'unit': 'blocos', 'description': 'Raio protegido ao redor do spawn.', 'restart': True},
    'player-idle-timeout': {'label': 'Limite de inatividade', 'type': 'number', 'min': 0, 'max': 1440, 'unit': 'min', 'description': 'Zero mantém jogadores inativos conectados.', 'restart': True},
    'motd': {'label': 'Descrição do servidor', 'type': 'text', 'max': 120, 'description': 'Mensagem exibida na lista de servidores.', 'restart': True},
}
NAME = re.compile(r'[.]?[A-Za-z0-9_]{1,16}\Z')
BACKUP = re.compile(r'(?:oak-\d{8}T\d{6}Z|control-[a-f0-9-]{36})\.tar\.gz\Z')


def clean_text(value, maximum=120, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or any(ord(c) < 32 for c in value):
        raise ValueError('Text has an invalid length or control characters.')
    return value.strip()


def settings_changes(changes):
    if not isinstance(changes, dict) or not changes or len(changes) > len(FIELDS):
        raise ValueError('Choose at least one supported setting.')
    result = {}
    for key, value in changes.items():
        field = FIELDS.get(key)
        if not field:
            raise ValueError('Unsupported setting: ' + str(key))
        if field['type'] == 'number':
            if type(value) is not int or not field['min'] <= value <= field['max']:
                raise ValueError('Setting outside its supported range: ' + key)
        elif field['type'] == 'boolean':
            if type(value) is not bool:
                raise ValueError('A boolean is required: ' + key)
        elif field['type'] == 'select':
            if value not in field['options']:
                raise ValueError('Unsupported setting value: ' + key)
        else:
            value = clean_text(value, field['max'])
            if '\\' in value or '§' in value:
                raise ValueError('Formatting and property escapes are not supported.')
        result[key] = value
    return result


def validate(kind, params, role='owner'):
    spec = OPERATIONS.get(kind)
    if not spec or ROLES.get(role, -1) < spec['role']:
        raise PermissionError('This role cannot perform this operation.')
    if not isinstance(params, dict):
        raise ValueError('Operation parameters must be an object.')
    if kind == 'environment_apply':
        from .environment import validate_environment
        return validate_environment(params)
    p = dict(params)
    if kind.startswith('backup_'):
        from .backup_repository import SNAPSHOT, validate_policy
        if kind == 'backup_policy':
            if p.keys() != {'revision', 'policy'} or type(p['revision']) is not int:
                raise ValueError('A current policy revision is required.')
            return {'revision': p['revision'], 'policy': validate_policy(p['policy'])}
        if kind == 'backup_compact':
            if p.keys() != {'revision'} or type(p['revision']) is not int:
                raise ValueError('A current retention revision is required.')
            return p
        if kind == 'backup_check':
            if p:
                raise ValueError('Unexpected operation parameters.')
            return {}
        allowed = {'backup'} if kind == 'backup_delete' else {'backup', 'name', 'pinned'}
        if p.keys() - allowed or not isinstance(p.get('backup'), str) or not SNAPSHOT.fullmatch(p['backup']):
            raise ValueError('Invalid recovery point.')
        if 'name' in p:
            p['name'] = clean_text(p['name'], 80, 1)
        if 'pinned' in p and type(p['pinned']) is not bool:
            raise ValueError('Pinned must be a boolean.')
        return p
    allowed = {
        'save': set(), 'recover_restore': set(), 'backup': {'name', 'automatic', 'activity_at'}, 'verify_backup': {'backup', 'boot'}, 'restore_backup': {'backup', 'fingerprint'},
        'map_refresh': set(), 'console': {'command'}, 'player_action': {'player', 'action', 'reason', 'target'},
        'server_control': {'action', 'force'}, 'settings_apply': {'changes', 'revision'}, 'maintenance': {'restart'},
    }[kind]
    if p.keys() - allowed:
        raise ValueError('Unexpected operation parameters.')
    if kind == 'backup':
        if type(p.get('automatic', False)) is not bool or not isinstance(p.get('activity_at', 0), (int, float)) or not math.isfinite(p.get('activity_at', 0)):
            raise ValueError('Invalid activity checkpoint.')
        p = {'name': clean_text(p.get('name', 'Backup'), 80, 1),
             'automatic': p.get('automatic', False), 'activity_at': max(0, p.get('activity_at', 0))}
    elif kind in ('verify_backup', 'restore_backup'):
        if not isinstance(p.get('backup'), str) or not (BACKUP.fullmatch(p['backup']) or re.fullmatch(r'[a-f0-9]{64}', p['backup'])):
            raise ValueError('Invalid recovery point.')
        if kind == 'restore_backup' and not re.fullmatch(r'[a-f0-9]{64}', p.get('fingerprint', '')):
            raise ValueError('A current recovery review is required.')
        if kind == 'verify_backup':
            if type(p.get('boot', False)) is not bool:
                raise ValueError('Boot verification must be a boolean.')
            p['boot'] = p.get('boot', False)
    elif kind == 'console':
        command = clean_text(p.get('command'), 500, 1).removeprefix('/')
        # Also reject nested execute/function dispatch that could bypass world locks.
        words = command.split()
        roots = [words[0]] + [words[i+1] for i, word in enumerate(words[:-1]) if word == 'run' and words[0].removeprefix('minecraft:') == 'execute']
        if any(word.removeprefix('minecraft:') in {'stop', 'save-off', 'save-on', 'save-all', 'function', 'schedule'} for word in roots):
            raise ValueError('Use the named world or server action for lifecycle commands.')
        p = {'command': command}
    elif kind == 'player_action':
        if not isinstance(p.get('player'), str) or not NAME.fullmatch(p['player']):
            raise ValueError('Invalid player identity.')
        actions = {'kick', 'ban', 'pardon', 'whitelist_add', 'whitelist_remove', 'op', 'deop', 'teleport'}
        if p.get('action') not in actions:
            raise ValueError('Unsupported player action.')
        if p['action'] in ('op', 'deop') and role != 'owner':
            raise PermissionError('Only the owner can change game operators.')
        if p['action'] in ('whitelist_add', 'whitelist_remove', 'teleport') and ROLES[role] < 2:
            raise PermissionError('Administrator access is required.')
        p['reason'] = clean_text(p.get('reason', 'Ação administrativa'), 160, 1)
        if p['action'] == 'teleport':
            target = p.get('target')
            if not isinstance(target, str) or not NAME.fullmatch(target):
                raise ValueError('Choose a destination player.')
        elif 'target' in p:
            raise ValueError('Unexpected teleport target.')
    elif kind == 'server_control':
        if p.get('action') not in ('start', 'stop', 'restart'):
            raise ValueError('Unsupported server action.')
        if type(p.get('force', False)) is not bool:
            raise ValueError('Force must be a boolean.')
    elif kind == 'settings_apply':
        p['changes'] = settings_changes(p.get('changes'))
        if not re.fullmatch(r'[a-f0-9]{64}', p.get('revision', '')):
            raise ValueError('A current configuration revision is required.')
    elif kind == 'maintenance':
        if type(p.get('restart', True)) is not bool:
            raise ValueError('Restart must be a boolean.')
        p = {'restart': p.get('restart', True)}
    return p


def place(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid place.')
    dimension = data.get('dimension', 'minecraft:overworld')
    if dimension not in ('minecraft:overworld', 'minecraft:the_nether', 'minecraft:the_end'):
        raise ValueError('Unsupported dimension.')
    result = {'name': clean_text(data.get('name'), 60, 1), 'dimension': dimension, 'note': clean_text(data.get('note', ''), 400)}
    for axis in ('x', 'y', 'z'):
        value = data.get(axis)
        if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 30000000:
            raise ValueError('Invalid coordinates.')
        result[axis] = value
    return result
