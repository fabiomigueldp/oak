"""Check authored model inheritance and texture references without a game client."""
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / 'pack/assets'


def resource(identifier, folder, suffix):
    namespace, name = identifier.split(':', 1)
    return ASSETS / namespace / folder / (name + suffix)


def model(identifier, ancestry=()):
    if identifier in ancestry:
        raise ValueError(f'Model inheritance cycle: {identifier}')
    data = json.loads(resource(identifier, 'models', '.json').read_text(encoding='utf-8'))
    parent = model(data['parent'], (*ancestry, identifier)) if 'parent' in data else {}
    return {**parent, **data, 'textures': {**parent.get('textures', {}), **data.get('textures', {})}}


def texture(reference, textures):
    seen = set()
    while reference.startswith('#'):
        if reference in seen:
            raise ValueError(f'Texture reference cycle: {reference}')
        seen.add(reference)
        reference = textures[reference[1:]]
    if not resource(reference, 'textures', '.png').is_file():
        raise ValueError(f'Missing texture: {reference}')


def main():
    paths = sorted((ASSETS / 'oak_aviary/models/item').rglob('*.json'))
    for path in paths:
        identifier = 'oak_aviary:' + path.relative_to(ASSETS / 'oak_aviary/models').with_suffix('').as_posix()
        try:
            data = model(identifier)
            texture('#particle', data['textures'])
            for element in data.get('elements', []):
                for face in element['faces'].values():
                    texture(face['texture'], data['textures'])
        except (KeyError, ValueError, OSError) as error:
            raise ValueError(f'{identifier}: {error}') from error
    if not paths:
        raise ValueError('No authored models found.')
    print(f'Pack references passed: {len(paths)} models, inherited particles and face textures.')


if __name__ == '__main__':
    main()
