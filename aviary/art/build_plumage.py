"""Two restrained plumage palettes; native model parents share all geometry."""
import json
from pathlib import Path
import random
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
def feather_texture(path, color, material):
    rgb = [int(color[n:n+2], 16) for n in (1, 3, 5)]
    rng = random.Random(material)
    rows = bytearray()
    for y in range(16):
        rows.append(0)
        for x in range(16):
            vane = (y + abs(x-7)) % 5 == 0
            factor = 1 + rng.choice((-.012, 0, 0, .012)) + (.075 if x == 7 else -.055 if vane else 0)
            rows.extend([max(0, min(255, round(c*factor))) for c in rgb] + [255])
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',16,16,8,6,0,0,0))
                     + chunk(b'IDAT', zlib.compress(bytes(rows),9)) + chunk(b'IEND', b''))

PALETTES = {
    'ash': {'feather': '#34393b', 'feather_light': '#4c514e', 'chestnut': '#72776c', 'chestnut_light': '#949781'},
    'amber': {'feather': '#433128', 'feather_light': '#5c4230', 'chestnut': '#986842', 'chestnut_light': '#b88753'},
}
ASSETS = ROOT/'pack/assets/oak_aviary'
parts = json.loads((ROOT/'art/condor-rig.json').read_text(encoding='utf-8'))
for palette, colors in PALETTES.items():
    for material, color in colors.items():
        feather_texture(ASSETS/'textures/item'/f'{palette}_{material}.png', color, material)
    for part in parts:
        model = {'parent': 'oak_aviary:item/'+part, 'textures': {material: f'oak_aviary:item/{palette}_{material}' for material in colors}}
        for folder in ('models/item', 'items'):
            (ASSETS/folder/palette).mkdir(parents=True, exist_ok=True)
        (ASSETS/'models/item'/palette/f'{part}.json').write_text(json.dumps(model, separators=(',', ':'))+'\n', encoding='utf-8')
        (ASSETS/'items'/palette/f'{part}.json').write_text(json.dumps({'model': {'type': 'minecraft:model', 'model': f'oak_aviary:item/{palette}/{part}'}}, separators=(',', ':'))+'\n', encoding='utf-8')
print('Exported ash and amber plumage; geometry and bone pivots are unchanged.')
