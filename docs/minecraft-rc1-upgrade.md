# Minecraft 26.3-rc-1 runtime upgrade

Activated at 21:27:32 UTC on 2026-09-10 (18:27:32 in America/Sao_Paulo), after
approximately 40 seconds of downtime. This game maintenance was separately
authorized from website deployment.
The official Mojang version manifest identifies `26.3-rc-1` as the current snapshot.
Java remains 25; DataVersion is 5020 and protocol is 1073742160. Datapack 121.0
and resource-pack 97.1 are unchanged from pre-3.

## Changes

- Official server SHA-1: `7ae096efda2563d58a0c01575256f3db7669d386`.
- Official client SHA-1: `a64d116707456e8c6069776a558936d11a2674e1`.
- Fabric Loader remains 0.19.5; Fabric API moves to 0.160.3+26.3. Download hashes
  were verified against the publishers' metadata.
- Telemetry 1.2.0 and Aviary 0.1.2 were rebuilt with exact `26.3-rc.1` constraints.
  Their class bytes are unchanged from the installed pre-3 builds. Aviary's
  resource-pack format and content remain unchanged.
- The custom Floodgate artifact changes only its exact Minecraft constraint.
  This is an Oak adaptation, not an upstream Floodgate release claim.
- The existing Bedrock bridge now forwards protocol 1073742160 and reads rc-1
  reports. Packet tables, block states, registries, commands and JSON-RPC schema
  compare equal to pre-3. All network-package class bytes are unchanged.
  `scripts/prepare-rc1-bridge.py` reproduces the staging change and rejects a
  different packet/registry delta. The live-status forwarding added since the
  previous upgrade is preserved.
- BlueMap uses the matching rc-1 client archive. Java/Bedrock MOTDs and website
  status identify rc-1. Game settings, keys, world data and generated tiles are
  retained; no provisioning, firewall or DNS changes are involved.

## Checks and scope

The already-started isolated checks passed for Floodgate authentication (including
wrong-key rejection), translated chunks/inventory, environment control and Aviary
short/long travel, interruption and cleanup. Synthetic clients ran only in private
network namespaces. No test chat was sent to production. The owner requested
minimal validation for this small update; no additional full-world boot drill or
expanded test campaign was performed. A real Bedrock device session remains
outside automated coverage, with the bridge's existing limitations unchanged.

## Recovery

The pinned pre-upgrade restic point is
`088c1fb9c230ba06b9866fd346c6dfc4cb66f1d5649a24719abe72b0d5de439d`.
Capture and repository structure checks passed; its authenticated world copy was
materialized, but not boot-tested on rc-1. It contains the pre-3 world and runtime.

A new compatible rc-1 point was captured after activation:
`ffc3c693e21d5dc832f7c65aa2ad790721b8e9672fe381823eb44be0ac0e677f`.
Production Java advertises protocol 1073742160; Bedrock responds on 26.45 and
displays the updated rc-1 MOTD. Game, bridge, Geyser and map timer are active.

External dependencies are preserved separately in the private VM archive
`/srv/oak/upgrades/26.3-rc-1/pre3-external.tar.gz`: crossplay, game/map units and map
resources/configuration. The activation receipt, download manifests, staged
artifacts and class comparison are in the same directory. None belong in Git.

Rollback requires the matching pre-3 snapshot and external dependencies together;
do not downgrade only the JAR against an rc-1-saved world. Stop game/crossplay and
the map timer, preserve the current state, restore the reviewed pre-3 runtime/world
and external files, and remove the game/map `97-rc1.conf` drop-ins. Keep the late
root-only stop helper override. Reload systemd and verify before restoring the
map timer. The unified backup policy stays in effect; the obsolete backup timer
must remain disabled. Use the normal exact-commit deployment for website rollback.
