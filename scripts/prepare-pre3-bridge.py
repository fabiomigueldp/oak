"""Stage the external Oak bridge for pre-3; never install it or start services."""
import argparse
import json
from pathlib import Path
import shutil


def prepare(source, reports, output):
    source, reports, output = map(lambda p: Path(p).resolve(), (source, reports, output))
    if output.exists() or source in output.parents or output in source.parents:
        raise ValueError('Use a new output directory separate from the source bridge.')
    previous = source / 'artifacts/26.3-pre-2/generated/reports'
    for name in ('blocks', 'registries'):
        if json.loads((previous / (name + '.json')).read_text()) != json.loads((reports / (name + '.json')).read_text()):
            raise ValueError('Registry/state changes require a separate mapping review: ' + name)
    old = json.loads((previous / 'packets.json').read_text())
    new = json.loads((reports / 'packets.json').read_text())
    if old.keys() != new.keys():
        raise ValueError('Unexpected protocol states.')
    for state in old:
        if old[state].keys() != new[state].keys():
            raise ValueError('Unexpected protocol directions.')
        for direction, entries in old[state].items():
            updated = new[state][direction]
            if (state, direction) == ('play', 'clientbound'):
                if updated.keys() - entries.keys() != {'minecraft:add_transient_block'} or entries.keys() - updated.keys():
                    raise ValueError('Unexpected clientbound packet changes.')
            elif entries != updated:
                raise ValueError('Unexpected packet changes: ' + state + '/' + direction)
    shutil.copytree(source, output, ignore=shutil.ignore_patterns('__pycache__', 'logs', 'field-codec.log'))
    shutil.copytree(reports, output / 'artifacts/26.3-pre-3/generated/reports')
    for path in output.glob('*.py'):
        path.write_text(path.read_text().replace('26.3-pre-2', '26.3-pre-3').replace('1073742158', '1073742159'))
    path = output / 'translate.py'
    content = path.read_text()
    anchor = 'def clientbound(state, name, payload):\n'
    if content.count(anchor) != 1:
        raise ValueError('Unexpected translator source; discard staging and review manually.')
    content = content.replace(anchor, anchor + """    if state == 'play' and name == 'add_transient_block':
        # Pre-3 client-only falling-block render continuity has no 26.2 equivalent.
        # Keep authoritative block updates; never turn temporary visuals into world state.
        stream = io.BytesIO(payload)
        exact(stream, 8)
        state_id = rv(stream)
        if stream.read(1) or state_id not in STATE_MAP:
            raise ValueError('Invalid transient block packet')
        return None
""")
    path.write_text(content)
    path = output / 'auth_bridge.py'
    content = path.read_text()
    if not content.startswith('"""'):
        raise ValueError('Unexpected bridge source header.')
    end = content.index('"""', 3) + 3
    content = ('"""Loopback-only Oak 26.2 to 26.3-pre-3 compatibility bridge.\n\n'
               'Preserves Floodgate forwarding; production backend listens on port 25565.\n"""' + content[end:])
    content = content.replace('Oak LAB 26.3 / experimental bridge', 'Oak 26.3-pre-3 / compatibility bridge')
    content = content.replace('Isolated Survival compatibility laboratory', 'Oak crossplay')
    path.write_text(content)
    print('Bridge staged. Review and isolated authentication/gameplay verification are required before installation.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--reports', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    prepare(args.source, args.reports, args.output)
