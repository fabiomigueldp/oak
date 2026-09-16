# Minecraft 26.3 release maintenance

Authorized game maintenance on 2026-09-16. Official Mojang manifest artifacts
were verified against their published SHA-1 hashes. Compared with rc-3, server
classes change only in `DetectedVersion` and `SharedConstants`; packet, block
and registry reports are identical. Release protocol is 777 and DataVersion
is 5023. Java remains 25, datapack format 121.0 and resource-pack format 97.1.

Fabric API remains 0.160.5+26.3 and Loader remains 0.19.5. Telemetry and the
current Aviary compile against 26.3 with byte-identical classes and resources,
except their Minecraft constraints. Recent companion and free-flight behavior,
the installed pack, settings, destinations and recovery journals are preserved.
Custom Floodgate changes only its exact Minecraft constraint. The existing
Bedrock bridge targets protocol 777 and release reports while preserving its
26.2-facing connection and existing translation limitations. This maintenance
does not migrate to a different crossplay stack. BlueMap resources and server
MOTDs follow 26.3; map tiles are preserved.

Validation is bounded to artifact integrity, compatibility comparison,
compilation and live startup/status, plus the required repository checks.
No repeated flight suite, synthetic login, world-copy boot or production test
chat was required for this metadata-only server delta. A physical Bedrock
session was not exercised. Python repository checks and JavaScript syntax
checks passed locally; Linux CI and exact-commit deployment remain release gates.

## Recovery

Pinned pre-upgrade restic point:
`0aef943fd626056803c183c0e2fb98aa130f22f9ebfb4fdb1efe8cc9f7387b5d`.
It contains the rc-3 world and runtime. Matching crossplay, units and map
resources are preserved at `/srv/oak/upgrades/26.3/rc3-external.tar.gz`.
Private download, activation and backup receipts live in the same directory.

Restore the matching world, runtime and external dependencies together; never
downgrade only the game JAR against a 26.3-saved world. Stop game/crossplay and
the map timer, preserve current state, restore reviewed rc-3 recovery material,
remove both `99-release.conf` unit drop-ins, reload systemd and verify before
resuming. Keep the corrected late stop override and unified backup policy.
Do not enable the obsolete backup timer, replace map tiles or run historical
provisioning.
