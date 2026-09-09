# Autonomous backups and administrative controls

This update supersedes the retention and session behavior in the initial control design and the pre-change inventory in `admin-policy-audit.md`.

## Backup lifecycle

The existing enabled three-hour interval and 20 GiB repository budget are preserved. Old `keep_recent`, `keep_daily` and `keep_weekly` values are ignored when reading an existing policy; saving the policy writes only the current fields. There is no age-based expiration. Every point remains a complete logical recovery point until space is needed.

Automatic evaluations compare observed player activity and fingerprints of included world/runtime files. Unchanged files reuse cached fingerprints keyed by size and nanosecond modification time. The read-only level.dat fingerprint excludes saved clock/weather countdown fields and LastPlayed, while retaining game rules and other metadata. Session locks and the redundant level.dat_old do not create an otherwise idle backup. Unknown metadata formats fall back to an exact-byte fingerprint. The source fingerprint belongs to the latest retained snapshot; deleting that point forces a new capture rather than trusting a missing recovery point.

An idle evaluation advances the next scheduled evaluation without creating a snapshot or disabling game saves. Manual creation always creates a point. Changes outside the normal included files remain outside backup scope. Player activity depends on the existing host collector; a collector outage can miss a visit, but relevant file changes still trigger capture.

The lifecycle reclaims unreferenced data and, under repository or disk pressure, forgets the oldest snapshot and measures reclaimed bytes before considering another. Automatic replacement always retains at least the newest available point. Manual deletion can delete any point, including the last one. Favorites are organizational markers, not retention holds. Successful boot tests do not pin data indefinitely.

The 20 GiB value is a retained-repository budget, not a filesystem quota. Capture and bounded repacking can transiently exceed it and require separate working space. If the only remaining snapshot itself exceeds the budget, it is preserved and `capacity_limited` is reported; automatic deletion never erases the sole recovery point merely to display a smaller number. This physical exception cannot be solved by retention. No fixed number of days is promised.

The unrelated hard-coded 20 GiB source ceiling is removed for restic capture. A disk reserve of 1% of filesystem capacity, bounded between 512 MiB and 2 GiB, replaces the fixed 20 GiB free-space reserve. Archive extraction admission is based on available disk space rather than a fixed 40 GiB expanded size. Path validation, file-count bounds, consistent capture, authenticated restic reads and save resumption remain enforced.

Checks and cleanup continue when automatic creation is paused. Failed automatic operations have a five-minute retry floor. Missed intervals coalesce instead of accumulating a backlog.

## Recovery and concurrency

Restore authenticates and extracts the selected snapshot before replacing files. Historical check failure, a failed boot test, or a changed external dependency fingerprint no longer disables every restoration. The confirmation identifies external service changes; external services remain at their current versions. A complete runtime is still required for full-server restoration. Initialization failure rolls back the journaled replacement.

A fresh backup is no longer a prerequisite for restoration. Atomic moves retain the actual pre-restore state under `control/rollback/<job>`, independently of repository capacity and source corruption. Completed rollback/staging workspaces receive a completion marker and expire after 24 hours during cleanup. Pending/unknown recovery workspaces are never removed by this routine. The panel exposes journal recovery for an interrupted replacement. The server-start guard remains in place until that replacement is resolved.

Two workers claim nonconflicting resource sets. Repository verification/compaction does not block independent game administration. Capture and replacement reserve both world and repository; repository mutations serialize with each other. Host locks and durable receipts remain authoritative across API disconnects. Running cancellation is cooperative at progress boundaries, allowing save resumption or rollback; already-delivered game commands cannot be undone and subprocesses finish their current bounded step.

Normal stop/restart still attempts a graceful save. An explicit service-control path bypasses an unresponsive RCON preflight. `Backup e reinício` retains its literal sequencing; administrators can independently restart through the normal or unresponsive-server controls when no backup is desired.

## Sessions, permissions and interface

Sessions default to a renewable 365-day lifetime (`OAK_ADMIN_SESSION_SECONDS`), with at most daily database/cookie renewal. A five-minute browser heartbeat also renews stream-only workspaces. Expired sessions are not revived. Explicit logout, session revocation and account suspension still terminate access. Device-session entries can be revoked from Access. Stored sessions survive a service restart; normal use no longer encounters a fixed 12-hour cutoff.

Account edits that change nothing do nothing. Promotions preserve sessions and permitted schedules/jobs. Role changes take effect on the next request and cancel only queued/scheduled work that the new role cannot authorize. Suspension revokes sessions. An owner can edit their own role when another active owner remains. Keeping at least one active owner and one usable passkey prevents accidental loss of all access.

Administrators can delete and restore backups. Owner-only access management, raw console and game-operator changes remain intentional privilege boundaries. Routine console/player/configuration/environment operations no longer require the generic review modal. Destructive deletion, restoration and server interruption retain a concise confirmation; restoration no longer requires typed text. Reviews retain binding to actor and exact parameters without the five-minute timer. Source revision checks still prevent a configuration change racing the actual execution.

Server and environment drafts persist per account and tab in sessionStorage. Navigation does not ask to discard successfully stored drafts. Environment drafts can be reapplied against the latest revision explicitly. If browser storage is unavailable, the unsaved-change warning remains. Logout hides private content; a different account does not read another account's drafts.

The backups list shows dates and storage additions; verification details are collapsed. Favorites do not imply health or permanence. Actual oldest point, next evaluation and an idle state replace promised historical coverage and misleading overdue notices for idle servers. Delete remains available on the latest/favorite point. Errors display the concrete returned reason.

Global stream limits are expanded consistently through both proxy and API. Authentication limits are scoped to the client address supplied through the existing trusted reverse-proxy chain. Saved-place and routine-count caps are removed. The console parser no longer rejects benign argument words that resemble lifecycle commands; direct/nested lifecycle dispatch still uses managed operations to preserve world locks.

## Deliberately retained constraints

The audit grouped technical validation with user-facing blockers. Removing valid JSON/type/path checks, CSRF/origin protection, passkey verification, finite world parameters, engine-supported configuration ranges, bounded HTTP payloads or isolated-test resources would not simplify normal administration. These remain. The installed in-game environment controller still detects external drift; the panel exposes reapplication and intervention instead of requiring a discarded draft. This update does not replace the telemetry mod or restart Minecraft.

No unattended raw console/restore schedule, arbitrary host path, shell command or unbounded parallel world operation is introduced. Unknown command delivery is not blindly replayed. Cancellation and interrupted recovery provide explicit resolution paths instead.

## Validation and deployment

Use the required repository checks plus `tests/test_admin.py`, `tests/test_backup_repository.py`, the admin JavaScript checks and model tests. Real-restic tests run on Oracle against temporary synthetic worlds, never the production map. They cover idle skips, runtime changes, oldest-first reclamation, preservation after failed replacement, complete extraction, explicit deletion and rollback independent of a new backup. API tests cover renewable/revocable sessions, promotions, review/idempotency binding and role enforcement. Queue tests cover concurrent resource scheduling.

Deploy the exact reviewed commit through `oak-site-deploy`, then run `scripts/install-control.py` for that same commit as documented in `admin-control.md`. No Minecraft restart or world replacement is part of rollout. The first automatic evaluation after upgrade establishes a content baseline if the previous release did not have one. The policy reader migrates the shape without disabling the existing routine.
