# Minecraft 26.3-rc-2 maintenance

Authorized game maintenance on 2026-09-12. The [official rc-2 notes](https://www.minecraft.net/en-us/article/minecraft-26-3-release-candidate-2)
contain one client folder-opening fix. Comparing the official server JARs found
only `DetectedVersion` and `SharedConstants` changed; packet, block and registry
reports are identical. Protocol is 1073742161, DataVersion 5021, Java 25,
datapack format 121.0 and resource-pack format 97.1.

Official SHA-1 hashes: server `26191a9be8f6bc3490dc368f94700a28508e04f1`,
client `5aec5ff7200d8f5778c0ad80209f4f7a84a460cb`. Fabric API is 0.160.4+26.3;
Loader remains 0.19.5. Downloads were checked against publisher metadata.

Telemetry and Aviary were compiled against rc-2. All compiled classes match the
installed versions byte for byte; Aviary's other embedded resources also match.
Only Minecraft constraints change. The installed Aviary resource pack, destination
identities, policy and recovery journals are preserved. The existing custom
Floodgate artifact also changes only its exact Minecraft constraint. The Bedrock
bridge uses rc-2 reports/protocol while retaining its 26.2-facing connection and
existing translation limitations. BlueMap resources and server MOTDs follow rc-2.

Validation is deliberately bounded: artifact integrity, compatibility comparison,
compilation and live startup/status. No repeated flight suite, synthetic login,
world-copy boot or production test chat was required for this version-only delta.
A physical Bedrock session was not exercised. Website checks follow the regular
repository/CI/deployment process. Concurrent activity-page edits in the original
checkout were left untouched; this change uses a separate worktree.

## Recovery

Pinned pre-upgrade restic point:
`37a2cc9cffa306c84419c4954c4bef7277e1d3bf6a81d70ef920311845e1c1b1`.
It contains the rc-1 world and runtime. Crossplay, units and map resources are
preserved separately at `/srv/oak/upgrades/26.3-rc-2/rc1-external.tar.gz`.
Private activation/download/hash receipts and staging live in that directory.

Restore the matching world, runtime and external dependencies together; never
downgrade only the game JAR against an rc-2-saved world. Stop game/crossplay and
the map timer, preserve current state, restore reviewed rc-1 recovery material,
remove both `98-rc2.conf` unit drop-ins, reload systemd and verify before resuming.
Keep the corrected late stop override and unified backup policy. Do not enable
the obsolete backup timer, replace map tiles or run historical provisioning.

Durable operator jobs: preparation `2d47f0c8-b50b-4eda-b6f2-2154c6cb6572`,
backup `8bb3e8b9-1623-4949-b122-629493200660`,
activation `a1004a79-7297-4f43-b926-c3e4d8d568a8`.
