"""Author cyclic joint curves once for Blender and the native display rig.

Angles are radians in Minecraft coordinates. Samples are two server ticks apart.
The shoulder's downstroke is shorter than its recovery; the wrist follows later.
"""
import math

CHANNELS = ('wing', 'tip', 'head', 'tail', 'feet', 'bank')
PERIOD = 48


def stroke(t):
    t %= 1
    if t < .38:
        return math.cos(math.pi * t / .38)
    return -math.cos(math.pi * (t - .38) / .62)


def clips():
    result = {name: [] for name in ('rest', 'glide', 'power')}
    for tick in range(0, PERIOD, 2):
        t = tick / PERIOD
        wave = math.sin(math.tau * t)
        result['rest'].append([.08 + wave * .012, .04, wave * .012, -.03, 0, 0])
        result['glide'].append([.10 + wave * .035, .05 + math.sin(math.tau*(t-.12))*.04,
                                wave * .008, -.04 + wave * .012, .95, wave * .009])
        result['power'].append([.18 + stroke(t) * .48, .06 + stroke(t-.11) * .20,
                                -stroke(t-.05)*.018, -.06 + stroke(t-.17)*.045,
                                .95 + wave*.035, wave * .012])
    return {'period': PERIOD, 'channels': CHANNELS,
            'clips': {k: [[round(x, 7) for x in row] for row in v] for k, v in result.items()}}
