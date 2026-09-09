# Server presentation

Oak uses one oak tree image and concise names. Java broadcasts two lines:
`Oak` and `Minecraft 26.3-pre-3` (gray). Geyser passes through the description and
actual player counts; its in-game server name is `Oak`. The Bedrock protocol
version remains the version advertised by Geyser, not the Java backend version.
External Bedrock entries do not use Java's server-icon.png.

## Artwork

`assets/oak-source.png` is the original built-in ImageGen output. Optimized RGB
PNG exports in `public/brand/` use Pillow Lanczos resizing without additional
creative edits: 32, 64, 180 and 512 pixels. The ICO includes 16/32/48/64px sizes.
The 64px PNG is installed as `/srv/oak/server/server-icon.png` by the separate
maintenance command. The website deployer installs only the web copies.

Generation prompt:

> Create a finished square Minecraft server icon for Oak. A single iconic mature oak tree, broad compact canopy with subtly irregular lobed silhouette, sturdy short trunk and two readable branches. Carefully crafted restrained pixel art, designed to survive reduction to exactly 64x64 pixels. Large simple pixel clusters, no fine texture, no tiny leaves. Muted pale moss green canopy with only 3 green tones, warm subdued brown trunk. Uniform opaque very dark forest green background #18211c, edge-to-edge square without rounded corners or border. Tree occupies central 76 percent, balanced breathing room. Quiet, adult, precise, unmistakable silhouette. Front view, flat sprite, not isometric. No landscape, ground platform, particles, glow, shadows outside tree, text, letters, frame, mockup or watermark. Output only the finished icon.

## Explicit runtime maintenance

After publishing the reviewed website commit, invoke separately:

```sh
sudo python3 /srv/oak/site-repo/scripts/server-presentation.py --apply
```

This is a version-pinned migration, not a routine website deployment hook. It
requires protocol 1073742159 and no players online. It validates the expected
bridge source, saves original files and metadata under the private directory
`/srv/oak/presentation-releases/<timestamp>/`, pauses active map/backup timers,
and refuses to interrupt a running map or backup job. It stops the game, bridge
and Geyser, installs the icon and configuration, then starts and verifies Java
and Bedrock status. Timers return to their previous activation state. Failure
after stopping triggers restoration of the previous files and service startup.
It never changes world files, map data, DNS, firewall or resource packs.

The external bridge receives `scripts/server_status.py`. Its status branch queries
the Java backend with a three-second socket timeout and bounded packet size,
preserving the real MOTD, icon and counts while advertising protocol 776 to its
loopback 26.2 client. Login and gameplay translation remain unchanged. A failed
backend query is not replaced with invented player counts.

Manual rollback: stop the same three services in an empty-server maintenance
window, restore each saved file using `manifest.json` ownership and mode, remove
only files explicitly recorded as previously absent, then start `oak`,
`oak-bedrock-bridge`, and `oak-geyser`. Restore any paused timers. Do not restore
world data for this presentation change.

## Verification

Run `python scripts/check.py` and `node --check public/app.js`. Status tests use
synthetic packets and mocked sockets. The deployment checks include restoration
of newly introduced assets. Check the PNG dimensions, served asset bytes, browser
header at desktop/mobile widths, and both server-list status protocols. Do not
send chat messages to players as a test.
