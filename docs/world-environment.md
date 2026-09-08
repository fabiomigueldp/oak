# World environment controller

Oak Control exposes **Ambiente**, also reachable from **Mundo**. The first release
controls the native overworld clock, global weather, and nine curated game rules.
Installation starts in Minecraft mode and does not change existing world behavior.
Presets edit a local draft; they do not execute commands. Administrators and owners
review changes through the existing CSRF-protected jobs and audit flow. Observers
and moderators can read the state but cannot change it.

## Runtime behavior

- The exact 26.3-pre-2 native clock manager changes clock rate, not server tick rate.
  Day spans ticks 0–11999, dusk 12000–12999, night 13000–22999, and dawn 23000–23999.
  Each phase accepts 0.25–240 minutes at 20 TPS. These editor phase boundaries are
  a policy convention for the pinned 24000-tick daylight timeline. Native sleeping
  still advances the clock. Clock pause does not pause entities or the server.
- Managed weather alternates bounded clear and wet intervals. Storm chance applies
  to an eligible wet interval, with a duration cap and minimum gap between starts.
  Timers follow persisted simulation ticks; server downtime does not consume them.
  Returning to Minecraft restores the captured native weather/timer baseline.
- Temporary weather overrides have UTC deadlines, limited to 120 real minutes.
  Expiry is handled on the next game tick, including after restart or a paused server
  resumes. The active climate policy resumes automatically. External changes that
  suspend the controller cancel overrides instead of keeping them indefinitely.
- External clock-rate/pause/advance or weather-state changes suspend automatic
  control. Oak releases values it still owns and preserves the detected external
  change. Review and reapply a policy to resume. Native sleep clearing the weather
  at dawn is accepted. Arbitrary time jumps alone do not constitute a conflict.
- Rule edits are persistent policy, applied on configuration and startup. Oak does
  not rewrite rules on every tick. Removing a rule from a programmatic policy stops
  managing it but does not restore its former value. The UI retains edited rules;
  choose their desired values explicitly. Native cycle/weather presets do not reset
  rules. Reversing a rule does not undo gameplay consequences.

Weather is world-global; it is not a per-region climate. Biomes still determine
visible precipitation. Clock-dependent timelines can share a clock across dimensions.
No chunk scans, chunk loads, map tile changes, per-player tasks, or simulation speed
changes are needed. The controller checks its small state once per game tick,
publishes one snapshot per second, and coalesces disk checkpoints. The panel polls
only while Ambiente is open, once every three seconds; it never streams policy
state through player positions.

## Security and durability

The Fabric mod has a separate Unix socket at `/run/oak-telemetry/control.sock`, mode
0600 owned by the game identity. The root administrative agent accesses it; the
unprivileged HTTP service cannot connect directly. The read-only positions socket
retains its existing separate permissions. No new TCP port or raw command endpoint
is introduced. Requests are bounded and use typed allowlists and optimistic revisions.
All native mutations execute on the game thread. Socket and durable-write work
execute outside the game thread, apart from startup loading and shutdown flushing.

`/srv/oak/server/config/oak-environment.json` holds policy, restoration baselines,
weather deadlines, revision, and the last 16 request receipts. It stays outside Git
and is included with the existing configuration backups. User changes are fsynced
and atomically renamed before application. The same request UUID can be retried
idempotently while retained. A lost acknowledgement requires refreshing observed
state; a failed HTTP request is not proof the game did not apply the change.
Storage errors suspend control and are surfaced in the panel. Inspect and repair
the state file offline if invalid; do not silently overwrite it with defaults.

## Release and verification

Deploy a reviewed exact commit using the website deployer, then run the separate
control installer and telemetry installer from that commit. Activate the staged
mod with a separately authorized graceful Minecraft restart. Website deployment
alone never restarts Minecraft. See `deploy/README.md` and `telemetry/README.md`.

`tests/test_environment.py` covers typed bounds, permissions, review content,
revision conflicts, idempotency, and simulated expiry without game delivery.
`tests/environment_smoke.py` is an opt-in Linux exact-version integration test:
it requires a private network namespace and copies only runtime jars into a fresh
temporary world. It exercises native clock rate/pause, weather transitions, rules,
revisions, receipts, release, restart expiry and external conflicts. It never uses
production worlds. The JVM-only socket path overrides exist to isolate this test.

Future scope includes named saved profiles, scheduled scenarios, regions and seasons.
They are not exposed as working controls in this release.
