"""Explicit localhost-only simulator. Never connects to Minecraft or SSH."""
import copy
import hashlib
import math
import time
import uuid

from .domain import FIELDS, validate
from .store import encode


class DemoAgent:
    def __init__(self):
        self.started = time.time()
        self.online = True
        self.pending_restart = False
        self.last_save = self.started - 600
        self.receipts = {}
        self.values = {'difficulty': 'normal', 'gamemode': 'survival', 'max-players': 12, 'view-distance': 20, 'simulation-distance': 6, 'white-list': False, 'enforce-whitelist': False, 'spawn-protection': 16, 'player-idle-timeout': 0, 'motd': 'Oak · um mundo compartilhado'}
        self.points = []
        for age, name, tested in ((1800, 'Antes da nova trilha', True), (7200, 'Rotina da manhã', False), (86400, 'Primeiras construções', True)):
            identifier = 'control-' + str(uuid.uuid4()) + '.tar.gz'
            self.points.append({'id': identifier, 'name': name, 'created': self.started - age, 'bytes': 687 * 1024 * 1024, 'source': 'oak', 'integrity': True, 'restoration': {'at': self.started - age + 60, 'level': 'boot' if tested else 'extraction', 'playable_boot_tested': tested}, 'replicated': False, 'fingerprint': hashlib.sha256(identifier.encode()).hexdigest(), 'manifest': {'versions': ['26.3-pre-2'], 'method': 'flush-and-stable-copy'}})

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
        if method == 'configuration':
            return self.configuration()
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
            if kind == 'console':
                preview['command'] = params['command']
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
            if kind in ('backup', 'maintenance'):
                identifier = 'control-' + jid + '.tar.gz'
                self.points.insert(0, {'id': identifier, 'name': params.get('name', 'Antes da manutenção'), 'created': time.time(), 'bytes': 689 * 1024 * 1024, 'source': 'oak', 'integrity': True, 'restoration': None, 'replicated': False, 'fingerprint': hashlib.sha256(identifier.encode()).hexdigest(), 'manifest': {'versions': ['26.3-pre-2']}})
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
