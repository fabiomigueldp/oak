# Aviary operations

[Index](README.md) · [Development](DEVELOPMENT.md)

## Read live state first

Oak: `/admin/#aviary`. It shows destinations, anchors, journeys and inspections.
Use the available Oak Operator skill for MCP/SDK/SSH access. Discover once;
`oak_call(method="describe", data={"method":"jobs.submit"})` describes jobs.
Client examples: [operator interfaces](../docs/operator-agents.md).

For a small read-only query, run this as a Python operator job (root on Oracle):

```python
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(12)
    connection.connect('/run/oak-telemetry/aviary.sock')
    connection.sendall(b'{"action":"status"}\n')
    with connection.makefile('rb') as stream:
        state = json.loads(stream.readline(131073))
print({key: state[key] for key in ('available', 'enabled', 'revision', 'error')})
print('ports', len(state['ports']), 'flights', len(state['flights']))
```

Read `network`, `waitingCalls` or individual ports only when needed. Avoid dumping
player names, guests and coordinates for a routine health check. An inspection
request is `{"action":"check","port":"<id>"}`; it returns current state with a
timestamped landing check. It does not generate terrain or validate the full route.

Use Oak's typed jobs for normal edits. `policy` requires the current `revision`,
`fieldPickup`, `discoverPublic`, `maxOwnedPerches` (1–16); optional `maxFlights`
(1–4), `shortcutDistance` (100–500). `edit` requires `port`, `revision`, `name`,
`shared`, `departureYaw`, `arrivalYaw` (null or −180…180); physical perches also
accept `color`, `style`, `birdName`, `hub`. Exact types/fields:
[`admin/aviary.py`](../admin/aviary.py). Refresh stale revisions; do not blindly retry.
Enablement, pack URL/hash, coordinates, ownership and arbitrary commands are not
part of this edit contract. A successful socket request is not a durable job receipt.

## Runtime files

| Purpose | Oracle path |
| --- | --- |
| Mod | `/srv/oak/server/mods/oak-aviary.jar` |
| Policy, ports and physical anchors | `/srv/oak/server/config/oak-aviary/settings.json` |
| Preferences and recovery journals | Same directory: `players/`, `journeys/` |
| Private control socket | `/run/oak-telemetry/aviary.sock` |
| Content-addressed resource packs | `/srv/oak/web/packs/` |
| Releases and rollback copies | `/opt/oak-aviary/releases/<commit>/` |

Keep runtime data outside Git. Query installed version/state instead of relying on
release-note counts or a previous agent's output. Do not rewrite settings while
Minecraft is running or delete recovery records to bypass an installation guard.

## Build, publish and recover

Use a clean reviewed Linux checkout and full commit SHA. The pinned build needs
Java 25, the exact server jar and installed Fabric API. Determine the current JDK
path; do not treat a previous temporary build directory as a permanent dependency.

```sh
python3 aviary/build.py --server /srv/oak/server --jdk /path/to/jdk --output /tmp/aviary-build
```

Output: `oak-aviary.jar`, `oak-aviary-pack.zip`, `build.json` (hashes/size).
Build output must be outside the server tree. Publish mod and pack together:

1. Run relevant checks and required CI. Check for active journeys, recovery
   records, administrative jobs and pending restores before maintenance.
2. If the Oak contract/UI changed, install matching Control with
   `sudo python3 scripts/install-control.py <sha>`; publish the website through
   `sudo /usr/local/sbin/oak-site-deploy <sha>`. [Website runbook](../deploy/README.md).
3. For an authorized mod upgrade, stop `oak`, then run
   `sudo python3 scripts/install-aviary.py <sha> --jdk /path/to/jdk` from that
   clean commit. Ensure Minecraft is restarted even if staging fails.
4. Confirm `oak` and `oak-geyser` active, startup version, socket health, preserved
   destinations and recovery state. The installer verifies public pack SHA-1.
   Reconnecting Java clients receive the new pack.

The installer does not restart Minecraft. Website deployment alone does not
upgrade Aviary. If staging/startup fails, stop Minecraft before restoring retained
`previous.jar` and `previous-settings.json`, preserve ownership/modes, then start
and inspect it. Review any newer runtime edits before replacing policy. For a
first install there is no previous mod: disable Aviary and recover journeys before
removing it. Retain old pack URLs for cached sessions.

For Java behavior changes, build separately with `--smoke`; never deploy that jar.
The harness copies libraries into a disposable world and needs a fresh network
namespace, not production terrain:

```sh
host_namespace=$(readlink /proc/self/ns/net)
sudo unshare --net python3 aviary/tests/run_smoke.py /srv/oak/server /tmp/aviary-smoke/oak-aviary.jar "$host_namespace"
```

Build `/tmp/aviary-smoke` with `--smoke` first. The harness currently launches
`/usr/bin/java`; verify it is Java 25. Read the saved `smoke.log` on failure.
Do not run this multi-minute suite for documentation or texture-reference edits.

## Diagnose by symptom

| Symptom | First evidence |
| --- | --- |
| No destinations | Java/pack status, discovery/access, active anchor, current network policy |
| Placement or takeoff rejected | Loaded support, safe dismount, current corridor; preview is not route approval |
| Stuck after interruption | Recovery journal and safe landing availability; never delete the journal |
| Missing rider or collapsed bird | Matching mod/pack, camera mode, renderer basis/pivots; Development rendering section |
| Bird darkens at ground contact | Light probe/inverse mesh offset; preserve world lighting |
| Client crashes | Client `latest.log` and fatal report first; distinguish native memory, Java heap and GPU failures |

A native `malloc` failure is not evidence of a full Java heap or an Aviary leak.
Check system commit headroom, physical RAM, disk space for page-file growth and
process memory at the failure time. A packet handler named in a JIT compile task
identifies work being compiled, not the source of all memory allocations.
Compare repeated equivalent journeys with and without travel only after restoring
system headroom; monitor memory/entity growth. Do not increase `-Xmx` blindly.

Incident, 2026-09-12: a Java 25 client failed allocating 1,535,896 native bytes in
the C2 compiler. Its report showed 831 MiB free RAM and 225 MiB available against
a 44,913 MiB Windows commit limit. This supports system memory pressure; it does
not establish which workload consumed it or rule out an Aviary contribution.
The separate client warnings about missing `particle` texture references led to
a model/generator fix. That fix is not a demonstrated remedy for the memory crash.
Raw reports and player logs stay outside Git.

References: [Java native-memory diagnosis](https://docs.oracle.com/en/java/javase/25/troubleshoot/troubleshooting-memory-leaks.html),
[Windows commit limits and page files](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows).
