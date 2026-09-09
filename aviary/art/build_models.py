"""Build the editable Blender scene and export the same cuboids as vanilla item models.

Run with Blender --background --python aviary/art/build_models.py.
Minecraft coordinates use X/right, Y/up, Z/back; one unit is one block.
"""
import bpy
import json
import math
from pathlib import Path
import random
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'art'
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
for name, color in PALETTE.items():
    rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    material = bpy.data.materials.new('Aviary.' + name)
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    material.diffuse_color = (*linear, 1)
    material.use_nodes = True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (*linear, 1)
    material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .88
    materials[name] = material
    texture = bpy.data.images.new('Aviary.' + name, width=16, height=16)
    rng = random.Random(name)
    pixels = []
    for y in range(16):
        for x in range(16):
            factor = 1 + rng.choice([-.07, -.03, 0, 0, 0, .03, .06])
            pixels.extend([min(1, c * factor) for c in rgb] + [1])
    texture.pixels = pixels
    texture.filepath_raw = str(ASSETS / 'textures/item' / (name + '.png'))
    texture.file_format = 'PNG'
    texture.save()

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
    return obj


part('body', (0, 0, 0))
cube('body', 'Chest', (0, .85, 0), (1.10, 1.0, 1.7), 'feather_light')
cube('body', 'Breast', (0, .9, -.65), (.9, .85, .5), 'chestnut')
cube('body', 'Back', (0, 1.18, .28), (1.12, .36, 1.30), 'chestnut')
for side in (-1, 1):
    for i in range(3):
        cube('body', 'Chest feathers', (side*.47, .55+i*.18, -.35+i*.15), (.22, .3, .65), 'feather')
    cube('body', 'Saddle strap', (side*.55, .83, .18), (.065, .85, .18), 'strap')
    cube('body', 'Buckle', (side*.59, .82, .18), (.05, .16, .2), 'gold')
cube('body', 'Saddle pad', (0, 1.39, .18), (.9, .13, .88), 'leather')
cube('body', 'Saddle seat', (0, 1.47, .18), (.7, .12, .68), 'strap')
cube('body', 'Saddle back', (0, 1.61, .55), (.8, .32, .15), 'leather')
cube('body', 'Saddle front', (0, 1.57, -.19), (.75, .21, .12), 'leather')

part('head', (0, 1.35, -.65))
cube('head', 'Neck', (0, 1.36, -.80), (.68, .65, .60), 'cream')
cube('head', 'Head', (0, 1.81, -1.02), (.83, .70, .76), 'ivory')
for side in (-1, 1):
    cube('head', 'Ruff side', (side*.4, 1.31, -.72), (.26, .3, .72), 'ivory')
    cube('head', 'Ruff rear', (side*.25, 1.3, -.40), (.3, .24, .3), 'cream')
    cube('head', 'Amber eye', (side*.425, 1.90, -1.20), (.035, .15, .18), 'gold')
    cube('head', 'Pupil', (side*.448, 1.90, -1.24), (.02, .09, .075), 'eye')
    cube('head', 'Brow', (side*.36, 2.01, -1.21), (.20, .09, .34), 'cream')
cube('head', 'Beak', (0, 1.69, -1.53), (.48, .30, .36), 'gold_light')
cube('head', 'Hook', (0, 1.54, -1.66), (.31, .25, .16), 'gold')

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
        cube(group, name+' primary', (x, 1.10, .45+i*.15), (.30, .11, .84-i*.025), 'feather' if i%2 else 'feather_light')
        if i<4:
            cube(group, name+' wing band', (x, 1.17, .38+i*.13), (.30, .08, .23), 'cream')

part('tail', (0, .68, .75))
for i in range(5):
    cube('tail', 'Tail feather', ((i-2)*.24, .66, 1.25+(.15 if i in (1,2,3) else 0)), (.29, .16, 1.10), 'feather' if i%2 else 'feather_light')
part('feet', (0, .25, .05))
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
        elements.append({'from': lo, 'to': hi, 'faces': {face: {'uv': [0,0,16,16], 'texture': '#'+c['material']} for face in ('up','down','north','south','east','west')}})
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
scene.render.fps = 24
scene.frame_end = 48
for name, spec in parts.items():
    obj = spec['object']
    for frame in (1, 13, 25, 37, 49):
        phase = (frame-1)/48*math.tau
        side = -1 if name.startswith('left') else 1
        obj.rotation_euler = (0, 0, 0)
        if 'wing' in name or 'tip' in name:
            obj.rotation_euler.y = side*(math.sin(phase)*.26 + .08)*(.18 if 'tip' in name else 1)
        obj.keyframe_insert(data_path='rotation_euler',frame=frame)
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
