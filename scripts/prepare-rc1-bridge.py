"""Stage the external pre-3 bridge for rc-1 without installing or starting it."""
import argparse
import json
from pathlib import Path
import shutil


def prepare(source, reports, output):
    source, reports, output = (Path(p).resolve() for p in (source, reports, output))
    if output.exists() or source in output.parents or output in source.parents:
        raise ValueError('Use a new output directory separate from the source bridge.')
    previous = source / 'artifacts/26.3-pre-3/generated/reports'
    for name in ('packets', 'blocks', 'registries'):
        if json.loads((previous / (name + '.json')).read_text()) != json.loads((reports / (name + '.json')).read_text()):
            raise ValueError('Additional translation review required: ' + name)
    bridge = (source / 'auth_bridge.py').read_text()
    if bridge.count('1073742159') != 1 or "report('26.3-pre-3', 'packets')" not in bridge:
        raise ValueError('Unexpected source bridge version.')
    shutil.copytree(source, output, ignore=shutil.ignore_patterns('__pycache__', 'logs', 'field-codec.log'))
    shutil.copytree(reports, output / 'artifacts/26.3-rc-1/generated/reports')
    for path in output.glob('*.py'):
        content = path.read_text().replace('26.3-pre-3', '26.3-rc-1').replace('1073742159', '1073742160')
        compile(content, str(path), 'exec')
        path.write_text(content)
    print('RC-1 bridge staged. Isolated authentication and gameplay checks are required before installation.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--reports', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    prepare(args.source, args.reports, args.output)
