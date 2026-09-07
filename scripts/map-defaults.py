"""Explicitly update public BlueMap quality defaults; never run the map renderer."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

# This operation is intentionally separate from the website deployment.
DEFAULTS = {'hires-slider-default': ('hiresSliderDefault', 250),
            'hires-slider-max': ('hiresSliderMax', 500),
            'lowres-slider-default': ('lowresSliderDefault', 2000),
            'lowres-slider-max': ('lowresSliderMax', 4000),
            'resolution-default': ('resolutionDefault', 1)}


def prepare(config, settings):
    data = json.loads(settings)
    if data.get('version') != '5.23':
        raise ValueError('Review the map profile integration before changing BlueMap versions.')
    for key, (json_key, value) in DEFAULTS.items():
        pattern = rf'(?m)^([ \t]*{re.escape(key)}[ \t]*:[ \t]*)[0-9.]+([ \t]*(?:#[^\r\n]*)?\r?)$'
        config, count = re.subn(pattern, lambda m: m[1] + str(value) + m[2], config)
        if count != 1:
            raise ValueError('Expected exactly one numeric configuration field: ' + key)
        data[json_key] = value
    return config, json.dumps(data, separators=(',', ':')) + '\n'


def replace_file(path, content):
    stat = path.stat()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.oak-profile-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        # Preserve access mode, but keep a fresh mtime for HTTP cache validation.
        shutil.copymode(path, temporary)
        if hasattr(os, 'chown'):
            os.chown(temporary, stat.st_uid, stat.st_gid)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(root, backup_root):
    paths = [root / 'config/webapp.conf', root / 'web/settings.json']
    originals = [p.read_bytes() for p in paths]
    updated = [s.encode() for s in prepare(*(b.decode() for b in originals))]
    if originals == updated:
        print('Map defaults already match the reviewed profile.')
        return
    backup = backup_root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True, mode=0o750)
    for path in paths:
        shutil.copy2(path, backup / path.name)
    installed = []
    try:
        for path, before, after in zip(paths, originals, updated):
            if path.read_bytes() != before:
                raise RuntimeError('Map configuration changed during update: ' + str(path))
            replace_file(path, after)
            installed.append((path, before))
    except Exception:
        for path, before in installed:
            replace_file(path, before)
        raise
    print('Map defaults updated; renderer and Minecraft were not restarted.')
    print('Previous public configuration: ' + str(backup))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write reviewed defaults with a backup.')
    args = parser.parse_args()
    root = Path('/srv/oak/map-test')
    if args.apply:
        if os.geteuid() != 0:
            parser.error('Run --apply with sudo.')
        apply(root, Path('/srv/oak/map-profile-backups'))
    else:
        prepare((root / 'config/webapp.conf').read_text(), (root / 'web/settings.json').read_text())
        print(json.dumps({key: value for key, (_, value) in DEFAULTS.items()}, indent=2))
        print('Dry run only; pass --apply to persist these defaults.')
