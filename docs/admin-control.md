# Oak Control

The private application at `/admin/` extends the public player dashboard. Its
implementation is in `admin/` and `public/admin/`. The [blueprint](admin-panel-blueprint.md)
records the initial investigation and future opportunities, not a list of shipped features.
External backup storage is deliberately deferred at the owner's request.

## Implemented workspaces

| Workspace | Capabilities |
| --- | --- |
| Now | Freshness-aware status, presence, host memory/disk/load, backup evidence, activity and persistent operation indicator |
| World | Persistent BlueMap frame, private player markers, focus/follow, saved places, position history |
| Players | Java/Bedrock identities, visits, kick/ban/pardon, whitelist, operator changes and player-to-player teleport |
| Backups | Named local checkpoints, archive integrity, isolated extraction, optional isolated game boot, reviewed restoration and safety checkpoint |
| Operations | Durable queue, steps/results, queued cancellation, interrupted receipt reconciliation, interval schedules and audit history |
| Server | Typed settings, revision conflicts and reviewed diffs, explicit start/stop/restart, backup before maintenance and bounded service journals |
| Access | Passkeys, one-use invitations, four roles, account suspension and session revocation |

The interface has a keyboard command palette, dark/light forest themes, responsive
navigation, native review dialogs and status announcements. Untrusted text is
rendered through text nodes, never HTML interpolation.

## Local development

Requires Python 3.12+ and Node 22+. Windows uses the explicit simulator; the live
host agent requires Linux. Install dependencies in an isolated virtual environment:

```sh
python -m venv .venv
# Use .venv/Scripts/python.exe on Windows, .venv/bin/python on Linux.
python -m pip install -r requirements-admin.txt
python -m admin demo --port 8092
```

Open `http://localhost:8092/admin/` and choose **Explorar demonstração**. Use the
exact `localhost` origin. Demo mode refuses non-loopback origins and never
connects to SSH/RCON. It uses illustrative identities and labels every screen.
Its database persists in `~/.oak-control-demo`; simulated game state is ephemeral.
The demo atlas is an original code-drawn isometric illustration with synthetic
terrain and positions. It does not contact or embed the production map.

```sh
python scripts/check.py
node --check public/app.js
python tests/test_admin.py
node --check public/admin/app.js
node --check public/admin/model.js
node --check public/map-admin-bridge.js
node --test tests/test_map_profile.js tests/test_admin_model.mjs tests/test_admin_bridge.js
```

Tests use generated P-256 passkeys and signed assertions, temporary SQLite/files,
synthetic archives and mocked game delivery. They never send real chat or restore
a production world. They cover origin/CSRF, roles, replay, stale state, idempotency,
review binding, archive traversal, failed copying, journal rollback and settings conflicts.

## Process and authentication boundaries

```text
Browser -> existing HTTPS Caddy/Nginx
  /admin/       -> static interface
  /admin/api/   -> existing website service :8091
                   -> loopback API 127.0.0.1:8092 (oak-control user)
                     -> /run/oak-control/agent.sock (private Unix socket)
                       -> root agent: fixed RCON/systemd/filesystem capabilities
```

No new public port, RCON exposure, firewall rule or DNS change is required. The
agent accepts neither shell commands nor caller-selected paths. The owner-only
raw console operates within Minecraft; lifecycle and function dispatch are
excluded to preserve locks. Game credentials stay inaccessible to the HTTP
process. Root receipts are private. Console results are owner-only.

Passkeys require user verification, resident credentials, exact RP/origin checks
and one-use challenges. Sessions are HttpOnly/Secure/SameSite Strict, last 12 hours,
and use CSRF tokens for mutations. Sensitive actions require authentication within
10 minutes; sign out and back in when fresh authentication is required. There is
no default password or public self-registration.

Reviews bind actor, operation and exact parameters for five minutes. Jobs have
actor-bound idempotency keys. One worker owns the database queue; the agent
serializes world operations and persists a receipt before delivery. Unknown
delivery is never replayed automatically. Permission changes end the affected
account's sessions, invalidate invitations, pause its routines and cancel queued
jobs. Already delivered actions cannot be undone by revocation. The local audit
is not tamper-proof against a host administrator with root access.

## First Oracle installation

Review the candidate, run all checks and deploy the exact clean commit through
the established website workflow. Install administrative services separately:

```sh
sudo /usr/local/sbin/oak-site-deploy <full-reviewed-commit>
sudo python3 /srv/oak/site-repo/scripts/install-control.py <full-reviewed-commit>
```

The installer needs Python venv support and access to the Python package registry.
It creates the `oak-control` system identity, code releases under
`/opt/oak-control/releases/<commit>`, private state under `/var/lib/oak-control`,
and two services. It also installs a Minecraft **start guard** drop-in and reloads
systemd. It does not restart Minecraft, render maps, modify game settings or run
the external map adapter. The guard prevents a later Java start from opening an
incomplete restore. Ordinary website deployment never installs this agent.

Upgrade is rejected while a restore journal or running receipt exists. Never
upgrade during an operation. Activation failure restores previous service files
and the code pointer. A failed first installation can leave its downloaded
release/account/private state for inspection. Old releases and restore staging
are deliberately retained, not automatically deleted.

Issue the initial owner's one-use invitation from SSH:

```sh
cd /opt/oak-control/current
sudo -u oak-control env OAK_ADMIN_STATE=/var/lib/oak-control \
  /opt/oak-control/current/.venv/bin/python -m admin invite --name Fabio --role owner
```

Keep the printed link private. It expires after 15 minutes and is consumed on
successful registration. Its token is a URL fragment, excluded from HTTP request
URLs. The owner completes their device's passkey registration themselves.
For lost keys, issue another owner invitation from trusted SSH, then suspend the
old account after verifying the new access.

For API rollback, stop the API then agent, restore `/opt/oak-control/current` to
the previous reviewed release and its matching units, reload systemd and start
the two administrative services. Keep the start guard and database. Schema v1 is
additive; review migration compatibility before running older code against any
future schema. Website rollback remains independent.

## Local backup contract

New checkpoints live in `/srv/oak/control/backups`, separate from the existing
`/srv/oak/backups` routine. Oak takes the existing backup lock as well. For an
online game, it persists save-disabled intent, disables saving, requires a
confirmed `save-all flush`, copies the approved inventory and checks that the
source matches the staged copy. Background chunk/entity writes observed on the
live 26.3-pre-2 server can continue after the flush. The copier reconciles changed
files across at most eight passes and a 120-second saving budget. It requires a
stable complete inventory before accepting the checkpoint; continuously changing
inputs fail closed. Saving is re-enabled in `finally`, before compression.
A persistent marker and systemd stop hook cover agent failure. This captures
Minecraft's flushed world; it cannot guarantee transactions in arbitrary future
mods with external databases.

Inventory includes world, mods/config, launchers, libraries/versions/Fabric,
properties, EULA and access lists. All remain outside Git and public directories.
Budgets: 20 GiB source, 40 GiB expanded archive, one million archive entries,
20 GiB reserved free disk. Retention keeps at most 14 Oak checkpoints within a
20 GiB archive budget and prunes only after a new verified checkpoint. The newest
point is always kept and can alone exceed the archive budget. Legacy archives
have their own policy and are never pruned by this application.

| Evidence | What was actually checked |
| --- | --- |
| Integrity | Full gzip/tar read and SHA-256 at creation; invalidated on size/mtime change; SHA recomputed before restoration |
| Extraction | Safe paths/types/quotas, isolated extraction and compressed NBT compound header in world/level.dat |
| Boot | Restored Fabric answers RCON and exits cleanly in a private network namespace |

Extraction is not full semantic validation of all world chunks. A boot test
needs the archived runtime and at least 4 GiB available host memory; it is capped
at 3 GiB, 50% CPU and 240 seconds. It cannot reach production ports/chat. Archived
mods execute under the existing game identity with a read-only system and writable
isolated copy. This supports owner-controlled backups, not hostile uploads;
there is no archive upload route. There is no off-Oracle copy.

Failed boot diagnostics are retained under `control/drill-reports/<job>.log`,
with root-only permissions and a bounded log tail. They may contain private
runtime data; inspect them through SSH and never publish or commit them. The
launcher records the host network namespace before dropping privileges, and the
drill checks its own namespace against that identity before starting Java.

## Restoration and interrupted recovery

Restoration requires the owner, recent authentication, a matching fingerprint,
one-use review and typed confirmation. The agent verifies the archive, makes a
fresh safety checkpoint, extracts separately, pauses active backup/map timers
and rendering, stops Minecraft, replaces the approved inventory and checks RCON
when restarting a previously online game. Map tiles remain unchanged and need a
later map update. Archives missing the runtime can be inspected/extracted but are
rejected for panel restoration; their migration requires an operator. Prefer full
Oak checkpoints with successful boot evidence.

`control/restore-pending.json` records each entry's original presence before any
rename. Previous data remains under `control/rollback/<job>`. Ordinary exceptions
trigger rollback. If the process or host dies during replacement, the start guard
blocks new game starts and the panel blocks further mutations. Inspect the
receipt/journal, then explicitly recover the previous state:

```sh
cd /opt/oak-control/current
sudo /usr/bin/python3 -m admin recover-restore
```

Recovery stops the game/renderer, uses repeatable rename checks, preserves
displaced files under `control/discarded`, removes the journal only after the
rollback is complete and resumes previously active services. It can be repeated
after another interruption. Never delete the journal to bypass the guard. Retained
rollback/staging/discarded files require deliberate operator cleanup; reserve
checks protect remaining disk space.

## Telemetry and map limits

Structured management is disabled in the current Fabric game. This release uses
bounded read-only RCON position/dimension sampling: three-second sampling budget,
up to 12 identities per pass, round-robin coverage and 30-second cached-position
expiry. A transport deadline can extend a pass slightly. Presence older than 30
seconds is stale. Host load is not CPU usage percentage. Production TPS/MSPT
remain **not measured**.

Only Overworld currently has rendered terrain. The interface reports unavailable
dimensions instead of locating someone on the wrong map. Private markers validate
postMessage source/origin and disappear after the parent stops updating. Public
map visitors never fetch private positions. The bridge targets [BlueMap 5.23's
application API](https://github.com/BlueMap-Minecraft/BlueMap/blob/v5.23/common/webapp/src/js/BlueMapApp.js)
and [HTML markers](https://github.com/BlueMap-Minecraft/BlueMap/blob/v5.23/common/webapp/src/js/markers/HtmlMarker.js).
Review compatibility when upgrading BlueMap.

Position history is retained for 24 hours; a query returns the latest 720 samples
in its selected interval. The timeline is sampled positions, not per-tick replay
or historical terrain. Follow centers the camera on observed positions. Native
management telemetry, inventories/editors, per-chunk rollback and additional
rendered dimensions remain future expansions.
