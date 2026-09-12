# Oak Operator operations

Oak Operator runs trusted jobs, routines and extensions on the existing Oracle
VM. The portal, CLI, Python SDK and MCP use the same daemon. Closing a client does
not cancel a job. The owner can execute arbitrary host code with the daemon's root
authority. SSH/sudo and Unix peer credentials establish that authority; an `actor`
value only labels the audit record.

Client setup and examples: [CLI, SDK and MCP](operator-agents.md).

## Components

| Component | Process and state |
| --- | --- |
| Portal API | `oak-control`, `/var/lib/oak-control/control.sqlite3` |
| Administrative queue, status and backup schedules | `oak-control-worker`, same database |
| Existing host actions and recovery | `oak-control-agent`, `/srv/oak/control` |
| Operator jobs, routines, events and notebooks | `oak-operator`, `/var/lib/oak-operator` |
| Native game queries and edits | Telemetry mod inside `oak.service` |
| External recovery copy | Windows scheduled task, `scripts/pull-backup.py` |

The production API sets `OAK_ADMIN_BACKGROUND=0`. Its worker is a separate
service, so restarting the API does not stop backup scheduling or administrative
queue processing. A file lease prevents two workers from owning the same queue.
The operator daemon has its own queue and lease.

The operator socket is `/run/oak-operator/operator.sock`. Root and the
`oak-control` service identity may connect. The native bridge uses
`/run/oak-telemetry/operator.sock`. Neither socket needs a public listener.
Only the portal owner can access `/admin/api/operator/`. WebMCP uses that same
session and CSRF protection; it does not carry an SSH key or operator token.

## Install or upgrade

This procedure targets the already provisioned Oracle VM with `oak-control`
installed. Use one clean, reviewed commit for the website, operator and control
services. Run the repository checks before committing:

```sh
python scripts/check.py
node --check public/app.js
python tests/test_admin.py
node --test tests/test_admin_model.mjs tests/test_operator_web.mjs
```

Push the commit and follow the CI requirement in the
[deployment guide](../deploy/README.md). Replace `<commit-sha>` below with the
full 40-character SHA:

```sh
ssh oracle "sudo /usr/local/sbin/oak-site-deploy <commit-sha>"
ssh oracle "sudo python3 /srv/oak/site-repo/scripts/install-operator.py <commit-sha>"
ssh oracle "sudo python3 /srv/oak/site-repo/scripts/install-control.py <commit-sha>"
```

The site deployer leaves `/srv/oak/site-repo` at that commit. Both installers
reject a dirty or different checkout. They create immutable release directories,
switch their `current` symlinks and verify service startup. Existing operator jobs
must finish or be cancelled before an operator upgrade. Resolve active host
receipts and interrupted restores before upgrading the control agent.

These commands do not restart Minecraft. They do not modify DNS, the firewall,
world files or generated map tiles. Python, Bash and Node executables used by
scripts must exist on the VM; installing the operator's Python dependencies does
not install Node.

### Native bridge activation

Stage the reviewed telemetry build separately. `<jdk-path>` must point to the
JDK required by the installed Minecraft version:

```sh
ssh oracle "sudo python3 /srv/oak/site-repo/scripts/install-telemetry.py <commit-sha> --jdk <jdk-path>"
```

The installer builds against the installed server, preserves the previous JAR
under `/opt/oak-telemetry/releases/<commit-sha>/previous.jar`, and stages the new
JAR. Minecraft must be restarted separately during a planned game maintenance
operation before the native bridge becomes available. Website deployment never
performs this restart.

After activation, inspect the native `discover` result. Its implemented methods
cover players/inventories, region inspection, block batches, entities, commands,
events and receipts. Region/block operations use loaded chunks, bounded batches
and a per-tick budget. Multi-page observations are not a single world snapshot.
Native receipts and events are bounded memory records scoped to a Minecraft
process; inspect effects before retrying after a game restart.

## Verify and operate

```sh
ssh oracle "sudo systemctl is-active oak-operator oak-control oak-control-agent oak-control-worker"
ssh -T oracle sudo -n /usr/local/bin/oak-operator discover
ssh -T oracle sudo -n /usr/local/bin/oak-operator jobs
ssh oracle "sudo journalctl -u oak-operator -u oak-control-worker -n 80 --no-pager"
```

Open **Operador** in the portal. It provides execution/output, routines, services,
packages, revisioned Markdown notebooks and events. `discover` lists available
methods; `describe` returns a method's input schema. Use `call` for methods that
do not have a separate UI control. See [agent interfaces](operator-agents.md) for
the stdio MCP configuration and optional authenticated Streamable HTTP.

| Operation | Behavior |
| --- | --- |
| Jobs | Shell, Python, JavaScript or Minecraft commands. Output stays on disk and uses byte cursors. Omitted/null execution timeout has no deadline. |
| Cancel | Cancels queued work or terminates the running process group. Previously delivered effects remain. |
| Routines | Interval, Unix timestamp or named event. Updates require the full definition and current revision. |
| Resources | Jobs declaring the same resource name serialize with each other. Direct SSH/game changes are outside this coordination. |
| Files | Absolute host paths; atomic text writes and optional expected hash. Use SSH or scripts for binary transfers. |
| Script packages | Versioned text files. Installation does not run code; use the returned path as a job/service directory. |
| Datapacks | Installation stores a version. Activation writes its managed world ZIP and runs `reload`; deactivation removes that ZIP and reloads. Existing gameplay effects remain. |
| Services | `services.upsert` writes a root systemd service without starting it. `start/stop/restart` control execution; `enable/disable` control boot policy. Deletion stops and disables the managed unit; source files remain. |
| Notebooks | Markdown, revision checks and retained revision files. Record job IDs, findings and next steps; keep credentials elsewhere. |

Use idempotency keys for retriable submissions. Losing a response does not prove
failure. Read the job or receipt before repeating an effect. A daemon restart
marks uncertain running jobs `interrupted`; it does not automatically replay
them. Use a managed service for programs that should restart under systemd.

## External recovery copy

The old `oak-*.tar.gz` download script does not cover the restic repository.
Use `scripts/pull-backup.py`. It starts `scripts/export-backup.py` over the existing
SSH/sudo connection, downloads to a partial file, checks every manifest hash, then
renames the verified archive and acknowledges the download on Oracle.

The downloader estimates archive size before starting and adds a 256 MiB free-space
reserve. `--reserve-mib` changes that reserve for the current run. It retains the
previous archives until the new copy is verified and acknowledged. Daily full
exports therefore need room for another complete archive before retention runs.

The export includes the restic repository and recovery key, its catalog/policy,
SQLite backups of the control/operator databases, and operator packages,
services and notebooks. It contains the backups already in the repository; it
does not create a new game checkpoint. It is not a VM image and does not include
arbitrary host files, operator workspace files, job output or generated map tiles.
The recovery key is included, so retain the export under the script's private
filesystem permissions.

Run from Windows:

```powershell
& C:\Users\fabio\Projects\oak\.venv\Scripts\python.exe C:\Users\fabio\Projects\oak\scripts\pull-backup.py --destination C:\Users\fabio\Backups\OakRecovery
```

The default retention is three verified exports. The script requires at least
5 GiB free before starting and applies a private Windows ACL to the destination.
It writes `latest.json` with the archive path, verification time, SHA-256 and
`server_acknowledged`. A failure writes `last-error.json`; compare its time with
the latest success because an older error file may remain.

Update the action of the existing **Oak - copia externa diaria** Windows task to
run `.venv\Scripts\pythonw.exe` with the maintained script and destination above.
Preserve its daily 06:00 trigger and missed-start behavior. Use absolute paths and
the Oak project as its working directory. Test a manual run, inspect `latest.json`
and check `Get-ScheduledTaskInfo -TaskName 'Oak - copia externa diaria'`.

Oracle records successful acknowledgements in `/srv/oak/control/external-copy.json`.
The backup API reports `external_copy: true` only for an acknowledgement less than
48 hours old. `external_copy_verified_at` is its Unix timestamp. This records the
operator's verified download; it does not continuously inspect the Windows disk
or prove that every later backup has been copied. If acknowledgement fails, the
local archive can still be valid; `server_acknowledged` will be false.

## Recovery and rollback

Installers attempt to restore the previous release and unit files if their own
installation fails. A manual code rollback requires idle queues, stopping the
affected services, inspecting the previous release and restoring its matching
`current` symlink/unit files. Do not rerun an installer against an existing release
directory or overwrite runtime databases with Git files.

For a game restore, use the existing [backup recovery procedure](backups.md) and
control-agent receipts. External exports back up each SQLite database separately
and copy files individually. They are not one atomic capture across the game,
databases and package files.

Before restoring an external export:

1. Verify the manifest and extract into a separate recovery directory.
2. Stop `oak-operator`, `oak-control`, `oak-control-worker` and
   `oak-control-agent`. Stop managed services that write the restored paths.
3. Review and restore the selected files and databases. Do not reuse old SQLite
   WAL/SHM sidecars with a restored database.
4. Delete all rows in the restored control database's `sessions` table before
   making the API available. Restored sessions may otherwise remain usable.
5. Inspect queued jobs, host receipts and enabled routines in both databases.
   Resolve or disable work that must not execute after recovery. Starting the
   workers can immediately run queued or due work.
6. Restart the reviewed services only after those checks. Recreate managed
   systemd units from their restored definitions as needed.

There is no automatic full-VM restore command in this release. Restoring game
files still follows the separate game maintenance and recovery procedure.
