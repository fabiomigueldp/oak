# Aviary 0.3.0

## Travel

Place a normal bell within six blocks of the aviport center, outside the clear
flight area. Right-clicking it opens Aviary for nearby Java players. Regular bell
behavior remains available. Destinations are sorted by favorites, then distance,
and paginated in groups of ten. Open a destination to see its distance, favorite
action and flight availability. A final server-side check always precedes travel.

The menu includes **Travel: Full / Quick** and **Camera: Follow / Free**.
Equivalent commands are `/aviary travel full`, `/aviary travel quick`,
`/aviary camera follow`, `/aviary camera free`, and `/aviary favorite home`.
Hold Shift for 0.4 seconds to request a safe skip; `/aviary skip` requests it
immediately. Quick travel shortens greeting/boarding/settling and uses the safe
abbreviated route. Full keeps continuous short routes; distances above the policy
threshold still use a cut. Preferences persist under `config/oak-aviary/players`.

Free camera keeps the player's native view and F5 controls throughout travel.
First-person free camera shows the normal mounted view, not an external view of
your own body. Follow camera continues to use the private mannequin for its owner.

## Motion and art

A damped movement controller limits acceleration before each route is validated.
Its complete sampled trajectory is cached per journey; every actual step still
receives the existing collision sweep. This is a planned movement controller,
not autonomous flight AI or a full aerodynamic simulation.

A greeting precedes boarding. Weight acceptance, launch preparation, low-effort
gliding, braking and settling have separate timing. Grounded wings fold through
shoulder, elbow and wrist. Tail steering follows the head/body response. The
follow shot opens during departure and gives arrival more space.

Sixteen item displays and 110 cuboids include two articulated leg bones and an
independent foot on each side. A two-bone solver keeps each foot connected while
the body compresses, then retracts the legs in flight. Native body light probes
remain above the deck. The player's native riding pose is unchanged.

Original synthesized feather, saddle and landing foley replaces the dragon-flap
sound. Quiet sounds are delivered only to the passenger using the resource pack.
Generate them with `python aviary/art/build_sounds.py` (NumPy and FFmpeg).
The geometry generator still exports the same editable Blender and vanilla pack
models. No extra mod or manual installation is needed on the Java client.

## Oak administration

Open **Aviary** in Oak Control to view aviports and active routes, locate a port on
the map, check its loaded landing area, or edit its name, shared access and preferred
departure/arrival headings. Zero degrees faces south; 90 west, ±180 north and −90
east. Automatic headings follow the route. A preferred heading is attempted first;
blocked approaches fall back to other validated directions or compact departure.
Existing registrations default to automatic and survive the upgrade.

Edits require an administrator, the current policy revision and an idle aviport.
The operation result is re-read before the form reports success. Inspection is
read-only and never generates terrain. A clear landing-area check is timestamped;
the full occupied corridor is validated again for the actual route before boarding.

The mod exposes an owner-only Unix socket at `/run/oak-telemetry/aviary.sock` for
the existing privileged Oak agent. It accepts typed status, inspection and aviport
edits only. Request size, duration and concurrency are bounded. World access runs
on the game thread. No public listener or new firewall rule is required.

Deploy the matching Aviary mod/resource pack and Oak Control installation from
the same reviewed commit. Website deployment alone does not upgrade the control
backend or Minecraft mod. Preserve runtime settings, preferences and journals.

## Evidence and limits

The native isolated suite checks model matrices, bone attachments, saddle alignment,
ground clearance, light-probe position, bounded acceleration, persistent favorites,
stale revision rejection, short/long/free-camera trips and interrupted recovery.
HTTP/agent validation tests reject unauthorized roles and out-of-contract edits.
These tests do not render a Minecraft client or approve its perceived animation.

Flight state stays at 20 Hz and joint transforms at 10 Hz with native interpolation.
The rig adds four display entities; multi-viewer bandwidth and client frame time
have not been benchmarked. The original concurrency limit is preserved.
Seamless custom dismount animation, custom passenger limb acting and in-game map
destination selection remain outside this release.
