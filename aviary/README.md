# Oak Aviary

Current release: [0.4.0 perches, whistles and shared journeys](RELEASE-0.4.md).

Server-side Fabric transportation for unmodified Minecraft Java 26.3-rc-1.
The optional resource pack contains original condor models and native screen
effects. Bedrock sessions are excluded through the installed Floodgate API;
declining the Java pack never prevents joining the server.

## Use

Craft a **Perch** and a **Whistle** using the recipes unlocked on Java login.
Place the perch on a safe solid block in the Overworld; the temporary preview
shows its direction, resting space and dismount point. Use it to name your
destination, choose access and customize its appearance. Use the whistle to
choose a destination, then interact with the arriving bird's saddle to board.
No commands or prescribed landing platform are required. The bird still needs
a clear checked approach and room to open its wings after leaving the support.

Use **Fly with a friend** to invite a nearby Java player. Each rider boards their
own bird; departure and landing are staggered. Field pickup is available from
nearby safe outdoor ground, when enabled by the server's network policy.

The following commands remain available for compatibility and administration:

| Command | Action |
| --- | --- |
| `/aviary claim home` | Register a legacy private destination |
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

Hold Shift for 0.4 seconds while riding to request a safe skip. Private
registrations control route access; they are not a land-claim system. Existing
destinations remain usable. Placing a kit near your legacy destination attaches
a physical perch while preserving its name, access and destination identity.
Only Overworld routes are supported.

## Implementation

The bird has sixteen native item displays and 110 cuboids. The real player rides an
invisible carrier; a separate invisible anchor supplies camera position. Because
the vanilla client hides its local player with an external camera, an owner-only
native mannequin renders the passenger's skin and equipment. It is never added to
the server world, player list or saved data. Observers see the real player. No
inventory replacement, spectator switch or player-ability change is used.
Version 0.2.0 replaces the separate vertical departure/cruise/arrival movements
with connected cubic flight paths. Only the start and landing ease to rest.
Heading follows the path; turn rate drives body bank, head anticipation and wing
asymmetry. Climb demand and speed drive powered flight. The first stroke follows
a preparation pose, and a damped lift response moves bird, saddle and rider together.
Grounded foot compensation keeps the claws planted during body compression.

The rig adds a neck, independent feet and an intermediate joint on each wing.
Shoulder, elbow and wrist rotations combine sweep, folding and flapping; the base
cuboid count remains 100. Root translations follow the carrier at 20 Hz and joint
metadata updates at 10 Hz with native interpolation. The private passenger follows
heading changes; its limbs still use the native riding pose, without custom grip
or arbitrary torso animation. Camera lag is bounded and its aim permits limited
subject movement within the frame while retaining both mirrored F5 views.

Version 0.2.1 places each display's native light probe 1.25 blocks above the flight
root, with the inverse offset in its mesh transform. This preserves geometry and
seat alignment while preventing ground compression from sampling the solid deck's
darkness. Lighting still follows the world's sky and block light; no full-bright
override or resource-pack change is used.

Routes above the configured horizontal threshold (500 blocks by default) use a
fade and relocation near the destination. The exit keeps moving through fade-out;
the entry starts moving during fade-in. Destination chunk/journal waits remain
fully hidden. A new settling beat finishes the landing before control returns.
Short routes use a prechecked curved corridor. Obstructed or unavailable corridors
use the abbreviated route. End-point approaches try a bounded set of directions
and reaches in loaded terrain, including a compact vertical alternative. Existing
ports are preserved; no surrounding terrain is cleared or generated for a shot.

Both planning and each actual movement step check swept volumes against blocks,
fluids, loaded chunks and the world border. Route tickets are bounded,
reference-counted and released after landing. The default limit is two concurrent
flights, configurable up to four. Sixteen displays increase tracking/metadata work
relative to the earlier rig; client frame time and multi-viewer network
cost have not been benchmarked. There is no mob AI, per-feather simulation or
full aerodynamic solver. These budgets are not a guarantee for every client.

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
- [Flight direction and implementation scope](art/FLIGHT_DIRECTION.md)

Run Blender with `--background --factory-startup --python aviary/art/build_models.py`.
This creates a separate scene and exports matching vanilla cuboid models. The
construction study leaves the front of the deck open, correcting a fence in the
generated concept. No OBJ/glTF loader or third-party model plugin is required.
The Blender scene uses the same packed pixel textures, face UV extents, parent
hierarchy and base motion samples as the resource pack/server rig. Its 20 fps
timeline previews one base powered-flight cycle. The game additionally applies
action-dependent folding, steering, lift, contact compensation and camera motion
through `BirdMotion`, `BirdRig` and `Journey`; the studio loop is not a preview of
that complete runtime scene. Lighting is a studio preview, not a simulation of Minecraft lighting.
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

Oak's console can inspect the service with `aviary status`. The Aviary page now
edits ports and inspects landing areas. In-game map destination selection and
additional bird species remain outside this release.
