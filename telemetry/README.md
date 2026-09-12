# Oak player telemetry

Version 1.2 adds a separate typed environment controller. Player telemetry remains
read-only; environment writes use their own owner-only socket and root agent.
See [World environment controller](../docs/world-environment.md) for behavior,
persistence, supported controls, and isolated integration verification.

Optional server-only Fabric mod pinned to Minecraft **26.3-rc-2**, Fabric Loader
0.19.5+, and the installed Fabric lifecycle API. Compile with Java 25 against the
installed unobfuscated server and modules extracted from the installed Fabric API
archive. Old processed-module caches are excluded from compilation. No remapper or
Minecraft launch is involved. Upgrade the exact Minecraft constraint and compile
again before changing game versions.

The end-tick callback captures immutable UUID/name/XYZ/dimension/yaw snapshots at
10 Hz at 20 TPS, or 1 Hz without readers. One bounded latest snapshot is retained.
Virtual threads serialize and transmit complete changed player batches; stationary
players receive a one-second heartbeat. Full batches deliberately avoid delta
recovery complexity for this server's 12-player capacity. Slow consumers disconnect
after 500 ms; at most four private readers are accepted. No commands, world reads,
chunk loading, or per-frame disk writes occur. The platform label remains the
existing Floodgate name-prefix convention; marker identity uses server UUIDs.

The Unix socket `/run/oak-telemetry/positions.sock` belongs to oak:oak-control with
mode 0660 inside a setgid 2750 directory. There is no TCP listener. Oak Control
shares one connection between authenticated map viewers, closes it without viewers,
validates bounded frames, and forwards them through `/admin/api/positions/stream`.
Sessions are rechecked each second. Ordinary five-second history sampling reads
the same socket and bypasses RCON when fresh; RCON remains the pre-activation or
failure fallback for ordinary snapshots. Live frames never enter SQLite.

The private marker uses UUID identity and a 150 ms interpolation buffer, stops at
the latest observed point, and snaps on dimension changes, jumps above 24 blocks,
or gaps above 1.5 seconds. It never extrapolates indefinitely. Hidden tabs close
their live stream. Rendering rate is independent of the 10 Hz capture rate.
Full map/terrain rendering remains completely independent.

## Player avatars

The extended frame includes body/head direction, pitch, pose, item use, a latched
arm-swing sequence, and equipment. Equipment/profile snapshots refresh twice per
second. Appearance is omitted from unchanged host frames and reconstructed by
the shared API receiver. Game profile signatures and external URLs never leave
the receiver; only allowlisted Minecraft texture hashes are exposed. Skins use a
bounded memory cache behind authenticated endpoints, with redirects disabled.

`public/map-player-avatar.js` uses BlueMap 5.23's exported Three.js 0.147 instance,
existing marker scene, and existing renderer. It never creates a WebGL context.
The model includes standard/slim skins, outer skin layers, classic skin fallback,
vanilla humanoid armor with leather dyes, cape/elytra, and held item sprites or
simple textured block cubes. It interpolates facing and derives walking from
observed displacement; poses and arm animations represent server state. Distant
and offscreen models are hidden while the accessible location marker remains.
Texture references and per-avatar geometry/materials are released on removal.

The installer extracts only allowlisted vanilla PNG textures from the already
installed client archive into private runtime state. These assets are not Git
content. Armor trims, enchantment glint, arbitrary resource-pack item models,
Bedrock custom geometry/emotes, and vehicle models are not replicated. Unsupported
held textures are hidden; unavailable skins use the standard fallback. These are
visual representations of authoritative states, not a full Minecraft renderer.

Geometry checks use Three.js 0.147 with a synthetic texture loader and no GPU:
set `OAK_TEST_THREE` to the absolute `three/build/three.cjs` path, then run
`node --test tests/test_avatar.js`.

## Installation

Run the regular website deployment and separate `scripts/install-control.py`
upgrade for the same reviewed commit. Then stage the mod explicitly:

```sh
sudo python3 /srv/oak/site-repo/scripts/install-telemetry.py <commit> --jdk <java-25-jdk-directory>
```

This builds outside the game directory, retains the artifact under
`/opt/oak-telemetry/releases/<commit>`, installs the mod and runtime permissions,
and reloads systemd. **It does not restart Minecraft.** Activation requires a
separate planned Minecraft restart. Do not invoke this from website deployment.
The build requires a JDK, not just the installed JRE; extracting the matching
Ubuntu JDK and JRE packages into an external build directory is sufficient.

After activation check that the socket delivers fresh frames as oak-control and
that the agent reports `fabric` telemetry. Do not publish actual player frames.
To roll back, remove only this mod jar (or restore `previous.jar` from the release)
and restart Minecraft separately; the ordinary panel retains RCON fallback.

Focused checks: `python tests/test_telemetry.py`, `node --test tests/test_admin_bridge.js`,
the normal administrative tests, and a compile against the exact server jars.
No visual browser review is required for this transport change.
