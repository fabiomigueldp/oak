# Aviary travel objects

The Perch is a low wooden support with leather foot pads, copper collars, rivets,
and a split woven cloth. It spans 1.52 blocks and rises 0.75 blocks above an
existing full support block. Placement does not replace terrain. The bird root
is the beam top; passenger boarding and dismounting use separate safe positions.

Three wood finishes (oak, spruce, birch) and sixteen quiet cloth colors share the
same geometry. Two native item displays render the wood and cloth; one native
interaction entity provides the click target. Displays are scenery, not collision.
The existing Minecraft support block supplies physical collision and support.
Placement respects the player's build ability, native spawn protection and
interaction permissions. Magma blocks cannot support a perch.

The Whistle is carved bone with a copper collar, mouthpiece, air opening and a
short leather cord. Both inventory objects have dedicated GUI and hand transforms.

## Build

- `python aviary/art/build_perches.py` exports original pixel textures, models,
  item definitions and recipes.
- `blender --background --factory-startup --python aviary/art/build_perches.py`
  also creates `perches.blend`, with separate collections and packed textures.
- `python aviary/art/build_plumage.py` exports ash and amber condor palettes.
  Parent models share the original geometry and bone pivots.
- `python aviary/art/build_sounds.py --calls-only` creates the original short
  whistle and reply sounds without rebuilding existing flight foley.

## Recipes and vanilla compatibility

Perch: two copper ingots, one leather, one stick and three planks, arranged as
`CLC / _S_ / PPP`. Whistle: bone, copper and string, arranged as `BC / _S`.
The server awards both recipes on Java login. Recipes are bundled in the mod's
native `data/oak_aviary/recipe` resources.

Both items use the vanilla disc-fragment-5 carrier with `item_model`, `item_name`,
`custom_data` and `max_stack_size: 1`. No new item or block registry entry is sent
to clients. An Ingredient mixin rejects marked travel objects as recipe inputs;
ordinary fragments are unaffected. Unlike paper or dyes, this carrier has no
cartography, brewing, furnace or block-placement behavior to intercept.

## Runtime ownership and lifecycle

`settings.json` stores anchor location, identity, owner and appearance. Items are
only requests. A packed kit carries a destination ID, but placement independently
requires an inactive destination and its owner. Once placed, every duplicate
pointer becomes unusable. Names and favorites survive moving.

Packing persists the inactive state before issuing a kit and requires an empty
inventory slot. A lost kit can be replaced through the packed-perches menu using
a newly crafted kit. This converts that kit in its existing inventory slot.

Only loaded anchors within 72 blocks of a player have scenery. Scenery is marked
transient and rebuilt after chunk unload or restart. A missing support immediately
fails departure checks and removes scenery during the one-second lifecycle pass;
the address is durably marked inactive and appears in the owner's packed-perches
menu. Replacing a destroyed perch uses a newly crafted kit and retains its name,
guests and favorites. All missing loaded supports are retired in one state write
per lifecycle pass; persistence failure rolls the batch back and retries after
five seconds. Unloaded supports are never retired. Explosions and
pistons cannot create a second anchor because identity stays at the stored position.
Breaking a support during an active route is refused. Held placement previews use
owner-only particles at 2 Hz and stop when the kit is put away.

An explicit kit placement within six blocks of an owned legacy destination
attaches its existing ID, name, access and favorites to the new support location.
This requires an idle destination and the same placement validation as a new one.
