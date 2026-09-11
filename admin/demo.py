"""Explicit localhost-only simulator. Never connects to Minecraft or SSH."""
import copy
import hashlib
import math
import time
import uuid

from .domain import FIELDS, validate
from .environment import DEFAULTS, RULES, environment_changes
from .store import encode


class DemoAgent:
    def __init__(self):
        from .backup_repository import DEFAULT_POLICY
        self.backup_policy = {'revision': 1, **DEFAULT_POLICY, 'enabled': True}
        self.started = time.time()
        self.online = True
        self.pending_restart = False
        self.last_save = self.started - 600
        self.receipts = {}
        self.aviary_state = {'available': True, 'enabled': True, 'revision': 0, 'error': '', 'maxFlights': 2, 'flights': [],
            'ports': [{'id': ident, 'name': name, 'dimension': 'minecraft:overworld', 'x': x, 'y': 80, 'z': z, 'yaw': 0,
                       'owner': '00000000-0000-0000-0000-000000000001', 'shared': True, 'busy': False, 'departureYaw': None, 'arrivalYaw': None}
                      for ident, name, x, z in [('harbor', 'Harbor', 16, 56), ('ridge', 'Ridge', -96, 184)]]}
        self.environment_state = {'available': True, 'revision': 0, 'policy': copy.deepcopy(DEFAULTS),
            'fields': RULES, 'drift': False, 'error': '', 'clock': 6000, 'rate': 1, 'paused': False,
            'weather': 'clear', 'next_weather_seconds': 0,
            'rules': {key: (100 if key == 'players_sleeping_percentage' else 3 if key == 'random_tick_speed' else key != 'keep_inventory') for key in RULES}}
        self.values = {'difficulty': 'normal', 'gamemode': 'survival', 'max-players': 12, 'view-distance': 20, 'simulation-distance': 6, 'white-list': False, 'enforce-whitelist': False, 'spawn-protection': 16, 'player-idle-timeout': 0, 'motd': 'Oak · um mundo compartilhado'}
        self.points = []
        for age, name, tested in ((1800, 'Antes da nova trilha', True), (7200, 'Rotina da manhã', False), (86400, 'Primeiras construções', True)):
            identifier = 'control-' + str(uuid.uuid4()) + '.tar.gz'
            self.points.append({'id': identifier, 'name': name, 'created': self.started - age, 'bytes': 687 * 1024 * 1024, 'source': 'oak', 'integrity': True, 'restoration': {'at': self.started - age + 60, 'level': 'boot' if tested else 'extraction', 'playable_boot_tested': tested}, 'replicated': False, 'fingerprint': hashlib.sha256(identifier.encode()).hexdigest(), 'manifest': {'versions': ['26.3-pre-2'], 'method': 'flush-and-stable-copy'}})
        for index, point in enumerate(self.points):
            point.update(id=point['fingerprint'], source='repository', compatible=True, restorable=True,
                         added_bytes=(24 if index < 2 else 520)*1024**2, duration=31+index*9, pinned=index == 2)
            point['manifest'].update(versions=['26.3-pre-3'], includes_runtime=True)
            point['restoration']['boot_seconds'] = 74

    def snapshot(self):
        now = time.time()
        movement = math.sin((now - self.started) / 30)
        players = [{'name': 'Lia', 'platform': 'java', 'position': [-96 + movement * 20, 72, 184], 'dimension': 'minecraft:overworld', 'sampled_at': now},
                   {'name': '.Caio', 'platform': 'bedrock', 'position': [84, 69, -128 + movement * 10], 'dimension': 'minecraft:overworld', 'sampled_at': now},
                   {'name': 'Bruna', 'platform': 'java', 'position': [16, 64, 56], 'dimension': 'minecraft:the_nether', 'sampled_at': now}]
        services = [{'id': name, 'ActiveState': 'active' if self.online or name != 'oak.service' else 'inactive', 'SubState': 'running', 'Result': 'success', 'MemoryCurrent': str(memory)} for name, memory in [('oak.service', 2800000000), ('oak-geyser.service', 520000000), ('oak-bedrock-bridge.service', 100000000), ('oak-chat.service', 12000000), ('oak-web-collector.service', 16000000)]]
        services.extend([{'id': 'oak-map.service', 'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success'}, {'id': 'oak-backup.service', 'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success'}])
        return {'sampled_at': now, 'source_updated': now, 'fresh': True, 'online': self.online, 'players': players if self.online else [], 'max_players': self.values['max-players'], 'version': '26.3-pre-2', 'loader': 'Fabric', 'mods': ['fabric-api.jar', 'floodgate-fabric.jar'], 'services': services, 'disk': {'total': 193 * 1024 ** 3, 'free': 166 * 1024 ** 3, 'used': 27 * 1024 ** 3}, 'memory': {'total': 12 * 1024 ** 3, 'available': 6.4 * 1024 ** 3}, 'cpu_count': 2, 'load': [.48 + movement * .1, .51, .6], 'tps': 20, 'mspt': 18 + movement * 2, 'last_save': {'at': self.last_save, 'source': 'manual'}, 'map': {'running': False, 'last_finished': self.started - 900, 'result': 'success', 'blocked': False, 'url': 'https://oak.fabiomigueldp.me/map/', 'telemetry': 'demonstration'}, 'warnings': [], 'demo': True}

    def configuration(self):
        return {'values': copy.deepcopy(self.values), 'fields': FIELDS, 'revision': hashlib.sha256(encode(self.values).encode()).hexdigest(), 'management_enabled': False, 'pending_restart': self.pending_restart}

    def call(self, method, data=None, progress=None):
        data = data or {}
        if method == 'snapshot':
            return self.snapshot()
        if method == 'backups':
            return copy.deepcopy(self.points)
        if method == 'backup_status':
            return {'ready': True, 'engine': 'restic', 'sampled_at': time.time(), 'backups': copy.deepcopy(self.points),
                    'policy': dict(self.backup_policy), 'bytes': 568*1024**2,
                    'logical_bytes': sum(p['bytes'] for p in self.points), 'free_bytes': 157*1024**3,
                    'reserve_bytes': 20*1024**3, 'next_run': self.points[0]['created']+self.backup_policy['interval_minutes']*60 if self.points else None,
                    'health': {'data_checked': self.started-3600, 'compacted': self.started}, 'prunable': [], 'external_copy': False}
        if method == 'configuration':
            return self.configuration()
        if method == 'environment':
            env = self.environment_state
            if env.get('override', {}).get('expires', float('inf')) <= time.time():
                env.pop('override')
                env['weather'] = 'clear'
            env['sampled_at'] = time.time()
            return copy.deepcopy(env)
        if method == 'aviary':
            if data.get('port'):
                port = next((p for p in self.aviary_state['ports'] if p['id'] == data['port']), None)
                if not port:
                    raise ValueError('Aviport no longer exists.')
                port['check'] = {'clear': True, 'message': 'Landing area clear (demo)', 'checked_at': time.time()}
            return {**copy.deepcopy(self.aviary_state), 'sampled_at': time.time()}
        if method == 'logs':
            return {'service': data['service'], 'lines': ['[demonstration] Server ready. No production commands are sent.', '[demonstration] World save completed.'], 'sampled_at': time.time()}
        if method == 'receipt':
            return self.receipts.get(data['job'])
        if method == 'preview':
            kind, params = data['kind'], validate(data['kind'], data['params'])
            preview = {'kind': kind, 'params': params, 'impact': 'Esta ação será simulada no ambiente de demonstração.', 'steps': ['Verificar condições', 'Executar a ação solicitada', 'Confirmar o resultado'], 'requires_confirmation': True}
            if kind == 'settings_apply':
                if params['revision'] != self.configuration()['revision']:
                    raise ValueError('Configuration changed. Reload and review again.')
                preview['changes'] = [{'key': k, 'label': FIELDS[k]['label'], 'before': self.values[k], 'after': v} for k, v in params['changes'].items()]
            if kind == 'environment_apply':
                if params['revision'] != self.environment_state['revision']:
                    raise ValueError('Environment changed. Reload and review again.')
                preview['changes'] = environment_changes(self.environment_state, params)
            if kind == 'aviary_edit' and params['revision'] != self.aviary_state['revision']:
                raise ValueError('Aviports changed. Refresh before saving.')
            if kind == 'console':
                preview['command'] = params['command']
            if kind == 'restore_backup':
                point = next(p for p in self.points if p['id'] == params['backup'])
                preview.update(point_name=point['name'], version='26.3-pre-3')
            return preview
        if method == 'execute':
            jid, kind = data['job'], data['kind']
            params = validate(kind, data['params'])
            if jid in self.receipts:
                return self.receipts[jid]['result']
            for step in ('Verificando condições', 'Executando', 'Verificando resultado'):
                if progress:
                    progress(step, 'Local demonstration. No production effect.')
                time.sleep(.3)
            result = {'demonstration': True}
            if kind == 'backup_policy':
                if params['revision'] != self.backup_policy['revision']:
                    raise ValueError('A política mudou. Atualize antes de salvar.')
                self.backup_policy = {'revision': params['revision'] + 1, **params['policy']}
                result.update(self.backup_policy)
            elif kind == 'backup_edit':
                point = next(p for p in self.points if p['id'] == params['backup'])
                point.update({k:v for k,v in params.items() if k != 'backup'})
            elif kind == 'backup_delete':
                self.points = [p for p in self.points if p['id'] != params['backup']]
            elif kind == 'aviary_edit':
                if params['revision'] != self.aviary_state['revision']:
                    raise ValueError('Aviports changed. Refresh before saving.')
                port = next((p for p in self.aviary_state['ports'] if p['id'] == params['port']), None)
                if not port or port['busy']:
                    raise ValueError('Aviport unavailable.')
                port.update({k: params[k] for k in ('name', 'shared', 'departureYaw', 'arrivalYaw')})
                port.pop('check', None)
                self.aviary_state['revision'] += 1
                result.update(copy.deepcopy(self.aviary_state))
            elif kind == 'environment_apply':
                env = self.environment_state
                if params['revision'] != env['revision']:
                    raise ValueError('Environment changed after review.')
                if params['action'] == 'configure':
                    env['policy'] = copy.deepcopy(params['policy'])
                    env['rules'].update(params['policy']['rules'])
                    env['paused'] = params['policy']['cycle'] == 'paused'
                elif params['action'] == 'override':
                    env['override'] = {'weather': params['weather'], 'expires': time.time() + params['minutes'] * 60}
                    env['weather'] = params['weather']
                else:
                    env.pop('override', None)
                    env['weather'] = 'clear'
                env['revision'] += 1
                result.update(copy.deepcopy(env))
            elif kind in ('backup', 'maintenance'):
                identifier = 'control-' + jid + '.tar.gz'
                self.points.insert(0, {'id': identifier, 'name': params.get('name', 'Antes da manutenção'), 'created': time.time(), 'bytes': 689 * 1024 * 1024, 'source': 'oak', 'integrity': True, 'restoration': None, 'replicated': False, 'fingerprint': hashlib.sha256(identifier.encode()).hexdigest(), 'manifest': {'versions': ['26.3-pre-2']}})
                point = self.points[0]
                point.update(id=point['fingerprint'], source='repository', compatible=True, restorable=True,
                             added_bytes=18*1024**2, duration=28, pinned=False, restoration={})
                point['manifest'].update(versions=['26.3-pre-3'], includes_runtime=True)
                identifier = point['id']
                result['backup'] = identifier
                self.last_save = time.time()
            elif kind == 'verify_backup':
                point = next(p for p in self.points if p['id'] == params['backup'])
                point['restoration'] = {'at': time.time(), 'level': 'boot' if params['boot'] else 'extraction', 'playable_boot_tested': params['boot']}
                result.update(point['restoration'])
            elif kind == 'save':
                self.last_save = time.time()
                result['saved_at'] = self.last_save
            elif kind == 'settings_apply':
                if params['revision'] != self.configuration()['revision']:
                    raise ValueError('Configuration changed after review.')
                self.values.update(params['changes'])
                self.pending_restart = True
                result.update(restart_required=True, revision=self.configuration()['revision'])
            elif kind == 'server_control':
                self.online = params['action'] != 'stop'
                if self.online:
                    self.pending_restart = False
                result['online'] = self.online
            elif kind == 'console':
                result['response'] = 'Demonstração: comando recebido: ' + params['command']
            elif kind == 'player_action':
                result['response'] = 'Ação demonstrativa registrada para ' + params['player']
            elif kind == 'map_refresh':
                result.update(requested=True, render_complete=False)
            elif kind == 'restore_backup':
                result['restored'] = params['backup']
            self.receipts[jid] = {'state': 'completed', 'result': result}
            return result
        raise ValueError('Unknown demonstration capability.')
