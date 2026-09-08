# Oak administrative panel: product and architecture blueprint

Status: investigation followed by an implementation authorized by the owner. See [Oak Control](admin-control.md) for shipped scope, limits, tests and installation. The owner deferred off-Oracle backups. Aspirational features below are not all implemented.

Evidence date: 2026-09-07, approximately 23:49-23:57 UTC. Repository baseline: `009101c1db29232d7389f6b9de89e6cfb4b9c451`, also recorded as the deployed website release. Runtime observations are a point-in-time inventory, not a capacity benchmark or a complete security audit. The inspection used read-only SSH queries and the public website. No game commands, test chat messages, restarts, rendering, backups, restores, firewall changes, or deployments were initiated.

## Product decision

Build a private Oak control workspace beside the existing public player dashboard. Its organizing idea is **the world and its history**: every player, place, event, recovery point, configuration revision, and operation can lead to related context.

The product promise is to help its owner understand the world, protect progress, and act with clear consequences. Visual excellence comes from continuity, hierarchy, and trustworthy feedback. The forest identity and large map are already distinctive assets.

Assumptions: one principal owner, a small number of future moderators, one production world/server, desktop beside the game, and mobile administration. These are working assumptions, not confirmed constraints. A future commercial multi-tenant hosting platform would require a different scope.

## Verified starting point

| Area | Observed state | Product consequence |
| --- | --- | --- |
| Host | Ubuntu on Oracle, ARM64 Neoverse-N1, 2 logical CPUs; `free -h` reports 11 GiB RAM, about 6.6 GiB available; no swap | Design for a shared, CPU-constrained machine. Available memory is a momentary observation, not reserved capacity. |
| Disk | Root ext4 volume reports 193 GiB total, 27 GiB used, 167 GiB available | Current storage headroom exists, but historical maps and retention need explicit budgets. |
| Other workloads | Several unrelated application, worker, proxy, and PostgreSQL containers | Oak cannot assume the whole VM belongs to Minecraft. |
| Minecraft | `oak.service` running Fabric launcher through Java 25; heap 2-6 GiB, service memory ceiling 8 GiB | README's Vanilla description is stale. Preserve actual Fabric compatibility in decisions. |
| Mods and crossplay | Fabric API `0.159.4+26.3`, Floodgate Fabric; active Geyser and custom Bedrock bridge services | Model Java/Bedrock identity and component health explicitly; the custom bridge deserves a separate compatibility/security review. |
| Game settings | Configured maximum 12 players; survival, normal difficulty; view distance 20, simulation distance 6; online mode enabled; whitelist disabled | Useful initial typed settings inventory. These values do not establish supported load. |
| Structured management | `management-server-enabled=false`, host `localhost`, TLS enabled, port 0 | Protocol integration is a candidate requiring deliberate setup and runtime schema discovery; it was not queried or enabled. |
| RCON | Enabled, listener on wildcard port 25575; observed IPv4 INPUT rules allow loopback and reject unmatched inbound traffic | Keep it internal. A wildcard listener alone is not evidence of public exposure; no external or OCI network audit was performed. |
| Website | Dependency-free static frontend, Python standard-library chat/status, Caddy to Nginx, exact-commit deployer | Preserve the public experience and proven release boundaries. Build administrative authentication separately. |
| Public state | Five-second collector, online list, chat, disk/backup file metadata; hardcoded version string | Public status is not an authoritative inventory, historical database, or authorization system. |
| Live observation | Server online, zero players in the inspected sample; current map render completed successfully | No live movement, busy-server performance, or Bedrock login was tested. |

The current map service is a oneshot job. `inactive/dead` after a successful run is normal, not an outage. Several historical experimental map units remained failed; the current `oak-map.service` succeeded. Health should identify managed production components instead of reporting every historical unit as an incident.

Effective configuration matters: the map service's base file specifies 50% CPU and 1500M memory, but active systemd property overrides change the effective ceiling to 100% CPU and 2 GiB. Show effective values and their origin, alongside proposed configuration.

### Backups and persistence

Six local archives totaled 2,068,484,837 bytes, about 1.93 GiB. The latest archive was about 629 MiB, from 2026-09-07 at 05:00 Sao Paulo time. The timer runs daily at that time. The world occupied about 943 MiB in the size inspection.

The external `oak-admin backup` implementation already provides an exclusive backup lock, free-space checks, `save-off`, a confirmed `save-all flush`, gzip/tar creation, a `world/level.dat` membership check, and a complete gzip read for CRC verification. It restores `save-on` in `finally`; the systemd unit also invokes `save-on` through `ExecStopPost` and has a 30-minute timeout. These are meaningful existing safeguards.

The archives include the world and selected configuration/mod/launcher files, including sensitive server configuration. Retention keeps at most 14 archives subject to a 20 GiB budget and free-space reserve, so 14 recovery points are not guaranteed. The size estimate is based on world size even though the archive includes additional files. Existing archives can be pruned before the new backup completes, while keeping the newest existing point.

Remaining gaps:

- No off-host copy or isolated restore drill was evidenced in the inspected Oak scripts and timers. `restic`, `borg`, and `rclone` were absent from PATH. This does not rule out an independently configured OCI or external backup system.
- Archive CRC and a file-presence check do not prove a playable recovery. Version/runtime dependencies and the separate Geyser/custom bridge configuration need a recovery manifest.
- World saving remains disabled during compression and verification. A future pipeline should bound the stable-copy interval and then compress/upload a separate immutable staging copy, after verifying consistency guarantees for the installed mods.
- Backup, map-update, restore, configuration, and maintenance need shared coordination. Existing per-script locks do not establish that contract.
- Failure recovery should persist intent and reconcile actual save/job state after process or host interruption, retaining the existing cleanup protections.

### Map pipeline

BlueMap CLI 5.23 consumes an adapted isolated renderer copy for Minecraft `26.3-pre-2`. The render timer waits one minute after the preceding run completes. The renderer snapshot occupied about 993 MiB and generated web assets about 1.2 GiB. These are independent of the playable world and backups.

The deployed converter reads live source regions and uses source timestamp checks for subsequent retries; this does not establish a coherent point-in-time snapshot. The portable repository adapter is also explicit that it requires a stable offline input and produces renderer data, not a playable world. A manual map update flushes the game and prioritizes four spawn regions; it is not a universal historical snapshot or recovery operation.

The storage guard stops map rendering above a 20 GiB map footprint or below 30 GiB free disk, and bounds game log retention. Preserve these safeguards while making blocked states and recovery steps visible.

## Experience structure

| Destination | User question | Core content |
| --- | --- | --- |
| Agora | Is the server healthy, and does anything need me? | Plain-language state, freshness, actionable issues, current operation, meaningful recent changes. |
| Mundo | What is happening, and where? | Atlas, dimensions, live players when supported, places, selected area, rendering context. |
| Jogadores | Who is here, and how can I help? | Identity, platform, sessions, contextual moderation, permissions. |
| Backups | How far can I recover, and what proves it? | Recovery points, verification, external copies, retention, restore workflow. |
| Operacoes (UI: Operações) | What is running or scheduled? | Durable jobs, schedules, maintenance sequences, failures and results. |
| Servidor | How is the game and host configured? | Typed game settings, effective runtime settings, component versions, resources. |

Access/security and preferences belong in utility navigation. Console is available globally as an advanced tool, with owner-only raw commands, rather than becoming the main navigation model. Public chat nicknames are never administrative identities.

Desktop: narrow navigation, dominant central workspace, contextual inspector, optional bottom console/time rail. Mobile: Agora, Mundo, Operações, Mais; full-screen details and forms; lazy map loading. An operation continues when its originating page closes.

### Signature workflows

1. **World to player to action.** Select a real sampled player marker, inspect UUID-backed identity, platform, dimension and sample age, follow their movement, then use authorized actions in context. Stop interpolation after stale samples; expose an accessible player list. Movement does not imply terrain freshness.
2. **Incident to surrounding history.** Select an unusual performance interval and see concurrent player sessions, render work, backups and configuration changes. Correlation is presented as evidence to investigate, not proof that a player caused lag.
3. **Recovery point to proven restoration.** Name a point, observe preparation/copy/check/replication separately, and inspect when recovery was last rehearsed. Open a dedicated restore review showing the exact destination, overwritten data, version requirements, downtime and fallback.
4. **Maintenance as a coherent sequence.** Review notice, save, backup, safe stop, approved change, start and validation as one operation. Cancellation only appears at safe boundaries; the interface identifies partial completion and recovery instructions.
5. **Search to reviewed action.** Find players, places, settings and actions from one palette. Common operations use typed forms; consequential ones show concrete impact. Completed actions have a durable record with actor, time and observed result.

Use distinct player-facing language: “Salvar mundo”, “Criar backup”, “Copiar para outro destino”, “Testar restauração”, and “Atualizar mapa”. Three clocks are always distinct: player telemetry sample, saved-world snapshot, and terrain publication. Backup completion, replica completion and last restore test add their own evidence.

### Visual direction and quality targets

Physical scene: the owner checks Oak beside Minecraft in a dim room, then follows an operation from a phone in daylight. Retain the dark forest signature and consider a carefully tuned light appearance after the primary theme works.

Use existing OKLCH tokens as a starting point, a system font, tabular numerals for changing data, open layouts, thin separators, three surface depths, and restrained green. Amber/red identify actual conditions and always include a text/shape equivalent. Keep the map visually dominant; avoid decorative glass, glow, identical metric-card grids and oversized status numbers.

Proposed acceptance targets, not measured guarantees: primary control feedback within 100 ms; ordinary transitions 150-250 ms with reduced-motion support; at least 44 px touch targets; no essential hover-only controls; clear keyboard focus and complete form/error states; usable 360 px layouts and real-device iPhone map checks. The admin shell should become usable before WebGL loads. Never reload the whole page to recover a local widget.

## Engineering architecture

```text
Public player dashboard --> bounded public status/chat projection

Private admin browser --> authenticated admin API --> durable state and events
                                                    |
                                             durable job worker
                                                    |
                                      capability-limited local agent
                                       /          |             \
                           game management    backup/storage   map/systemd
                           telemetry + RCON     controller       controller
```

Recommended starting stack: TypeScript UI with a component framework and reusable accessible controls; Python typed API (FastAPI is a candidate); SQLite/WAL and one explicit durable worker for this single-server workload. Keep the public site independent. Package versions should be selected and locked during implementation. Avoid adding a distributed stack simply to support an ambitious interface.

SQLite has one writer and local-host requirements in WAL mode. Keep transactions short, handle contention, checkpoint deliberately, and back it up with its supported online backup mechanism. Do not share an unrelated application's database by accident. Choose PostgreSQL if concurrency or multi-host requirements actually justify it. [SQLite WAL documentation](https://sqlite.org/wal.html)

Backups, restores and maintenance are persistent jobs, not disposable request callbacks or in-process background tasks. Persist state before acknowledging requests, lease work, retain an operation ID, recover interrupted execution and verify outcomes. FastAPI's background-task facility alone is not that durability contract. [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)

Core records: Server, Capability, ComponentVersion, PlayerIdentity, PlayerSession, Place, Event, Operation, OperationStep, ConfigurationRevision, RecoveryPoint, Replica, RestoreDrill, RenderPublication and Alert. World/dimension IDs, actor IDs, operation IDs and timestamps connect these records. An application audit table is not automatically tamper-proof.

### Game interface and telemetry

Prefer Minecraft's Server Management Protocol for supported structured operations. It uses authenticated JSON-RPC over WebSocket, provides runtime `rpc.discover`, and includes player, allowlist/operator, setting, game-rule and save/stop functions. Its actual schema must drive supported actions. It is currently disabled on Oak and must remain a private service-side connection when introduced. [Official protocol introduction](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-9)

Mojang's 26.3 Snapshot 1 notes changes to log output and recommends this protocol for dependent tools. This strengthens the case for replacing English-log parsing gradually. Exact live position and detailed tick metrics are not established by that documentation. [Official 26.3 changes](https://www.minecraft.net/en-us/article/minecraft-26-3-snapshot-1)

Use a small version-compatible Fabric telemetry integration if required for positions, dimensions, tick times and game events. Keep server-thread work bounded and move serialization/I/O away from it. Prototype against an isolated copied world first. RCON can bridge missing supported actions, but arbitrary repeated polling of player NBT is not a sound default for high-frequency telemetry.

Proposed sampling: presence/events as available from the structured protocol; coordinates every 1-2 seconds only while useful, with configurable privacy and retention; infrastructure samples around five seconds; finer performance aggregation produced locally instead of streaming every tick. Measure overhead under real play before selecting final rates. Browser SSE suits most updates; the Minecraft WebSocket connection can stay behind the API. Reconnect must reconcile snapshots and event cursors.

Every metric carries source, sample time, unit and unavailable/stale states. Separate process running, game responsive, crossplay healthy and website reachable. An external observer is eventually necessary to report VM outages because an on-host panel disappears with the VM.

### Access and operation safety

Use established OIDC/passkey authentication and secure sessions. Enforce owner, administrator, moderator and observer capabilities on the backend, including read access to locations/logs and access to raw console. Use CSRF protection, session expiry and recent authentication for high-impact recovery/access changes. Show routine safe actions immediately; reserve interruption for concrete consequences.

The API runs as an unprivileged account. A Unix socket or similarly private typed interface reaches a narrow local agent; reviewed helpers constrain any privileged operation. The browser never supplies arbitrary host paths, shell strings, systemd unit names or credentials. `oak-admin` currently passes arbitrary text to RCON and should not be exposed directly as an HTTP endpoint.

Return operation receipts with request, target, actor, timestamps, result and verification evidence. A timeout can mean delivery is uncertain. Do not automatically replay non-idempotent commands such as item grants or teleports. Shared world locks and explicit preconditions coordinate save/backup/restore/map-copy/maintenance. Heavy background jobs share resource budgets and yield to gameplay.

Keep public/private projections distinct. The current public status includes operational disk/backup details; future administrative fields should not be shipped to public browsers and merely hidden with CSS. BlueMap custom HTML markers require safe content handling, and chat/player data continue to use textContent. Review proxy trust and the custom crossplay identity chain separately before production administrative access.

### Recovery design

A recovery point records world scope, coherent snapshot method, Minecraft/loader/mod versions, configuration hashes, artifact availability, size, archive integrity, replica status and restore evidence. Include Oak/crossplay service configuration in a protected recovery manifest; never put credentials or world data in Git or public assets.

Candidate backup engine: restic for encrypted, deduplicated external storage, behind Oak's own job/state/restore experience. Repository checks, complete data verification and actual restore drills remain different operations. Object storage provider, account, retention and cost require a later concrete choice. [Integrity checks](https://restic.readthedocs.io/en/stable/045_working_with_repos.html#checking-integrity-and-consistency), [restore documentation](https://restic.readthedocs.io/en/stable/050_restore.html)

Proposed protection objective: evaluate 15-60 minute recovery points during active play, a verified external copy, and regular isolated recovery drills. These are candidate objectives, not current guarantees. Daily archives currently permit roughly a day's progress loss between successful backups. Final frequency, retention and recovery-time targets follow measured copy time, world growth and external storage cost.

Restore sequence: validate manifest and disk budget; rehearse into a separate private destination; record the requested target; make a fresh safety point when feasible; stop the intended game instance; restore the compatible world/runtime/configuration; start and validate game access; retain a rollback path until checks pass. Test instances must be isolated from public game/chat/crossplay endpoints. A gzip integrity check does not qualify as this rehearsal.

Region-only rollback is later research. Region files do not encompass all related entities, POI, player inventory and cross-dimension state. Never infer a safe partial restore from map selection alone. An upgraded world may not support downgrade; restoring the matching pre-upgrade checkpoint is the recovery strategy.

## Oak Atlas, extending BlueMap

BlueMap 5.23 advertises Minecraft support through 26.2 and adds SSE updates; Oak's 26.3-pre-2 path remains adapted. Fabric availability on the server does not prove that the 5.23 Fabric artifact supports this prerelease. [BlueMap 5.23 release](https://github.com/BlueMap-Minecraft/BlueMap/releases/tag/v5.23)

Keep BlueMap terrain rendering, then add Oak-owned places, search, player inspection, role-filtered overlays and a small version-pinned viewer bridge. Existing documented markers support points, lines, shapes and extrusions. Integrated live player data usually comes from a compatible mod/plugin backend; externally hosted assets need live-route proxying. Existing CLI output cannot create missing live telemetry. [Markers](https://bluemap.bluecolored.de/wiki/customization/Markers.html), [plugin configuration](https://bluemap.bluecolored.de/wiki/configs/Plugin.html), [external hosting](https://bluemap.bluecolored.de/wiki/webserver/ExternalWebserversFile.html)

Maintain one WebGL scene, device budgets and accessible list equivalents. Use documented custom-script/style hooks first. If iframe constraints become material, maintain a custom viewer build against pinned BlueMap contracts before considering a renderer fork. [Customization](https://bluemap.bluecolored.de/community/Customisation.html)

Progression:

- First: named places, dimensions, player overlay and inspector, actual render/snapshot freshness.
- Next: recorded session/event replay, selected-area render requests, world-border and portal context.
- Later: historical terrain comparison for explicitly retained checkpoints with separate generated outputs and a storage/render budget.
- Research: safe region recovery, entity layers and performance hotspot attribution with adequate telemetry.

A time slider may replay recorded events/positions immediately once collected. It cannot display arbitrary past terrain without retained compatible world snapshots and rendered outputs. This distinction must remain visible in the interface.

## Build versus adopt

Crafty already documents scheduled commands, backups and chained tasks; Pterodactyl uses a panel plus Wings and Docker-managed game instances. They are useful references and potential alternatives if rapid conventional administration becomes the main objective. Adopting either would require an integration/migration assessment against the current Fabric/systemd/crossplay/map workflow. [Crafty scheduler](https://docs.craftycontrol.com/pages/user-guide/task-scheduler/), [Pterodactyl architecture terminology](https://pterodactyl.io/project/terms.html)

For the requested Oak-specific map/history/recovery experience, the recommendation is a focused custom product that reuses mature infrastructure components. Building a generic multi-game hosting panel would spread effort across capabilities that do not advance this one world's experience.

## Delivery sequence and completion gates

| Stage | Deliverable | Evidence required to call it complete |
| --- | --- | --- |
| 0: foundation | Reconciled inventory, threat/permission model, runtime manifest, telemetry compatibility spike, recovery rehearsal design | Actual Fabric/crossplay versions, supported management schema in an isolated environment, resource baseline, recovery dependencies. |
| 1: useful control center | Authenticated shell, real current state, component health, backup/render inventory, persistent operation/event history | Unauthorized users rejected, stale/unavailable states exercised, public/private fields verified, mobile/keyboard review. |
| 2: dependable operations | Save, backup, external replica, tested restoration, guided commands, configuration diff and explicit maintenance | Mocked dangerous-command tests; interruptions, full disk, stale locks and ambiguous delivery handled; successful isolated restoration. |
| 3: living atlas | Real player positions, identity/platform inspector, follow, places, render freshness, event timeline | Exact-version telemetry compatibility, visibility permissions, measured game overhead, browser/device performance checks. |
| 4: advanced experience | Maintenance recipes, configuration drift, upgrade rehearsals, named historical checkpoints, grounded incident assistance | Demonstrated recovery, measured storage budget, explicit retained-history limits and auditable outcomes. |

A polished first release combines stages 1 and the essential recovery/operation path from stage 2. A strong vertical slice is: sign in, understand the world, create a named recovery point, observe durable progress, verify its external copy, and review the restoration plan. This is enough to establish the product's quality before every advanced feature exists.

Intelligent assistance is optional later: explain an incident from bounded recorded evidence and prepare a reviewable plan. Player chat/logs are untrusted input. Assistance must use the same typed capabilities and permissions as the rest of the panel, never unrestricted shell access or silent high-impact execution.

Timing cannot be credibly estimated from UI scope alone. Exact prerelease compatibility, consistent snapshots, crossplay recovery and safe restores are the major uncertainty drivers. Establish those through bounded prototypes before committing to calendar dates.

## Verification and unresolved items

Existing local checks passed during the review: `python scripts/check.py`, `node --check public/app.js`, `node --check public/map-profile.js`, and all 13 `node --test tests/test_map_profile.js` tests. The checks use synthetic data/mocked RCON. They validate the current repository baseline, not proposed administrative capabilities or a production restore.

Open decisions: owner-only versus delegated moderation; public visibility of precise player locations; willingness to change prerelease versions or install a small Fabric telemetry mod; backup recovery objectives and external destination; whether future multi-server support is actually needed. The proposal proceeds with owner-first administration, private detailed telemetry, the current Fabric setup, and one server.

Unverified: OCI-level policies/backups, busy-server TPS/MSPT and I/O behavior, live Bedrock authentication, complete runtime artifact reproducibility, isolated world startup after restoration, native BlueMap compatibility with this prerelease, and exact management-protocol runtime methods. None should be represented as already working.

Primary local references: `PRODUCT.md`, `DESIGN.md`, `public/index.html`, `public/app.js`, `public/map-profile.js`, `server/collect.py`, `server/chat-server.py`, `scripts/deploy.py`, `deploy/README.md`, `map/README.md`, and the external runtime scripts/units inspected over SSH. Preserve exact-commit deployment, separate runtime state and map outputs, and the existing no-production-chat-test rule during all implementation stages.
