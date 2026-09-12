# Unified backups

Oak uses a private local restic v2 repository at `/srv/oak/control/repository`.
The Ubuntu-packaged restic 0.16.4 is the tested production engine. Its key is
`/srv/oak/control/repository.key` (root-only); the API cannot read either path.
The repository and its key must both survive to recover data. The repository is
local protection. The separate [external recovery export](operator.md#external-recovery-copy)
copies it and selected control state to the owner's computer; it is not a VM image.

## Capture and storage

The existing world lock and legacy backup lock serialize captures, verification,
maintenance and restores. Online capture persists save-disabled intent, flushes
Minecraft, reconciles a separate copy (eight passes, 120-second copy budget), and
reenables saving before repository work. The source is never restored in place
during verification. A stopped server can also be captured.

Restic reads the staged files, authenticates/encrypts/compresses their content and
deduplicates chunks across snapshots. Capture checks repository structure before
marking its index/integrity evidence successful. Full pack reading is a separate
scheduled or manual operation. Capture still reads/copies the source inventory;
deduplication saves repository storage, not all source disk I/O.

Each point contains the approved game inventory from `admin/runtime.py`, including
world, mods, libraries, versions, configuration and launchers. An encrypted
manifest records version selection, mod hashes, source size and an external
dependency fingerprint. It does not contain a VM image, the Oak Control database,
systemd units, crossplay directories or generated map tiles.

The private catalog stores names, pins and verification evidence. A complete
repository check reconciles missing catalog records from encrypted manifests;
recovered records default to pinned. Renames, pins and previous drill receipts
are not reconstructed from a lost catalog. Snapshot IDs are immutable full
restic IDs, never abbreviated identifiers accepted from callers.

## One policy

`backup-policy.json` is the revisioned host authority. The separate
`oak-control-worker` service queues policy work using actor `backup-policy`,
independently of user sessions and the administrative API process.
It coalesces downtime, runs one operation at a time, rejects stale host evidence,
and backs off each failed/attempted automatic operation for 30 minutes without
blocking other due kinds. The worker and host agent must be running for scheduling
and execution; restarting only the API leaves them active. The public collector
reports overdue protection from the recorded backup state.

Default enabled production policy:

- Capture every 180 minutes.
- Keep 16 recent points, 7 daily buckets and 4 weekly buckets, as a union.
- Use fixed UTC-03:00 calendar buckets. These are recovery points, not guaranteed
  coverage when captures were missed.
- Preserve every pin, the newest point and the latest point with a successful
  isolated boot, even outside the retention buckets.
- Verify all repository data and test a recent recovery every 7 days.
- Apply retention and reclaim unused data daily, after checking structure.
- Reserve 20 GiB of free disk; use a 20 GiB repository admission budget. A capture
  can exceed the budget by its newly written data; the next capture is blocked
  until space/policy is resolved. Pins are never discarded to meet a budget.

The panel edits interval, retention, budget and check/test frequency. Pausing the
policy pauses automatic captures, tests, verification and cleanup. Manual actions
remain available. There is no second backup schedule in Operations. Existing
legacy schedule rows are disabled when the unified worker sees them due.

Manual deletion forgets a snapshot reference; shared data is reclaimed during
cleanup. Cleanup limits each repack pass to 1 GiB and requires additional free
space. Protected points cannot be manually deleted until their protection is
removed or superseded. Restic's locks are never forcibly unlocked automatically.

## Recovery and evidence

Recovery materialization streams an authenticated snapshot into a temporary tar,
then reuses Oak's bounded archive inspection, extraction and journaled replacement.
Archive paths, types, sizes and NBT header are checked. A drill starts Fabric in
a private network namespace with 3 GiB memory, 50% CPU and 240 seconds maximum;
it requires 4 GiB available host memory. It redirects both telemetry sockets into
the drill directory and hides production world/crossplay/socket paths.

A boot receipt means RCON answered and Java shut down cleanly. It does not prove
every chunk, mod feature or external crossplay integration. The panel distinguishes
structure/integrity, extraction and boot evidence, and exposes full-data check time.

Restore requires the owner, a one-use review, the exact snapshot ID, matching
external dependencies, and no unresolved failed repository check. It captures a
fresh safety point before stopping the game, retains the previous inventory and
uses the existing restore journal/start guard. The safety capture never prunes
the selected restore source. Generated map tiles require a subsequent refresh.

External units/bridge/Geyser changes invalidate automatic compatibility. There is
no "force" bypass in the API. Review those recoveries from SSH and coordinate the
matching external deployment. See `admin-control.md` for interrupted-restore
recovery; do not delete the journal to bypass the start guard.

## Installation and migration

Run the repository checks, review and deploy an exact commit, then install its
administrative agent using `scripts/install-control.py`. Install the Ubuntu
restic package separately (`sudo apt-get install restic`). From the installed
release:

```sh
sudo python3 scripts/migrate-backups.py prepare
```

This initializes storage with automation paused and records an exact private
inventory of the obsolete archives. Create a fresh point through the panel or
the queue CLI below, run its isolated boot and a full repository verification.
Only after both succeed:

```sh
sudo python3 scripts/migrate-backups.py activate
sudo python3 scripts/migrate-backups.py cleanup
```

Activation disables the old `oak-backup.timer` and redirects `oak-admin backup`
to the same durable queue as the panel. Cleanup is explicitly destructive: it
deletes only unchanged archives inventoried by prepare, after recovery gates.
The migration never restarts Minecraft, renders maps or changes network policy.

Direct queue access, including when the web UI is unavailable:

```sh
sudo -u oak-control env OAK_ADMIN_STATE=/var/lib/oak-control \
  /opt/oak-control/current/.venv/bin/python -m admin backup
```

Inspect failed CLI diagnostics in `control/repository-last-error.log`; failed boot
diagnostics are in `control/drill-reports`. They are private and must not be
published. After abrupt process termination, inspect receipts and restic locks
before any explicit operator unlock. A failed capture may leave an unindexed
snapshot; complete data verification recovers its catalog entry.

## Tests

`python scripts/check.py` includes policy/scheduler tests. With `restic` in PATH,
`python tests/test_backup_repository.py` additionally creates disposable encrypted
repositories, checks deduplication, extracts synthetic worlds, restores only a
temporary synthetic world, tests deletion protection and rebuilds missing catalog
metadata. `tests/test_admin.py` covers permissions, CSRF, review/idempotency and
the underlying copy/restore exception handling. No test sends real chat.
