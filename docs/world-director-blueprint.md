# Oak world director

Status: first release implemented; see `world-environment.md` for the shipped scope.
Regional rules, scenarios, seasons, and map previews below remain future design.
Evidence reviewed on 2026-09-08 against the deployed Minecraft 26.3-pre-2/Fabric
server, Oak Control, and the locally generated 26.3 command report. No game
settings or weather were changed during this investigation.

## Product intent

Make the world's behavior understandable and intentionally configurable from Oak.
The administrator should see what is happening, what policy produced it, what
happens next, and how a proposed edit changes the experience. Player-facing labels
remain Brazilian Portuguese. The workspace is called **Ambiente** within Mundo,
with **Ciclo**, **Clima**, **Regras**, and **Cenários** as progressive sections.

Keep the map as spatial context. Use an editable horizontal daylight timeline,
an actual-versus-configured weather view, grouped rule rows, and a contextual
inspector. Avoid a new grid of disconnected metric cards or a full-screen dashboard
within the dashboard. On mobile the inspector becomes a sheet; timelines retain
numeric alternatives, keyboard controls, and a single deliberate scrolling area.
Map lighting preview must be labeled as a preview and must never change gameplay.

## Verified foundations

- Existing Oak capabilities cover jobs, audit, schedules, roles, reviewed changes,
  backups, and server properties. They do not yet provide world policy management.
- Native `time` supports named clocks, rate, pause/resume, time markers, and queries.
- Clock rate changes clock-dependent timelines; it does not accelerate simulation
  as `tick rate` does. Do not use tick rate for longer days or shorter nights.
- The generated command report exposes `advance_time`, `advance_weather`,
  `players_sleeping_percentage`, `random_tick_speed`, `keep_inventory`, `pvp`,
  `mob_griefing`, `spawn_mobs`, `spawn_monsters`, `spawn_patrols`, `spawn_phantoms`,
  `spawn_wardens`, `spawn_wandering_traders`, `raids`, and other typed rules.
- Native weather commands select clear/rain/thunder with a duration. Frequency,
  coordinated dwell intervals, and temporary policy precedence need a controller.
- The existing telemetry socket is read-only. Do not convert it into a general
  command channel or expose new raw command dispatch through the public API.

Sources:
- https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1
- https://docs.fabricmc.net/develop/game-rules
- Oracle: `/srv/oak/map-test/reports263/reports/commands.json` (read-only evidence).
- Repository: `admin/domain.py`, `admin/runtime.py`, `admin/worker.py`, `telemetry/`.

## Cycle editor

Represent daylight, dusk, night, and dawn as four timeline segments with durations
in human units. Provide two simple duration inputs first, then expose transition
durations in the advanced inspector. Read phase boundaries from supported native
timelines rather than assuming half the cycle is daylight. Show total cycle length
and time until the next phase as estimates derived from actual clock progress.

Modes: native, custom cycle, or paused at a selected phase. Defaults preserve the
current server. Presets are proposals: long building days, balanced survival,
short nights, and an extended sunset for photography.

Use the native clock rate and change it at phase boundaries. Do not repeatedly
reset absolute time, skip days, or move the calendar backwards. Preserve the phase
when applying a new duration; offer application at the next cycle boundary.
The pure planner computes the rate from phase length and requested duration.

Specify whether durations use active simulation time or elapsed wall time.
Default to simulation time, labeled as minutes at normal server speed. Server
pauses and degraded TPS can extend real elapsed duration. Do not silently catch
up by advancing multiple phases when the server restarts.

Sleep is a first-class interaction: preserve native sleep by default, discover
the wake-up marker, and reconcile immediately after a sleep skip. Separate the
sleeping percentage from storm-clearing behavior. Never treat a clock as unique
to a dimension without discovering which dimensions share it.

## Weather director

Offer native climate, managed climate, and a temporary override. Managed controls:
dry interval range, rain duration range, thunderstorm chance conditional on a rain
episode, storm duration ceiling, and minimum gap between storms. Use labels such
as “20% of rain episodes become storms,” not an ambiguous “20% probability.”

Draw the next interval once on a state transition and persist it. Changing the
browser refresh rate must not change the weather distribution. A stochastic
forecast should show a range; only show an exact time when the controller has
actually scheduled that transition. Make the forecast's source explicit.

Examples of proposed profiles: mostly clear, balanced, wet period, and severe
weather. Present human-readable consequences before application. Separate rain
from destructive lightning/fire consequences where the available mechanics allow.

Quick action example: “Clear skies for 20 minutes; then resume the active profile.”
Temporary overrides have a bounded expiry and an explicit resume policy. On expiry
recompute the active base policy rather than blindly restoring an old snapshot.
Sleep and external weather commands have documented ownership behavior.

Vanilla weather state is not arbitrary regional climate. Biomes still determine
visible rain/snow and precipitation eligibility. Do not promise snow in deserts,
independent region storms, or seasonal vegetation without a separate compatible
mechanics/rendering implementation. Validate crossplay effects through Geyser.

## Rules and impact

Group controls around player goals rather than command names:

| Group | Examples | Main caution |
| --- | --- | --- |
| Survival | difficulty, inventory retention, sleep threshold, respawn radius | Changes progression and consequences |
| Creatures | monsters, phantoms, patrols, traders, raids | Affects challenge and farms |
| Protection | mob griefing, projectile block damage, fire propagation | Rule rollback does not undo world damage |
| Social play | PvP and event-specific policies | Define global versus regional scope |
| Simulation | random tick speed, simulation/view distance | Potential CPU load; not a universal growth multiplier |

Explain secondary effects. `mob_griefing` is broader than creeper damage;
`random_tick_speed` does not speed up every machine or entity. Avoid fictional
CPU percentages. Build a capability registry from the installed version, with
supported ranges, native identifier, scope, apply mechanism, and restart needs.

## Scenarios and automations

A scenario is a versioned set of overrides with duration, permissions, and an
explicit end condition. Start with named templates and a small constrained rule
builder: trigger -> conditions -> supported action -> duration -> cooldown.
Do not introduce arbitrary scripts or an unrestricted visual programming graph.

Potential experiences:
- Building session: longer daylight, clear weather, selected protection rules.
- Adventure evening: extended sunset followed by a bounded storm window.
- Photography session: paused sunset, clear weather, automatic release.
- Competitive session: temporary PvP with an explicit end and current-base resume.
- Climate seasons: alternate dry and wet policies across a server calendar; no
  claim of physical biome/vegetation transformation.
- Recovery preparation: checkpoint before an impactful event, attached to its
  history without automatically restoring the world afterwards.

Future region tools may add protected places, opt-in arenas and arrival effects.
Use event hooks and a spatial index over loaded players, never chunk scans or
forced chunk loading. Regional game rule behavior requires specific implementation;
global gamerules cannot be treated as region-local merely by changing the UI.

## Reliability model

Separate desired policy, observed state, temporary overrides, and execution receipts.
Record revision, owner, affected capability/clock, timestamps, and drift reason.
Suggested precedence: explicit temporary override > active scenario > base policy
> native behavior. Reject equal-priority overlapping scenarios rather than let
them repeatedly overwrite each other. Provide a visible “why this value?” trace.

The mod is the runtime authority and keeps policies operating without a browser
or API process. The administrative service owns authorization and audit. Add a
separate bounded private control socket accessible only through the existing
privileged agent, with typed operations, revision checks, idempotency identifiers,
deadlines, and no caller-specified paths or shell execution.

Validate off-thread, then apply one prepared configuration on the game thread.
Persist a new revision atomically and acknowledge only after durable completion.
Coordinate persistence and application so interrupted commits recover to an
explicit committed or unapplied state. A lost acknowledgement can be reconciled
by receipt instead of replaying an operation. Batch changes need per-field outcome
reporting if the underlying native operations cannot be committed atomically.

Use a small event-driven state machine per managed clock/weather domain. A cheap
tick hook checks the next deadline; no RCON loops, disk writes per tick, repeated
NBT serialization, or scans of entities/chunks. Persist policy changes and important
transitions, with coalesced writes. Publish UI state around 1 Hz and on changes;
the player movement stream remains separate.

Detect commands or other mods changing owned values. Respect manual intervention
by default, surface drift, and require an explicit action to resume management.
If strict enforcement is later offered, make it opt-in and visible. On controller
failure, do not leave a frozen clock or permanent storm silently; retain a small
verified recovery command set and original ownership baselines for safe release.

Restoring settings does not undo fires, deaths, loot, broken blocks, or progression.
World restore is a separate operation with its existing review and backup rules.
After a backup restore, reconcile policy revision against restored world state
before resuming scheduled scenarios. Expired events do not replay on startup.

## Delivery sequence

1. Environment foundation: capability discovery, read-only observed state, cycle
   and weather editors, typed control channel, versioned persistence, native mode,
   bounded overrides, conflict handling, and audit.
2. Experience controls: curated rules, profiles, shared review, clear impact
   descriptions, scenario expiry and precise ownership/resume behavior.
3. Event tools: conditional automations, climate calendar, timeline history,
   scenario preview and an opt-in region system with bounded runtime costs.

Acceptance checks target phase changes, fractional clock rates, sleep skips,
shared clocks, empty-server pauses, restart recovery, conflicting edits, external
commands, expired overrides, role enforcement, and state after restoration.
Use a pure planner with a fake clock and synthetic adapter tests first. Add one
isolated game integration exercise for native mutations; do not experiment with
production weather, spawn rules, or destructive events during development.

No default game rules change merely because the feature is installed. Routine
policy edits should apply live after the initial mod installation. A complete
experience includes understandable failure and recovery states, not only controls.
