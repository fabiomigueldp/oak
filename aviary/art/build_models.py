"""Build the editable Blender scene and export the same cuboids as vanilla item models.

Run with Blender --background --python aviary/art/build_models.py.
Minecraft coordinates use X/right, Y/up, Z/back; one unit is one block.
"""
import bpy
import json
import math
from pathlib import Path
import random
import struct
import sys
import zlib
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'art'
sys.path.insert(0, str(ART))
from motion import clips
motion = clips()
(ART/'condor-motion.json').write_text(json.dumps(motion, separators=(',', ':')), encoding='utf-8')
PACK = ROOT / 'pack'
ASSETS = PACK / 'assets/oak_aviary'
for name in ('models/item', 'items', 'textures/item'):
    (ASSETS / name).mkdir(parents=True, exist_ok=True)

PALETTE = {
    'feather': '#352d28', 'feather_light': '#4b3b30', 'chestnut': '#865638',
    'chestnut_light': '#a57049', 'cream': '#ddd4b6', 'ivory': '#f1e7c9',
    'gold': '#bf9448', 'gold_light': '#d9b367', 'eye': '#201d17',
    'leather': '#68432c', 'strap': '#3f3026', 'talon': '#332c22',
    'stone': '#777a67', 'oak': '#a48656', 'moss': '#556b3d', 'cloud': '#f1efdf',
}
materials = {}


def texture_png(path, rgb, name):
    """Original pixel art: directional feather vanes and quiet solid surfaces."""
    rng = random.Random(name)
    rows = bytearray()
    for y in range(16):
        rows.append(0)
        for x in range(16):
            noise = rng.choice((-.012, 0, 0, .012))
            if name in ('feather', 'feather_light', 'chestnut', 'chestnut_light'):
                vane = ((y + abs(x-7)) % 5 == 0)
                factor = 1 + noise + (.075 if x == 7 else -.055 if vane else 0)
            elif name in ('cream', 'ivory'):
                factor = 1 + noise * .4 - (.025 if (y + x//3) % 9 == 0 else 0)
            elif name in ('eye', 'talon'):
                factor = 1
            elif name in ('gold', 'gold_light'):
                factor = 1 + noise * .3 + (.025 if y < 5 else -.02 if y > 12 else 0)
            elif name in ('leather', 'strap'):
                factor = 1 + noise - (.025 if y % 8 == 0 else 0)
            else:
                factor = 1 + noise * 2
            rows.extend([round(min(1, c * factor)*255) for c in rgb] + [255])
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',16,16,8,6,0,0,0))
                     + chunk(b'IDAT', zlib.compress(bytes(rows),9)) + chunk(b'IEND', b''))


for name, color in PALETTE.items():
    rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    material = bpy.data.materials.new('Aviary.' + name)
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    material.diffuse_color = (*linear, 1)
    material.use_nodes = True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (*linear, 1)
    material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .88
    materials[name] = material
    texture_path = ASSETS / 'textures/item' / (name + '.png')
    texture_png(texture_path, rgb, name)
    texture = bpy.data.images.load(str(texture_path))
    texture.pack()
    node = material.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = texture
    node.interpolation = 'Closest'
    material.node_tree.links.new(node.outputs['Color'], material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])

scene = bpy.data.scenes.new('Oak Aviary')
if bpy.context.window:
    bpy.context.window.scene = scene
scene.unit_settings.system = 'METRIC'
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.render.resolution_x = 1440
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100
scene.world = bpy.data.worlds.new('Aviary studio')
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.43, .48, .40, 1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value = .65
parts = {}
root = bpy.data.objects.new('Condor', None)
scene.collection.objects.link(root)


def xyz(value):
    return (value[0], -value[2], value[1])


def part(name, pivot):
    obj = bpy.data.objects.new(name, None)
    obj.parent = root
    obj.location = xyz(pivot)
    scene.collection.objects.link(obj)
    parts[name] = {'pivot': list(pivot), 'cubes': [], 'object': obj}
    return name


def face_uv(size, face):
    # Eight texels per block on every face; do not stretch an entire tile onto
    # a narrow feather edge. Center the shaft on the feather's broad face.
    a, b = (0, 2) if face in ('up', 'down') else (0, 1) if face in ('north', 'south') else (2, 1)
    width, height = size[a]*8, size[b]*8
    return [round(8-width/2, 4), 0, round(8+width/2, 4), round(height, 4)]


def cube(group, name, center, size, material):
    spec = parts[group]
    local = [center[i] - spec['pivot'][i] for i in range(3)]
    spec['cubes'].append({'name': name, 'center': local, 'size': list(size), 'material': material})
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = name
    obj.parent = spec['object']
    obj.location = xyz(local)
    obj.scale = (size[0], size[2], size[1])
    obj.data.materials.append(materials[material])
    # Use the exported face UV extents in the editable scene as well.
    uv = obj.data.uv_layers.active.data
    for polygon in obj.data.polygons:
        normal = polygon.normal
        face = ('east' if normal.x > 0 else 'west') if abs(normal.x) > .5 else ('north' if normal.y > 0 else 'south') if abs(normal.y) > .5 else ('up' if normal.z > 0 else 'down')
        u0, v0, u1, v1 = face_uv(size, face)
        for loop_index in polygon.loop_indices:
            v = obj.data.vertices[obj.data.loops[loop_index].vertex_index].co
            x, y, z = v.x+.5, v.z+.5, .5-v.y
            u, w = {'up': (x,z), 'down': (x,1-z), 'north': (1-x,1-y), 'south': (x,1-y), 'east': (1-z,1-y), 'west': (z,1-y)}[face]
            uv[loop_index].uv = ((u0+u*(u1-u0))/16, 1-(v0+w*(v1-v0))/16)
    return obj


part('body', (0, 0, 0))
cube('body', 'Chest', (0, .89, .05), (1.02, .84, 1.48), 'feather_light')
cube('body', 'Breast', (0, 1.0, -.57), (.88, .76, .52), 'chestnut')
cube('body', 'Belly', (0, .56, .12), (.80, .30, 1.06), 'feather')
cube('body', 'Back', (0, 1.20, .24), (1.06, .30, 1.20), 'chestnut')
for side in (-1, 1):
    for i in range(3):
        cube('body', 'Chest feathers', (side*.47, .55+i*.18, -.35+i*.15), (.22, .3, .65), 'feather')
    cube('body', 'Saddle strap', (side*.55, .83, .18), (.065, .85, .18), 'strap')
    cube('body', 'Buckle', (side*.59, .82, .18), (.05, .16, .2), 'gold')
cube('body', 'Saddle pad', (0, 1.39, .18), (.9, .13, .88), 'leather')
cube('body', 'Saddle seat', (0, 1.47, .18), (.7, .12, .68), 'strap')
cube('body', 'Saddle back', (0, 1.61, .55), (.8, .32, .15), 'leather')
cube('body', 'Saddle front', (0, 1.57, -.19), (.75, .21, .12), 'leather')
for side in (-1, 1):
    cube('body', 'Saddle piping', (side*.43, 1.44, .18), (.045, .045, .86), 'gold')

part('head', (0, 1.35, -.65))
cube('head', 'Neck', (0, 1.36, -.80), (.68, .65, .60), 'cream')
cube('head', 'Head', (0, 1.79, -1.02), (.79, .62, .72), 'ivory')
for side in (-1, 1):
    cube('head', 'Ruff side', (side*.4, 1.31, -.72), (.26, .3, .72), 'ivory')
    cube('head', 'Ruff rear', (side*.25, 1.3, -.40), (.3, .24, .3), 'cream')
    cube('head', 'Amber eye', (side*.40, 1.88, -1.18), (.025, .15, .18), 'gold')
    cube('head', 'Pupil', (side*.419, 1.88, -1.215), (.018, .095, .085), 'eye')
    cube('head', 'Eye glint', (side*.432, 1.91, -1.239), (.009, .025, .022), 'ivory')
    cube('head', 'Brow', (side*.35, 1.99, -1.18), (.16, .07, .27), 'cream')
cube('head', 'Beak', (0, 1.68, -1.49), (.44, .24, .36), 'gold_light')
cube('head', 'Hook', (0, 1.55, -1.64), (.27, .23, .16), 'gold')
cube('head', 'Lower beak', (0, 1.52, -1.48), (.33, .055, .24), 'gold')

for side, name in [(-1, 'left'), (1, 'right')]:
    group = part(name + '_wing', (side*.48, 1.13, .04))
    cube(group, name+' shoulder', (side*1.01, 1.16, .08), (1.18, .27, .70), 'chestnut')
    for i in range(5):
        x = side*(.66+i*.23)
        cube(group, name+' covert', (x, 1.25, .30+i*.065), (.28, .13, .63), 'chestnut_light' if i%2 else 'chestnut')
        cube(group, name+' secondary', (x, 1.10, .72+i*.06), (.28, .12, .69), 'cream' if i==1 else 'feather_light')
    group = part(name+'_tip', (side*1.65, 1.15, .06))
    cube(group, name+' forewing', (side*2.11, 1.15, .10), (.95, .20, .58), 'chestnut')
    for i in range(6):
        x = side*(1.83+i*.25)
        length = (.84, .94, 1.06, 1.12, 1.05, .86)[i]
        cube(group, name+' primary', (x, 1.10-i*.012, .45+i*.15), (.285, .10, length), 'feather' if i%2 else 'feather_light')
        if i<4:
            cube(group, name+' wing band', (x, 1.17, .38+i*.13), (.30, .08, .23), 'cream')

part('tail', (0, .68, .75))
for i in range(5):
    cube('tail', 'Tail feather', ((i-2)*.24, .66-abs(i-2)*.012, 1.25+.08*(2-abs(i-2))), (.27, .12, 1.02+.06*(2-abs(i-2))), 'feather' if i%2 else 'feather_light')
part('feet', (0, .52, .05))
for side in (-1, 1):
    cube('feet', 'Leg', (side*.33, .35, -.14), (.22, .5, .25), 'gold')
    for i in range(3):
        cube('feet', 'Toe', (side*.33+(i-1)*.12, .07, -.36), (.105, .14, .55), 'gold')
        cube('feet', 'Claw', (side*.33+(i-1)*.12, .04, -.66), (.105, .09, .16), 'talon')

# The model exporter uses the authored cuboid coordinates directly. Blender-only
# preview lighting and materials never become gameplay dependencies.
rig = {}
for name, spec in parts.items():
    elements = []
    for c in spec['cubes']:
        lo = [round(8+(c['center'][i]-c['size'][i]/2)*4, 5) for i in range(3)]
        hi = [round(8+(c['center'][i]+c['size'][i]/2)*4, 5) for i in range(3)]
        assert all(-16 <= a <= 32 for a in lo+hi), name
        elements.append({'from': lo, 'to': hi, 'faces': {face: {'uv': face_uv(c['size'], face), 'texture': '#'+c['material']} for face in ('up','down','north','south','east','west')}})
    model = {'textures': {m: 'oak_aviary:item/'+m for m in PALETTE}, 'elements': elements, 'gui_light': 'front'}
    (ASSETS/'models/item'/f'{name}.json').write_text(json.dumps(model, separators=(',',':')), encoding='utf-8')
    (ASSETS/'items'/f'{name}.json').write_text(json.dumps({'model': {'type':'minecraft:model','model':'oak_aviary:item/'+name}}), encoding='utf-8')
    rig[name] = {'pivot':spec['pivot'], 'cubes':spec['cubes'], 'scale':4}
(ART/'condor-rig.json').write_text(json.dumps(rig,indent=2),encoding='utf-8')

# Outer wing joints inherit their upper wing's motion.
for side in ('left', 'right'):
    tip, wing = parts[side+'_tip'], parts[side+'_wing']
    tip['object'].parent = wing['object']
    tip['object'].location = xyz([tip['pivot'][i]-wing['pivot'][i] for i in range(3)])
scene.render.fps = 20
scene.frame_end = motion['period']
for name, spec in parts.items():
    obj = spec['object']
    for index, sample in enumerate(motion['clips']['power'] + [motion['clips']['power'][0]]):
        frame = index*2+1
        wing, tip, head, tail, feet, bank = sample
        side = -1 if name.startswith('left') else 1
        obj.rotation_euler = (0, 0, 0)
        if 'wing' in name: obj.rotation_euler.y = -side*wing
        if 'tip' in name: obj.rotation_euler.y = -side*tip
        if name == 'head': obj.rotation_euler.x = head
        if name == 'tail': obj.rotation_euler.x = tail
        if name == 'feet': obj.rotation_euler.x = feet
        obj.keyframe_insert(data_path='rotation_euler',frame=frame)
    for curve in obj.animation_data.action.layers[0].strips[0].channelbag(obj.animation_data.action_slot).fcurves:
        for key in curve.keyframe_points: key.interpolation = 'LINEAR'
# Bank around the saddle attachment, keeping the passenger's seat fixed.
root.location = xyz((0, 1.47, .18))
for name, spec in parts.items():
    if not name.endswith('_tip'):
        spec['object'].location = xyz([spec['pivot'][i]-(0,1.47,.18)[i] for i in range(3)])
for index, sample in enumerate(motion['clips']['power'] + [motion['clips']['power'][0]]):
    root.rotation_euler.y = -sample[5]
    root.keyframe_insert(data_path='rotation_euler', frame=index*2+1)
for curve in root.animation_data.action.layers[0].strips[0].channelbag(root.animation_data.action_slot).fcurves:
    for key in curve.keyframe_points: key.interpolation = 'LINEAR'
scene.frame_set(7)

# A separate buildable port model. Keep the complete approach edge unobstructed.
port = bpy.data.collections.new('Aviport construction')
scene.collection.children.link(port)
def block(name, position, scale, mat):
    bpy.ops.mesh.primitive_cube_add(size=1, location=position)
    obj=bpy.context.object;obj.name=name;obj.scale=scale;obj.data.materials.append(materials[mat])
    for collection in list(obj.users_collection):collection.objects.unlink(obj)
    port.objects.link(obj)
for x in range(-4,5):
    for y in range(-4,5):
        block('Deck block',(x,y,-.51),(.98,.98,1),'oak')
        if abs(x)==4 or abs(y)==4:block('Foundation',(x,y,-1.1),(.98,.98,.2),'stone')
for x in (-4,4):
    for y in (-3,0,3):block('Side post',(x,y,.1),(.25,.25,1.2),'oak')
    for y in (-1.5,1.5):block('Side rail',(x,y,.5),(.12,2.7,.16),'oak')
for y,z in ((-5,-.85),(-6,-1.25)):
    block('Rear stair',(0,y,z),(2,1,.35),'stone')
block('Bell post',(3,-3,1),(.25,.25,3),'oak')
block('Bell arm',(2.65,-3,2.45),(.9,.25,.25),'oak')
block('Bell',(2.4,-3,2.05),(.4,.4,.5),'gold')

camera_data=bpy.data.cameras.new('Aviary camera')
camera=bpy.data.objects.new('Aviary camera',camera_data);scene.collection.objects.link(camera)
camera.location=(8,11,7)
camera.rotation_euler=(Vector((0,0,.8))-camera.location).to_track_quat('-Z','Y').to_euler()
camera_data.type='ORTHO';camera_data.ortho_scale=9.5;scene.camera=camera
for name,location,power,size in [('Key',(-4,2,9),1400,7),('Fill',(5,-2,5),700,5)]:
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size
    obj=bpy.data.objects.new(name,data);scene.collection.objects.link(obj);obj.location=location
    obj.rotation_euler=(Vector((0,0,1))-obj.location).to_track_quat('-Z','Y').to_euler()
scene.render.image_settings.file_format='PNG'
scene.render.filepath=str(ART/'condor-preview.png')
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=str(ART/'condor.blend'))
bpy.ops.render.render(write_still=True)
print(json.dumps({'blend':str(ART/'condor.blend'),'parts':len(parts),'cuboids':sum(len(v['cubes']) for v in parts.values()),'pack':str(PACK)}))
