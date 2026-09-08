"""Opt-in exact-version game integration. Requires a fresh private network namespace.

Run through unshare --net; arguments: source server, built mod, host net namespace.
Copies only runtime jars into a disposable world, never opens existing world data.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid


def main():
    source, mod, host_namespace = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    if not host_namespace.startswith('net:[') or str(Path('/proc/self/ns/net').readlink()) == host_namespace:
        raise RuntimeError('A separate network namespace is mandatory.')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    with tempfile.TemporaryDirectory(prefix='oak-environment-smoke-') as directory:
        root = Path(directory)
        for name in ('libraries', 'versions'):
            shutil.copytree(source / name, root / name)
        for path in source.glob('*.jar'):
            shutil.copy2(path, root / path.name)
        launch = source / 'fabric-server-launcher.properties'
        if launch.exists():
            shutil.copy2(launch, root / launch.name)
        (root / 'mods').mkdir()
        for path in (source / 'mods').glob('fabric-api*.jar'):
            shutil.copy2(path, root / 'mods' / path.name)
        shutil.copy2(mod, root / 'mods' / mod.name)
        (root / 'eula.txt').write_text('eula=true\n')
        (root / 'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25565\nonline-mode=false\nview-distance=2\nsimulation-distance=2\nlevel-type=minecraft:flat\nspawn-protection=0\npause-when-empty-seconds=-1\nsync-chunk-writes=false\n')
        endpoint = root / 'control.sock'

        def call(action='status', **params):
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(12)
                sock.connect(str(endpoint))
                sock.sendall(json.dumps({'action': action, **params}).encode() + b'\n')
                with sock.makefile('rb') as reader:
                    result = json.loads(reader.readline(65537))
                return result

        def change(action, **params):
            request = {'id': str(uuid.uuid4()), 'revision': call()['revision'], **params}
            result = call(action, **request)
            assert not result.get('error'), result
            return result, request

        command = ['/usr/bin/java', '-Xms256M', '-Xmx1500M',
                   '-Doak.environment.socket=' + str(endpoint),
                   '-Doak.telemetry.socket=' + str(root / 'positions.sock'),
                   '-jar', 'fabric-server-launch.jar', '--nogui']

        def start(output):
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Isolated server exited before startup.')
                try:
                    result = call()
                    assert not result.get('error'), result
                    return process, result
                except OSError:
                    time.sleep(1)
            process.terminate()
            process.wait(timeout=30)
            raise TimeoutError('Isolated server startup timed out.')

        def stop(process):
            if process.poll() is None:
                process.stdin.write('stop\n'); process.stdin.flush()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()

        with (root / 'output.log').open('w') as output:
            process = None
            try:
                process, initial = start(output)
                assert initial['policy']['cycle'] == 'native'
                policy = {**initial['policy'], 'cycle': 'custom', 'day': 20, 'dusk': 2, 'night': 5, 'dawn': 2,
                          'weather': 'managed', 'clear_min': .25, 'clear_max': .25, 'rain_min': .25, 'rain_max': .25,
                          'storm_chance': 100, 'storm_gap': .25, 'rules': {'pvp': False, 'players_sleeping_percentage': 50}}
                configured, request = change('configure', policy=policy)
                assert abs(configured['rate'] - .5) < .001, configured
                assert configured['rules']['pvp'] is False
                assert call('configure', **request)['revision'] == configured['revision']
                assert call('configure', **{**request, 'id': str(uuid.uuid4())}).get('error')
                # A full transition verifies native weather mutation and tick deadlines.
                time.sleep(17)
                assert call()['weather'] in ('rain', 'thunder'), call()
                overridden, _ = change('override', weather='thunder', minutes=1)
                assert overridden['weather'] == 'thunder'
                restored, _ = change('release')
                assert 'override' not in restored
                paused, _ = change('configure', policy={**policy, 'cycle': 'paused'})
                assert paused['paused'] is True
                resumed, _ = change('configure', policy=initial['policy'])
                assert resumed['rate'] == initial['rate'] and resumed['paused'] == initial['paused']
                # Restart with an expired durable override; it must not revive.
                change('override', weather='rain', minutes=1)
                stop(process); process = None
                stored_path = root / 'config/oak-environment.json'
                stored = json.loads(stored_path.read_text())
                stored['override']['expires'] = 0
                stored_path.write_text(json.dumps(stored))
                process, restarted = start(output)
                time.sleep(2)
                assert 'override' not in call()
                change('configure', policy=policy)
                process.stdin.write('gamerule minecraft:advance_weather true\n'); process.stdin.flush()
                time.sleep(2)
                assert call()['drift'] is True, call()
                assert 'override' not in call()
                print('Native cycle, pause, weather transition, rules, revision, receipt, release, restart expiry and external drift passed.', flush=True)
            except Exception:
                output.flush()
                print((root / 'output.log').read_text()[-10000:], file=sys.stderr)
                raise
            finally:
                if process is not None:
                    stop(process)


if __name__ == '__main__':
    main()
