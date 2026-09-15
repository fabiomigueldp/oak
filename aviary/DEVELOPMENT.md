# Aviary development

[Index](README.md) · [Usage](GAMEPLAY.md) · [Deployment and diagnosis](OPERATIONS.md)

## Source map

Java files below are under `src/me/oak/aviary/`. Read the relevant row together,
rather than scanning every source file or generated asset. Paths beginning with
`../` are relative to this guide.

| Area | Entry points |
| --- | --- |
| Login, commands, access, queues, shared tick budget | `Aviary.java` |
| Durable policy, anchors, preferences, recovery | `AviaryStore.java` |
| Items, placement, scenery, support lifecycle | `Perches.java`, `PerchMenus.java`, `mixin/IngredientGuard.java` |
| Friend invitations and coordination | `GroupFlights.java` |
| Free flight and companion lifecycle | `Companions.java`, `RoamingBird.java`, `SteeringMotor.java`, `RoamingTickets.java`, `AirLandingSearch.java`, `mixin/GameListener.java` |
| Boarding, travel phases, camera, transfer, cleanup | `Journey.java`, `PassengerVisual.java` |
| Paths, incremental planning, clearance | `LandingSearch.java`, `FlightPlanner.java`, `FlightSpace.java`, `FlightScene.java`, `FlightPath.java` |
| Rig, acting and sound | `BirdRig.java`, `BirdMotion.java`, `BirdTraits.java`, `FlightMotor.java`, `FlightSound.java` |
| Oak transport and input validation | `AviaryControl.java`, `TravelDiagnostics.java`, `../admin/aviary.py` |
| Oak state, jobs and UI | `../admin/runtime.py`, `../admin/domain.py`, `../public/admin/aviary.js`, `../public/admin/aviary.css` |

## State and ownership

`AviaryStore` keeps destination addresses separate from physical `PerchData`.
Preserve the legacy Port record and old preference/settings decoding. Anchors
store support coordinates, appearance, guests and active/hub flags; player
preferences store favorites, discovery, home, five recent arrivals, first-use
guidance and travel/camera choices. Legacy preferences default missing fields;
legacy `network.maxOwnedPerches` is ignored. There is no per-player quota. The
128-address global storage bound and simultaneous-flight budget remain. Runtime files
live outside Git; see Operations for paths.

Custom items use vanilla `DISC_FRAGMENT_5`, native components and server recipes.
Their IDs are requests, not authority. Placement rechecks ownership and inactivity.
Packing/deletion persists before kit issuance; revision/nonce checks prevent stale
forms and repeat refunds. Native recipes reject reserved items as ingredients.

Scenery uses two ItemDisplays and one Interaction, only for loaded anchors within
72 blocks of a player. Both `oak_aviary_temporary` and `oak_aviary_perch` tags
identify it. A loaded missing/unsafe support retires the address in one batched
write per second; a failed write rolls back and retries after five seconds.
Unloaded supports are not missing. Placement previews are owner-only, 2 Hz.

## Journey lifecycle

Prepare and plan → bird approaches → greeting/wait → explicit boarding → durable
recovery journal → takeoff/travel → landing → return control → bird departure.
Long or obstructed routes can fade and transfer near the destination. Transfer
journaling and chunk readiness precede revealing the arrival.

Before boarding, calls must not lock movement, grant immunity or create recovery
records. After boarding, restore camera, transport state and Aviary effects on
every exit. Disconnect recovery chooses a safe registered landing; if none exists,
disconnect with an explanation rather than dropping the player into danger.
Preserve journals until recovery completes. Port reservations include the bird's
farewell; companion trips coordinate departure and landing separately.

`LandingSearch` checks loaded nearby ground at radii 2/3/5/8/12, sixteen angles
and five heights, with duplicate positions skipped. It shares the planner deadline.
A field call starts with a provisional origin; rejected perch geometry or approach
can retry up to eight nearby alternatives per endpoint. Preparation expires after
40 seconds. Chosen coordinates are journey-local: address identity, access and
reservations survive, and recovery journals contain the actual landing coordinates.
The owner-only boarding marker uses eight particles at 2 Hz and disappears when
boarding/cancelling without persistent marker entities. Search never edits terrain
or generates chunks; accepted endpoints use the existing bounded ticket lifecycle.

`FlightPlanner` yields across ticks. The journey loop shares a cooperative 2 ms
planning deadline, not a hard runtime guarantee. No background thread reads the
world. Five conservative pose volumes cover body, wings, tail and feet; planning
and actual movement check swept bounds, fluids, border and chunk availability.
Resting geometry is cached, terrain results are not. Chunk tickets are bounded
and reference-counted. Never load an entire long-distance corridor.

## Free flight and companion lifecycle

One `RoamingBird` retains its rig through call, wait, mount, flight and landing.
`Companions` owns sessions and saved follow/stay/off profiles. Unmounted birds
never load chunks; they disappear beyond 96 blocks or on disconnect. Restore
follow on open ground, or stay within 64 blocks of the saved landing. No offline
AI. Return saves the accessible home/last landing and dismisses the visible bird.
Starting an automatic route suspends the unmounted companion.

Boarding reserves a shared flight slot and writes the existing recovery journal
before mounting. Native `ServerboundPlayerInputPacket` sets movement intent;
player yaw/pitch steers without a detached camera or mannequin. The motor bounds
acceleration to 0.045 blocks/tick², turns the body gradually and slows before
swept terrain, liquid, border or unloaded-chunk collisions. Default glide is
0.38, forward 0.75 and sprint 1 block/tick; these are server intents, not measured
client speeds. Input packets do not directly supply position or velocity.

Mounted birds hold reference-counted origin/current/look-ahead chunk areas, at
most 27 chunks per rider. Surface landing searches 65 loaded columns within 20
blocks, sharing the journey planning deadline. It checks the final pose sweep,
navigates to the approach, checks again, journals the landing and dismounts only
on safe ground. Disconnect keeps recovery; successful landing clears it.
Non-fall damage aborts to safe ground before normal damage handling. Unmounted
calls grant no protection. Terrain is never modified.

Follow uses an offset target with distance hysteresis, bounded acceleration and
local obstacle alternatives. It is not a general maze pathfinder. Indoor owners
do not pull the bird through walls. Idle birds update at 4 Hz; airborne roots
reuse the existing 20 Hz translation/10 Hz articulation. Packet interpolation
does not provide client-side prediction; real network latency still matters.

## Rendering and art

Each bird has sixteen native displays. Root translation runs at 20 Hz; articulated
metadata at 10 Hz with native interpolation. No per-feather entities or mob AI.
An invisible carrier holds the player; automatic routes use a separate camera anchor.
An owner-only mannequin supplies skin/equipment when vanilla hides LocalPlayer.
It is packet-only, absent from world persistence; observers see the real player.
Equipment sends only on change; cleanup removes the private entity.

Keep native item-renderer Y(180°) compensation, parent pivots and the fixed saddle
consistent. Displays sample light 1.25 blocks above the root, with the inverse
mesh offset; do not replace world lighting with full brightness. Wing folding,
bank, head anticipation and foot contact derive from motion/clearance. A studio
flap loop does not reproduce the complete game scene or lighting.

| Change | Authoring source and output |
| --- | --- |
| Bird geometry/UVs | Blender `art/build_models.py` → `condor.blend`, rig JSON and pack models |
| Base motion | `art/motion.py`, `art/condor-motion.json`; runtime adds acting/steering |
| Perch/whistle | `art/build_perches.py` → models, textures, recipes; Blender also writes `perches.blend` |
| Plumage | `art/build_plumage.py` → inherited ash/amber models and textures |
| Call sounds | `art/build_sounds.py --calls-only` preserves existing flight foley |

Run Blender generators with:

```sh
blender --background --factory-startup --python aviary/art/build_models.py
```

Substitute the perch generator when needed. Update generator and exported assets
together. Every concrete model needs a resolvable `particle` texture, including
inherited palette overrides. Keep native post effects in `build.py`; no core-shader
replacement. Detailed dimensions/materials: [item authoring](art/PERCH_OBJECTS.md).

## Oak contract

The typed socket supports `status`, `check`, `edit`, `policy`; it is not a command
executor. `admin/aviary.py` strictly validates portal inputs. Writes use the
authenticated `aviary_edit` job and current revision; busy destinations reject
edits. Keep drafts separate from observed state and refresh after a successful job.
Inspection carries a timestamp/revision and does not promise a clear full route.
Owner/guest management remains in-game. Runtime-only `diagnostics` records calls,
arrivals, failures, preparation wall time and active journey-loop time. The last
16 failure reasons contain no player IDs. These values exclude client frame/memory
cost and do not measure whole-server tick time. See Operations for live reads.
Private status also exposes `companions` (player, phase, riding, x/y/z); Oak shows
their phase and counts mounted birds against the shared flight capacity.

## Verification

Before a commit, run repository-required `python scripts/check.py` and
`node --check public/app.js`. Add only checks relevant to changed behavior:

| Change | Check |
| --- | --- |
| Oak schema/UI | `python tests/test_aviary.py`; `node --test tests/test_aviary_web.mjs` |
| Asset references | `python aviary/tests/check_pack.py` |
| Java, boarding, geometry, recovery | Exact-version build and isolated smoke in Operations |
| Native controls and companion lifecycle | Separate build with `--smoke --smoke-scope roaming`, then the same isolated harness |

The native regression checks recipes, permissions, durability, geometry, short/
long/compact/group/field trips, camera packets and cleanup. Fake clients do not
render frames. Use actual Java-client acceptance for camera/F5, textures, GPU
effects and latency-sensitive cuts; do not infer visual quality or leak freedom
from server tests. Keep current behavior here; use release notes for history.
