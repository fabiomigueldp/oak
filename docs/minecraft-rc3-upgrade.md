# Minecraft 26.3-rc-3 maintenance

Authorized game maintenance on 2026-09-14. Official Mojang manifest artifacts
were verified against their published SHA-1 hashes. Comparing rc-2 and rc-3
server classes found changes only in `DetectedVersion` and `SharedConstants`;
packet, block and registry reports are identical. The new protocol is
1073742162 and DataVersion is 5022. Java remains 25, datapack format 121.0
and resource-pack format 97.1.

Fabric API is 0.160.5+26.3; Loader remains 0.19.5. Telemetry and Aviary compile
against rc-3 with byte-identical classes and resources, except their Minecraft
constraints. The installed Aviary resource pack, settings, destinations and
recovery journals are preserved. Custom Floodgate changes only its constraint.
The existing Bedrock bridge targets the new protocol and rc-3 reports while
preserving its 26.2-facing connection and existing translation limitations.
BlueMap resources and server MOTDs follow rc-3; map tiles are preserved.

Validation is bounded to artifact integrity, compatibility comparison,
compilation and live startup/status. No repeated flight suite, synthetic login,
world-copy boot or production test chat is required for this metadata-only
server delta. A physical Bedrock session was not exercised. The Windows
repository check hit its known local HTTP connection abort in the mocked chat
test; the standard Linux CI and deployment checks remain the release gates.

## Recovery

Pinned pre-upgrade restic point:
`61f2cd3d39bb1691f6047ffebd905dfb39d76992d4cdcd6eb8bd55bb04f1da1e`.
It contains the rc-2 world and runtime. Matching crossplay, units and map
resources are preserved at `/srv/oak/upgrades/26.3-rc-3/rc2-external.tar.gz`.
Private download, activation and backup receipts live in the same directory.

Restore the matching world, runtime and external dependencies together; never
downgrade only the game JAR against an rc-3-saved world. Stop game/crossplay and
the map timer, preserve current state, restore reviewed rc-2 recovery material,
remove both `99-rc3.conf` unit drop-ins, reload systemd and verify before resuming.
Keep the corrected late stop override and unified backup policy. Do not enable
the obsolete backup timer, replace map tiles or run historical provisioning.
