"""Synthetic reference for palette normalization; not a BlueMap patch or world editor."""
from copy import deepcopy

def normalize(state, defaults):
    """Return a legacy-shaped state; explicit properties override version-specific defaults."""
    if isinstance(state, str):
        identifier, separator, properties = state.partition('[')
        explicit = dict(item.split('=', 1) for item in properties.rstrip(']').split(',')) if separator else {}
    elif isinstance(state, dict):
        identifier = state.get('Name', state.get('id', state.get('')))
        explicit = deepcopy(state.get('Properties', state.get('properties', {})))
    else:
        raise ValueError('Unsupported palette entry')
    if not identifier:
        raise ValueError('Missing block identifier')
    properties = {**defaults.get(identifier, {}), **explicit}
    return {'Name': identifier, **({'Properties': properties} if properties else {})}

def self_test():
    # Deliberately synthetic defaults, not a redistributed Mojang registry.
    defaults = {'example:log': {'axis': 'y'}, 'example:leaves': {'persistent': 'false', 'waterlogged': 'false'}}
    cases = [
        ('example:log', {'Name': 'example:log', 'Properties': {'axis': 'y'}}),
        ('example:log[axis=x]', {'Name': 'example:log', 'Properties': {'axis': 'x'}}),
        ({'': 'example:log'}, {'Name': 'example:log', 'Properties': {'axis': 'y'}}),
        ({'id': 'example:leaves', 'properties': {'persistent': 'true'}},
         {'Name': 'example:leaves', 'Properties': {'persistent': 'true', 'waterlogged': 'false'}}),
        ({'Name': 'example:log', 'Properties': {'axis': 'z'}},
         {'Name': 'example:log', 'Properties': {'axis': 'z'}}),
    ]
    for source, expected in cases:
        before = deepcopy(source)
        assert normalize(source, defaults) == expected
        assert normalize(expected, defaults) == expected
        assert source == before
    print('PASS: five synthetic shapes, explicit overrides, no input mutation, idempotence')

if __name__ == '__main__':
    self_test()
