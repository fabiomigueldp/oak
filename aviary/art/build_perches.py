"""Author compact travel objects and export vanilla models plus an editable scene.

Run with Python to export assets, or Blender --background --factory-startup
--python aviary/art/build_perches.py to also save perches.blend. The original
pixel textures and cuboids are shared by the client pack and Blender scene.
"""
import json
from pathlib import Path
import random
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'pack/assets/oak_aviary'
DATA = ROOT / 'data/oak_aviary'
for folder in ('models/item', 'items', 'textures/item'):
    (ASSETS / folder).mkdir(parents=True, exist_ok=True)

PALETTE = {
    'perch_oak': '#9b754b', 'perch_spruce': '#624b39', 'perch_birch': '#c1ad7c',
    'perch_copper': '#b97b54', 'perch_edge': '#d59b6a', 'perch_leather': '#544233',
    'whistle_bone': '#d9c6a0', 'whistle_dark': '#443d31',
    'cloth_white': '#d7d3c4', 'cloth_orange': '#bc7d45', 'cloth_magenta': '#a06c96',
    'cloth_light_blue': '#82b2bb', 'cloth_yellow': '#cbb46e', 'cloth_lime': '#94ae6b',
    'cloth_pink': '#c79599', 'cloth_gray': '#5d6766', 'cloth_light_gray': '#a5aba0',
    'cloth_cyan': '#568f91', 'cloth_purple': '#857899', 'cloth_blue': '#587b9b',
    'cloth_brown': '#80694f', 'cloth_green': '#758b5c', 'cloth_red': '#aa6156', 'cloth_black': '#3b4441',
}


def texture(name, color):
    rgb = tuple(int(color[n:n+2], 16) for n in (1, 3, 5))
    rng = random.Random(name)
    data = bytearray()
    for y in range(16):
        data.append(0)
        for x in range(16):
            grain = -.055 if (x + y//7) % 6 == 0 else .025 if x % 7 == 2 else 0
            if name.startswith('cloth_'):
                grain = (.025 if (x+y) % 2 == 0 else -.015) - (.1 if x in (1, 14) else 0)
            elif 'copper' in name or 'edge' in name:
                grain = .045 if y < 3 else -.025 if y > 12 else 0
            elif 'leather' in name:
                grain = -.035 if y in (1, 14) else 0
            f = 1 + grain + rng.choice((-.015, 0, 0, .015))
            data.extend([max(0, min(255, round(c*f))) for c in rgb] + [255])
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind+value))
    (ASSETS/'textures/item'/f'{name}.png').write_bytes(
        b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 16, 16, 8, 6, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(bytes(data), 9)) + chunk(b'IEND', b''))


def box(name, low, high, material):
    return {'name': name, 'low': low, 'high': high, 'material': material}


def perch(style):
    wood = 'perch_' + style
    parts = [box('Sole', (-.49, 0, -.23), (.49, .085, .23), wood)]
    for sign, side in ((-1, 'Left'), (1, 'Right')):
        x = sign*.34
        parts += [
            box(side+' post', (x-.085, .085, -.085), (x+.085, .58, .085), wood),
            box(side+' shoe', (x-.10, .085, -.10), (x+.10, .16, .10), 'perch_copper'),
            box(side+' brace', (x-.16, .45, -.10), (x+.16, .58, .10), wood),
            box(side+' leather', (x-.16, .715, -.19), (x+.16, .75, .19), 'perch_leather'),
            box(side+' collar', (sign*.66-.055, .55, -.19), (sign*.66+.055, .72, .19), 'perch_copper'),
            box(side+' rivet', (sign*.66-.026, .59, -.204), (sign*.66+.026, .645, -.19), 'perch_edge'),
        ]
    parts += [box('Beam', (-.76, .555, -.175), (.76, .715, .175), wood),
              box('Cloth clasp', (-.07, .52, -.225), (.07, .595, -.188), 'perch_copper'),
              box('Cloth pin', (-.022, .545, -.238), (.022, .585, -.215), 'perch_edge')]
    return parts


def cloth(color):
    material = 'cloth_' + color
    return [box('Woven cloth', (-.175, .275, -.205), (.175, .555, -.18), material),
            box('Left hem', (-.175, .22, -.205), (-.025, .275, -.18), material),
            box('Right hem', (.025, .22, -.205), (.175, .275, -.18), material)]


whistle = [
    box('Carved chamber', (-.22, .08, -.10), (.17, .245, .10), 'whistle_bone'),
    box('Mouthpiece', (.17, .11, -.06), (.35, .19, .06), 'whistle_bone'),
    box('Lip', (.30, .105, -.065), (.35, .195, .065), 'perch_copper'),
    box('Air window', (.065, .245, -.055), (.145, .255, .055), 'whistle_dark'),
    box('Collar', (-.175, .075, -.108), (-.12, .25, .108), 'perch_copper'),
    box('Cord upper', (-.38, .195, -.022), (-.22, .225, .022), 'perch_leather'),
    box('Cord lower', (-.38, .10, -.022), (-.22, .13, .022), 'perch_leather'),
    box('Cord end', (-.40, .10, -.022), (-.37, .225, .022), 'perch_leather'),
]


def export(name, parts, display=None):
    elements = []
    for part in parts:
        low, high = part['low'], part['high']
        size = [high[n]-low[n] for n in range(3)]
        faces = {}
        for face in ('up', 'down', 'north', 'south', 'east', 'west'):
            a, b = (0, 2) if face in ('up', 'down') else (0, 1) if face in ('north', 'south') else (2, 1)
            width, height = min(16, size[a]*16), min(16, size[b]*16)
            faces[face] = {'texture': '#'+part['material'], 'uv': [round(8-width/2, 3), 0, round(8+width/2, 3), round(height, 3)]}
        elements.append({'from': [round(8+n*16, 4) for n in low], 'to': [round(8+n*16, 4) for n in high], 'faces': faces})
    model = {'textures': {p['material']: 'oak_aviary:item/'+p['material'] for p in parts}, 'elements': elements, 'gui_light': 'side'}
    model['textures']['particle'] = '#'+parts[0]['material']
    if display:
        model['display'] = display
    (ASSETS/'models/item'/f'{name}.json').write_text(json.dumps(model, separators=(',', ':'))+'\n', encoding='utf-8')
    (ASSETS/'items'/f'{name}.json').write_text(json.dumps({'model': {'type': 'minecraft:model', 'model': 'oak_aviary:item/'+name}}, separators=(',', ':'))+'\n', encoding='utf-8')


for name, color in PALETTE.items():
    texture(name, color)
for style in ('oak', 'spruce', 'birch'):
    export('perch_'+style, perch(style))
for color in (n[6:] for n in PALETTE if n.startswith('cloth_')):
    export('perch_cloth_'+color, cloth(color))
export('perch', perch('oak')+cloth('green'), {
    'gui': {'rotation': [25, 225, 0], 'translation': [0, -3, 0], 'scale': [.55, .55, .55]},
    'ground': {'translation': [0, 2, 0], 'scale': [.28, .28, .28]},
    'firstperson_righthand': {'rotation': [0, 30, 0], 'translation': [1, -2, -1], 'scale': [.4, .4, .4]},
    'thirdperson_righthand': {'rotation': [75, 0, 0], 'translation': [0, 2, 0], 'scale': [.35, .35, .35]},
})
export('whistle', whistle, {
    'gui': {'rotation': [25, 205, 10], 'translation': [0, -2, 0], 'scale': [1.3, 1.3, 1.3]},
    'ground': {'translation': [0, 2, 0], 'scale': [.65, .65, .65]},
    'firstperson_righthand': {'rotation': [0, 30, 0], 'translation': [1, 0, -1], 'scale': [.8, .8, .8]},
    'thirdperson_righthand': {'rotation': [75, 0, 0], 'translation': [0, 2, 0], 'scale': [.75, .75, .75]},
})

for kind, pattern, ingredients in (
    ('perch', ['CLC', ' S ', 'PPP'], {'C': 'minecraft:copper_ingot', 'L': 'minecraft:leather', 'S': 'minecraft:stick', 'P': '#minecraft:planks'}),
    ('whistle', ['BC', ' S'], {'B': 'minecraft:bone', 'C': 'minecraft:copper_ingot', 'S': 'minecraft:string'}),
):
    recipe = {'type': 'minecraft:crafting_shaped', 'category': 'equipment', 'pattern': pattern, 'key': ingredients,
              'result': {'id': 'minecraft:disc_fragment_5', 'count': 1, 'components': {
                  'minecraft:item_name': kind.title(), 'minecraft:item_model': 'oak_aviary:'+kind,
                  'minecraft:custom_data': {'oak_aviary': kind}, 'minecraft:max_stack_size': 1}}}
    (DATA/'recipe').mkdir(parents=True, exist_ok=True)
    (DATA/'recipe'/f'{kind}.json').write_text(json.dumps(recipe, indent=2)+'\n', encoding='utf-8')


def blender_scene():
    import bpy
    from mathutils import Vector
    scene = bpy.data.scenes.new('Aviary travel objects')
    if bpy.context.window:
        bpy.context.window.scene = scene
    scene.unit_settings.system = 'METRIC'
    materials = {}
    for name in PALETTE:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        image = bpy.data.images.load(str(ASSETS/'textures/item'/f'{name}.png'))
        image.pack()
        tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
        tex.image = image
        tex.interpolation = 'Closest'
        mat.node_tree.links.new(tex.outputs['Color'], mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
        mat.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = .82
        materials[name] = mat
    # Separate collections preserve useful object names and original dimensions.
    for label, parts, offset in [('Oak perch', perch('oak')+cloth('green'), (0, 0, 0)), ('Spruce perch', perch('spruce')+cloth('blue'), (2.2, 0, 0)), ('Birch perch', perch('birch')+cloth('red'), (-2.2, 0, 0)), ('Whistle', whistle, (0, -1.2, 0))]:
        collection = bpy.data.collections.new(label)
        scene.collection.children.link(collection)
        for part in parts:
            lo, hi = part['low'], part['high']
            vertices = [(x+offset[0], -z+offset[1], y+offset[2]) for x, y, z in [(lo[0],lo[1],lo[2]),(hi[0],lo[1],lo[2]),(hi[0],hi[1],lo[2]),(lo[0],hi[1],lo[2]),(lo[0],lo[1],hi[2]),(hi[0],lo[1],hi[2]),(hi[0],hi[1],hi[2]),(lo[0],hi[1],hi[2])]]
            faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(3,7,6,2),(0,4,7,3),(1,2,6,5)]
            mesh = bpy.data.meshes.new(part['name'])
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            uv = mesh.uv_layers.new()
            size = [hi[n]-lo[n] for n in range(3)]
            for polygon, axes in zip(mesh.polygons, [(0,1),(0,1),(0,2),(0,2),(2,1),(2,1)]):
                width, height = min(1,size[axes[0]]), min(1,size[axes[1]])
                left, right = .5-width/2, .5+width/2
                for loop, pair in zip(polygon.loop_indices, [(left,1),(right,1),(right,1-height),(left,1-height)]):
                    uv.data[loop].uv = pair
            obj = bpy.data.objects.new(part['name'], mesh)
            collection.objects.link(obj)
            obj.data.materials.append(materials[part['material']])
    scene.world = bpy.data.worlds.new('Warm daylight')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.38, .43, .36, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .7
    camera_data = bpy.data.cameras.new('Objects camera')
    camera = bpy.data.objects.new('Objects camera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = (4, -6, 4)
    camera.rotation_euler = (Vector((0, 0, .3))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = 7
    scene.camera = camera
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'art/perches.blend'))


try:
    import bpy
except ImportError:
    print('Exported Aviary travel objects and recipes. Use Blender to save the editable scene.')
else:
    blender_scene()
