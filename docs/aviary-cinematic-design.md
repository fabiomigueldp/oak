# Oak aviports: cinematic travel feasibility

Status: original design study; see [the current documentation index](../aviary/README.md)
for implemented behavior and compatibility. Some proposals below remain future work. Original investigation:
2026-09-08, against installed Minecraft Java 26.3-pre-2 server/client jars. Scope:
unmodified Java clients with a server-delivered resource pack. Bedrock cinematic
support is explicitly out of scope; the existing Bedrock login path must remain usable.

## Findings from the exact runtime

`ClientboundSetCameraPacket` identifies a client-known entity. The client handler
resolves that entity and invokes `Minecraft.setCameraEntity`, which sets the main
camera entity and checks its entity post-effect. The handler itself does not test
spectator mode. This is evidence for a server-driven camera prototype, not proof
that all camera motion, input, rendering and riding interactions work correctly.

`ServerPlayer.setCamera` also teleports the player to the camera entity and resets
connection position. Do not use this helper as an independent cinematic-camera
API. Keep transport state separate and investigate scoped camera packets instead.
The camera entity must be sent before attachment and retained while referenced.
Display entities expose position/rotation and transformation interpolation; test
camera-anchor interpolation separately from animated model interpolation.

The client's camera setter does not select first/third-person mode. F5 state,
mouse input, FOV, clipping and camera reset paths require a compatibility prototype.
The native `camera_distance` attribute affects third-person distance; it is not
a complete cinematic lens API. Full lens control, roll, arbitrary post-processing,
HUD control and framebuffer crossfades are not promised for vanilla clients.

Sources:
- https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-6
- https://www.minecraft.net/en-us/article/minecraft-26-3-snapshot-2

The current implementation uses native resource-pack post effects, introduced in
[26.3 Snapshot 3](https://www.minecraft.net/en-us/article/minecraft-26-3-snapshot-3).
This was verified against the installed pre-3 server/client APIs. It uses the
built-in blit shader rather than core-shader overrides. A server acknowledgement
of a loaded resource pack does not prove that a client's GPU rendered an effect;
the original caution about real-client acceptance still applies.

## Experience

Within one dimension, routes above 500 horizontal blocks default to abbreviated
travel. The threshold is configurable. Shorter routes traverse a real approved
corridor; scenic full travel can be an optional future mode.

Illustrative timing, subject to prototype:

| Phase | Target | Visible behavior |
| --- | --- | --- |
| Arrival and boarding | 3–5 seconds | Bird approaches, settles and accepts passenger |
| Departure | 3–4 seconds | Clear anticipation, push-off and widening shot |
| Departure cut | 1–2 seconds | Camera holds while bird leaves the frame; image is occluded |
| Transfer | Readiness gated | Hidden relocation; never reveal an unready destination |
| Arrival | 4–6 seconds | Matching motion, destination reveal and controlled landing |

Use separate, authored departure and arrival shot templates in aviport-local
coordinates. A cut preserves direction and motion; a cloud/wings wipe masks the
discontinuity. Merely moving the bird outside the frame does not hide a teleport
of the background. Avoid repeated long cinematics: a shorter travel preference
and a safe skip path should be designed from the start. Times are targets, not
guarantees under all network and rendering conditions.

## Components

- **Oak Aviary server mod:** separate Fabric module for ports, reservations,
  routes, passengers, state machine and recovery. Integrate with Oak's existing
  authenticated agent and jobs; keep player-location telemetry read-only.
- **Transport controller:** authoritative carrier, collision/landing validation,
  one active journey per player, transfer and restoration. Use client-known native
  entity types rather than registering a type that requires a client mod.
- **Bird presentation:** original item-display rig with a small number of animated
  parts, custom item models, textures and sound. Presentation does not own transport
  correctness. Render to nearby Java viewers; keep camera anchors private where practical.
- **Camera director:** per-viewer native camera packets, tracking lifetime,
  interpolation and shot scheduling. Camera failure must restore the player's view.
- **Destination preparation:** bounded chunk tickets around actual start/end scenes,
  scheduled loading and client chunk delivery. A network acknowledgement does not
  prove that a client GPU has rendered the terrain. Hide the cut for an adequate
  bounded interval and provide a safe timeout path.
- **Oak editor:** register platform, approach direction, arrival point and camera
  framing on the map; expose permissions, route availability and travel profiles.

Runtime state and generated resource packs remain outside Git; source models,
pack definitions and code belong in the repository. Resource-pack acceptance
gates the custom visual experience. A pack decline must have an explicit fallback.
Bedrock connections must not receive unsupported camera/model packets.

## State and recovery

`requested -> reserved -> preparing -> boarding -> departing -> covered ->
transferring -> arriving -> landed -> completed`, plus `cancelled` and `recovering`.

Persist journey UUID, player UUID, original relevant state, safe origin/destination,
phase and transfer commitment before relocation. The transfer must be idempotent.
Do not serialize inventory or remove/recreate it to simulate a passenger. Restore
camera, movement and any temporary protections on every exit path. Handle logout,
restart, death, voluntary dismount, missing entity, blocked landing and removed port.
Recovery prioritizes placing the player at a verified safe point, not resuming an
old camera sequence. Gameplay-affecting state should be restored only if the trip
still owns it, preserving unrelated operator changes.

## Performance and acceptance

Abbreviated travel avoids simulating/loading an entire intermediate corridor.
It does not eliminate destination chunk, mesh, texture or network costs. Model and
camera updates must have bounded rates, entity counts and tracking distances;
limit simultaneous departures and release tickets deterministically.

First milestone: one bird and two existing, prepared ports over 500 blocks apart,
with a complete departure/cut/arrival on a vanilla Java client. Check first/third
person, camera motion with latency, passenger appearance, ordinary resource-pack
delivery, deliberate disconnect/recovery and safe skip. This is the decision gate
before building an elaborate editor or a fleet of bird variants. No production
game changes were made during this feasibility investigation.
