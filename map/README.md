# External BlueMap palette adapter

`adapter.py` contains the full NBT reader/writer, palette transformation, and MCA region rewriting used by the external workaround, extracted into a portable command-line tool. It is AI-assisted project code, not a native BlueMap modification or an upstream-approved implementation.

The production-specific paths and orchestration have been removed. This package has no dependency on the original working directory, SSH, RCON, systemd, a website, or a private server. Publishing it does not install it into any existing renderer.

## Requirements and use

- Python 3.12+; standard library only.
- An offline copy of a Minecraft 26.3-pre-2 **region directory** and a separate output directory.
- The exact same Minecraft version's generated `reports/blocks.json`. Obtain this with that version's official data generator; it is not bundled here. The program hashes the report for cache invalidation, but cannot establish that the operator chose the correct version.
- Matching client resources supplied separately to BlueMap. This tool does not download or distribute Minecraft assets.

Example (replace the illustrative paths):

```sh
python map/adapter.py --source ./input/region --destination ./render-copy/region --blocks-report ./reports/blocks.json
```

Use `--force` to convert unchanged regions again. `--min-free-gib` defaults to 1 GiB, checked before each region; it is not a strict disk quota or a guarantee against disk exhaustion while writing a large region.

Point BlueMap at a separate **complete renderer world copy**, placing converted region files at the corresponding dimension's region location. The script accepts the region directory explicitly and therefore does not assume either the legacy `world/region` path or a newer dimension layout. It does not create `level.dat`, copy other world metadata, remove deleted source regions, or generate terrain. Never use the output as a playable world or replace the original world with it.

Run on a stable offline copy. The input files are read only. A changed source timestamp prevents an incremental stamp from being recorded, but this is not a coherent live-world snapshot protocol. Stop/synchronize the renderer separately before switching its inputs; whole-directory publication is not atomic.

## Transformation and preservation

The adapter converts string palettes and compound entries with `id`/`properties` or empty-key wrappers into `Name`/`Properties`. It fills omitted block properties from the supplied default-state report, then applies explicit saved values over the defaults. Palette order, packed block indices, DataVersion, other parsed NBT fields, and region timestamp tables are preserved semantically. Compression and sector allocation are rewritten, so the output is not byte-identical to the input. Unknown block IDs without an entry in the supplied report retain their explicit properties; correct rendering of such blocks is not guaranteed.

Incremental stamps incorporate adapter revision, report SHA-256, source size, and source mtime. A missing destination is regenerated. Keep the output and stamps together and use `--force` after manually changing either. Bump the adapter revision when changing transformation semantics.

## Supported boundaries

- Inline MCA chunks compressed with gzip (1), zlib (2), or uncompressed NBT (3); output uses zlib.
- Standard NBT numeric, string, list, compound, and array tags used by the observed world.
- External/oversized chunk storage (`.mcc`), LZ4, unknown compression types, and output chunks requiring more than 255 sectors are rejected.
- Basic region bounds and chunk-length checks precede output replacement. This is a trusted-local-data tool, not a hardened parser for hostile or arbitrarily large inputs.
- Source/destination must not overlap. Input file symlinks and symlinks already present in the output directory are refused by the CLI. This is an operator safeguard, not a defense against concurrent filesystem manipulation.
- No strict total-storage budget, stale-region pruning, scheduler, or deployment integration is included.

## Validation and provenance

```sh
python tests/test_map_adapter.py
```

Tests build synthetic NBT/MCA data, covering mixed entry forms, default/explicit precedence, idempotence, packed-index preservation, source integrity, three supported compression modes, rejected input without output replacement, path overlap, and report-based incremental invalidation. They run in the repository's CI without private worlds or Mojang assets.

The original deployment-specific implementation produced the [documented visual evidence](../docs/bluemap-26.3/README.md). The portable extraction adds CLI/path/storage guards and report-aware stamps; its synthetic tests do not replace a full render of every new block or independently establish native compatibility. Existing production installations have not been replaced by this publication.
