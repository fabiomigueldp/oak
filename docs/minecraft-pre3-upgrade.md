# Minecraft 26.3-pre-3 runtime upgrade

Activated on 2026-09-09 at 02:54:59 UTC (2026-09-08 at 23:54:59 in
America/Sao_Paulo). Minecraft stopped at 02:53:52 UTC with no players online.
This was a separately authorized game-runtime maintenance operation, not a
website deployment. No firewall, DNS, player permissions or world-generation
settings were changed.

## Reviewed release and compatibility delta

The [official pre-3 notes](https://www.minecraft.net/en-us/article/minecraft-26-3-pre-release-3)
list 19 fixes, including villager trades, shovel durability and the float
`mod`/`pow` semantics. The downloaded official `version.json` confirms Java 25,
DataVersion 5019, protocol 1073742159, datapack format 121.0 and unchanged
resource-pack format 97.1. There were no external world datapacks to migrate.

The supplied report correctly leaves packet serialization unverified. Generating
reports from the actual server JAR revealed an additional integration requirement:
`play/clientbound` adds `minecraft:add_transient_block`, shifting 106 existing
packet IDs. Other state/direction tables are identical to pre-2. Blocks,
registries, commands and the JSON-RPC schema compare equal.

Comparing the unobfuscated server archives found 31 changed and three added
classes. In the network package, changes are limited to the game packet type
table, protocol registration and listener interface, plus the new transient
block packet. Existing packet implementation classes are byte-identical. The
new packet encodes a block position and block-state ID; the client handler only
queues a short-lived rendered block. The 26.2 bridge validates and omits that
visual packet, retaining normal authoritative block updates. It does not create
a permanent block to approximate the effect.

## Installed components

| Component | Installed state |
| --- | --- |
| Minecraft server | 26.3-pre-3; official SHA-1 `74b30963f532fa08c5f32311cc7caa0de41bf29e` |
| Matching client resources | SHA-1 `50c9b4d653476352609b6a0e052fb203323220fa` |
| Fabric Loader | Existing 0.19.5, also current in Fabric metadata for pre-3 |
| Fabric API | 0.160.1+26.3, replacing 0.159.4+26.3; download SHA-512 verified |
| Oak telemetry/environment | 1.2.0 rebuilt against pre-3 and the new API; exact Minecraft constraint updated |
| Floodgate | Existing custom 2.2.6-SNAPSHOT artifact; only exact Minecraft constraint changed to `=26.3-pre.3` |
| Python crossplay bridge | Exact pre-3 packet reports, protocol number and transient packet handling |
| Geyser and OakPreview extension | Existing artifacts retained; no pre-2 version literals found in their class/JSON/YAML entries |
| BlueMap | Existing 5.23 CLI, now using verified pre-3 client resources and generated block report |

Floodgate's mixin targets were reviewed against the class comparison and its
mixins loaded successfully in isolated game tests. This metadata adjustment is
an Oak compatibility artifact, not a claim of an upstream Floodgate pre-3 release.
[Geyser officially targets Java 26.2](https://geysermc.org/wiki/geyser/supported-versions/);
pre-3 access still depends on Oak's custom bridge and retains its existing
unsupported-component and visual limitations.

`scripts/prepare-pre3-bridge.py` reproduces the staged bridge transformation from
the external pre-2 bridge and generated pre-3 reports. It requires a new output
directory, checks unchanged block/registry mappings and the expected packet-table
delta, and does not install files or start services. Runtime bridge sources,
generated reports and Minecraft assets remain outside Git.

The game unit also gained `zz-oak-stop.conf`. The old `crossplay.conf` sorted after
`95-telemetry.conf` and appended a second, unprivileged invocation of the root-only
stop helper. During maintenance the first invocation saved and stopped the game,
but the duplicate reported `203/EXEC`. The late override resets `ExecStop` to one
privileged invocation without broadening helper permissions. The telemetry
installer now preserves this ordering for subsequent installations.

## Validation evidence

- Exact-version telemetry compilation passed.
- A disposable world in a private network namespace loaded Fabric API, Floodgate
  and telemetry. Native/custom/paused cycles, weather transitions, rules,
  revisions, receipts, override release, restart expiry and drift detection passed.
- In a separate isolated online-mode test, a fresh Floodgate key rejected an
  invalid-key handshake. An allowlisted synthetic identity authenticated through
  the staged bridge and received play login, position, inventory and translated
  chunks. Valid and malformed transient packets were checked. No production
  player identity, production key or test chat message was used.
- A copy of the verified production backup world loaded on the staged pre-3
  runtime inside a private network namespace. The playable source was untouched.
- Production Java status reports `26.3 Pre-Release 3` / `1073742159`; the bridge
  advertises the 26.2-facing protocol 776. Bedrock RakNet status reports 26.45 /
  2169. Minecraft, Geyser, bridge, chat, collector and control services are active;
  the telemetry socket serves frames.
- BlueMap loaded the pre-3 resources and completed its incremental render with
  “maps are now all up-to-date.” Existing map tiles were retained.

An actual Bedrock device/Xbox login and interactive play session were not executed
by the automation. The synthetic authentication test and UDP status are useful
evidence, but do not establish every Bedrock feature or device behavior.

## Recovery material

Private VM paths (never add these archives or their contents to Git):

- `/srv/oak/backups/oak-20260909T024640Z.tar.gz`: verified online pre-upgrade backup;
  a restored world copy was startup-tested.
- `/srv/oak/upgrades/26.3-pre-3/pre2-offline.tar.gz`: full stopped server, crossplay
  runtime, game units and relevant map configuration/resources. Archive membership
  and the complete gzip CRC read passed before any runtime replacement.
- `/srv/oak/upgrades/26.3-pre-3/activation.json`: activation time and installed
  server, mod and bridge hashes. Official download metadata, generated reports,
  class comparison, staged artifacts and isolated test programs are beside it.

Rollback must restore the matching **pre-2 world and runtime together**. Do not
launch the pre-2 JAR against a world already saved by pre-3. Stop the map/backup
timers and game/bridge, take a fresh recovery point of the current state, extract
the offline archive into a separate private directory, and review it before
restoring the server and crossplay directories. Remove the pre-3 version drop-ins
from both game and map units; retain the corrected late stop override. Reload
systemd, start and verify the restored services, then restore the previous timer
states. Revert the website's displayed version through its normal exact-commit
deployment process. Do not restore map tiles or run historical provisioning.
