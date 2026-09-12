"""Persistent operator services managed by systemd. No game service is restarted implicitly."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import threading
from .packages import atomic


class Services:
    def __init__(self, state, demo=False, unit_root=Path('/etc/systemd/system'), run=None):
        self.state, self.demo, self.units = Path(state) / 'services', demo, Path(unit_root)
        self.run = run or self._run
        self.lock = threading.RLock()

    @staticmethod
    def _run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or 'Service operation failed.')
        return result.stdout

    def call(self, method, data, actor):
        with self.lock:
            if method == 'services.list':
                values = [json.loads(p.read_text()) for p in sorted(self.state.glob('*.json'))]
                for value in values:
                    if not self.demo:
                        value['status'] = self.run('systemctl', 'show', value['unit'], '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'UnitFileState')
                        observed = dict(line.split('=', 1) for line in value['status'].splitlines() if '=' in line)
                        value.update(state=observed.get('ActiveState'), active=observed.get('ActiveState') == 'active', enabled=observed.get('UnitFileState') == 'enabled')
                return {'services': values}
            name = data.get('name', '')
            if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
                raise ValueError('Service name must use lowercase letters, digits, underscores or hyphens.')
            unit = 'oak-operator-' + name + '.service'
            record = self.state / (name + '.json')
            if method == 'services.upsert':
                source, kind = data.get('source'), data.get('kind', 'shell')
                if not isinstance(source, str) or not source.strip() or kind not in ('shell', 'python', 'javascript'):
                    raise ValueError('Provide source and kind shell, python or javascript.')
                cwd = data.get('cwd', '/srv/oak')
                if not isinstance(cwd, str) or not (Path(cwd).is_absolute() or (self.demo and PurePosixPath(cwd).is_absolute())) or any(c in cwd for c in '\n\r\0%"'):
                    raise ValueError('Working directory must be an absolute path without systemd expansions.')
                script = self.state / name / ('main.' + {'shell': 'sh', 'python': 'py', 'javascript': 'mjs'}[kind])
                atomic(script, source.encode())
                executable = {'shell': '/bin/bash', 'python': '"' + sys.executable + '"', 'javascript': '/usr/bin/env node'}[kind]
                content = ('[Unit]\nDescription=Oak operator service ' + name + '\nAfter=network.target\n\n'
                           '[Service]\nType=simple\nUser=root\nWorkingDirectory="' + cwd + '"\n'
                           'Environment="PYTHONPATH=' + str(Path(__file__).resolve().parents[1]) + '"\n'
                           'ExecStart=' + executable + ' "' + str(script) + '"\n'
                           'Restart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\nUMask=0077\n\n'
                           '[Install]\nWantedBy=multi-user.target\n')
                path = self.units / unit
                if path.exists() and not record.exists():
                    raise ValueError('Unit exists outside operator management.')
                value = {'name': name, 'unit': unit, 'kind': kind, 'source': source, 'cwd': cwd, 'actor': actor, 'demo': self.demo}
                if not self.demo:
                    atomic(path, content.encode())
                    path.chmod(0o644)
                    self.run('systemctl', 'daemon-reload')
                atomic(record, json.dumps(value).encode())
                return value
            if not record.exists():
                raise ValueError('Service not found.')
            if method == 'services.control':
                action = data.get('action')
                if action not in ('start', 'stop', 'restart', 'enable', 'disable'):
                    raise ValueError('Action must be start, stop, restart, enable or disable.')
                output = '' if self.demo else self.run('systemctl', action, unit)
                return {'name': name, 'action': action, 'output': output, 'demo': self.demo}
            if method == 'services.logs':
                output = 'Simulated service.' if self.demo else self.run('journalctl', '-u', unit, '-n', '200', '--no-pager', '-o', 'short-iso')
                return {'name': name, 'output': output, 'demo': self.demo}
            if method == 'services.delete':
                if not self.demo:
                    self.run('systemctl', 'disable', '--now', unit)
                    (self.units / unit).unlink(missing_ok=True)
                    self.run('systemctl', 'daemon-reload')
                record.unlink()
                return {'deleted': name, 'demo': self.demo}
            raise ValueError('Unknown service method.')
