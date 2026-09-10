# Aviary flight direction

Status: direction based on the 0.1.2 implementation at commit `d8ae4f5`.
Version 0.2.0 implements the first runtime pass described below. This document
also contains longer-term direction; it is not a claim of in-game acceptance.

## Implemented in 0.2.0

- Connected curved short routes, bounded oblique end-point approaches and a
  compact fallback; moving exit/entry shots during long-route fades.
- Preparatory wing pose and body compression before launch, lift-driven root
  response, velocity/climb-driven effort, path-driven heading/bank, and a
  settling beat after contact.
- Twelve joints at the same 100-cuboid geometry budget: a neck, split feet and
  three articulated segments per wing. Multi-axis wing sweep/folding and
  planted-foot compensation accompany the shared base clips.
- Shared root movement for bird/rider, explicit passenger heading updates, and
  bounded camera lag/aim offsets that allow movement within the frame.
- Native swept block/fluid checks, loaded-world approach selection, unchanged
  journal/recovery guarantees and no terrain modification.

Still separate work: free-form aerodynamic steering, split/fanning tail geometry,
full leg inverse kinematics, arbitrary passenger limb acting, port approach-heading
UI, and measured client/network budgets at maximum concurrency. The current
controller follows authored curves and uses a small damped lift response; it is
not a full physical flight simulation. The native client still needs to judge the
acting and camera composition.

## Character and visual intent

The condor is a large, attentive, experienced animal carrying someone it protects.
Its movement should read as deliberate effort followed by economical flight.
It notices the passenger, prepares to carry weight, commits to takeoff, settles
into flight, judges its landing and absorbs the final contact.

The viewer should understand those intentions from the silhouette and body
movement before textures, audio or effects are added. Smooth interpolation alone
does not communicate intention or weight.

Biological reference: Williams et al., *Physical limits of flight performance in
the heaviest soaring bird*, PNAS (2020), recorded more than 216 hours of Andean
condor flight and found flapping occupied about 1% of the recorded flight time.
Use the distinction between costly powered flight and economical soaring as a
reference, not a literal schedule for a fantasy bird carrying a player. The study
does not establish that passenger transport or our compact vertical launch is
biologically realistic. Source: <https://pmc.ncbi.nlm.nih.gov/articles/PMC7395523/>.

## Diagnosis of the 0.1.2 mechanical appearance

| Implementation | Visible consequence |
| --- | --- |
| `Journey.yaw` is final and shared throughout travel | No anticipation or response to a turn; the animal always faces one direction |
| `call`, `depart` and `arrive` change only height | The condor arrives and leaves like a lift |
| Departure and arrival each take 80 ticks, independently eased to a stop | Flight reads as separate moving-platform stages rather than one connected action |
| Cruise is a fixed horizontal line with a sine-shaped height arc | Terrain moves underneath a predetermined track |
| `move(current)` freezes the root during fades and transition waits | The bird hovers motionless while the wings continue playing |
| The wing loop repeats every 48 ticks; power is selected by a clock envelope | Wing effort does not respond to the speed or climb the viewer sees |
| Wing joints rotate around one axis and both sides are mirrored | Broad solid paddles, with no folding recovery or steering gesture |
| Root position is independent of wing effort; body has no pitch channel | Strong wingbeats have no visible effect on the carried mass |
| Roll pivots around a world-stable saddle | Good seat attachment, but no shared rise, settling or acceleration response |
| Camera eye and target use constant offsets from the root | Subject is effectively fixed in frame; the environment supplies all apparent movement |
| Passenger proxy receives appearance/equipment but no authored acting | Passenger reinforces the impression of a fixed seat |

The 0.1.2 improvements addressed joint interpolation and local detail. They did
not replace this movement architecture. More noise, faster packets or higher
texture resolution would leave the main problem intact.

## Scene structure

Timing ranges below are starting points for blocking, not uniform timers to apply
to every route. A short everyday trip should keep its recognition and weight cues
without requiring a long cutscene. Skip and recovery behavior must remain available.

| Beat | Body and intention | Camera |
| --- | --- | --- |
| Approach, roughly 2–3 seconds when space permits | Enter on an oblique descending arc, look at the deck, open the tail and prepare the feet before contact | Establish the port and incoming silhouette; keep the flight direction readable |
| Contact and recognition, roughly 0.7–1.1 seconds | Feet arrive before the breast settles; brief compression and damped recovery, then a small look toward the passenger | Hold a clear view of the animal's reaction |
| Boarding | Lower the saddle slightly, accept the passenger's weight, stabilize, look toward departure | Keep rider and saddle readable; avoid cutting during the mounting transition |
| Anticipation, roughly 0.35–0.55 seconds | Lower the body through bent legs, prepare the wings and shift weight toward departure | Remain close to the deck long enough to show the preparation |
| Launch, roughly 1.0–1.6 seconds | Leg push initiates movement, powerful stroke sustains it, feet release and retract after contact ends; gain forward speed through an arc | Let the bird move within the frame before the camera catches up |
| Cruise | Short bouts of effort when climbing/accelerating, longer glides, small directional corrections; head leads a turn and tail follows | Follow a filtered route target with gentle lag and look-ahead, keeping a stable horizon |
| Long-route cut | Continue a validated exit motion; hide relocation during full fade, then resume a compatible entry motion | Preserve screen direction, subject scale and approximate motion across the cut |
| Final approach, roughly 1.5–2.5 seconds when space permits | Lose speed progressively, raise the breast, spread the braking surfaces and extend feet before reaching the deck | Allow the destination to become visible before contact |
| Landing and release, roughly 0.5–0.9 seconds | Feet contact, legs/body absorb weight, wings finish their correction and settle at different times | Hold through the settling gesture, then return control without a framing snap |

Do not substitute a decorative orbit for the current vertical lift. Each turn
needs a visible purpose and enough validated room. Do not wait for an arbitrary
wing-cycle boundary before starting an action, either: transition into an authored
takeoff or braking gesture with a bounded blend.

## Movement relationships

1. **Intent precedes mass.** The head and neck turn toward the destination before
   the breast and heading respond. Foot preparation precedes landing contact.
2. **Effort affects the body.** A downstroke produces a restrained lift response
   after the wings accelerate. The root, saddle and passenger share that response.
   Avoid unrelated sine-wave bobbing, which looks like floating or swimming.
3. **Recovery changes wing shape.** A wing does not retrace its downstroke as a
   single plank. Elbow/wrist folding and sweep should distinguish recovery from
   pushing air. Preserve solid attachment at each pivot.
4. **Turns recruit the whole animal.** Heading change, bank, wing asymmetry and
   tail correction derive from the same turn command. Do not roll periodically
   while flying perfectly straight.
5. **Parts settle at different times.** The breast arrives first, extremities
   follow, and the head maintains a comparatively stable visual target. Keep the
   delay small enough that the rig does not look loose or detached.
6. **Variation is motivated and bounded.** Vary stroke spacing and intensity by
   action, required acceleration and a stable per-flight seed. Use low-frequency
   corrections, never frame-by-frame randomness or a different random pose for
   each display entity.
7. **Stillness is an action.** Grounded rest includes occasional attention shifts
   and subtle breathing, with genuinely quiet intervals. Every part need not move
   continuously. Gliding should feel supported, not frozen or vibrating.

## Rig and passenger

First improve root movement and staging with the existing mesh. Judge the result
as an untextured silhouette, with both a fixed external view and the actual travel
camera. This separates weak animation from camera masking.

Then evaluate a 12–14-part rig, instead of treating eight displays as an artistic
ceiling. Useful additions are a separate neck, an intermediate joint on each wing,
independent feet and, if necessary, a split tail. Each additional part must solve
a visible articulation problem. Numerous separately animated feathers and soft-body
simulation are outside the intended scope.

Add multi-axis shoulder/wrist controls before adding fine feather motion. Define
limits and folded silhouettes that respect the body, rider and landing envelope.
Contact poses must keep feet on the deck while the body compresses; simply moving
the entire existing rig down would sink the claws into blocks.

Keep the actual player attached to the carrier. Calculate the seat from the same
root pose used for the bird, and apply the same movement to the visual passenger.
Dynamic heading must update the private passenger as well as the real carrier.
The existing mannequin/native riding pose does not provide evidence of arbitrary
per-limb acting. Treat free torso lean, hand grips and custom limb animation as
a separate vanilla-client capability investigation, not a promised feature.
Use modest bank and pitch until rider/armor clearance is established.

## Engineering structure

Separate four responsibilities while retaining the existing recovery state machine:

- **Flight plan:** validate a route and its full occupied corridor, including
  approach/departure alternatives. Supply position, direction, speed and clearance
  targets. Preload only bounded route/end-point chunks.
- **Movement controller:** maintain velocity, acceleration, heading and turn rate.
  Follow the safe plan with feed-forward motion and bounded damped corrections.
  Plan connected position/velocity and, where practical, acceleration at adjoining
  segments. Independent ease-to-zero motion for every phase must disappear.
- **Animal animation:** consume action, speed, climb, turn rate and contact state.
  Select authored gestures, vary powered-flight rhythm, calculate root lift and
  secondary motion, then solve the seat and joint poses together.
- **Camera director:** consume the same flight state while filtering small body
  corrections. Use grounded establishment, travel follow and landing framing;
  blend framing continuously with bounded lag. Handle yaw wrapping and preserve
  safe front/rear F5 framing rather than assuming control over the client's F5 mode.

Keep safety phases such as journal completion and chunk delivery separate from
visible action phases. The existing `arrival-load` delay is a readiness barrier,
not a shot. Stay fully faded while it is unresolved. Once ready, reveal a moving
approach. During fade-out, continue only inside a prevalidated exit segment; if
blocked, use an authored braking/holding gesture rather than moving through blocks.

Every actual root step needs an occupied-volume sweep, not only an endpoint test.
Include the articulated bird, passenger and procedural displacement allowance.
Use a conservative boundary near the deck. If the proposed correction would leave
the corridor, reduce it smoothly or select the safe alternative; do not silently
permit clipping or abruptly project the visible bird back onto a centerline.

## Aviport constraint

The current registered port contract guarantees a clear 7 × 7 column, 24 blocks
high. That does not guarantee an unobstructed horizontal approach. No animation
change can safely invent that space beside buildings, trees or a cliff face.

Plan preferred launch/landing directions from a few bounded, loaded-world probes.
Allow an optional approach heading for deliberately built ports. Use available
space for a diagonal launch or flare. Preserve existing port registrations and
choose a compact, authored launch when no side approach is safe. If the required
occupied volume is unavailable, explain the obstruction before boarding.
Never clear terrain, generate an unbounded route or invalidate all existing ports
merely to make the choreography easier.

## Implementation order and acceptance

1. Block a complete short scene: recognize, accept weight, prepare, launch,
   glide, brake, contact and settle. Use the real rider silhouette. Confirm that
   actions remain legible from a stationary view before adding camera movement.
2. Connect route segments and animal/root/seat motion. Include two ports of
   different heights and a narrow but valid port; avoid vertical-stage pauses.
3. Add the justified rig joints and contact-aware feet. Refine secondary action
   after the weight and flight direction already read correctly.
4. Add camera lag, look-ahead and motion-matched long-route transitions. Keep a
   stable horizon and preserve user-controlled F5 modes.
5. Check the complete scene in the vanilla client. Native matrix tests remain
   useful for attachment, clearance and recovery, but cannot approve acting,
   perceived weight, smoothness under network latency or camera composition.

Keep computation bounded: small cached clips, one controller per journey, no mob
AI/pathfinder, no per-feather entities, and no full aerodynamic simulation. Start
with the existing 20 Hz state / 10 Hz pose schedule and native interpolation, then
measure tracking/network behavior before changing that budget. Separately verify
that carrier and display interpolation do not introduce visible relative lag;
organic animation cannot conceal a synchronization defect.

Measure client frame time and outbound entity/metadata traffic with one flight
and the configured maximum concurrency before promising a larger rig is equally
cheap. The target is readable weight, efficient soaring and calm composition,
not maximal movement in every channel.
