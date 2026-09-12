"""Opt-in native integration; only fresh worlds inside a private network namespace."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def main():
    source, mod, host_namespace = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    if not host_namespace.startswith('net:[') or str(Path('/proc/self/ns/net').readlink()) == host_namespace:
        raise RuntimeError('A separate network namespace is mandatory.')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    with tempfile.TemporaryDirectory(prefix='oak-native-smoke-') as directory:
        root = Path(directory)
        for name in ('libraries', 'versions'):
            shutil.copytree(source / name, root / name)
        for path in source.glob('*.jar'):
            shutil.copy2(path, root / path.name)
        launcher = source / 'fabric-server-launcher.properties'
        if launcher.exists():
            shutil.copy2(launcher, root / launcher.name)
        (root / 'mods').mkdir()
        for path in (source / 'mods').glob('fabric-api*.jar'):
            shutil.copy2(path, root / 'mods' / path.name)
        shutil.copy2(mod, root / 'mods' / mod.name)
        (root / 'eula.txt').write_text('eula=true\n')
        (root / 'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25565\nonline-mode=false\nview-distance=2\nsimulation-distance=2\nlevel-type=minecraft:flat\nspawn-protection=0\npause-when-empty-seconds=1\nsync-chunk-writes=false\n')
        endpoint = root / 'operator.sock'

        def call(method, data=None, key=None):
            request = {'method': method, 'data': data or {}}
            if key:
                request['idempotency'] = key
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(35)
                connection.connect(str(endpoint))
                connection.sendall(json.dumps(request).encode() + b'\n')
                with connection.makefile('rb') as reader:
                    result = json.loads(reader.readline(8 * 1024 * 1024))
            return result

        def success(method, data=None, key=None):
            response = call(method, data, key)
            assert response.get('ok'), response
            return response['result']

        command = ['/usr/bin/java', '-Xms256M', '-Xmx1500M',
                   '-Doak.environment.socket=' + str(root / 'control.sock'),
                   '-Doak.telemetry.socket=' + str(root / 'positions.sock'),
                   '-Doak.operator.socket=' + str(endpoint),
                   '-jar', 'fabric-server-launch.jar', '--nogui']
        with (root / 'output.log').open('w') as output:
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
            try:
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('Isolated server exited during startup')
                    try:
                        native = success('discover')
                        break
                    except OSError:
                        time.sleep(1)
                else:
                    raise TimeoutError('Isolated server startup timed out')
                assert endpoint.stat().st_mode & 0o777 == 0o600
                assert native['loaded_chunks_only'] and 'blocks.apply' in native['methods']
                assert native['schemas']['blocks.apply']['required'] == ['blocks']
                assert success('players', {'inventory': True})['players'] == []
                command_result = success('command', {'command': 'forceload add 0 0'}, 'force-chunk')
                assert command_result['messages'], command_result
                assert success('command', {'command': 'forceload add 0 0'}, 'force-chunk') == command_result
                assert not call('command', {'command': 'list'}, 'force-chunk')['ok']
                assert success('receipt', {'idempotency': 'force-chunk'})['found']
                bounds = {'min': [0, 0, 0], 'max': [2, 0, 0], 'limit': 1}
                for _ in range(30):
                    first = success('region.inspect', bounds)
                    if first['blocks'][0]['loaded']:
                        break
                    time.sleep(.1)
                assert first['blocks'][0]['loaded'], first
                assert first['next_cursor'] == 1 and first['has_more']
                original = first['blocks'][0]['state']
                changed = success('blocks.apply', {'blocks': [{'position': [0, 0, 0], 'state': 'minecraft:gold_block', 'expected': original}]}, 'place-test')
                assert changed['results'][0]['after'] == 'minecraft:gold_block', changed
                assert success('blocks.apply', {'blocks': [{'position': [0, 0, 0], 'state': 'minecraft:gold_block', 'expected': original}]}, 'place-test') == changed
                conflict = success('blocks.apply', {'blocks': [{'position': [0, 0, 0], 'state': 'minecraft:diamond_block', 'expected': original}]})
                assert 'error' in conflict['results'][0], conflict
                restored = success('blocks.apply', {'blocks': [{'position': [0, 0, 0], 'state': original, 'expected': 'minecraft:gold_block'}]})
                assert restored['results'][0]['after'] == original
                # Exact installed height API defines max_y inclusively.
                top = next(d['max_y'] for d in native['dimensions'] if d['id'] == 'minecraft:overworld')
                assert success('region.inspect', {'min': [0, top, 0], 'max': [0, top, 0]})['blocks'][0]['loaded']
                success('command', {'command': 'summon minecraft:armor_stand 1 0 1'})
                entities = success('entities', {'min': [0, -5, 0], 'max': [4, 10, 4], 'type': 'minecraft:armor_stand'})
                assert len(entities['entities']) == 1, entities
                events = success('events')
                assert any(event['name'] == 'world.blocks' for event in events['events'])
                assert success('events', {'after': events['next']})['events'] == []
                assert not call('entities', {'min': [0, 0, 0], 'max': [300, 1, 1]})['ok']
                assert not call('region.inspect', {'min': [0, 0, 0], 'max': [1.1, 1, 1]})['ok']
                assert not call('blocks.apply', {'blocks': [], 'expected_epoch': 'wrong-epoch'})['ok']
                time.sleep(3)
                idle = success('discover')
                time.sleep(.2)
                assert success('discover')['tick'] == idle['tick'], 'Fixture must be paused while checking native requests'
                assert success('command', {'command': 'list'})['messages']
                assert success('events')['epoch'] == idle['epoch']
                print('Native discovery, socket permissions, commands, receipts, regions, writes/conflicts/restore, height, entities, events, epoch preconditions and paused-server access passed.', flush=True)
            except Exception:
                output.flush()
                print((root / 'output.log').read_text()[-10000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.stdin.write('stop\n'); process.stdin.flush()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()


if __name__ == '__main__':
    main()
