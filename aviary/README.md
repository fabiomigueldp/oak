# Oak Aviary

Server-side Fabric transportation for unmodified Minecraft Java 26.3-pre-3.
The optional resource pack contains original condor models and native screen
effects. Bedrock sessions are excluded through the installed Floodgate API;
declining the Java pack never prevents joining the server.

## Use

Build a level landing deck with a solid 3 × 3 center and a clear 7 × 7 area,
24 blocks high. A 9 × 9 deck leaves room for rails outside the landing area.
Stand at its center, then use:

| Command | Action |
| --- | --- |
| `/aviary claim home` | Register a private aviport; two per player |
| `/aviary name home Home` | Change its display name |
| `/aviary share home true` | Let other players use it |
| `/aviary share home false` | Make it private again |
| `/aviary unclaim home` | Remove your aviport registration |
| `/aviary` | Open the native destination menu |
| `/aviary fly home` | Depart for a visible destination |
| `/aviary skip` | Request the abbreviated route safely |
| `/aviary pack` | Request the optional resource pack again |
| `/aviary port add spawn` | Administrator: register a shared aviport |
| `/aviary port remove spawn` | Administrator: remove a registration |
| `/aviary status` | Administrator: inspect service state |

Hold Shift during travel to request a safe skip. Travel starts only within four
blocks of an accessible aviport. Private registrations control route access;
they are not a land-claim or block-protection system. No structures are placed
automatically. Only overworld routes are supported in this release.

## Implementation

The bird has eight native item displays and 100 cuboids. The real player rides an
invisible carrier; a separate invisible anchor supplies camera position. Because
the vanilla client hides its local player with an external camera, an owner-only
native mannequin renders the passenger's skin and equipment. It is never added to
the server world, player list or saved data. Observers see the real player. No
inventory replacement, spectator switch or player-ability change is used.
Model poses update every two server ticks with native client interpolation.
The simulation does not run mob AI or scan the whole world.

Version 0.1.2 uses shared, sampled rest/glide/powered-flight clips. The wing cycle
continues across phase changes; nine-tick exponential blending changes flight
intensity without snapping to a new pose. The wrist follows the shoulder, feet
retract with altitude, and head/tail motion is independent of the wing angle.
Banking pivots around the saddle attachment. Flap audio follows the downstroke and
stays silent during gliding or boarding. These changes keep the same entity count
and ten pose updates per second; six joint channels are interpolated from a small
table loaded once, rather than running a skeletal animation engine.

Routes above 500 horizontal blocks use a fade and relocation near the destination.
Short routes use a loaded, unobstructed corridor; unavailable corridors use the
same cut. Endpoint chunks are loaded before departure. Route tickets are bounded,
reference-counted and released after landing. The initial limit is two concurrent
flights, configurable up to four. The threshold can be reduced to 100 blocks.
This is a resource budget, not a measured guarantee for every client or server.

An atomic, fsynced recovery journal precedes boarding and transfer. Damage and
gameplay interactions are blocked while travelling. Departure/arrival ports are
reserved for one flight. All exits restore the camera and remove only Aviary's
screen effects. Recovery searches for a safe nearby landing if a deck has changed;
when no safe location exists, the player is disconnected with an explanation
instead of being dropped into danger. Runtime policy, owners and journals remain
outside Git under `config/oak-aviary`.

The screen fade uses the native post-effect feature introduced in
[26.3 Snapshot 3](https://www.minecraft.net/en-us/article/minecraft-26-3-snapshot-3),
with the existing blit shader. It does not replace core shaders. Compatibility is
pinned to the installed pre-release; a Minecraft upgrade requires rebuilding and
checking the model format, mixins, camera and effect APIs again.

## Art

- [Concepts and exact prompts](art/concepts/README.md)
- [Editable Blender scene](art/condor.blend)
- [Blender preview](art/condor-preview.png)
- [Geometry and texture generator](art/build_models.py)
- [Exported rig](art/condor-rig.json)
- [Shared motion authoring](art/motion.py)
- [Runtime motion samples](art/condor-motion.json)

Run Blender with `--background --factory-startup --python aviary/art/build_models.py`.
This creates a separate scene and exports matching vanilla cuboid models. The
construction study leaves the front of the deck open, correcting a fence in the
generated concept. No OBJ/glTF loader or third-party model plugin is required.
The Blender scene uses the same packed pixel textures, face UV extents and motion
samples as the resource pack and server rig. Its 20 fps timeline previews one
powered-flight cycle; the game also blends rest/glide clips according to phase and
altitude. Lighting is a studio preview, not a simulation of Minecraft lighting.
Textures use eight texels per world block, directional feather vanes, quieter
head/beak surfaces and solid pupils; thin feather edges no longer stretch an
entire 16-pixel tile. A smaller breast/belly silhouette, tapered primary feathers,
eye highlights and saddle trim complete this pass.

## Build and install

Build on the pinned Linux runtime with a Java 25 JDK:

```sh
python3 aviary/build.py --server /srv/oak/server --jdk /path/to/jdk --output /tmp/aviary-build
```

The output contains the mod, deterministic resource pack and hashes. The installer
requires a clean, reviewed commit; it publishes a content-addressed pack, verifies
the public HTTPS hash and stages the server mod. It does not restart Minecraft:

```sh
sudo python3 scripts/install-aviary.py <full-commit-sha> --jdk /path/to/jdk
```

Restart Minecraft separately under the server's normal maintenance procedure.
Rollback: restore the retained `previous.jar` and `previous-settings.json` from
`/opt/oak-aviary/releases/<commit>`, then restart. For a first installation, disable
Aviary in runtime settings and restart; recover pending journeys before removing
the mod. Keep old content-addressed packs available for cached sessions.

## Evidence and remaining work

The exact-version isolated server test exercises a short route, a long route,
interruption, inventory preservation, camera/effect packets, and temporary-entity
cleanup. Its fake connection does not render frames. Build with `--smoke` and use
`tests/run_smoke.py` only inside a fresh `unshare --net` namespace. The test artifact
refuses startup without its isolated-test property and is rejected by the installer.

Version 0.1.1 corrects the implicit Y(180 degrees) rotation in the native item
renderer, which previously folded independently positioned parts into the body.
The regression runs every non-tail cube vertex through the display metadata and
client render basis. Camera head yaw is synchronized explicitly; the camera
anchor's distance attribute also keeps the subject in front of the mirrored F5
view. First person, rear third person and front third person have different
framing because the vanilla client's F5 setting remains under player control.
The owner-only mannequin uses the native skin/equipment capabilities documented
in [Minecraft 1.21.9](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9).
The animation regression also checks a complete boarding/departure/cruise/landing
sequence through native display matrices: attached wrists, fixed saddle position,
per-update displacement, deck clearance and the reserved flight envelope.

Actual vanilla-client camera framing, F5 behavior, input, GPU effects, skin/armor
clearance, chunk delivery under latency, and Bedrock coexistence still need an
in-game acceptance pass. The server cannot prove that a resource-pack post effect
rendered on a client's GPU. Do not describe the current build as a verified AAA
cinematic or promise invisible cuts under every connection condition.

Oak's existing console can inspect the service with `aviary status`. A dedicated
Oak route editor, map destination selection, more varied approach shots and
additional birds remain separate work; they are not implemented by this release.
